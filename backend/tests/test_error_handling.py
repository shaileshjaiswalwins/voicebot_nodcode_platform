from unittest.mock import patch

from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError

from backend import db as db_module
from backend.main import app


def test_mongo_error_mid_request_returns_helpful_503_not_bare_500(auth_headers):
    with TestClient(app, raise_server_exceptions=False) as raw_client:
        with patch.object(db_module.bots, "find", side_effect=PyMongoError("connection lost")):
            resp = raw_client.get("/api/bots", headers=auth_headers)
    assert resp.status_code == 503
    assert "Database is temporarily unreachable" in resp.json()["detail"]


def test_unexpected_error_mid_request_returns_structured_500_not_plaintext(auth_headers):
    with TestClient(app, raise_server_exceptions=False) as raw_client:
        with patch.object(db_module.bots, "find", side_effect=RuntimeError("boom")):
            resp = raw_client.get("/api/bots", headers=auth_headers)
    assert resp.status_code == 500
    assert resp.json()["detail"]  # structured JSON with a real detail field, not bare text


def test_existing_404_paths_still_return_specific_messages_not_generic_500(client, auth_headers):
    """Guards against the new catch-all Exception handler accidentally swallowing the
    router's own HTTPException(404, ...) calls and turning them into generic 500s."""
    resp = client.get("/api/bots/000000000000000000000000", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Bot not found"
