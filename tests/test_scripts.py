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


def run_split(wrapper, tmp_path, extra_env, args=(), path_prefix=None):
    out = tmp_path / "stub.out"
    out.unlink(missing_ok=True)
    path = os.environ["PATH"] if path_prefix is None else f"{path_prefix}:{os.environ['PATH']}"
    env = {"PATH": path, "STUB_OUT": str(out), **extra_env}
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
            CISCO_SPLIT_INC_0_MASKLEN="16",
            CISCO_SPLIT_INC_1_ADDR="10.2.0.0",
            CISCO_SPLIT_INC_1_MASKLEN="16",
            VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16",
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "2"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.1.0.0"
    assert env["CISCO_SPLIT_INC_1_ADDR"] == "10.2.0.0"
    assert "CISCO_SPLIT_INC_0_MASK" not in env


def default_route_include(index, **overrides):
    """A server split include that vpnc-script turns into a default route."""
    env = {
        f"CISCO_SPLIT_INC_{index}_ADDR": "0.0.0.0",
        f"CISCO_SPLIT_INC_{index}_MASK": "0.0.0.0",
        f"CISCO_SPLIT_INC_{index}_MASKLEN": "0",
    }
    env.update(overrides)
    return env


def test_server_split_with_a_default_route_include_uses_configured_routes(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            CISCO_SPLIT_INC="2",
            CISCO_SPLIT_INC_0_ADDR="10.1.0.0",
            CISCO_SPLIT_INC_0_MASK="255.255.0.0",
            CISCO_SPLIT_INC_0_MASKLEN="16",
            VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16",
            **default_route_include(1),
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "1"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.10.0.0"
    assert env["CISCO_SPLIT_INC_0_MASKLEN"] == "16"
    assert "CISCO_SPLIT_INC_1_ADDR" not in env


def test_server_split_with_a_default_route_include_and_no_routes_is_refused(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(tmp_path, CISCO_SPLIT_INC="1", **default_route_include(0)),
    )

    assert proc.returncode == 1
    assert "refusing to take the default route" in proc.stderr
    assert env == {}  # vpnc-script never ran
    assert not (tmp_path / "x.iface").exists()


def test_masklen_zero_include_is_also_a_default_route(split, tmp_path):
    proc, _ = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            CISCO_SPLIT_INC="1",
            CISCO_SPLIT_INC_0_ADDR="10.1.0.0",
            CISCO_SPLIT_INC_0_MASK="0.0.0.0",
            CISCO_SPLIT_INC_0_MASKLEN="0",
        ),
    )

    assert proc.returncode == 1


