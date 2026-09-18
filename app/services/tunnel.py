"""Tunnel state machine.

One worker thread per connect or disconnect. In-memory state is reconciled
with the pid and iface files on every read, so the dashboard survives app
restarts and notices tunnels that drop on their own.

    disconnected --connect()--> connecting --iface file + live pid--> connected
    connecting   --nonzero exit / dead pid / timeout--> error(message)
    connected    --disconnect()--> disconnecting --helper done--> disconnected
    connected    --pid gone (seen on refresh)--> error("tunnel dropped: ...")
    error        --connect() / disconnect()--> as above
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services import status as st
from app.services.registry import TRUSTED_CA, Registry, VpnDef, save_servercert
from app.services.runner import HelperRunner, HelperUnavailable

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"
DISCONNECTING = "disconnecting"
ERROR = "error"

HELPER_HINT = "privilege helper not available: run sudo scripts/setup-privileges.sh"
PIN_HINT = "(delete servercert for this VPN in vpns.yaml to trust the new certificate)"


class TunnelError(Exception):
    """Base class for errors the API turns into HTTP status codes."""


class UnknownVpn(TunnelError):
    pass


class MissingCredentials(TunnelError):
    def __init__(self, missing: list[str]) -> None:
        super().__init__("missing credentials: " + ", ".join(missing))
        self.missing = missing


class InvalidTransition(TunnelError):
    pass


@dataclass(frozen=True)
class TunnelState:
    state: str = DISCONNECTED
    interface: str | None = None
    ip: str | None = None
    message: str | None = None
    since: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TunnelManager:
    def __init__(
        self,
        registry: Registry,
        runner: HelperRunner,
        state_dir: Path,
        *,
        connect_timeout: float = 30,
        poll_interval: float = 0.5,
        pid_alive: Callable[[int], bool] = st.pid_alive,
        route_counter: Callable[[str], int] = st.count_routes,
        thread_factory: Callable[..., Any] = threading.Thread,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.registry = registry
        self.runner = runner
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.connect_timeout = connect_timeout
        self.poll_interval = poll_interval
        self._pid_alive = pid_alive
        self._route_counter = route_counter
        self._thread_factory = thread_factory
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._states: dict[str, TunnelState] = {vpn.id: TunnelState() for vpn in registry.vpns}

    # ----- lookups -------------------------------------------------------

    def _vpn(self, vpn_id: str) -> VpnDef:
        vpn = self.registry.get(vpn_id)
        if vpn is None:
            raise UnknownVpn(f"unknown vpn: {vpn_id}")
        return vpn

    def state_of(self, vpn_id: str) -> TunnelState:
        with self._lock:
            return self._states[vpn_id]

    def _set(
        self,
        vpn_id: str,
        state: str,
        *,
        interface: str | None = None,
        ip: str | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock:
            self._states[vpn_id] = TunnelState(state, interface, ip, message, _now())

    def _clear_files(self, vpn_id: str) -> None:
        st.pid_path(self.state_dir, vpn_id).unlink(missing_ok=True)
        st.iface_path(self.state_dir, vpn_id).unlink(missing_ok=True)

    def _spawn(self, target: Callable[..., None], *args: Any) -> None:
        self._thread_factory(target=target, args=args, daemon=True).start()

    # ----- reconciliation ------------------------------------------------

    def refresh(self) -> None:
        """Bring in-memory state in line with the pid and iface files on disk."""
        for vpn in self.registry.vpns:
            with self._lock:
                current = self._states[vpn.id]
                if current.state in (CONNECTING, DISCONNECTING):
                    continue  # a worker owns this id right now
                pid = st.read_pid(self.state_dir, vpn.id)
                alive = pid is not None and self._pid_alive(pid)
                if alive:
                    iface = st.read_iface(self.state_dir, vpn.id)
                    if current.state != CONNECTED:
                        if iface is not None:
                            self._set(
                                vpn.id,
                                CONNECTED,
                                interface=iface[0],
                                ip=iface[1],
                                message="adopted running tunnel",
                            )
                        else:
                            self._set(
                                vpn.id,
                                ERROR,
                                message=(
                                    f"openconnect is running (pid {pid}) but no tunnel is "
                                    "configured; disconnect to clean up"
                                ),
                            )
                    continue
                if pid is not None:
                    self._clear_files(vpn.id)
                if current.state == CONNECTED:
                    reason = self._failure_message(vpn.id) or "process exited"
                    self._set(vpn.id, ERROR, message=f"tunnel dropped: {reason}")

    # ----- actions -------------------------------------------------------

    def connect(self, vpn_id: str) -> None:
        vpn = self._vpn(vpn_id)
        if vpn.missing_credentials:
            raise MissingCredentials(vpn.missing_credentials)
        self.refresh()
        with self._lock:
            current = self._states[vpn_id].state
            if current in (CONNECTING, CONNECTED, DISCONNECTING):
                raise InvalidTransition(f"{vpn_id} is already {current}")
            pid = st.read_pid(self.state_dir, vpn_id)
            if pid is not None and self._pid_alive(pid):
                raise InvalidTransition(
                    f"{vpn_id} still has a running openconnect process (pid {pid}); "
                    "disconnect it first"
                )
            self._set(vpn_id, CONNECTING)
        self._spawn(self._connect_worker, vpn)

    def disconnect(self, vpn_id: str) -> None:
        self._vpn(vpn_id)
        self.refresh()
        with self._lock:
            current = self._states[vpn_id].state
            if current in (CONNECTING, DISCONNECTING):
                raise InvalidTransition(f"{vpn_id} is busy ({current}); try again shortly")
            has_pid = st.read_pid(self.state_dir, vpn_id) is not None
            if current == DISCONNECTED and not has_pid:
                raise InvalidTransition(f"{vpn_id} is not connected")
            self._set(vpn_id, DISCONNECTING)
        self._spawn(self._disconnect_worker, vpn_id)

    def connect_all(self) -> dict[str, list]:
        started: list[str] = []
        skipped: list[dict[str, str]] = []
        for vpn in self.registry.vpns:
            try:
                self.connect(vpn.id)
                started.append(vpn.id)
            except (MissingCredentials, InvalidTransition) as exc:
                skipped.append({"id": vpn.id, "reason": str(exc)})
        return {"started": started, "skipped": skipped}

    def disconnect_all(self) -> dict[str, list]:
        started: list[str] = []
        skipped: list[dict[str, str]] = []
        for vpn in self.registry.vpns:
            try:
                self.disconnect(vpn.id)
                started.append(vpn.id)
            except InvalidTransition as exc:
                skipped.append({"id": vpn.id, "reason": str(exc)})
        return {"started": started, "skipped": skipped}

    # ----- reads ---------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        self.refresh()
        return [self._describe(vpn) for vpn in self.registry.vpns]

    def snapshot_one(self, vpn_id: str) -> dict[str, Any]:
        vpn = self._vpn(vpn_id)
        self.refresh()
        return self._describe(vpn)

    def log_tail(self, vpn_id: str, lines: int = 50) -> list[str]:
        self._vpn(vpn_id)
        return [
            st.strip_timestamp(line) for line in st.read_log_tail(self.state_dir, vpn_id, lines)
        ]

    def _describe(self, vpn: VpnDef) -> dict[str, Any]:
        s = self.state_of(vpn.id)
        routes_count = 0
        if s.state == CONNECTED and s.interface:
            routes_count = self._route_counter(s.interface)
        return {
            **vpn.public(),
            "state": s.state,
            "interface": s.interface,
            "ip": s.ip,
            "routes_count": routes_count,
            "message": s.message,
            "since": s.since,
        }

    # ----- workers -------------------------------------------------------

    def _connect_worker(self, vpn: VpnDef) -> None:
        try:
            vpn = self._ensure_servercert(vpn)
            self._clear_files(vpn.id)
            result = self.runner.connect(vpn)
            if result.returncode != 0:
                self._clear_files(vpn.id)
                message = self._failure_message(vpn.id, result.output)
                self._set(
                    vpn.id,
                    ERROR,
                    message=message or f"openconnect exited with status {result.returncode}",
                )
                return
            self._wait_for_tunnel(vpn.id)
        except HelperUnavailable:
            self._set(vpn.id, ERROR, message=HELPER_HINT)
        except TunnelError as exc:
            self._set(vpn.id, ERROR, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - a worker must always leave a visible state
            self._set(vpn.id, ERROR, message=f"{type(exc).__name__}: {exc}")

    def _wait_for_tunnel(self, vpn_id: str) -> None:
        deadline = self._clock() + self.connect_timeout
        while True:
            pid = st.read_pid(self.state_dir, vpn_id)
            if pid is not None and self._pid_alive(pid):
                iface = st.read_iface(self.state_dir, vpn_id)
                if iface is not None:
                    self._set(vpn_id, CONNECTED, interface=iface[0], ip=iface[1])
                    return
            elif pid is not None:
                self._clear_files(vpn_id)
                message = self._failure_message(vpn_id)
                self._set(
                    vpn_id, ERROR, message=message or "openconnect exited before the tunnel came up"
                )
                return
            if self._clock() >= deadline:
                break
            self._sleep(self.poll_interval)
        timed_out = f"timed out after {int(self.connect_timeout)} s"
        detail = self._failure_message(vpn_id) or "no output from openconnect"
        try:
            self.runner.disconnect(vpn_id)
        except HelperUnavailable:
            # Nothing can stop the process now, so keep the pid file: refresh()
            # still reports it instead of forgetting a running tunnel.
            self._set(vpn_id, ERROR, message=f"{timed_out}; {HELPER_HINT}")
            return
        self._clear_files(vpn_id)
        self._set(vpn_id, ERROR, message=f"{timed_out}: {detail}")

    def _disconnect_worker(self, vpn_id: str) -> None:
        try:
            self.runner.disconnect(vpn_id)
            self._clear_files(vpn_id)
            self._set(vpn_id, DISCONNECTED)
        except HelperUnavailable:
            self._set(vpn_id, ERROR, message=HELPER_HINT)
        except Exception as exc:  # noqa: BLE001
            self._set(vpn_id, ERROR, message=f"{type(exc).__name__}: {exc}")

    def _failure_message(self, vpn_id: str, extra: str = "") -> str | None:
        log = "\n".join(st.read_log_tail(self.state_dir, vpn_id))
        message = st.parse_failure(log) or (extra.strip() or None)
        if message and "failed verification" in message:
            message = f"{message} {PIN_HINT}"
        return message

    def _ensure_servercert(self, vpn: VpnDef) -> VpnDef:
        """Probe the certificate pin once and store it in vpns.yaml."""
        if vpn.servercert:
            return vpn
        output = self.runner.probe(vpn).output
        pin = st.pin_from_probe(output)
        if pin is None:
            if not st.server_reachable(output):
                detail = st.parse_failure(output) or "no output"
                raise TunnelError(f"could not probe server certificate: {detail}")
            pin = TRUSTED_CA
        updated = dataclasses.replace(vpn, servercert=pin)
        with self._lock:
            save_servercert(self.registry.path, vpn.id, pin)
            self.registry.vpns = [updated if v.id == vpn.id else v for v in self.registry.vpns]
        return updated
