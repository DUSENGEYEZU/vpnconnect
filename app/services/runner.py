"""The single place that spawns processes: `sudo -n <helper> ...`.

Everything else in the app talks to a HelperRunner, so tests swap in a fake
and never touch sudo or openconnect.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from app.services.registry import TRUSTED_CA, VpnDef


@dataclass(frozen=True)
class RunResult:
    returncode: int
    output: str


class HelperUnavailable(RuntimeError):
    """sudo refused (no NOPASSWD rule) or the helper is not installed."""


class HelperRunner(Protocol):
    def probe(self, vpn: VpnDef) -> RunResult: ...

    def connect(self, vpn: VpnDef) -> RunResult: ...

    def disconnect(self, vpn_id: str) -> RunResult: ...


class SudoHelperRunner:
    def __init__(self, helper_path: str, connect_timeout: int = 30, disconnect_grace: int = 5):
        self.helper_path = helper_path
        self.connect_timeout = connect_timeout
        self.disconnect_grace = disconnect_grace

    def _run(self, args: list[str], stdin: str | None, timeout: int) -> RunResult:
        cmd = ["sudo", "-n", self.helper_path, *args]
        try:
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
            )
        except FileNotFoundError as exc:
            raise HelperUnavailable(str(exc)) from exc
        except subprocess.TimeoutExpired:
            return RunResult(124, f"helper timed out after {timeout}s")
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 and output.lstrip().startswith("sudo:"):
            raise HelperUnavailable(output.strip())
        return RunResult(proc.returncode, output)

    def probe(self, vpn: VpnDef) -> RunResult:
        return self._run(["probe", vpn.server, vpn.authgroup, vpn.protocol], stdin=None, timeout=60)

    def connect(self, vpn: VpnDef) -> RunResult:
        if not vpn.username or not vpn.password:
            raise ValueError(f"{vpn.id}: credentials missing")
        args = [
            "connect",
            vpn.id,
            vpn.server,
            vpn.authgroup,
            vpn.protocol,
            vpn.username,
            vpn.servercert or TRUSTED_CA,
            vpn.routes_arg(),
        ]
        # openconnect --background returns once the tunnel is up or auth failed;
        # give it the same budget as the manager plus a margin.
        return self._run(args, stdin=vpn.password + "\n", timeout=self.connect_timeout + 15)

    def disconnect(self, vpn_id: str) -> RunResult:
        return self._run(["disconnect", vpn_id, str(self.disconnect_grace)], stdin=None, timeout=30)
