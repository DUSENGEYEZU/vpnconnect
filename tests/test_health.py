def test_health_returns_ok(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_manager_is_attached_to_the_app(app, manager):
    assert app.extensions["tunnels"] is manager
