import contextlib
import dataclasses
import ipaddress
import os
import subprocess
import time

import pytest

from app.services import status
from app.services.registry import VpnDef
from app.services.runner import HelperUnavailable, RunResult, SudoHelperRunner

VPN = VpnDef(
    id="mininfra",
    name="MININFRA",
    server="vpn.example",
    authgroup="Staff",
    routes=(ipaddress.IPv4Network("10.10.0.0/16"),),
    servercert="pin-sha256:abc=",
    username="longin",
    password="s3cret",
)
HELPER = "/usr/local/libexec/vpnconnect/vpnconnect-helper"


class FakePopen:
    """Stands in for subprocess.Popen: records the command, the keyword
    arguments and the stdin text, then returns canned output.
    """

    def __init__(self):
        self.calls = []
        self.returncode = 0
        self.stdout_text = ""
        self.stderr_text = ""
        self.raise_exc = None
        self.stdin = self.stdout = self.stderr = None

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self

    def communicate(self, input=None, timeout=None):
        self.calls[-1][1].update(input=input, timeout=timeout)
        return self.stdout_text, self.stderr_text


@pytest.fixture
def fake_popen(monkeypatch):
    fake = FakePopen()
    monkeypatch.setattr("app.services.runner.subprocess.Popen", fake)
    return fake


def test_connect_builds_helper_command_and_passes_password_on_stdin(fake_popen):
    fake_popen.stdout_text = "out"
    runner = SudoHelperRunner(HELPER, connect_timeout=30, disconnect_grace=5)

    result = runner.connect(VPN)

    cmd, kwargs = fake_popen.calls[0]
    assert cmd == [
        "sudo",
        "-n",
        HELPER,
        "connect",
        "mininfra",
        "vpn.example",
        "Staff",
        "anyconnect",
        "longin",
        "pin-sha256:abc=",
        "10.10.0.0:255.255.0.0:16",
    ]
    assert kwargs["input"] == "s3cret\n"
    assert kwargs["stdin"] is subprocess.PIPE
    assert "s3cret" not in " ".join(cmd)
    assert kwargs["timeout"] == 45
    assert kwargs["text"] is True
    assert result == RunResult(0, "out")


def test_connect_uses_trusted_ca_and_dash_when_unset(fake_popen):
    vpn = dataclasses.replace(VPN, servercert=None, routes=())

    SudoHelperRunner(HELPER).connect(vpn)

    assert fake_popen.calls[0][0][-2:] == ["trusted-ca", "-"]


def test_probe_and_disconnect_commands(fake_popen):
    runner = SudoHelperRunner(HELPER, disconnect_grace=7)

    runner.probe(VPN)
    runner.disconnect("mininfra")

    assert fake_popen.calls[0][0] == [
        "sudo",
        "-n",
        HELPER,
        "probe",
        "vpn.example",
        "Staff",
        "anyconnect",
    ]
    assert fake_popen.calls[0][1]["input"] is None
    assert fake_popen.calls[0][1]["stdin"] is subprocess.DEVNULL
    assert fake_popen.calls[1][0] == ["sudo", "-n", HELPER, "disconnect", "mininfra", "7"]
    assert fake_popen.calls[1][1]["stdin"] is subprocess.DEVNULL


def test_output_combines_stdout_and_stderr(fake_popen):
    fake_popen.stdout_text = "a\n"
    fake_popen.stderr_text = "b\n"

    assert SudoHelperRunner(HELPER).probe(VPN).output == "a\nb\n"


def test_sudo_refusal_raises_helper_unavailable(fake_popen):
    fake_popen.returncode = 1
    fake_popen.stderr_text = "sudo: a password is required\n"

    with pytest.raises(HelperUnavailable, match="password is required"):
        SudoHelperRunner(HELPER).disconnect("mininfra")


def test_missing_helper_raises_helper_unavailable(fake_popen):
    fake_popen.returncode = 1
    fake_popen.stderr_text = f"sudo: {HELPER}: command not found\n"

    with pytest.raises(HelperUnavailable, match="command not found"):
        SudoHelperRunner(HELPER).connect(VPN)


def test_missing_sudo_binary_raises_helper_unavailable(fake_popen):
    fake_popen.raise_exc = FileNotFoundError("sudo")

    with pytest.raises(HelperUnavailable):
        SudoHelperRunner(HELPER).connect(VPN)


def test_nonzero_exit_from_openconnect_is_returned_not_raised(fake_popen):
    fake_popen.returncode = 2
    fake_popen.stderr_text = "vpnconnect-helper: something\n"

    result = SudoHelperRunner(HELPER).connect(VPN)

    assert result.returncode == 2
    assert result.output == "vpnconnect-helper: something\n"


def test_timeout_reports_124_and_never_signals_the_root_child(tmp_path):
    """sudo runs the helper as root, so killing it from here fails with EPERM;
    the runner reports the timeout and lets go of the process instead.
    """
    marker = tmp_path / "helper.pid"
    helper = tmp_path / "slow-helper"
    helper.write_text(f'#!/bin/bash\necho $$ >"{marker}"\nexec sleep 5\n')
    helper.chmod(0o755)
    runner = SudoHelperRunner(str(helper), command_prefix=())

    result = runner._run(["disconnect", "mininfra"], stdin=None, timeout=1)

    assert result == RunResult(124, "helper did not return within 1 s")
    deadline = time.monotonic() + 2  # a loaded machine may start bash late
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(marker.read_text())
    try:
        assert status.pid_alive(pid)  # left running, not signalled
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 9)


def test_connect_runs_the_real_helper_with_the_password_on_stdin(tmp_path):
    record = tmp_path / "record"
    helper = tmp_path / "fake-helper"
    helper.write_text(
        "#!/bin/bash\nread -r pw\n"
        f'printf "args=%s\\npw=%s\\n" "$*" "$pw" >"{record}"\n'
        'echo "helper said this"\nexit 7\n'
    )
    helper.chmod(0o755)

    result = SudoHelperRunner(str(helper), command_prefix=()).connect(VPN)

    assert result == RunResult(7, "helper said this\n")
    args_line, pw_line = record.read_text().splitlines()
    assert args_line.startswith("args=connect mininfra vpn.example Staff anyconnect longin ")
    assert "s3cret" not in args_line
    assert pw_line == "pw=s3cret"
