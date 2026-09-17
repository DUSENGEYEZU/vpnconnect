import dataclasses
import ipaddress
import subprocess

import pytest

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


class FakeRun:
    def __init__(self):
        self.calls = []
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""
        self.raise_exc = None

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr("app.services.runner.subprocess.run", fake)
    return fake


def test_connect_builds_helper_command_and_passes_password_on_stdin(fake_run):
    fake_run.stdout = "out"
    runner = SudoHelperRunner(HELPER, connect_timeout=30, disconnect_grace=5)

    result = runner.connect(VPN)

    cmd, kwargs = fake_run.calls[0]
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
    assert "s3cret" not in " ".join(cmd)
    assert kwargs["timeout"] == 45
    assert kwargs["text"] is True
    assert result == RunResult(0, "out")


def test_connect_uses_trusted_ca_and_dash_when_unset(fake_run):
    vpn = dataclasses.replace(VPN, servercert=None, routes=())

    SudoHelperRunner(HELPER).connect(vpn)

    assert fake_run.calls[0][0][-2:] == ["trusted-ca", "-"]


def test_probe_and_disconnect_commands(fake_run):
    runner = SudoHelperRunner(HELPER, disconnect_grace=7)

    runner.probe(VPN)
    runner.disconnect("mininfra")

    assert fake_run.calls[0][0] == [
        "sudo",
        "-n",
        HELPER,
        "probe",
        "vpn.example",
        "Staff",
        "anyconnect",
    ]
    assert fake_run.calls[0][1]["input"] is None
    assert fake_run.calls[1][0] == ["sudo", "-n", HELPER, "disconnect", "mininfra", "7"]


def test_output_combines_stdout_and_stderr(fake_run):
    fake_run.stdout = "a\n"
    fake_run.stderr = "b\n"

    assert SudoHelperRunner(HELPER).probe(VPN).output == "a\nb\n"


def test_sudo_refusal_raises_helper_unavailable(fake_run):
    fake_run.returncode = 1
    fake_run.stderr = "sudo: a password is required\n"

    with pytest.raises(HelperUnavailable, match="password is required"):
        SudoHelperRunner(HELPER).disconnect("mininfra")


def test_missing_helper_raises_helper_unavailable(fake_run):
    fake_run.returncode = 1
    fake_run.stderr = f"sudo: {HELPER}: command not found\n"

    with pytest.raises(HelperUnavailable, match="command not found"):
        SudoHelperRunner(HELPER).connect(VPN)


def test_missing_sudo_binary_raises_helper_unavailable(fake_run):
    fake_run.raise_exc = FileNotFoundError("sudo")

    with pytest.raises(HelperUnavailable):
        SudoHelperRunner(HELPER).connect(VPN)


def test_nonzero_exit_from_openconnect_is_returned_not_raised(fake_run):
    fake_run.returncode = 2
    fake_run.stderr = "vpnconnect-helper: something\n"

    result = SudoHelperRunner(HELPER).connect(VPN)

    assert result.returncode == 2
    assert result.output == "vpnconnect-helper: something\n"


def test_timeout_is_reported_as_result(fake_run):
    fake_run.raise_exc = subprocess.TimeoutExpired(cmd="x", timeout=45)

    result = SudoHelperRunner(HELPER).connect(VPN)

    assert result.returncode == 124
    assert "timed out" in result.output
