import textwrap

import pytest

from app.services.registry import (
    TRUSTED_CA,
    RegistryError,
    load_registry,
    save_servercert,
)

VALID_YAML = textwrap.dedent("""
    vpns:
      - id: mininfra
        name: MININFRA office
        server: vpn.mininfra.example
        authgroup: Staff
        routes:
          - 10.10.0.0/16
          - 10.20.5.0/24
      - id: rica-hq
        server: vpn.rica.example:8443
        authgroup: Employees
        servercert: pin-sha256:abc123=
""")


@pytest.fixture
def vpns_file(tmp_path):
    path = tmp_path / "vpns.yaml"
    path.write_text(VALID_YAML)
    return path


def test_load_registry_parses_all_fields(vpns_file):
    registry = load_registry(vpns_file, env={})

    assert registry.ids() == ["mininfra", "rica-hq"]
    mininfra = registry.get("mininfra")
    assert mininfra.name == "MININFRA office"
    assert mininfra.server == "vpn.mininfra.example"
    assert mininfra.authgroup == "Staff"
    assert mininfra.protocol == "anyconnect"
    assert [str(r) for r in mininfra.routes] == ["10.10.0.0/16", "10.20.5.0/24"]
    assert mininfra.servercert is None


def test_name_defaults_to_id_and_servercert_is_kept(vpns_file):
    rica = load_registry(vpns_file, env={}).get("rica-hq")

    assert rica.name == "rica-hq"
    assert rica.servercert == "pin-sha256:abc123="
    assert rica.routes == ()
    assert load_registry(vpns_file, env={}).get("ghost") is None


def test_credentials_come_from_env_with_dash_mapped_to_underscore(vpns_file):
    env = {"VPN_RICA_HQ_USERNAME": "longin", "VPN_RICA_HQ_PASSWORD": "s3cret"}
    registry = load_registry(vpns_file, env=env)

    rica = registry.get("rica-hq")
    assert rica.env_prefix == "VPN_RICA_HQ"
    assert rica.username == "longin"
    assert rica.password == "s3cret"
    assert rica.has_credentials is True
    assert rica.missing_credentials == []

    mininfra = registry.get("mininfra")
    assert mininfra.has_credentials is False
    assert mininfra.missing_credentials == ["VPN_MININFRA_USERNAME", "VPN_MININFRA_PASSWORD"]


def test_blank_env_value_counts_as_missing(vpns_file):
    env = {"VPN_MININFRA_USERNAME": "longin", "VPN_MININFRA_PASSWORD": ""}

    assert load_registry(vpns_file, env=env).get("mininfra").missing_credentials == [
        "VPN_MININFRA_PASSWORD"
    ]


def test_routes_arg_builds_addr_mask_len_triples(vpns_file):
    registry = load_registry(vpns_file, env={})

    assert (
        registry.get("mininfra").routes_arg()
        == "10.10.0.0:255.255.0.0:16,10.20.5.0:255.255.255.0:24"
    )
    assert registry.get("rica-hq").routes_arg() == "-"


def test_public_never_exposes_credentials(vpns_file):
    env = {"VPN_MININFRA_USERNAME": "u", "VPN_MININFRA_PASSWORD": "p"}
    public = load_registry(vpns_file, env=env).get("mininfra").public()

    assert "password" not in public
    assert "username" not in public
    assert public["has_credentials"] is True
    assert public["missing_credentials"] == []
    assert public["routes"] == ["10.10.0.0/16", "10.20.5.0/24"]
    assert public["servercert"] is None


@pytest.mark.parametrize(
    ("yaml_text", "fragment"),
    [
        ("vpns:\n  - id: Bad Id\n    server: s\n    authgroup: g\n", "id must match"),
        ("vpns:\n  - id: ok\n    authgroup: g\n", "'server' is required"),
        ("vpns:\n  - id: ok\n    server: s\n", "'authgroup' is required"),
        (
            "vpns:\n  - id: ok\n    server: s\n    authgroup: g\n    routes: [nonsense]\n",
            "not an IPv4 network",
        ),
        (
            "vpns:\n  - id: dup\n    server: s\n    authgroup: g\n"
            "  - id: dup\n    server: s\n    authgroup: g\n",
            "duplicate id",
        ),
        ("vpns: notalist\n", "'vpns' must be a list"),
        ("vpns:\n  - id: [unclosed\n", "invalid YAML"),
        ("vpns:\n  - oops\n", "entry must be a mapping"),
    ],
)
def test_invalid_files_raise_registry_error_naming_the_problem(tmp_path, yaml_text, fragment):
    path = tmp_path / "vpns.yaml"
    path.write_text(yaml_text)

    with pytest.raises(RegistryError, match=fragment):
        load_registry(path, env={})


def test_missing_file_raises(tmp_path):
    with pytest.raises(RegistryError, match="does not exist"):
        load_registry(tmp_path / "nope.yaml", env={})


def test_save_servercert_updates_only_that_vpn(vpns_file):
    save_servercert(vpns_file, "mininfra", "pin-sha256:NEW=")

    registry = load_registry(vpns_file, env={})
    assert registry.get("mininfra").servercert == "pin-sha256:NEW="
    assert registry.get("rica-hq").servercert == "pin-sha256:abc123="
    assert [str(r) for r in registry.get("mininfra").routes] == ["10.10.0.0/16", "10.20.5.0/24"]


def test_save_servercert_unknown_id_raises(vpns_file):
    with pytest.raises(RegistryError, match="not found"):
        save_servercert(vpns_file, "ghost", TRUSTED_CA)


def test_save_servercert_invalid_yaml_raises(tmp_path):
    path = tmp_path / "vpns.yaml"
    path.write_text("vpns:\n  - id: [unclosed\n")

    with pytest.raises(RegistryError, match="invalid YAML"):
        save_servercert(path, "mininfra", TRUSTED_CA)


def test_repr_never_shows_credentials(vpns_file):
    env = {"VPN_MININFRA_USERNAME": "longin", "VPN_MININFRA_PASSWORD": "s3cret-value"}
    vpn = load_registry(vpns_file, env=env).get("mininfra")

    vpn_repr = repr(vpn)
    assert "s3cret-value" not in vpn_repr
    assert "longin" not in vpn_repr
    assert "mininfra" in vpn_repr
