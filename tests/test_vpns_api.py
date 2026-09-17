import dataclasses


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
