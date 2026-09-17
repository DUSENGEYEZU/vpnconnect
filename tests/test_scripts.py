"""Run the bash scripts against stubs. Nothing here needs root or a network."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

STUB_VPNC_SCRIPT = """#!/bin/bash
env | sort > "$STUB_OUT"
echo "STUB_ARGS=$*" >> "$STUB_OUT"
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture
def split(tmp_path):
    """vpnc-split.sh installed into tmp against a stub vpnc-script that records its environment."""
    stub = tmp_path / "vpnc-script"
    stub.write_text(STUB_VPNC_SCRIPT)
    stub.chmod(0o755)
    wrapper = tmp_path / "vpnc-split.sh"
    wrapper.write_text(
        (SCRIPTS / "vpnc-split.sh").read_text().replace("__VPNC_SCRIPT__", str(stub))
    )
    wrapper.chmod(0o755)
    return wrapper


def run_split(wrapper, tmp_path, extra_env, args=()):
    out = tmp_path / "stub.out"
    out.unlink(missing_ok=True)
    env = {"PATH": os.environ["PATH"], "STUB_OUT": str(out), **extra_env}
    proc = subprocess.run([str(wrapper), *args], env=env, capture_output=True, text=True)
    recorded = {}
    if out.exists():
        for line in out.read_text().splitlines():
            key, _, value = line.partition("=")
            recorded[key] = value
    return proc, recorded


def connect_env(tmp_path, **overrides):
    env = {
        "reason": "connect",
        "TUNDEV": "utun9",
        "INTERNAL_IP4_ADDRESS": "10.9.9.9",
        "VPNCONNECT_ID": "x",
        "VPNCONNECT_STATE_DIR": str(tmp_path),
        "VPNCONNECT_ROUTES": "",
    }
    env.update(overrides)
    return env


def test_server_split_routes_are_left_alone(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            CISCO_SPLIT_INC="2",
            CISCO_SPLIT_INC_0_ADDR="10.1.0.0",
            VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16",
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "2"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.1.0.0"
    assert "CISCO_SPLIT_INC_0_MASK" not in env


def test_full_tunnel_with_configured_routes_is_forced_to_split(split, tmp_path):
    routes = "10.10.0.0:255.255.0.0:16,10.20.5.0:255.255.255.0:24"

    proc, env = run_split(split, tmp_path, connect_env(tmp_path, VPNCONNECT_ROUTES=routes))

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "2"
    assert (
        env["CISCO_SPLIT_INC_0_ADDR"],
        env["CISCO_SPLIT_INC_0_MASK"],
        env["CISCO_SPLIT_INC_0_MASKLEN"],
    ) == (
        "10.10.0.0",
        "255.255.0.0",
        "16",
    )
    assert (
        env["CISCO_SPLIT_INC_1_ADDR"],
        env["CISCO_SPLIT_INC_1_MASK"],
        env["CISCO_SPLIT_INC_1_MASKLEN"],
    ) == (
        "10.20.5.0",
        "255.255.255.0",
        "24",
    )
    assert env["CISCO_SPLIT_INC_0_PROTOCOL"] == "0"


def test_full_tunnel_without_routes_is_refused(split, tmp_path):
    proc, env = run_split(split, tmp_path, connect_env(tmp_path))

    assert proc.returncode == 1
    assert "refusing to take the default route" in proc.stderr
    assert env == {}  # vpnc-script never ran
    assert not (tmp_path / "x.iface").exists()


def test_zero_split_count_is_treated_as_full_tunnel(split, tmp_path):
    proc, _ = run_split(split, tmp_path, connect_env(tmp_path, CISCO_SPLIT_INC="0"))

    assert proc.returncode == 1


def test_pre_init_is_passed_through_without_checks(split, tmp_path):
    proc, env = run_split(split, tmp_path, connect_env(tmp_path, reason="pre-init"))

    assert proc.returncode == 0, proc.stderr
    assert env["reason"] == "pre-init"
    assert not (tmp_path / "x.iface").exists()


def test_iface_file_written_on_connect_and_removed_on_disconnect(split, tmp_path):
    common = connect_env(tmp_path, CISCO_SPLIT_INC="1")

    run_split(split, tmp_path, common)
    assert (tmp_path / "x.iface").read_text() == "utun9 10.9.9.9\n"

    run_split(split, tmp_path, {**common, "reason": "disconnect"})
    assert not (tmp_path / "x.iface").exists()


def test_disconnect_reinjects_routes_so_vpnc_script_removes_them(split, tmp_path):
    env_in = connect_env(
        tmp_path, reason="disconnect", VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16"
    )

    proc, env = run_split(split, tmp_path, env_in)

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "1"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.10.0.0"


def test_arguments_and_exit_code_pass_through_and_failure_records_nothing(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(tmp_path, CISCO_SPLIT_INC="1", STUB_EXIT="3"),
        args=("alpha", "beta"),
    )

    assert proc.returncode == 3
    assert env["STUB_ARGS"] == "alpha beta"
    assert not (tmp_path / "x.iface").exists()
