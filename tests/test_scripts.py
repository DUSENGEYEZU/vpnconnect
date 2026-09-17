"""Run the bash scripts against stubs. Nothing here needs root or a network."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from app.services import status

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


FAKE_OPENCONNECT = """#!/bin/bash
# Fake openconnect for tests. Understands the flags vpnconnect-helper passes.
#   probe (no --passwd-on-stdin): print a certificate pin, exit 1
#   password "good": background a sleeper named openconnect, write the pid file,
#                    run --script with reason=connect, print the success line, exit 0
#   any other password: "Login failed.", exit 1
pidfile=""; script=""; passwd_on_stdin=0
for arg in "$@"; do
  case "$arg" in
    --pid-file=*) pidfile="${arg#--pid-file=}" ;;
    --script=*) script="${arg#--script=}" ;;
    --passwd-on-stdin) passwd_on_stdin=1 ;;
  esac
done
echo "fake-openconnect: $*"
if [ "$passwd_on_stdin" -eq 0 ]; then
  echo 'Certificate from VPN server "vpn.example" failed verification.'
  echo 'To trust this server in future, perhaps add this to your command line:'
  echo '    --servercert pin-sha256:FAKEPIN0000000000000000000000000000000000000='
  exit 1
fi
read -r password
if [ "$password" != "good" ]; then
  echo "Login failed."
  exit 1
fi
( exec -a openconnect sleep 300 ) &
echo $! > "$pidfile"
if ! reason=connect TUNDEV=utun9 INTERNAL_IP4_ADDRESS=10.9.9.9 CISCO_SPLIT_INC=1 "$script"; then
  echo "Script '$script' returned error 1"
  exit 1
