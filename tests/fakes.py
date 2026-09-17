"""Test doubles shared by the tunnel and API tests."""

from __future__ import annotations

from pathlib import Path

from app.services.runner import HelperUnavailable, RunResult

VPNS_YAML = """\
vpns:
  - id: mininfra
    name: MININFRA
    server: vpn.mininfra.example
    authgroup: Staff
    routes: [10.10.0.0/16]
    servercert: pin-sha256:known=
  - id: rica
    name: RICA
    server: vpn.rica.example
    authgroup: Employees
"""

ENV = {
    "VPN_MININFRA_USERNAME": "longin",
    "VPN_MININFRA_PASSWORD": "pw1",
    "VPN_RICA_USERNAME": "longin",
    "VPN_RICA_PASSWORD": "pw2",
}

PROBE_WITH_PIN = (
    'Certificate from VPN server "vpn.rica.example" failed verification.\n'
    "To trust this server in future, perhaps add this to your command line:\n"
    "    --servercert pin-sha256:probed=\n"
)
CONFIGURED_LINE = "Configured as 10.9.9.9, with SSL connected and DTLS in progress"


class ImmediateThread:
    """Runs the target synchronously so tests observe the final state right away."""

    def __init__(self, target, args=(), daemon=True):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class FakeRunner:
    """Pretends to be the sudo helper: writes the files openconnect and the wrapper would."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.alive: set[int] = set()
        self.calls: list[tuple] = []
        self.next_pid = 4000
        self.probe_output = PROBE_WITH_PIN
        self.connect_mode = "ok"
        self.log_text = CONFIGURED_LINE + "\n"

    def probe(self, vpn):
        self.calls.append(("probe", vpn.id))
        return RunResult(0, self.probe_output)

    def connect(self, vpn):
        self.calls.append(("connect", vpn.id, vpn.servercert, vpn.routes_arg()))
        if self.connect_mode == "helper-missing":
            raise HelperUnavailable("sudo: a password is required")
        log = self.state_dir / f"{vpn.id}.log"
        if self.connect_mode == "login-failed":
            log.write_text("[t] Connected to HTTPS on x\n[t] Login failed.\n")
            return RunResult(1, "")
        if self.connect_mode == "cert-mismatch":
            log.write_text(
                '[t] Certificate from VPN server "vpn.mininfra.example" failed verification.\n'
                "[t] Reason: certificate does not match pin\n"
            )
            return RunResult(1, "")
        self.next_pid += 1
        pid = self.next_pid
        (self.state_dir / f"{vpn.id}.pid").write_text(f"{pid}\n")
        if self.connect_mode == "die":
            log.write_text("[t] Script '/x/vpnc-split.sh' returned error 1\n")
            return RunResult(0, "")  # pid file exists but the process is not alive
        log.write_text(self.log_text)
        self.alive.add(pid)
        if self.connect_mode != "no-iface":
            (self.state_dir / f"{vpn.id}.iface").write_text("utun9 10.9.9.9\n")
        return RunResult(0, "")

    def disconnect(self, vpn_id):
        self.calls.append(("disconnect", vpn_id))
        pidfile = self.state_dir / f"{vpn_id}.pid"
        if pidfile.exists():
            text = pidfile.read_text().strip()
            if text.isdigit():
                self.alive.discard(int(text))
            pidfile.unlink()
        (self.state_dir / f"{vpn_id}.iface").unlink(missing_ok=True)
        return RunResult(0, "")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
