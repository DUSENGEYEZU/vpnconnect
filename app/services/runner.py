"""The single place that spawns processes: `sudo -n <helper> ...`.

Everything else in the app talks to a HelperRunner, so tests swap in a fake
and never touch sudo or openconnect.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Sequence
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


def _abandon(proc: subprocess.Popen) -> None:
    """Let go of a process this user cannot signal, without leaking it.

    The helper and its openconnect run as root: `Popen.kill()` from the app's
    user fails with EPERM and `wait()` would block until openconnect gives up.
    Close the pipes and reap the process in a background thread instead.
    """
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass
    threading.Thread(target=proc.wait, daemon=True).start()


class SudoHelperRunner:
    def __init__(
        self,
        helper_path: str,
        connect_timeout: int = 30,
        disconnect_grace: int = 5,
        command_prefix: Sequence[str] = ("sudo", "-n"),
    ):
        self.helper_path = helper_path
        self.connect_timeout = connect_timeout
        self.disconnect_grace = disconnect_grace
        # Tests run a fake helper directly by passing ().
        self.command_prefix = tuple(command_prefix)

    def _run(self, args: list[str], stdin: str | None, timeout: int) -> RunResult:
        cmd = [*self.command_prefix, self.helper_path, *args]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise HelperUnavailable(str(exc)) from exc
        try:
            out, err = proc.communicate(input=stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Never signal the child; the caller's timeout path disconnects
            # once a pid file exists and reconciliation adopts a late success.
            _abandon(proc)
            return RunResult(124, f"helper did not return within {timeout} s")
        output = (out or "") + (err or "")
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
