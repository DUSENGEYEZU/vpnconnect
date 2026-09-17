from pathlib import Path

import pytest

from app.config import Config, env_int

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_paths_default_to_the_repository():
    assert Path(Config.VPNS_FILE) == REPO_ROOT / "vpns.yaml"
    assert Path(Config.STATE_DIR) == REPO_ROOT / "state"


def test_helper_and_timeouts_have_defaults():
    assert Config.HELPER == "/usr/local/libexec/vpnconnect/vpnconnect-helper"
    assert Config.CONNECT_TIMEOUT == 30
    assert Config.DISCONNECT_GRACE == 5


@pytest.mark.parametrize(("value", "expected"), [("12", 12), (" 7 ", 7), ("", 30), (None, 30)])
def test_env_int(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SOME_INT", raising=False)
    else:
        monkeypatch.setenv("SOME_INT", value)
    assert env_int("SOME_INT", 30) == expected
