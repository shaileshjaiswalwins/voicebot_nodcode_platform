def test_phrase_list_empty_by_default(client, auth_headers):
    resp = client.get("/api/library/phrases", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_and_update_phrase_roundtrip(client, auth_headers):
    create_resp = client.post(
        "/api/library/phrases",
        json={"category": "voicemail", "text": "Please call back later", "language": "hi"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 200
    phrase = create_resp.json()
    assert phrase["text"] == "Please call back later"

    update_resp = client.put(
        f"/api/library/phrases/{phrase['_id']}",
        json={"text": "Updated text"},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["text"] == "Updated text"


def test_update_unknown_phrase_returns_404(client, auth_headers):
    resp = client.put(
        "/api/library/phrases/000000000000000000000000",
        json={"text": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_delete_unknown_phrase_returns_404(client, auth_headers):
    resp = client.delete("/api/library/phrases/000000000000000000000000", headers=auth_headers)
    assert resp.status_code == 404


def test_create_phrase_rejects_invalid_category(client, auth_headers):
    resp = client.post(
        "/api/library/phrases",
        json={"category": "bogus", "text": "x"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_outcome_upsert_is_idempotent_by_key(client, auth_headers):
    first = client.put(
        "/api/library/outcomes/interested",
        json={"display_label": "Interested", "description": "Buyer wants a callback"},
        headers=auth_headers,
    )
    assert first.status_code == 200
    second = client.put(
        "/api/library/outcomes/interested",
        json={"display_label": "Interested (updated)", "description": ""},
        headers=auth_headers,
    )
    assert second.status_code == 200

    listing = client.get("/api/library/outcomes", headers=auth_headers).json()
    assert len(listing) == 1
    assert listing[0]["display_label"] == "Interested (updated)"


def test_language_settings_empty_by_default(client, auth_headers):
    resp = client.get("/api/library/languages", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_language_setting(client, auth_headers):
    client.put(
        "/api/library/languages",
        json={"id": "hi", "name": "Hindi", "timeout_message": "x", "inactivity_nudge": "y", "lang_notes": ""},
        headers=auth_headers,
    )
    resp = client.delete("/api/library/languages/hi", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    listing = client.get("/api/library/languages", headers=auth_headers).json()
    assert not any(l["id"] == "hi" for l in listing)


def test_delete_unknown_language_setting_returns_404(client, auth_headers):
    resp = client.delete("/api/library/languages/nonexistent", headers=auth_headers)
    assert resp.status_code == 404
