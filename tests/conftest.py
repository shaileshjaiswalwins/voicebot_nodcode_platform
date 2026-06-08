"""Shared test fixtures.

Patches voicebot_platform.mongo.get_client to a mongomock client so the full
platform module tree can be imported and exercised without a live Mongo. This
covers config_store, phrase_library, outcome_catalog, language_settings, and
the FastAPI API routes.
"""

from __future__ import annotations

import mongomock
import pytest


_MEMORY_CLIENT: mongomock.MongoClient | None = None


@pytest.fixture(autouse=True)
def patch_mongo(monkeypatch):
    global _MEMORY_CLIENT
    _MEMORY_CLIENT = mongomock.MongoClient()

    # Patch every module that imports get_client at top level.
    import voicebot_platform.mongo as mongo_module

    def _fake_get_client():
        return _MEMORY_CLIENT

    monkeypatch.setattr(mongo_module, "get_client", _fake_get_client)

    # Re-import anything that grabbed get_db at import time would need patching too,
    # but get_db just calls get_client() lazily so we're safe.
    yield

    _MEMORY_CLIENT = None
