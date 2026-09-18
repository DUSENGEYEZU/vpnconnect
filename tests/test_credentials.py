import os
import stat
import textwrap

import pytest
from dotenv import dotenv_values

from app.services.credentials import (
    CredentialError,
    env_var_names,
    remove_credentials,
    scaffold_credentials,
    set_credentials,
)

EXISTING = textwrap.dedent("""\
    SECRET_KEY=keep-me

    # One pair per VPN id.
    VPN_MININFRA_USERNAME=longin
    VPN_MININFRA_PASSWORD=old-pw
    # VPNCONNECT_STATE_DIR=./state
""")


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(EXISTING)
    return path


def test_env_var_names_follow_the_id():
    assert env_var_names("rica-hq") == ("VPN_RICA_HQ_USERNAME", "VPN_RICA_HQ_PASSWORD")


def test_update_in_place_keeps_comments_order_and_other_variables(env_file):
    env = {}

    set_credentials(env_file, "mininfra", username="new-user", password="new-pw", env=env)

    assert env_file.read_text() == textwrap.dedent("""\
        SECRET_KEY=keep-me

        # One pair per VPN id.
        VPN_MININFRA_USERNAME=new-user
        VPN_MININFRA_PASSWORD=new-pw
        # VPNCONNECT_STATE_DIR=./state
    """)
    assert env == {"VPN_MININFRA_USERNAME": "new-user", "VPN_MININFRA_PASSWORD": "new-pw"}


def test_a_new_id_appends_both_lines(env_file):
    set_credentials(env_file, "rica-hq", username="longin", password="", env={})

    lines = env_file.read_text().splitlines()
    assert lines[-2:] == ["VPN_RICA_HQ_USERNAME=longin", "VPN_RICA_HQ_PASSWORD="]
    assert dotenv_values(env_file)["VPN_MININFRA_USERNAME"] == "longin"


def test_none_keeps_the_current_line(env_file):
    set_credentials(env_file, "mininfra", password="rotated", env={})

    values = dotenv_values(env_file)
    assert values["VPN_MININFRA_USERNAME"] == "longin"
    assert values["VPN_MININFRA_PASSWORD"] == "rotated"


def test_nothing_to_write_leaves_the_file_untouched(env_file):
    set_credentials(env_file, "mininfra", env={})

    assert env_file.read_text() == EXISTING


def test_remove_deletes_both_lines_and_nothing_else(env_file):
    env = {"VPN_MININFRA_USERNAME": "longin", "VPN_MININFRA_PASSWORD": "old-pw", "OTHER": "x"}

    remove_credentials(env_file, "mininfra", env=env)

    assert env_file.read_text() == textwrap.dedent("""\
        SECRET_KEY=keep-me

        # One pair per VPN id.
        # VPNCONNECT_STATE_DIR=./state
    """)
    assert env == {"OTHER": "x"}


def test_removing_an_unknown_id_is_a_no_op(env_file):
    remove_credentials(env_file, "ghost", env={})

    assert env_file.read_text() == EXISTING


def test_duplicate_lines_for_one_key_collapse_into_one(tmp_path):
    path = tmp_path / ".env"
    path.write_text("VPN_X_USERNAME=first\nKEEP=1\nVPN_X_USERNAME=second\n")

    set_credentials(path, "x", username="third", env={})

    assert path.read_text() == "VPN_X_USERNAME=third\nKEEP=1\n"


def test_export_and_spaced_assignments_are_recognised(tmp_path):
    path = tmp_path / ".env"
    path.write_text("export VPN_X_USERNAME=old\nVPN_X_PASSWORD =old\n")

    set_credentials(path, "x", username="u", password="p", env={})

    assert path.read_text() == "VPN_X_USERNAME=u\nVPN_X_PASSWORD=p\n"


def test_a_missing_file_is_created(tmp_path):
    path = tmp_path / ".env"

    set_credentials(path, "x", username="u", password="p", env={})

    assert dotenv_values(path) == {"VPN_X_USERNAME": "u", "VPN_X_PASSWORD": "p"}


@pytest.mark.parametrize(
    ("value", "line"),
    [
        ("simple", "VPN_X_PASSWORD=simple"),
        ("with space", "VPN_X_PASSWORD=with space"),
        (" padded ", 'VPN_X_PASSWORD=" padded "'),
        ("hash#tag", 'VPN_X_PASSWORD="hash#tag"'),
        ('quo"te', 'VPN_X_PASSWORD="quo\\"te"'),
        ("back\\slash", "VPN_X_PASSWORD=back\\slash"),
        ("two\nlines", 'VPN_X_PASSWORD="two\\nlines"'),
        ("'single'", "VPN_X_PASSWORD=\"'single'\""),
    ],
)
def test_values_are_quoted_only_when_dotenv_needs_it(tmp_path, value, line):
    path = tmp_path / ".env"

    set_credentials(path, "x", password=value, env={})

    assert path.read_text().splitlines() == [line]
    assert dotenv_values(path)["VPN_X_PASSWORD"] == value


def test_the_file_is_written_atomically_with_mode_0600(tmp_path):
    path = tmp_path / ".env"
    path.write_text("KEEP=1\n")
    path.chmod(0o644)

    set_credentials(path, "x", password="p", env={})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_os_environ_is_the_default_target(monkeypatch, env_file):
    monkeypatch.setenv("VPN_MININFRA_USERNAME", "stale")
    monkeypatch.setenv("VPN_MININFRA_PASSWORD", "stale")

    set_credentials(env_file, "mininfra", username="fresh", password="fresh-pw")
    assert os.environ["VPN_MININFRA_USERNAME"] == "fresh"
    assert os.environ["VPN_MININFRA_PASSWORD"] == "fresh-pw"

    remove_credentials(env_file, "mininfra")
    assert "VPN_MININFRA_USERNAME" not in os.environ
    assert "VPN_MININFRA_PASSWORD" not in os.environ


@pytest.mark.parametrize("value", ["${HOME}", "pre${VAR}post"])
def test_a_value_dotenv_would_interpolate_is_refused(env_file, value):
    with pytest.raises(CredentialError, match="VPN_MININFRA_PASSWORD must not contain"):
        set_credentials(env_file, "mininfra", password=value, env={})

    assert env_file.read_text() == EXISTING


def test_scaffold_keeps_values_already_in_the_file(env_file):
    scaffold_credentials(env_file, "mininfra", env={})

    assert env_file.read_text() == EXISTING


def test_scaffold_writes_what_is_given_and_appends_only_missing_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("VPN_X_USERNAME=by-hand\n")

    scaffold_credentials(path, "x", password="pw", env={})

    assert path.read_text() == "VPN_X_USERNAME=by-hand\nVPN_X_PASSWORD=pw\n"


def test_scaffold_appends_both_lines_empty_for_a_fresh_id(env_file):
    env = {}

    scaffold_credentials(env_file, "rica-hq", env=env)

    assert env_file.read_text().splitlines()[-2:] == [
        "VPN_RICA_HQ_USERNAME=",
        "VPN_RICA_HQ_PASSWORD=",
    ]
    assert env == {"VPN_RICA_HQ_USERNAME": "", "VPN_RICA_HQ_PASSWORD": ""}
