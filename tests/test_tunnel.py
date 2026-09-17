import pytest

from app.services import tunnel
from app.services.registry import load_registry
from app.services.tunnel import (
    CONNECTED,
    DISCONNECTED,
    ERROR,
    InvalidTransition,
    MissingCredentials,
    TunnelManager,
    UnknownVpn,
)
from tests.fakes import ENV, VPNS_YAML, FakeClock, FakeRunner, ImmediateThread

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
    return TunnelManager(
        load_registry(vpns_file, env),
        runner,
        state_dir,
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
