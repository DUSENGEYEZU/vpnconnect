import dataclasses

import pytest


def _drop_password(manager, vpn_id):
    manager.registry.vpns = [
        dataclasses.replace(v, password=None) if v.id == vpn_id else v
        for v in manager.registry.vpns
    ]


def test_list_vpns_returns_public_fields_and_state(client):
    response = client.get("/api/v1/vpns")

    assert response.status_code == 200
    vpns = response.get_json()["vpns"]
    assert [v["id"] for v in vpns] == ["mininfra", "rica"]
    assert vpns[0]["state"] == "disconnected"
    assert vpns[0]["has_credentials"] is True
    assert vpns[0]["routes"] == ["10.10.0.0/16"]
    for vpn in vpns:
        assert "password" not in vpn
        assert "username" not in vpn


def test_get_single_vpn_and_unknown(client):
    assert client.get("/api/v1/vpns/mininfra").get_json()["id"] == "mininfra"

    response = client.get("/api/v1/vpns/ghost")
    assert response.status_code == 404
    assert response.get_json() == {"error": "unknown vpn: ghost"}


def test_connect_returns_202_and_row_becomes_connected(client):
    response = client.post("/api/v1/vpns/mininfra/connect")

    assert response.status_code == 202
    assert response.get_json() == {"id": "mininfra", "state": "connecting"}
    row = client.get("/api/v1/vpns/mininfra").get_json()
    assert row["state"] == "connected"
    assert row["interface"] == "utun9"
    assert row["ip"] == "10.9.9.9"
    assert row["routes_count"] == 3


def test_connect_conflict_and_unknown(client):
    client.post("/api/v1/vpns/mininfra/connect")

    response = client.post("/api/v1/vpns/mininfra/connect")
    assert response.status_code == 409
    assert response.get_json() == {"error": "mininfra is already connected"}
    assert client.post("/api/v1/vpns/ghost/connect").status_code == 404


def test_connect_without_credentials_is_400(client, manager):
    _drop_password(manager, "rica")

    response = client.post("/api/v1/vpns/rica/connect")

    assert response.status_code == 400
    assert response.get_json() == {"error": "missing credentials: VPN_RICA_PASSWORD"}


def test_error_state_is_visible(client, runner):
    runner.connect_mode = "login-failed"

    client.post("/api/v1/vpns/mininfra/connect")

    row = client.get("/api/v1/vpns/mininfra").get_json()
    assert row["state"] == "error"
    assert row["message"] == "Login failed."


def test_disconnect_flow(client):
    response = client.post("/api/v1/vpns/mininfra/disconnect")
    assert response.status_code == 409
    assert response.get_json() == {"error": "mininfra is not connected"}

    client.post("/api/v1/vpns/mininfra/connect")
    response = client.post("/api/v1/vpns/mininfra/disconnect")

    assert response.status_code == 202
    assert response.get_json() == {"id": "mininfra", "state": "disconnecting"}
    assert client.get("/api/v1/vpns/mininfra").get_json()["state"] == "disconnected"
    assert client.post("/api/v1/vpns/ghost/disconnect").status_code == 404


def test_connect_all_and_disconnect_all(client, manager):
    _drop_password(manager, "rica")

    response = client.post("/api/v1/vpns/connect-all")
    assert response.status_code == 202
    assert response.get_json() == {
        "started": ["mininfra"],
        "skipped": [{"id": "rica", "reason": "missing credentials: VPN_RICA_PASSWORD"}],
    }

    response = client.post("/api/v1/vpns/disconnect-all")
    assert response.status_code == 202
    assert response.get_json() == {
        "started": ["mininfra"],
        "skipped": [{"id": "rica", "reason": "rica is not connected"}],
    }


def test_log_endpoint(client, state_dir):
    client.post("/api/v1/vpns/mininfra/connect")
    (state_dir / "mininfra.log").write_text("[t] one\n[t] two\n[t] three\n")

    response = client.get("/api/v1/vpns/mininfra/log?lines=2")

    assert response.status_code == 200
    assert response.get_json() == {"id": "mininfra", "lines": ["two", "three"]}
    assert client.get("/api/v1/vpns/mininfra/log").get_json()["lines"] == ["one", "two", "three"]
    assert client.get("/api/v1/vpns/ghost/log").status_code == 404


def test_log_lines_parameter_is_clamped(client, state_dir):
    (state_dir / "mininfra.log").write_text("\n".join(str(i) for i in range(600)) + "\n")

    assert len(client.get("/api/v1/vpns/mininfra/log?lines=0").get_json()["lines"]) == 1
    assert len(client.get("/api/v1/vpns/mininfra/log?lines=9999").get_json()["lines"]) == 500
    assert len(client.get("/api/v1/vpns/mininfra/log?lines=abc").get_json()["lines"]) == 50


