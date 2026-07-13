"""Answers the "did you test concurrent draft edits or double-publish races" question:
publish_draft() previously did read-bot -> check draft_version_id -> write with no atomic
compare-and-swap, so two concurrent publishes could both read the same draft_version_id
before either cleared it and both would "succeed". Fixed via an atomic
find_one_and_update filtered on the current draft_version_id (see routers/bots.py) so only
one concurrent caller's claim can match. These tests exercise that fix directly rather
than just documenting the gap."""

import threading

from fastapi.testclient import TestClient

from backend.main import app


def _create_bot(client, auth_headers):
    resp = client.post("/api/bots", json={"name": "Race Bot", "config": {}}, headers=auth_headers)
    return resp.json()


def test_double_publish_race_exactly_one_winner(client, auth_headers):
    """Fires several publishes concurrently against the same draft. Exactly one must
    succeed (200); every other thread must cleanly lose, never double-publish.

    A losing thread can surface as either:
    - 409, if its own read still saw the draft set and it lost the atomic
      find_one_and_update claim to a faster thread, or
    - 400 "No draft to publish", if its own initial read already observed the draft as
      cleared by a thread that had already won by that point.
    Both are legitimate "someone beat you to it" outcomes — the atomicity guarantee only
    promises a single winner, not a uniform loser status code."""
    bot = _create_bot(client, auth_headers)
    results = []
    lock = threading.Lock()

    def publish():
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)
            with lock:
                results.append(resp.status_code)

    threads = [threading.Thread(target=publish) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(200) == 1, f"expected exactly one winner, got: {results}"
    assert all(code in (200, 400, 409) for code in results), f"unexpected status codes: {results}"


def test_publish_after_race_leaves_bot_in_consistent_state(client, auth_headers):
    bot = _create_bot(client, auth_headers)

    def publish():
        with TestClient(app, raise_server_exceptions=False) as c:
            c.post(f"/api/bots/{bot['_id']}/publish", headers=auth_headers)

    threads = [threading.Thread(target=publish) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    assert detail["bot"]["draft_version_id"] is None
    assert detail["bot"]["active_version_id"] is not None
    published = [v for v in detail["versions"] if v["state"] == "published"]
    assert len(published) == 1


def test_concurrent_draft_saves_do_not_corrupt_version_numbering(client, auth_headers):
    """save_draft() forks a new version by reading the max version and writing max+1.
    Two concurrent saves should not produce a duplicate version number or a partially
    written document."""
    bot = _create_bot(client, auth_headers)
    errors = []

    def save(i):
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.put(
                f"/api/bots/{bot['_id']}/draft",
                json={"config": {"organization_name": f"Org {i}"}},
                headers=auth_headers,
            )
            if resp.status_code != 200:
                errors.append(resp.status_code)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected non-200 responses during concurrent saves: {errors}"

    detail = client.get(f"/api/bots/{bot['_id']}", headers=auth_headers).json()
    versions = [v["version"] for v in detail["versions"]]
    assert len(versions) == len(set(versions)), f"duplicate version numbers under concurrency: {versions}"