fi
echo "Configured as 10.9.9.9, with SSL connected and DTLS in progress"
echo "Continuing in background; pid $(cat "$pidfile")"
exit 0
"""

CONNECT_ARGS = (
    "connect",
    "mininfra",
    "vpn.example",
    "Staff",
    "anyconnect",
    "longin",
    "pin-sha256:abc=",
    "10.10.0.0:255.255.0.0:16",
)


@pytest.fixture
def helper(tmp_path):
    """The helper installed into tmp exactly as setup-privileges.sh does,
    against a fake openconnect.
    """
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    state = tmp_path / "state"

    fake_oc = tmp_path / "openconnect"
    fake_oc.write_text(FAKE_OPENCONNECT)
    fake_oc.chmod(0o755)

    stub_vpnc = tmp_path / "vpnc-script"
    stub_vpnc.write_text("#!/bin/bash\nexit 0\n")
    stub_vpnc.chmod(0o755)

    split = libexec / "vpnc-split.sh"
    split.write_text(
        (SCRIPTS / "vpnc-split.sh").read_text().replace("__VPNC_SCRIPT__", str(stub_vpnc))
    )
    split.chmod(0o755)

    installed = libexec / "vpnconnect-helper"
    installed.write_text(
        (SCRIPTS / "vpnconnect-helper")
        .read_text()
        .replace("__STATE_DIR__", str(state))
        .replace("__LIBEXEC_DIR__", str(libexec))
        .replace("__OPENCONNECT__", str(fake_oc))
    )
    installed.chmod(0o755)

    yield installed, state

    for pidfile in state.glob("*.pid") if state.exists() else []:
        text = pidfile.read_text().strip()
        if text.isdigit():
            try:
                os.kill(int(text), 9)
            except ProcessLookupError:
                pass


def run_helper(installed, *args, stdin=None):
    return subprocess.run([str(installed), *args], input=stdin, capture_output=True, text=True)


def test_helper_probe_prints_openconnect_output(helper):
    installed, _ = helper

    proc = run_helper(installed, "probe", "vpn.example", "Staff", "anyconnect")

    assert proc.returncode == 0, proc.stderr
    assert "pin-sha256:FAKEPIN" in proc.stdout
    assert "--authgroup=Staff" in proc.stdout
    assert "--passwd-on-stdin" not in proc.stdout


def test_helper_connect_success_writes_pid_log_and_iface(helper):
    installed, state = helper

    proc = run_helper(installed, *CONNECT_ARGS, stdin="good\n")

    assert proc.returncode == 0, proc.stderr
    pid = int((state / "mininfra.pid").read_text())
    assert status.pid_alive(pid)
    log = (state / "mininfra.log").read_text()
    assert "--servercert pin-sha256:abc=" in log
    assert "--user=longin" in log
    assert "--authgroup=Staff" in log
    assert "--passwd-on-stdin" in log
    assert "--background" in log
    assert "good" not in log
    assert "Configured as 10.9.9.9" in log
    assert (state / "mininfra.iface").read_text() == "utun9 10.9.9.9\n"


def test_helper_connect_trusted_ca_omits_servercert_flag(helper):
    installed, state = helper
    args = list(CONNECT_ARGS)
    args[6] = "trusted-ca"
    args[7] = "-"

    proc = run_helper(installed, *args, stdin="good\n")

    assert proc.returncode == 0, proc.stderr
    assert "--servercert" not in (state / "mininfra.log").read_text()


def test_helper_connect_wrong_password_returns_nonzero_and_logs(helper):
    installed, state = helper

    proc = run_helper(installed, *CONNECT_ARGS, stdin="wrong\n")

    assert proc.returncode == 1
    assert "Login failed." in (state / "mininfra.log").read_text()
    assert not (state / "mininfra.pid").exists()
    assert not (state / "mininfra.iface").exists()


def test_helper_connect_truncates_previous_log(helper):
    installed, state = helper
    run_helper(installed, *CONNECT_ARGS, stdin="wrong\n")
    run_helper(installed, "disconnect", "mininfra")

    run_helper(installed, *CONNECT_ARGS, stdin="good\n")

    assert (state / "mininfra.log").read_text().count("fake-openconnect:") == 1


def test_helper_disconnect_kills_process_and_removes_files(helper):
    installed, state = helper
    run_helper(installed, *CONNECT_ARGS, stdin="good\n")
    pid = int((state / "mininfra.pid").read_text())

    proc = run_helper(installed, "disconnect", "mininfra", "2")

    assert proc.returncode == 0, proc.stderr
    time.sleep(0.2)
    assert not status.pid_alive(pid)
    assert not (state / "mininfra.pid").exists()
    assert not (state / "mininfra.iface").exists()


def test_helper_disconnect_with_missing_or_stale_pid_is_a_noop(helper):
    installed, state = helper

    assert run_helper(installed, "disconnect", "mininfra").returncode == 0

    state.mkdir(exist_ok=True)
    (state / "mininfra.pid").write_text("999999\n")
    (state / "mininfra.iface").write_text("utun9 10.9.9.9\n")
    assert run_helper(installed, "disconnect", "mininfra").returncode == 0
    assert not (state / "mininfra.pid").exists()
    assert not (state / "mininfra.iface").exists()


def test_helper_disconnect_refuses_to_kill_a_process_that_is_not_openconnect(helper):
    installed, state = helper
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        state.mkdir(exist_ok=True)
        (state / "mininfra.pid").write_text(f"{sleeper.pid}\n")

        assert run_helper(installed, "disconnect", "mininfra").returncode == 0

        assert sleeper.poll() is None  # still alive: pid was treated as stale
        assert not (state / "mininfra.pid").exists()
    finally:
        sleeper.kill()


@pytest.mark.parametrize("bad_id", ["../etc", "Bad Id", "-x", "", "UPPER"])
def test_helper_rejects_invalid_ids(helper, bad_id):
    installed, _ = helper
    args = list(CONNECT_ARGS)
    args[1] = bad_id

    proc = run_helper(installed, *args, stdin="good\n")

    assert proc.returncode == 65
    assert "invalid id" in proc.stderr
    assert run_helper(installed, "disconnect", bad_id).returncode == 65


def test_helper_rejects_bad_grace_and_usage(helper):
    installed, _ = helper

    assert run_helper(installed, "disconnect", "mininfra", "soon").returncode == 65
    assert run_helper(installed).returncode == 64
    assert run_helper(installed, "bogus").returncode == 64
    assert run_helper(installed, "connect", "only", "three").returncode == 64
    assert run_helper(installed, "probe", "one").returncode == 64


def test_setup_script_replaces_every_placeholder_and_parses():
    setup = (SCRIPTS / "setup-privileges.sh").read_text()
    helper_src = (SCRIPTS / "vpnconnect-helper").read_text()
    split_src = (SCRIPTS / "vpnc-split.sh").read_text()

    for name in ("__STATE_DIR__", "__LIBEXEC_DIR__", "__OPENCONNECT__"):
        assert name in helper_src
        assert name in setup
    assert "__VPNC_SCRIPT__" in split_src
    assert "__VPNC_SCRIPT__" in setup
    assert "NOPASSWD" in setup
    assert "visudo -cf" in setup
    for script in ("setup-privileges.sh", "vpnconnect-helper", "vpnc-split.sh"):
        assert subprocess.run(["bash", "-n", str(SCRIPTS / script)]).returncode == 0
        assert os.access(SCRIPTS / script, os.X_OK)
