import os

os.environ["USE_INMEMORY_DB"] = "true"
os.environ.setdefault("DASHBOARD_JWT_SECRET", "test-secret-32-bytes-minimum-length")

import pytest
from fastapi.testclient import TestClient

from backend import db as db_module
from backend import analysis_prompts as analysis_prompts_module
from backend.main import app


@pytest.fixture(autouse=True)
def _clean_db():
    """mongomock collections persist across tests since the client is created once at
    import time — wipe every collection before each test so tests don't leak state."""
    for coll in (
        db_module.users,
        db_module.bots,
        db_module.bot_versions,
        db_module.platform_settings,
        db_module.campaigns,
        db_module.language_settings,
        db_module.db["tbl_ai_vb_library_phrases"],
        db_module.db["tbl_ai_vb_outcome_catalog"],
        db_module.db["tbl_ai_vb_analysis_prompts"],
        db_module.phone_numbers,
        db_module.audit_log,
        db_module.db["tbl_ai_vb_pricing_config"],
    ):
        coll.delete_many({})
    analysis_prompts_module._cache.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(client):
    resp = client.post("/api/auth/login", json={"email": "admin@justdial.com", "password": "password"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}