SECRET = "s3cret-from-the-api"
NEW_VPN = {
    "id": "new-vpn",
    "name": "New VPN",
    "server": "vpn.new.example",
    "authgroup": "Staff",
    "routes": ["10.30.0.0/16"],
    "username": "longin",
    "password": SECRET,
}


def test_create_vpn_returns_201_with_the_public_object(client):
    response = client.post("/api/v1/vpns", json=NEW_VPN)

    assert response.status_code == 201
    body = response.get_json()
    assert body["id"] == "new-vpn"
    assert body["name"] == "New VPN"
    assert body["server"] == "vpn.new.example"
    assert body["authgroup"] == "Staff"
    assert body["routes"] == ["10.30.0.0/16"]
    assert body["state"] == "disconnected"
    assert body["has_credentials"] is True
    assert body["servercert"] is None
    assert body == client.get("/api/v1/vpns/new-vpn").get_json()
    assert [v["id"] for v in client.get("/api/v1/vpns").get_json()["vpns"]] == [
        "mininfra",
        "rica",
        "new-vpn",
    ]


def test_no_response_ever_contains_a_password(client):
    created = client.post("/api/v1/vpns", json=NEW_VPN)
    updated = client.put("/api/v1/vpns/new-vpn", json={"password": SECRET, "name": "Renamed"})
    listed = client.get("/api/v1/vpns")
    one = client.get("/api/v1/vpns/new-vpn")

    for response in (created, updated, listed, one):
        assert SECRET not in response.get_data(as_text=True)
        assert "longin" not in response.get_data(as_text=True)
    for body in (created.get_json(), updated.get_json(), one.get_json()):
        assert "password" not in body
        assert "username" not in body


def test_a_password_never_reaches_vpns_yaml(client, vpns_file):
    client.post("/api/v1/vpns", json=NEW_VPN)
    client.put("/api/v1/vpns/mininfra", json={"username": "longin", "password": SECRET})

    text = vpns_file.read_text()
    assert SECRET not in text
    assert "username" not in text and "password" not in text


def test_create_vpn_duplicate_id_is_409(client):
    response = client.post("/api/v1/vpns", json={**NEW_VPN, "id": "mininfra"})

    assert response.status_code == 409
    assert response.get_json() == {"error": "vpns[mininfra]: duplicate id"}


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({**NEW_VPN, "id": "Bad Id"}, "id must match"),
        ({k: v for k, v in NEW_VPN.items() if k != "server"}, "'server' is required"),
        ({k: v for k, v in NEW_VPN.items() if k != "authgroup"}, "'authgroup' is required"),
        ({**NEW_VPN, "routes": ["nonsense"]}, "not an IPv4 network"),
        ({**NEW_VPN, "routes": ["0.0.0.0/0"]}, "default route"),
        ({**NEW_VPN, "routes": "10.0.0.0/8"}, "'routes' must be a list"),
        ({**NEW_VPN, "password": 5}, "'password' must be a string"),
        ({**NEW_VPN, "servercert": "pin-sha256:x="}, "unknown field: 'servercert'"),
        ({**NEW_VPN, "protocol": "nc"}, "unknown field: 'protocol'"),
        ({}, "'id' is required"),
    ],
)
def test_create_vpn_invalid_body_is_400_naming_the_field(client, body, fragment):
    response = client.post("/api/v1/vpns", json=body)

    assert response.status_code == 400
    assert fragment in response.get_json()["error"]
    assert [v["id"] for v in client.get("/api/v1/vpns").get_json()["vpns"]] == ["mininfra", "rica"]


def test_create_vpn_with_a_non_object_body_is_400(client):
    response = client.post("/api/v1/vpns", json=["nope"])

    assert response.status_code == 400
    assert response.get_json() == {"error": "body must be a JSON object"}


def test_connect_works_on_a_vpn_added_through_the_api(client):
    client.post("/api/v1/vpns", json=NEW_VPN)

    assert client.post("/api/v1/vpns/new-vpn/connect").status_code == 202

    row = client.get("/api/v1/vpns/new-vpn").get_json()
    assert row["state"] == "connected"
    assert row["interface"] == "utun9"


