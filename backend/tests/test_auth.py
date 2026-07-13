def test_login_with_default_admin_succeeds(client):
    resp = client.post("/api/auth/login", json={"email": "admin@justdial.com", "password": "password"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@justdial.com"
    assert body["token"]


def test_login_with_wrong_password_returns_helpful_401(client):
    resp = client.post("/api/auth/login", json={"email": "admin@justdial.com", "password": "wrong"})
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.json()["detail"]


def test_login_with_unknown_email_returns_401_not_500(client):
    resp = client.post("/api/auth/login", json={"email": "nobody@justdial.com", "password": "password"})
    assert resp.status_code == 401


def test_protected_route_without_token_returns_401(client):
    resp = client.get("/api/bots")
    assert resp.status_code == 401
    assert "Missing bearer token" in resp.json()["detail"]


def test_protected_route_with_garbage_token_returns_401_not_500(client):
    resp = client.get("/api/bots", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401
    assert "Invalid or expired token" in resp.json()["detail"]


def test_health_check_is_public(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
