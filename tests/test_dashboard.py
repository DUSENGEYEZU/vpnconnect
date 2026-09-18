def test_dashboard_is_served_at_root(client):
    response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert "<title>vpnconnect</title>" in html
    assert 'id="connect-all"' in html
    assert 'id="disconnect-all"' in html
    assert 'id="rows"' in html
    assert '"/api/v1/vpns"' in html


def test_dashboard_offers_an_add_vpn_form(client):
    html = client.get("/").data.decode()

    assert 'id="add-vpn"' in html
    assert 'id="vpn-form"' in html
    assert 'id="vpn-form-title"' in html
    for field in ("id", "name", "server", "authgroup", "routes", "username", "password"):
        assert f'id="vpn-{field}"' in html
    assert 'id="vpn-password" type="password"' in html
    assert "One IPv4 CIDR per line" in html
    assert "Leave blank to keep the stored password" in html


def test_dashboard_rows_offer_edit_and_delete(client):
    html = client.get("/").data.decode()

    assert 'data-act="edit"' in html
    assert 'data-act="delete"' in html
    assert "canEdit" in html  # both buttons are disabled unless the row is idle
    assert "confirm(" in html
    assert '"PUT"' in html
    assert '"DELETE"' in html