def test_update_vpn_changes_only_what_is_sent(client):
    response = client.put("/api/v1/vpns/mininfra", json={"name": "MININFRA HQ"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "MININFRA HQ"
    assert body["server"] == "vpn.mininfra.example"
    assert body["routes"] == ["10.10.0.0/16"]
    assert body["has_credentials"] is True


def test_update_vpn_new_server_clears_the_stored_pin(client):
    body = client.put("/api/v1/vpns/mininfra", json={"server": "vpn.moved.example"}).get_json()

    assert body["server"] == "vpn.moved.example"
    assert body["servercert"] is None


def test_update_vpn_unknown_id_is_404(client):
    response = client.put("/api/v1/vpns/ghost", json={"name": "x"})

    assert response.status_code == 404
    assert response.get_json() == {"error": "unknown vpn: ghost"}


def test_update_vpn_cannot_change_the_id(client):
    response = client.put("/api/v1/vpns/mininfra", json={"id": "other"})

    assert response.status_code == 400
    assert "id cannot be changed" in response.get_json()["error"]


def test_update_and_delete_are_409_while_connected(client):
    client.post("/api/v1/vpns/mininfra/connect")

    updated = client.put("/api/v1/vpns/mininfra", json={"name": "x"})
    deleted = client.delete("/api/v1/vpns/mininfra")

    assert updated.status_code == 409
    assert deleted.status_code == 409
    assert "mininfra is connected" in updated.get_json()["error"]
    assert "mininfra is connected" in deleted.get_json()["error"]
    assert client.get("/api/v1/vpns/mininfra").get_json()["name"] == "MININFRA"


def test_delete_vpn_returns_204_and_removes_it(client, state_dir):
    (state_dir / "rica.log").write_text("[t] old\n")

    response = client.delete("/api/v1/vpns/rica")

    assert response.status_code == 204
    assert response.get_data() == b""
    assert [v["id"] for v in client.get("/api/v1/vpns").get_json()["vpns"]] == ["mininfra"]
    assert client.get("/api/v1/vpns/rica").status_code == 404
    assert not (state_dir / "rica.log").exists()


def test_delete_vpn_unknown_id_is_404(client):
    response = client.delete("/api/v1/vpns/ghost")

    assert response.status_code == 404
    assert response.get_json() == {"error": "unknown vpn: ghost"}


MUTATIONS = [
    ("post", "/api/v1/vpns/mininfra/connect"),
    ("post", "/api/v1/vpns/mininfra/disconnect"),
    ("post", "/api/v1/vpns/connect-all"),
    ("post", "/api/v1/vpns/disconnect-all"),
    ("post", "/api/v1/vpns"),
    ("put", "/api/v1/vpns/mininfra"),
    ("delete", "/api/v1/vpns/mininfra"),
]


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_a_foreign_origin_is_refused_on_every_mutation(client, method, path):
    response = getattr(client, method)(path, headers={"Origin": "http://evil.example"})

    assert response.status_code == 403
    assert "origin" in response.get_json()["error"]
    assert client.get("/api/v1/vpns/mininfra").get_json()["state"] == "disconnected"


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_a_foreign_host_is_refused_on_every_mutation(client, method, path):
    response = getattr(client, method)(path, base_url="http://vpnconnect.example")

    assert response.status_code == 403
    assert "host" in response.get_json()["error"]


def test_reads_are_never_blocked(client):
    for base in ("http://localhost", "http://vpnconnect.example"):
        assert client.get("/api/v1/vpns", base_url=base).status_code == 200
        assert (
            client.get(
                "/api/v1/vpns/mininfra", headers={"Origin": "http://evil.example"}
            ).status_code
            == 200
        )


@pytest.mark.parametrize("base", ["http://localhost", "http://127.0.0.1:5110"])
def test_the_dashboards_own_origin_is_allowed(client, base):
    response = client.post("/api/v1/vpns/mininfra/connect", base_url=base, headers={"Origin": base})

    assert response.status_code == 202


def test_a_body_less_post_without_a_content_type_still_works(client):
    response = client.post("/api/v1/vpns/connect-all")

    assert response.status_code == 202


@pytest.mark.parametrize("field", ["username", "password"])
def test_a_credential_dotenv_would_interpolate_is_refused(client, field):
    response = client.post("/api/v1/vpns", json={**NEW_VPN, field: "pa${LEAKED}ss"})

    assert response.status_code == 400
    assert field in response.get_json()["error"]
    assert "must not contain" in response.get_json()["error"]
    assert "LEAKED" not in response.get_data(as_text=True)
    assert [v["id"] for v in client.get("/api/v1/vpns").get_json()["vpns"]] == ["mininfra", "rica"]


def test_an_interpolating_password_is_refused_on_update_too(client):
    response = client.put("/api/v1/vpns/mininfra", json={"password": "x${LEAKED}y"})

    assert response.status_code == 400
    assert "LEAKED" not in response.get_data(as_text=True)
    assert client.get("/api/v1/vpns/mininfra").get_json()["has_credentials"] is True
