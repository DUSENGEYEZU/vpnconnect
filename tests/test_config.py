import logging
from pathlib import Path

import pytest

from app import create_app
from app.config import Config, env_int

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_paths_default_to_the_repository():
    assert Path(Config.VPNS_FILE) == REPO_ROOT / "vpns.yaml"
    assert Path(Config.STATE_DIR) == REPO_ROOT / "state"
    assert Path(Config.ENV_FILE) == REPO_ROOT / ".env"


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


def test_create_app_builds_the_real_manager_from_a_yaml_file(tmp_path):
    class TmpConfig(Config):
        VPNS_FILE = str(REPO_ROOT / "vpns.example.yaml")
        STATE_DIR = str(tmp_path / "state")

    app = create_app(TmpConfig)

    assert [v["id"] for v in app.extensions["tunnels"].snapshot()] == ["mininfra", "rica-hq"]


def test_a_helper_installed_with_another_state_dir_warns_at_startup(tmp_path, caplog):
    """The helper derives every path from its own baked STATE_DIR, so an app
    looking elsewhere would never see the pid, log and iface files.
    """
    state_dir = tmp_path / "state"
    helper = tmp_path / "vpnconnect-helper"

    class TmpConfig(Config):
        VPNS_FILE = str(REPO_ROOT / "vpns.example.yaml")
        STATE_DIR = str(state_dir)
        HELPER = str(helper)

    helper.write_text(f'#!/bin/bash\nSTATE_DIR="{state_dir}"\n')
    with caplog.at_level(logging.WARNING):
        create_app(TmpConfig)
    assert caplog.text == ""

    helper.write_text('#!/bin/bash\nSTATE_DIR="/var/somewhere/else"\n')
    with caplog.at_level(logging.WARNING):
        create_app(TmpConfig)

    assert "/var/somewhere/else" in caplog.text
    assert str(state_dir) in caplog.text
    assert "setup-privileges.sh" in caplog.text
