import pytest

from app import create_app
from app.config import Config
from app.services.registry import load_registry
from app.services.tunnel import TunnelManager
from tests.fakes import ENV, VPNS_YAML, FakeClock, FakeRunner, ImmediateThread


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


@pytest.fixture
def manager(vpns_file, runner, state_dir, clock):
    return TunnelManager(
        load_registry(vpns_file, ENV),
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
def app(manager):
    class TestConfig(Config):
        TESTING = True
        SECRET_KEY = "test-secret"

    return create_app(TestConfig, manager=manager)


@pytest.fixture
def client(app):
    return app.test_client()
