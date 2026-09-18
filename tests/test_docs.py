import pytest


@pytest.fixture
def spec(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.get_json()


def test_swagger_ui_is_served_at_docs(client):
    response = client.get("/docs/")

    assert response.status_code == 200
    assert b"swagger" in response.data.lower()
    assert b"<title>vpnconnect API</title>" in response.data


def test_openapi_spec_describes_the_api(spec):
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "vpnconnect API"
    assert spec["info"]["version"]


def test_openapi_spec_documents_health_endpoint(spec):
    assert "200" in spec["paths"]["/api/v1/health"]["get"]["responses"]


def test_openapi_spec_defines_vpn_status_schema(spec):
    props = spec["components"]["schemas"]["VpnStatus"]["properties"]

    assert set(props) == {
        "id",
        "name",
        "server",
        "authgroup",
        "protocol",
        "routes",
        "servercert",
        "has_credentials",
        "missing_credentials",
        "state",
        "interface",
        "ip",
        "routes_count",
        "message",
        "since",
    }
    assert props["state"]["enum"] == [
        "disconnected",
        "connecting",
        "connected",
        "disconnecting",
        "error",
    ]
    assert {"VpnStatus", "ActionAccepted", "BulkResult", "LogTail", "Error"} <= set(
        spec["components"]["schemas"]
    )


def test_openapi_spec_documents_every_vpn_endpoint(spec):
    paths = spec["paths"]

    assert "200" in paths["/api/v1/vpns"]["get"]["responses"]
    assert set(paths["/api/v1/vpns/{vpn_id}"]["get"]["responses"]) == {"200", "404"}
    assert set(paths["/api/v1/vpns/{vpn_id}/connect"]["post"]["responses"]) == {
        "202",
        "400",
        "404",
        "409",
    }
    assert set(paths["/api/v1/vpns/{vpn_id}/disconnect"]["post"]["responses"]) == {
        "202",
        "404",
        "409",
    }
    assert "202" in paths["/api/v1/vpns/connect-all"]["post"]["responses"]
    assert "202" in paths["/api/v1/vpns/disconnect-all"]["post"]["responses"]
    assert set(paths["/api/v1/vpns/{vpn_id}/log"]["get"]["responses"]) == {"200", "404"}


def test_openapi_spec_documents_the_editor_endpoints(spec):
    paths = spec["paths"]

    assert set(paths["/api/v1/vpns"]["post"]["responses"]) == {"201", "400", "409"}
    assert set(paths["/api/v1/vpns/{vpn_id}"]["put"]["responses"]) == {"200", "400", "404", "409"}
    assert set(paths["/api/v1/vpns/{vpn_id}"]["delete"]["responses"]) == {"204", "404", "409"}


def test_openapi_spec_defines_write_only_credential_schemas(spec):
    schemas = spec["components"]["schemas"]

    assert {"VpnCreate", "VpnUpdate"} <= set(schemas)
    create = schemas["VpnCreate"]
    assert create["required"] == ["id", "server", "authgroup"]
    assert set(create["properties"]) == {
        "id",
        "name",
        "server",
        "authgroup",
        "routes",
        "username",
        "password",
    }
    assert create["properties"]["password"]["writeOnly"] is True
    assert create["properties"]["username"]["writeOnly"] is True
    assert "id" not in schemas["VpnUpdate"]["properties"]