def test_disconnect_replaces_a_default_route_include_the_same_way(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            reason="disconnect",
            CISCO_SPLIT_INC="1",
            VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16",
            **default_route_include(0),
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "1"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.10.0.0"


def test_pushed_dns_servers_get_host_routes_through_the_tunnel(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            CISCO_SPLIT_INC="2",
            CISCO_SPLIT_INC_0_ADDR="10.1.0.0",
            CISCO_SPLIT_INC_0_MASKLEN="16",
            CISCO_SPLIT_INC_1_ADDR="10.10.34.10",
            CISCO_SPLIT_INC_1_MASKLEN="32",
            INTERNAL_IP4_DNS="10.10.34.10 10.10.34.11",
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "3"  # 10.10.34.10 was already an include
    assert env["CISCO_SPLIT_INC_2_ADDR"] == "10.10.34.11"
    assert env["CISCO_SPLIT_INC_2_MASK"] == "255.255.255.255"
    assert env["CISCO_SPLIT_INC_2_MASKLEN"] == "32"
    assert env["CISCO_SPLIT_INC_2_PROTOCOL"] == "0"


def test_pushed_dns_servers_are_added_after_injected_routes(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            VPNCONNECT_ROUTES="10.10.0.0:255.255.0.0:16",
            INTERNAL_IP4_DNS="10.10.34.10",
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "2"
    assert env["CISCO_SPLIT_INC_0_ADDR"] == "10.10.0.0"
    assert env["CISCO_SPLIT_INC_1_ADDR"] == "10.10.34.10"
    assert env["CISCO_SPLIT_INC_1_MASKLEN"] == "32"


def test_disconnect_adds_the_same_dns_host_routes_and_skips_junk(split, tmp_path):
    proc, env = run_split(
        split,
        tmp_path,
        connect_env(
            tmp_path,
            reason="disconnect",
            CISCO_SPLIT_INC="1",
            CISCO_SPLIT_INC_0_ADDR="10.1.0.0",
            CISCO_SPLIT_INC_0_MASKLEN="16",
            INTERNAL_IP4_DNS="10.10.34.10 not-an-ip",
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert env["CISCO_SPLIT_INC"] == "2"
    assert env["CISCO_SPLIT_INC_1_ADDR"] == "10.10.34.10"
    assert "CISCO_SPLIT_INC_2_ADDR" not in env


def fake_dns_tools(tmp_path):
    """A scutil that records its stdin and a dig whose exit code FAKE_DIG_EXIT controls."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    (fakebin / "scutil").write_text('#!/bin/bash\ncat >> "${STUB_OUT}.scutil"\n')
    (fakebin / "dig").write_text(
        '#!/bin/bash\necho "$*" >> "${STUB_OUT}.dig"\nexit "${FAKE_DIG_EXIT:-0}"\n'
    )
    for f in fakebin.iterdir():
        f.chmod(0o755)
    return fakebin


def dns_env(tmp_path, **overrides):
    base = {
        "CISCO_SPLIT_INC": "1",
        "CISCO_SPLIT_INC_0_ADDR": "10.1.0.0",
        "CISCO_SPLIT_INC_0_MASKLEN": "16",
        "INTERNAL_IP4_DNS": "10.10.34.10 10.10.34.11",
        "CISCO_DEF_DOMAIN": "idc.bsc.rw",
    }
    base.update(overrides)
    return connect_env(tmp_path, **base)


def test_pushed_dns_is_hidden_from_vpnc_script_and_registered_as_supplemental(split, tmp_path):
    fakebin = fake_dns_tools(tmp_path)

    proc, env = run_split(split, tmp_path, dns_env(tmp_path), path_prefix=fakebin)

    assert proc.returncode == 0, proc.stderr
    assert "INTERNAL_IP4_DNS" not in env  # vpnc-script's DNS block never runs
    assert env["CISCO_SPLIT_INC"] == "3"  # the /32 includes were still added
    scutil = (tmp_path / "stub.out.scutil").read_text()
    assert "d.add ServerAddresses * 10.10.34.10 10.10.34.11" in scutil
    assert "d.add SupplementalMatchDomains * idc.bsc.rw" in scutil
    assert "set State:/Network/Service/utun9/DNS" in scutil
    assert "OverridePrimary" not in scutil
    assert "SearchDomains" not in scutil
    dig = (tmp_path / "stub.out.dig").read_text()
    assert "@10.10.34.10 idc.bsc.rw SOA" in dig


def test_split_dns_domains_join_the_supplemental_list(split, tmp_path):
    fakebin = fake_dns_tools(tmp_path)

    proc, _ = run_split(
        split,
        tmp_path,
        dns_env(tmp_path, CISCO_SPLIT_DNS="mininfra.gov.rw,rha.gov.rw"),
        path_prefix=fakebin,
    )

    assert proc.returncode == 0, proc.stderr
    scutil = (tmp_path / "stub.out.scutil").read_text()
    assert "d.add SupplementalMatchDomains * idc.bsc.rw mininfra.gov.rw rha.gov.rw" in scutil


def test_dns_that_does_not_answer_through_the_tunnel_is_not_registered(split, tmp_path):
    fakebin = fake_dns_tools(tmp_path)

    proc, env = run_split(
        split, tmp_path, dns_env(tmp_path, FAKE_DIG_EXIT="9"), path_prefix=fakebin
    )

    assert proc.returncode == 0, proc.stderr
    assert "INTERNAL_IP4_DNS" not in env
    assert not (tmp_path / "stub.out.scutil").exists()
    assert "did not answer through utun9" in proc.stderr


def test_dns_without_a_domain_is_not_registered(split, tmp_path):
    fakebin = fake_dns_tools(tmp_path)

    proc, env = run_split(
        split, tmp_path, dns_env(tmp_path, CISCO_DEF_DOMAIN=""), path_prefix=fakebin
    )

    assert proc.returncode == 0, proc.stderr
    assert "INTERNAL_IP4_DNS" not in env
    assert not (tmp_path / "stub.out.scutil").exists()
    assert not (tmp_path / "stub.out.dig").exists()
    assert "without a domain" in proc.stderr


def test_dns_values_from_the_gateway_are_filtered_to_plain_names_and_addresses(split, tmp_path):
    fakebin = fake_dns_tools(tmp_path)
    evil = "idc.bsc.rw\nset State:/Network/Global/DNS"

    proc, _ = run_split(
        split,
        tmp_path,
        dns_env(
            tmp_path,
            CISCO_DEF_DOMAIN=evil,
            CISCO_SPLIT_DNS="ok.example,bad;domain",
            INTERNAL_IP4_DNS="10.10.34.10 10.10.34.11;rm",
        ),
        path_prefix=fakebin,
    )

    assert proc.returncode == 0, proc.stderr
    scutil = (tmp_path / "stub.out.scutil").read_text()
    lines = scutil.splitlines()
    supplemental = [ln for ln in lines if ln.startswith("d.add SupplementalMatchDomains")]
    assert len(supplemental) == 1  # an injected newline cannot add a scutil command
    assert "idc.bsc.rw" in supplemental[0] and "ok.example" in supplemental[0]
    assert "bad;domain" not in scutil and "State:/Network/Global" not in scutil
    assert "d.add ServerAddresses * 10.10.34.10" in scutil
    assert "10.10.34.11;rm" not in scutil
    dig = (tmp_path / "stub.out.dig").read_text()
    assert ";rm" not in dig
    assert not [ln for ln in lines if ln.startswith("set ") and "Global" in ln]


def test_disconnect_removes_the_supplemental_dns_entry(split, tmp_path):
    fakebin = fake_dns_tools(tmp_path)

    proc, env = run_split(
        split, tmp_path, dns_env(tmp_path, reason="disconnect"), path_prefix=fakebin
    )

    assert proc.returncode == 0, proc.stderr
    assert "INTERNAL_IP4_DNS" not in env
    scutil = (tmp_path / "stub.out.scutil").read_text()
    assert "remove State:/Network/Service/utun9/DNS" in scutil
    assert "set State:" not in scutil


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


def test_iface_file_is_recreated_not_written_through_a_symlink(split, tmp_path):
    other = tmp_path / "other.txt"
    other.write_text("keep me\n")
    iface = tmp_path / "x.iface"
    iface.symlink_to(other)

    proc, _ = run_split(split, tmp_path, connect_env(tmp_path, CISCO_SPLIT_INC="1"))

    assert proc.returncode == 0, proc.stderr
    assert other.read_text() == "keep me\n"
    assert not iface.is_symlink()
    assert iface.read_text() == "utun9 10.9.9.9\n"


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
#                    run --script with reason=connect and exit 0 whatever the
#                    script returned, exactly as openconnect does
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
echo "Configured as 10.9.9.9, with SSL connected and DTLS in progress"
# openconnect discards the connect script's exit code: it logs the error and
# leaves the tun device up with no address or routes.
reason=connect TUNDEV=utun9 INTERNAL_IP4_ADDRESS=10.9.9.9 CISCO_SPLIT_INC=1 "$script" \
  || echo "Script '$script' returned error $?"
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


def run_helper(installed, *args, stdin=None, env=None):
    return subprocess.run(
        [str(installed), *args], input=stdin, capture_output=True, text=True, env=env
    )


def test_helper_probe_prints_openconnect_output(helper):
    installed, _ = helper

    proc = run_helper(installed, "probe", "vpn.example", "Staff", "anyconnect")

    assert proc.returncode == 0, proc.stderr
    assert "pin-sha256:FAKEPIN" in proc.stdout
    assert "--authgroup=Staff" in proc.stdout
    assert "--passwd-on-stdin" not in proc.stdout
    assert "--disable-ipv6" not in proc.stdout  # the probe never builds a tunnel


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
    # v1 is IPv4-only: without this a server can still push an IPv6 default route.
    assert "--disable-ipv6" in log
    assert "good" not in log
    assert "Configured as 10.9.9.9" in log
    assert (state / "mininfra.iface").read_text() == "utun9 10.9.9.9\n"


def test_connect_script_failure_still_backgrounds_and_writes_the_pid(helper, tmp_path):
    """openconnect discards the connect script's exit code: it logs the error,
    keeps the tun device up, backgrounds and writes the pid file.
    """
    installed, state = helper
    (tmp_path / "vpnc-script").write_text("#!/bin/bash\nexit 1\n")

    proc = run_helper(installed, *CONNECT_ARGS, stdin="good\n")

    assert proc.returncode == 0, proc.stderr
    assert "returned error 1" in (state / "mininfra.log").read_text()
    assert status.pid_alive(int((state / "mininfra.pid").read_text()))
    assert not (state / "mininfra.iface").exists()


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


def test_helper_connect_recreates_symlinked_log_and_pid(helper, tmp_path):
    installed, state = helper
    state.mkdir()
    other_log = tmp_path / "other-log.txt"
    other_pid = tmp_path / "other-pid.txt"
    other_log.write_text("keep log\n")
    other_pid.write_text("keep pid\n")
    (state / "mininfra.log").symlink_to(other_log)
    (state / "mininfra.pid").symlink_to(other_pid)

    proc = run_helper(installed, *CONNECT_ARGS, stdin="good\n")

    assert proc.returncode == 0, proc.stderr
    assert other_log.read_text() == "keep log\n"
    assert other_pid.read_text() == "keep pid\n"
    for name in ("mininfra.log", "mininfra.pid"):
        assert not (state / name).is_symlink()
        assert (state / name).is_file()
    assert (state / "mininfra.pid").read_text().strip().isdigit()
    assert "fake-openconnect:" in (state / "mininfra.log").read_text()


def test_helper_disconnect_requires_the_exact_process_name(helper):
    installed, state = helper
    sleeper = subprocess.Popen(["bash", "-c", "exec -a openconnect-extra sleep 30"])
    try:
        state.mkdir(exist_ok=True)
        (state / "mininfra.pid").write_text(f"{sleeper.pid}\n")

        assert run_helper(installed, "disconnect", "mininfra").returncode == 0

        assert sleeper.poll() is None  # name only contains "openconnect": treated as stale
        assert not (state / "mininfra.pid").exists()
    finally:
        sleeper.kill()


def test_helper_uses_a_fixed_path_not_the_callers(helper, tmp_path):
    installed, state = helper
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    marker = tmp_path / "fake-ps-ran"
    fake_ps = fakebin / "ps"
    fake_ps.write_text(f'#!/bin/bash\ntouch "{marker}"\necho openconnect\n')
    fake_ps.chmod(0o755)
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        state.mkdir(exist_ok=True)
        (state / "mininfra.pid").write_text(f"{sleeper.pid}\n")
        env = {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}"}

        assert run_helper(installed, "disconnect", "mininfra", env=env).returncode == 0

        assert not marker.exists()
        assert sleeper.poll() is None
    finally:
        sleeper.kill()


def test_helper_accepts_well_formed_routes(helper):
    installed, state = helper
    args = list(CONNECT_ARGS)
    args[7] = "10.10.0.0:255.255.0.0:16,192.168.5.0:255.255.255.0:24"

    proc = run_helper(installed, *args, stdin="good\n")

    assert proc.returncode == 0, proc.stderr
    assert (state / "mininfra.pid").exists()


@pytest.mark.parametrize(
    "bad_routes",
    ["10.0.0.0:255.0.0.0:8;id", "foo", "10.0.0.0:255.0.0.0", "10.0.0.0:255.0.0.0:8,", ""],
)
def test_helper_rejects_malformed_routes_before_touching_state(helper, bad_routes):
    installed, state = helper
    args = list(CONNECT_ARGS)
    args[7] = bad_routes

    proc = run_helper(installed, *args, stdin="good\n")

    assert proc.returncode == 65
    assert "invalid routes" in proc.stderr
    assert not (state / "mininfra.pid").exists()
    assert not (state / "mininfra.log").exists()


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
    for script in (
        "setup-privileges.sh",
        "vpnconnect-helper",
        "vpnc-split.sh",
        "app-start.sh",
        "app-stop.sh",
        "autostart.sh",
    ):
        assert subprocess.run(["bash", "-n", str(SCRIPTS / script)]).returncode == 0
        assert os.access(SCRIPTS / script, os.X_OK)
