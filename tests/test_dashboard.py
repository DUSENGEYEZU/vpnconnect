def test_dashboard_is_served_at_root(client):
    response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert "<title>vpnconnect</title>" in html
    assert 'id="connect-all"' in html
    assert 'id="disconnect-all"' in html
    assert 'id="rows"' in html
    assert '"/api/v1/vpns"' in html
