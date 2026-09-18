import os
import subprocess

import pytest

from app.services import status

NETSTAT = """\
Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            10.8.120.1         UGScg                 en0
10.10.34.10/32     10.10.47.29        UGSc                utun6
10.10.34.11/32     10.10.47.29        UGSc                utun6
10.10.47.24/29     link#25            UCS                 utun6
192.168.1.1        a8:6b:ad:1:2:3     UHLWIir               en0   1189
224.0.0/4          link#25            UmCSI               utun6
"""


def test_paths_are_derived_from_state_dir_and_id(tmp_path):
    assert status.pid_path(tmp_path, "x") == tmp_path / "x.pid"
    assert status.log_path(tmp_path, "x") == tmp_path / "x.log"
    assert status.iface_path(tmp_path, "x") == tmp_path / "x.iface"


def test_read_pid_returns_none_when_missing_or_garbage(tmp_path):
    assert status.read_pid(tmp_path, "x") is None
    (tmp_path / "x.pid").write_text("abc\n")
    assert status.read_pid(tmp_path, "x") is None


def test_read_pid_parses_number(tmp_path):
    (tmp_path / "x.pid").write_text(" 4242 \n")
    assert status.read_pid(tmp_path, "x") == 4242


def test_pid_alive_for_own_process_and_finished_child():
    assert status.pid_alive(os.getpid()) is True
    child = subprocess.Popen(["true"])
    child.wait()
    assert status.pid_alive(child.pid) is False


def test_pid_alive_treats_permission_error_as_alive(monkeypatch):
    def deny(pid, sig):
        raise PermissionError

    monkeypatch.setattr(status.os, "kill", deny)
    assert status.pid_alive(1) is True


def test_read_iface(tmp_path):
    assert status.read_iface(tmp_path, "x") is None
    (tmp_path / "x.iface").write_text("utun7 10.10.47.29\n")
    assert status.read_iface(tmp_path, "x") == ("utun7", "10.10.47.29")
    (tmp_path / "x.iface").write_text("utun7\n")
    assert status.read_iface(tmp_path, "x") is None


def test_read_log_tail_returns_last_non_empty_lines(tmp_path):
    (tmp_path / "x.log").write_text("a\n\nb\nc\n")
    assert status.read_log_tail(tmp_path, "x", lines=2) == ["b", "c"]
    assert status.read_log_tail(tmp_path, "x") == ["a", "b", "c"]
    assert status.read_log_tail(tmp_path, "missing") == []


def test_strip_timestamp():
    assert status.strip_timestamp("[2026-09-17 14:00:01] Login failed.") == "Login failed."
    assert status.strip_timestamp("no stamp") == "no stamp"


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        (
            "[2026-09-17 14:00:01] Connected to HTTPS on x\n[2026-09-17 14:00:02] Login failed.\n",
            "Login failed.",
        ),
        (
            'Certificate from VPN server "x" failed verification.\nReason: signer not found\n',
            'Certificate from VPN server "x" failed verification.',
        ),
        (
            "vpnconnect: server pushed a full tunnel and no routes are configured\n"
            "Script '/x' returned error 1\n",
            "vpnconnect: server pushed a full tunnel and no routes are configured",
        ),
        ("Script '/x' returned error 1\nsomething else\n", "Script '/x' returned error 1"),
        ("Failed to connect to host vpn.x\n", "Failed to connect to host vpn.x"),
        ("sudo: a password is required\n", "sudo: a password is required"),
        ("Continuing in background; pid 77\n", "Continuing in background; pid 77"),
        ("", None),
        ("\n\n", None),
    ],
)
def test_parse_failure_prefers_specific_messages_then_falls_back_to_last_line(log, expected):
    assert status.parse_failure(log) == expected


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        (
            "vpnconnect: server pushed a full tunnel and no routes are configured\n"
            "Script '/x' returned error 1\n",
            "vpnconnect: server pushed a full tunnel and no routes are configured",
        ),
        (
            "[2026-09-17 14:00:01] Script '/x' returned error 1\n",
            "Script '/x' returned error 1",
        ),
        ("Configured as 10.9.9.9, with SSL connected\nLogin failed.\n", None),
        ("", None),
    ],
)
def test_script_failure_only_matches_a_failed_connect_script(log, expected):
    assert status.script_failure(log) == expected


def test_pin_from_probe():
    out = (
        "To trust this server in future, perhaps add this to your command line:\n"
        "    --servercert pin-sha256:0Yl6a3cSB6AQ8r5k7fT9m6yLxWvqNzR2pCd3eF4gH5I=\n"
    )
    assert status.pin_from_probe(out) == "pin-sha256:0Yl6a3cSB6AQ8r5k7fT9m6yLxWvqNzR2pCd3eF4gH5I="
    assert status.pin_from_probe("Login failed.") is None


def test_server_reachable():
    reachable = "Connected to HTTPS on vpn.x with ciphersuite (TLS1.3)\nLogin failed.\n"
    assert status.server_reachable(reachable) is True
    assert status.server_reachable("Got HTTP response: HTTP/1.1 200 OK\n") is True
    assert status.server_reachable("Failed to connect to host vpn.x\n") is False
    assert status.server_reachable("") is False


def test_count_routes_counts_lines_on_interface():
    assert status.count_routes("utun6", NETSTAT) == 4
    assert status.count_routes("en0", NETSTAT) == 2
    assert status.count_routes("utun9", NETSTAT) == 0


def test_count_routes_runs_netstat_when_no_output_given(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["netstat", "-rn", "-f", "inet"]
        return subprocess.CompletedProcess(cmd, 0, stdout=NETSTAT, stderr="")

    monkeypatch.setattr(status.subprocess, "run", fake_run)
    assert status.count_routes("utun6") == 4
