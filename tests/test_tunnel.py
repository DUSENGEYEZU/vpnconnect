import logging

import pytest
from dotenv import dotenv_values

from app.services import status as st
from app.services import tunnel
from app.services.credentials import env_var_names
from app.services.registry import DuplicateVpn, add_vpn, load_registry, remove_vpn
from app.services.runner import HelperUnavailable, RunResult
from app.services.tunnel import (
    CONNECTED,
    DISCONNECTED,
    ERROR,
    InvalidTransition,
    MissingCredentials,
    TunnelManager,
    UnknownVpn,
)
from tests.fakes import ENV, ENV_TEXT, VPNS_YAML, FakeClock, FakeRunner, ImmediateThread

CAPTURED_THREADS: list["CapturedThread"] = []


class CapturedThread:
    """Stand-in for threading.Thread that records the worker call instead of
    running it, so a test can inspect the CONNECTING state while the worker
    is still pending, then run it on demand."""

    def __init__(self, target, args=(), daemon=True):
        self._target = target
        self._args = args

    def start(self):
        CAPTURED_THREADS.append(self)

    def run(self):
        self._target(*self._args)


@pytest.fixture
def state_dir(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    return path


@pytest.fixture
def vpns_file(tmp_path):
    path = tmp_path / "vpns.yaml"
    path.write_text(VPNS_YAML)
    return path


@pytest.fixture
def runner(state_dir):
    return FakeRunner(state_dir)


@pytest.fixture
def clock():
    return FakeClock()


def make_manager(vpns_file, runner, state_dir, clock, env=ENV):
    # The .env the manager may edit lives beside the temporary vpns.yaml.
    env_file = vpns_file.with_name(".env")
    env_file.write_text(ENV_TEXT)
    return TunnelManager(
        load_registry(vpns_file, env),
        runner,
        state_dir,
        env_file=env_file,
        env=dict(env),
        connect_timeout=30,
        poll_interval=0.5,
        pid_alive=lambda pid: pid in runner.alive,
        route_counter=lambda iface: 3,
        thread_factory=ImmediateThread,
        clock=clock,
        sleep=clock.sleep,
    )


@pytest.fixture
def manager(vpns_file, runner, state_dir, clock):
    return make_manager(vpns_file, runner, state_dir, clock)


def test_initial_snapshot_is_disconnected_with_public_fields(manager):
    snap = manager.snapshot()

    assert [s["id"] for s in snap] == ["mininfra", "rica"]
    assert all(s["state"] == DISCONNECTED for s in snap)
    assert snap[0]["routes"] == ["10.10.0.0/16"]
    assert snap[0]["routes_count"] == 0
    assert snap[0]["interface"] is None
    assert "password" not in snap[0]


def test_connect_success_marks_connected_with_interface(manager, runner):
    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == CONNECTED
    assert s["interface"] == "utun9"
    assert s["ip"] == "10.9.9.9"
    assert s["routes_count"] == 3
    assert s["since"]
    assert ("connect", "mininfra", "pin-sha256:known=", "10.10.0.0:255.255.0.0:16") in runner.calls


def test_connect_probes_and_saves_pin_when_servercert_missing(manager, runner, vpns_file):
    manager.connect("rica")

    assert runner.calls[0] == ("probe", "rica")
    assert runner.calls[1][:3] == ("connect", "rica", "pin-sha256:probed=")
    assert "pin-sha256:probed=" in vpns_file.read_text()
    assert manager.snapshot_one("rica")["servercert"] == "pin-sha256:probed="


def test_probe_without_pin_but_reachable_stores_trusted_ca(manager, runner):
    runner.probe_output = (
        "Connected to HTTPS on vpn.rica.example with ciphersuite X\nLogin failed.\n"
    )

    manager.connect("rica")

    assert runner.calls[1][:3] == ("connect", "rica", "trusted-ca")
    assert manager.snapshot_one("rica")["state"] == CONNECTED


def test_probe_that_cannot_reach_server_is_an_error(manager, runner):
    runner.probe_output = "Failed to connect to host vpn.rica.example\n"

    manager.connect("rica")

    s = manager.snapshot_one("rica")
    assert s["state"] == ERROR
    assert s["message"] == (
        "could not probe server certificate: Failed to connect to host vpn.rica.example"
    )
    assert not any(call[0] == "connect" for call in runner.calls)


def test_login_failure_is_reported_from_log(manager, runner):
    runner.connect_mode = "login-failed"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == "Login failed."


def test_certificate_mismatch_tells_how_to_reprobe(manager, runner):
    runner.connect_mode = "cert-mismatch"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == (
        'Certificate from VPN server "vpn.mininfra.example" failed verification. ' + tunnel.PIN_HINT
    )


def test_process_dying_before_tunnel_is_reported(manager, runner, state_dir):
    runner.connect_mode = "die"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == "Script '/x/vpnc-split.sh' returned error 1"
    assert not (state_dir / "mininfra.pid").exists()


def test_timeout_disconnects_and_reports(manager, runner):
    runner.connect_mode = "no-iface"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"].startswith("timed out after 30 s: Configured as 10.9.9.9")
    assert ("disconnect", "mininfra") in runner.calls


STALLED = "helper did not return within 45 s"


def stalling_connect(runner, state_dir, pid=7001):
    """The helper has not returned yet; openconnect already wrote its pid file."""

    def connect(vpn):
        runner.calls.append(("connect", vpn.id))
        (state_dir / f"{vpn.id}.pid").write_text(f"{pid}\n")
        runner.alive.add(pid)
        return RunResult(124, STALLED)

    runner.connect = connect
    return pid


def test_helper_timeout_adopts_a_late_success(manager, runner, state_dir, clock):
    """A helper that has not returned may still be starting: keep the state
    files and let the wait see the tunnel come up.
    """
    pid = stalling_connect(runner, state_dir)

    def sleep_then_finish(seconds):
        clock.sleep(seconds)
        (state_dir / "mininfra.iface").write_text("utun7 10.7.7.7\n")

    manager._sleep = sleep_then_finish

    manager.connect("mininfra")

    s = manager.state_of("mininfra")
    assert s.state == CONNECTED
    assert (s.interface, s.ip) == ("utun7", "10.7.7.7")
    assert st.read_pid(state_dir, "mininfra") == pid  # never cleared
    assert ("disconnect", "mininfra") not in runner.calls


def test_helper_timeout_that_never_comes_up_ends_in_the_timeout_path(
    manager, runner, state_dir, clock
):
    stalling_connect(runner, state_dir)
    seen = {}
    fake_disconnect = runner.disconnect

    def disconnect(vpn_id):
        seen["pid_file"] = st.pid_path(state_dir, vpn_id).exists()
        return fake_disconnect(vpn_id)

    runner.disconnect = disconnect

    manager.connect("mininfra")

    s = manager.state_of("mininfra")
    assert s.state == ERROR
    assert STALLED in s.message
    assert seen["pid_file"] is True  # cleared only on the disconnect path
    assert not (state_dir / "mininfra.pid").exists()
    assert clock.now >= 30


def test_timeout_keeps_the_pid_file_when_the_helper_is_gone(manager, runner, state_dir):
    """Nothing can stop the process, so the pid file has to stay: refresh()
    still reports it instead of forgetting a running tunnel.
    """
    runner.connect_mode = "no-iface"

    def refuse(vpn_id):
        runner.calls.append(("disconnect", vpn_id))
        raise HelperUnavailable("sudo: a password is required")

    runner.disconnect = refuse

    manager.connect("mininfra")

    s = manager.state_of("mininfra")
    assert s.state == ERROR
    assert s.message == f"timed out after 30 s; {tunnel.HELPER_HINT}"
    assert (state_dir / "mininfra.pid").exists()


def test_refused_connect_script_fails_fast_with_the_refusal(manager, runner, clock):
    """openconnect ignores a failing connect script and backgrounds anyway, so
    the interface can never appear: stop on the refusal instead of waiting.
    """
    refusal = (
        "vpnconnect: server pushed a full tunnel and no routes are configured "
        "for this VPN; refusing to take the default route"
    )
    runner.connect_mode = "no-iface"
    runner.log_text = (
        f"[t] Configured as 10.9.9.9, with SSL connected\n[t] {refusal}\n"
        "[t] Script '/usr/local/libexec/vpnconnect/vpnc-split.sh' returned error 1\n"
    )

    manager.connect("mininfra")

    s = manager.state_of("mininfra")
    assert s.state == ERROR
    assert s.message == refusal
    assert clock.now == 0.0  # seen on the first poll, no waiting
    assert ("disconnect", "mininfra") in runner.calls


def test_connect_worker_exception_is_logged_with_the_vpn_id(manager, runner, caplog):
    def boom(vpn):
        raise RuntimeError("kaboom")

    runner.connect = boom

    with caplog.at_level(logging.ERROR):
        manager.connect("mininfra")

    assert manager.state_of("mininfra").message == "RuntimeError: kaboom"
    assert "mininfra" in caplog.text
    assert "RuntimeError: kaboom" in caplog.text
    assert "Traceback" in caplog.text


def test_disconnect_worker_exception_is_logged_with_the_vpn_id(manager, runner, caplog):
    manager.connect("mininfra")

    def boom(vpn_id):
        raise RuntimeError("no such luck")

    runner.disconnect = boom

    with caplog.at_level(logging.ERROR):
        manager.disconnect("mininfra")

    assert manager.state_of("mininfra").message == "RuntimeError: no such luck"
    assert "mininfra" in caplog.text
    assert "Traceback" in caplog.text


def test_helper_unavailable_gives_setup_hint(manager, runner):
    runner.connect_mode = "helper-missing"

    manager.connect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == ERROR
    assert s["message"] == tunnel.HELPER_HINT


def test_missing_credentials_and_unknown_id(vpns_file, runner, state_dir, clock):
    m = make_manager(vpns_file, runner, state_dir, clock, env={"VPN_MININFRA_USERNAME": "longin"})

    with pytest.raises(MissingCredentials) as exc:
        m.connect("mininfra")
    assert exc.value.missing == ["VPN_MININFRA_PASSWORD"]
    assert str(exc.value) == "missing credentials: VPN_MININFRA_PASSWORD"
    with pytest.raises(UnknownVpn):
        m.connect("ghost")
    with pytest.raises(UnknownVpn):
        m.snapshot_one("ghost")
    with pytest.raises(UnknownVpn):
        m.disconnect("ghost")


def test_connect_twice_is_invalid(manager):
    manager.connect("mininfra")

    with pytest.raises(InvalidTransition, match="mininfra is already connected"):
        manager.connect("mininfra")


def test_disconnect_flow(manager, runner, state_dir):
    with pytest.raises(InvalidTransition, match="mininfra is not connected"):
        manager.disconnect("mininfra")

    manager.connect("mininfra")
    manager.disconnect("mininfra")

    assert manager.snapshot_one("mininfra")["state"] == DISCONNECTED
    assert ("disconnect", "mininfra") in runner.calls
    assert not (state_dir / "mininfra.iface").exists()


def test_disconnect_clears_error_state(manager, runner):
    runner.connect_mode = "login-failed"
    manager.connect("mininfra")

    manager.disconnect("mininfra")

    s = manager.snapshot_one("mininfra")
    assert s["state"] == DISCONNECTED
    assert s["message"] is None


def test_refresh_detects_dropped_tunnel(manager, runner, state_dir):
    manager.connect("mininfra")
    runner.alive.clear()  # openconnect died on its own
    (state_dir / "mininfra.log").write_text(
        "[t] Configured as 10.9.9.9\n[t] SSL read error; reconnecting.\n"
        "[t] Failed to reconnect to host\n"
    )

    s = manager.snapshot_one("mininfra")

    assert s["state"] == ERROR
    assert s["message"] == "tunnel dropped: Failed to reconnect to host"
    assert not (state_dir / "mininfra.pid").exists()


def test_refresh_adopts_tunnel_running_from_previous_app_instance(
    vpns_file, runner, state_dir, clock
):
    (state_dir / "mininfra.pid").write_text("5555\n")
    (state_dir / "mininfra.iface").write_text("utun4 10.1.1.1\n")
    runner.alive.add(5555)

    s = make_manager(vpns_file, runner, state_dir, clock).snapshot_one("mininfra")

    assert s["state"] == CONNECTED
    assert s["interface"] == "utun4"
    assert s["ip"] == "10.1.1.1"
    assert s["message"] == "adopted running tunnel"


def test_refresh_cleans_stale_pid_file_without_error(vpns_file, runner, state_dir, clock):
    (state_dir / "mininfra.pid").write_text("5555\n")  # not alive, never connected here

    s = make_manager(vpns_file, runner, state_dir, clock).snapshot_one("mininfra")

    assert s["state"] == DISCONNECTED
    assert not (state_dir / "mininfra.pid").exists()


def test_connect_all_and_disconnect_all(vpns_file, runner, state_dir, clock):
    m = make_manager(vpns_file, runner, state_dir, clock, env={**ENV, "VPN_RICA_PASSWORD": ""})

    result = m.connect_all()
    assert result["started"] == ["mininfra"]
    assert result["skipped"] == [{"id": "rica", "reason": "missing credentials: VPN_RICA_PASSWORD"}]

    again = m.connect_all()
    assert again["started"] == []
    assert again["skipped"][0] == {"id": "mininfra", "reason": "mininfra is already connected"}

    off = m.disconnect_all()
    assert off["started"] == ["mininfra"]
    assert off["skipped"] == [{"id": "rica", "reason": "rica is not connected"}]


def test_log_tail_strips_timestamps(manager, runner, state_dir):
    manager.connect("mininfra")
    (state_dir / "mininfra.log").write_text(
        "[2026-09-17 14:00:00] first\n[2026-09-17 14:00:01] second\n"
    )

    assert manager.log_tail("mininfra", lines=1) == ["second"]
    assert manager.log_tail("mininfra") == ["first", "second"]
    with pytest.raises(UnknownVpn):
        manager.log_tail("ghost")


def test_refresh_flags_running_process_without_iface(vpns_file, runner, state_dir, clock):
    (state_dir / "mininfra.pid").write_text("5555\n")
    runner.alive.add(5555)

    m = make_manager(vpns_file, runner, state_dir, clock)
    s = m.snapshot_one("mininfra")

    assert s["state"] == ERROR
    assert s["message"] == (
        "openconnect is running (pid 5555) but no tunnel is configured; disconnect to clean up"
    )
    assert (state_dir / "mininfra.pid").exists()

    with pytest.raises(InvalidTransition, match="disconnect it first"):
        m.connect("mininfra")

    m.disconnect("mininfra")

    assert m.snapshot_one("mininfra")["state"] == DISCONNECTED
    assert not (state_dir / "mininfra.pid").exists()
    assert ("disconnect", "mininfra") in runner.calls


def test_connecting_state_is_visible_and_owned_by_worker(vpns_file, runner, state_dir, clock):
    CAPTURED_THREADS.clear()
    manager = TunnelManager(
        load_registry(vpns_file, ENV),
        runner,
        state_dir,
        env_file=vpns_file.with_name(".env"),
        env=dict(ENV),
        connect_timeout=30,
        poll_interval=0.5,
        pid_alive=lambda pid: pid in runner.alive,
        route_counter=lambda iface: 3,
        thread_factory=CapturedThread,
        clock=clock,
        sleep=clock.sleep,
    )

    manager.connect("mininfra")

    assert manager.snapshot_one("mininfra")["state"] == tunnel.CONNECTING

    manager.refresh()
    assert manager.state_of("mininfra").state == tunnel.CONNECTING

    with pytest.raises(InvalidTransition, match=r"is busy \(connecting\)"):
        manager.disconnect("mininfra")

    with pytest.raises(InvalidTransition, match="already connecting"):
        manager.connect("mininfra")

    CAPTURED_THREADS[-1].run()

    s = manager.snapshot_one("mininfra")
    assert s["state"] == CONNECTED
    assert s["interface"] == "utun9"


def test_reload_keeps_existing_states_adds_new_ids_and_drops_removed(manager, vpns_file):
    manager.connect("mininfra")
    assert manager.state_of("mininfra").state == CONNECTED

    add_vpn(vpns_file, {"id": "extra", "server": "vpn.extra.example", "authgroup": "g"})
    remove_vpn(vpns_file, "rica")
    manager.reload()

    assert [s["id"] for s in manager.snapshot()] == ["mininfra", "extra"]
    assert manager.state_of("mininfra").state == CONNECTED
    assert manager.state_of("extra").state == DISCONNECTED
    with pytest.raises(UnknownVpn):
        manager.snapshot_one("rica")


def test_reload_picks_up_credentials_added_to_the_environment(manager, vpns_file):
    manager.env.pop("VPN_RICA_PASSWORD")
    manager.reload()
    assert manager.snapshot_one("rica")["has_credentials"] is False

    manager.env["VPN_RICA_PASSWORD"] = "pw2"
    manager.reload()

    assert manager.snapshot_one("rica")["has_credentials"] is True


def test_create_vpn_writes_both_files_and_can_connect(manager, vpns_file):
    row = manager.create_vpn(
        {
            "id": "new-vpn",
            "name": "New VPN",
            "server": "vpn.new.example",
            "authgroup": "Staff",
            "routes": ["10.30.0.0/16"],
            "username": "longin",
            "password": "fresh-pw",
        }
    )

    assert row["id"] == "new-vpn"
    assert row["state"] == DISCONNECTED
    assert row["has_credentials"] is True
    assert row["routes"] == ["10.30.0.0/16"]
    assert "password" not in row and "username" not in row
    assert load_registry(vpns_file, env={}).get("new-vpn").server == "vpn.new.example"
    assert dotenv_values(manager.env_file)["VPN_NEW_VPN_PASSWORD"] == "fresh-pw"
    assert "fresh-pw" not in vpns_file.read_text()

    manager.connect("new-vpn")

    assert manager.state_of("new-vpn").state == CONNECTED


def test_create_vpn_writes_empty_lines_when_no_credentials_are_given(manager):
    row = manager.create_vpn({"id": "bare", "server": "vpn.bare.example", "authgroup": "g"})

    assert row["has_credentials"] is False
    assert row["missing_credentials"] == list(env_var_names("bare"))
    assert dotenv_values(manager.env_file)["VPN_BARE_PASSWORD"] == ""


def test_create_vpn_refuses_a_duplicate_id(manager, vpns_file):
    with pytest.raises(DuplicateVpn, match="duplicate id"):
        manager.create_vpn({"id": "mininfra", "server": "s.example", "authgroup": "g"})

    assert load_registry(vpns_file, env={}).get("mininfra").server == "vpn.mininfra.example"


def test_edit_vpn_keeps_the_password_when_none_is_given(manager):
    row = manager.edit_vpn("mininfra", {"name": "MININFRA HQ", "username": "other"})

    assert row["name"] == "MININFRA HQ"
    assert row["has_credentials"] is True
    assert manager.registry.get("mininfra").username == "other"
    assert manager.registry.get("mininfra").password == "pw1"
    assert dotenv_values(manager.env_file)["VPN_MININFRA_PASSWORD"] == "pw1"


def test_edit_vpn_in_error_state_clears_the_pin_on_a_new_server(manager, runner):
    runner.connect_mode = "login-failed"
    manager.connect("mininfra")
    assert manager.state_of("mininfra").state == ERROR

    row = manager.edit_vpn("mininfra", {"server": "vpn.moved.example"})

    assert row["server"] == "vpn.moved.example"
    assert row["servercert"] is None
    assert row["state"] == ERROR


def test_edit_and_delete_are_refused_while_connected(manager, vpns_file):
    manager.connect("mininfra")

    with pytest.raises(InvalidTransition, match="mininfra is connected"):
        manager.edit_vpn("mininfra", {"name": "x"})
    with pytest.raises(InvalidTransition, match="mininfra is connected"):
        manager.delete_vpn("mininfra")

    assert load_registry(vpns_file, env={}).get("mininfra").name == "MININFRA"


def test_edit_and_delete_reject_an_unknown_id(manager):
    with pytest.raises(UnknownVpn, match="unknown vpn: ghost"):
        manager.edit_vpn("ghost", {"name": "x"})
    with pytest.raises(UnknownVpn, match="unknown vpn: ghost"):
        manager.delete_vpn("ghost")


def test_delete_vpn_removes_the_entry_the_credentials_and_the_log(manager, vpns_file, state_dir):
    (state_dir / "rica.log").write_text("[t] old output\n")

    manager.delete_vpn("rica")

    assert [s["id"] for s in manager.snapshot()] == ["mininfra"]
    assert load_registry(vpns_file, env={}).ids() == ["mininfra"]
    assert not (state_dir / "rica.log").exists()
    assert "VPN_RICA_PASSWORD" not in dotenv_values(manager.env_file)
    assert "VPN_RICA_PASSWORD" not in manager.env
    assert dotenv_values(manager.env_file)["VPN_MININFRA_PASSWORD"] == "pw1"


def test_delete_vpn_is_refused_while_openconnect_is_running(manager, state_dir, runner):
    (state_dir / "mininfra.pid").write_text("5555\n")
    runner.alive.add(5555)
    assert manager.snapshot_one("mininfra")["state"] == ERROR

    with pytest.raises(InvalidTransition, match="disconnect it first"):
        manager.delete_vpn("mininfra")

    assert (state_dir / "mininfra.pid").exists()
    assert manager.registry.ids() == ["mininfra", "rica"]
