from datetime import datetime, timedelta, timezone

from bson import ObjectId

from backend import auth as auth_module
from backend import db as db_module
from backend.metrics import compute_metric

# _evaluate_rule is exercised directly below (see the incident-lifecycle tests) since
# incident open/resolve logic lives in the worker, not the router.
from alert_worker.worker import _claim_due_rules, _evaluate_rule


VALID_RULE_PAYLOAD = {
    "name": "High call volume",
    "metric": "call_count",
    "comparator": "gt",
    "threshold_value": 0,
    "window": "1h",
    "frequency": "5m",
}


def _other_user_headers(client, email="pm@justdial.com", role="user"):
    auth_module.create_user(email, "password", role=role)
    resp = client.post("/api/auth/login", json={"email": email, "password": "password"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}, email


def _insert_bot(owner: str) -> str:
    return str(db_module.bots.insert_one({"name": "Bot", "owner": owner}).inserted_id)


# --------------------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------------------


def test_create_list_update_delete_rule_roundtrip(client, auth_headers):
    create_resp = client.post("/api/alerts/rules", json=VALID_RULE_PAYLOAD, headers=auth_headers)
    assert create_resp.status_code == 200, create_resp.text
    rule = create_resp.json()
    assert rule["name"] == "High call volume"
    assert rule["metric"] == "call_count"

    listing = client.get("/api/alerts/rules", headers=auth_headers).json()
    assert len(listing) == 1
    assert listing[0]["_id"] == rule["_id"]

    update_resp = client.put(
        f"/api/alerts/rules/{rule['_id']}",
        json={**VALID_RULE_PAYLOAD, "name": "Updated name", "threshold_value": 5},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200, update_resp.text
    updated = update_resp.json()
    assert updated["name"] == "Updated name"
    assert updated["threshold_value"] == 5

    delete_resp = client.delete(f"/api/alerts/rules/{rule['_id']}", headers=auth_headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"ok": True}

    listing_after = client.get("/api/alerts/rules", headers=auth_headers).json()
    assert listing_after == []


def test_update_unknown_rule_returns_404(client, auth_headers):
    resp = client.put(
        "/api/alerts/rules/000000000000000000000000",
        json=VALID_RULE_PAYLOAD,
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_delete_unknown_rule_returns_404(client, auth_headers):
    resp = client.delete("/api/alerts/rules/000000000000000000000000", headers=auth_headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------


def test_create_rule_rejects_incompatible_window_frequency(client, auth_headers):
    resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "window": "5m", "frequency": "12h"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_rule_rejects_an_11th_rule_for_a_user_at_the_cap(client, auth_headers):
    for i in range(10):
        resp = client.post(
            "/api/alerts/rules",
            json={**VALID_RULE_PAYLOAD, "name": f"Rule {i}"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

    resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "name": "Rule 11"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_create_rule_rejects_bot_the_caller_does_not_own(client, auth_headers):
    other_headers, other_email = _other_user_headers(client, email="pm-validation@justdial.com")
    someone_elses_bot = _insert_bot(owner="not-pm-validation@justdial.com")

    resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "filters": {"bot_ids": [someone_elses_bot]}},
        headers=other_headers,
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------------------


def test_resolve_owned_bot_ids_restricts_non_admin_to_their_own_bots():
    owned_bot = _insert_bot(owner="scoped-pm@justdial.com")
    _insert_bot(owner="someone-else@justdial.com")

    owned = auth_module.resolve_owned_bot_ids("scoped-pm@justdial.com", "user")
    assert owned == [owned_bot]


def test_resolve_owned_bot_ids_is_unrestricted_for_admin():
    _insert_bot(owner="whoever@justdial.com")
    _insert_bot(owner="someone-else@justdial.com")

    assert auth_module.resolve_owned_bot_ids("admin@justdial.com", "admin") is None


def test_platform_wide_rule_for_non_admin_evaluates_only_their_own_bots(client, auth_headers):
    other_headers, other_email = _other_user_headers(client, email="pm-scope@justdial.com")
    own_bot = _insert_bot(owner=other_email)
    _insert_bot(owner="not-pm-scope@justdial.com")

    # Platform-wide (empty bot_ids filter) for this non-admin creator.
    resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "filters": {"bot_ids": []}},
        headers=other_headers,
    )
    assert resp.status_code == 200, resp.text
    rule_doc = db_module.alert_rules.find_one({"created_by": other_email})

    from alert_worker.worker import _resolve_rule_bot_ids

    resolved = _resolve_rule_bot_ids(rule_doc)
    assert resolved == [own_bot]


def test_platform_wide_rule_for_admin_is_unrestricted(client, auth_headers):
    _insert_bot(owner="admin@justdial.com")
    _insert_bot(owner="anyone@justdial.com")

    resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "filters": {"bot_ids": []}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rule_doc = db_module.alert_rules.find_one({"created_by": "admin@justdial.com"})

    from alert_worker.worker import _resolve_rule_bot_ids

    assert _resolve_rule_bot_ids(rule_doc) is None


# --------------------------------------------------------------------------------------
# compute_metric unit tests
# --------------------------------------------------------------------------------------


def _seed_transcript(**overrides):
    now = datetime.now(timezone.utc)
    doc = {
        "bot_id": "bot-1",
        "created_at": now,
        "status": "completed",
        "session_errors": [],
    }
    doc.update(overrides)
    db_module.transcripts.insert_one(doc)


def test_compute_metric_call_count():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    _seed_transcript()
    _seed_transcript()
    _seed_transcript(bot_id="bot-2")

    assert compute_metric("call_count", ["bot-1"], window_start, window_end) == 2.0
    assert compute_metric("call_count", None, window_start, window_end) == 3.0


def test_compute_metric_task_completion_rate_pct():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    _seed_transcript(status="completed")
    _seed_transcript(status="completed")
    _seed_transcript(status="disconnected")
    _seed_transcript(status="not_interested")

    # 3 of 4 calls are non-failure statuses (completed x2 + not_interested) -> 75.0%
    assert compute_metric("task_completion_rate_pct", ["bot-1"], window_start, window_end) == 75.0


def test_compute_metric_task_completion_rate_pct_with_no_calls_is_zero():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert compute_metric("task_completion_rate_pct", ["bot-1"], window_start, window_end) == 0.0


def test_compute_metric_call_outcome_filter_unreachable_for_workflow_bot_transcripts():
    # Locks in a known cross-feature gap: bot.py's workflow-bot gate means a Workflow
    # Builder bot's transcript can only ever have analysis.call_outcome of "Abusive Lead"
    # or "Could Not Confirm" (see callback_worker/analysis.py::status_to_outcome and
    # backend/tests/test_post_call_analysis.py's `!= "Approved"` assertion). Any of the
    # other AlertCallOutcome values (e.g. "Not Interested") can never match a workflow
    # bot's calls, so an alert rule filtered on one of those values silently never fires.
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    _seed_transcript(analysis={"call_outcome": "Could Not Confirm"})

    # Positive control: the value the workflow gate can actually produce does match.
    assert compute_metric(
        "task_completion_rate_pct", ["bot-1"], window_start, window_end, call_outcome="Could Not Confirm"
    ) == 100.0

    # One of the other 17 legacy AlertCallOutcome values is unreachable for this bot type.
    assert compute_metric(
        "task_completion_rate_pct", ["bot-1"], window_start, window_end, call_outcome="Not Interested"
    ) == 0.0


def test_compute_metric_session_error_count():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    _seed_transcript(session_errors=[{"type": "timeout", "message": "x", "recoverable": True, "timestamp": datetime.now(timezone.utc)}])
    _seed_transcript(session_errors=[{"type": "disconnect", "message": "y", "recoverable": False, "timestamp": datetime.now(timezone.utc)}])
    _seed_transcript(session_errors=[])

    assert compute_metric("session_error_count", ["bot-1"], window_start, window_end) == 2.0
    assert compute_metric("session_error_count", ["bot-1"], window_start, window_end, error_type="timeout") == 1.0


def test_compute_metric_platform_error_rate_pct():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    # dropped == status "disconnected" OR any session_errors entry present.
    _seed_transcript(status="completed", session_errors=[])  # not dropped
    _seed_transcript(status="disconnected", session_errors=[])  # dropped (status)
    _seed_transcript(status="not_interested", session_errors=[])  # not dropped
    _seed_transcript(
        status="completed",
        session_errors=[{"type": "timeout", "message": "x", "recoverable": True, "timestamp": datetime.now(timezone.utc)}],
    )  # dropped (has session error)
    _seed_transcript(status="in_progress", session_errors=[])  # not dropped

    # 2 of 5 calls dropped (disconnected + has-session-error) -> 40.0%
    assert compute_metric("platform_error_rate_pct", ["bot-1"], window_start, window_end) == 40.0


def test_compute_metric_platform_error_rate_pct_with_no_calls_is_zero():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert compute_metric("platform_error_rate_pct", ["bot-1"], window_start, window_end) == 0.0


def test_compute_metric_p95_turn_latency_ms():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    # Split 20 known latencies across two calls; combined+sorted this is exactly
    # [100, 200, 300, ..., 2000] (100 * i for i in 1..20).
    _seed_transcript(response_latencies_ms=[100, 300, 500, 700, 900, 1100, 1300, 1500, 1700, 1900])
    _seed_transcript(response_latencies_ms=[200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000])

    # _percentile on 20 sorted values uses idx = round(0.95 * 19) = round(18.05) = 18
    # (0-based) -> sorted_values[18] == 1900 (the 19th smallest of 100..2000 step 100).
    assert compute_metric("p95_turn_latency_ms", ["bot-1"], window_start, window_end) == 1900.0


def test_compute_metric_p95_turn_latency_ms_with_no_calls_is_zero():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert compute_metric("p95_turn_latency_ms", ["bot-1"], window_start, window_end) == 0.0


def test_compute_metric_concurrency_used_peak_overlap_and_adjacent_tie_break():
    # created_at is treated as end-of-call time; each call's interval is
    # [created_at - call_duration_sec, created_at]. Anchor everything to t0.
    t0 = datetime.now(timezone.utc)

    # call1: interval [t0-100s, t0]
    _seed_transcript(created_at=t0, call_duration_sec=100)
    # call2: interval [t0-50s, t0+50s] -> overlaps call1 during [t0-50s, t0], so both are
    # simultaneously active at that moment -> peak concurrency of 2.
    _seed_transcript(created_at=t0 + timedelta(seconds=50), call_duration_sec=100)
    # call3: interval [t0-150s, t0-100s] -> its end (t0-100s) is exactly call1's start
    # (t0-100s). The sweep sorts same-instant events with closes (-1) before opens (+1),
    # so this adjacent, non-overlapping pair must NOT bump the peak to 3.
    _seed_transcript(created_at=t0 - timedelta(seconds=100), call_duration_sec=50)

    window_start = t0 - timedelta(seconds=200)
    window_end = t0 + timedelta(seconds=100)

    assert compute_metric("concurrency_used", ["bot-1"], window_start, window_end) == 2.0


def test_compute_metric_concurrency_used_with_no_calls_is_zero():
    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    window_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert compute_metric("concurrency_used", ["bot-1"], window_start, window_end) == 0.0


# --------------------------------------------------------------------------------------
# Incident lifecycle
# --------------------------------------------------------------------------------------


def test_breach_opens_incident_and_recovery_resolves_it(client, auth_headers):
    create_resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "metric": "call_count", "comparator": "gt", "threshold_value": 0},
        headers=auth_headers,
    )
    rule_id = create_resp.json()["_id"]
    rule_doc = db_module.alert_rules.find_one({"created_by": "admin@justdial.com"})

    now = datetime.now(timezone.utc)

    # No calls yet -> call_count(0) > 0 is False, no incident should open.
    _evaluate_rule(rule_doc, now)
    assert db_module.alert_incidents.find_one({"rule_id": rule_id}) is None

    # Breach: a call lands in the window -> call_count(1) > 0 is True, incident opens.
    # created_at must fall strictly before `now` since compute_metric's window is [start, now).
    _seed_transcript(bot_id="any-bot", created_at=now - timedelta(seconds=1))
    _evaluate_rule(rule_doc, now)
    incident = db_module.alert_incidents.find_one({"rule_id": rule_id})
    assert incident is not None
    assert incident["status"] == "open"
    assert incident["current_value"] == 1.0

    # Still breached on the next tick -> same incident stays open, no duplicate row.
    _evaluate_rule(rule_doc, now)
    assert db_module.alert_incidents.count_documents({"rule_id": rule_id}) == 1
    assert db_module.alert_incidents.find_one({"rule_id": rule_id})["status"] == "open"

    # Recovery: raise the threshold above current volume -> incident resolves.
    db_module.alert_rules.update_one({"_id": rule_doc["_id"]}, {"$set": {"threshold_value": 100}})
    rule_doc = db_module.alert_rules.find_one({"_id": rule_doc["_id"]})
    _evaluate_rule(rule_doc, now)
    resolved = db_module.alert_incidents.find_one({"rule_id": rule_id})
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None
    assert db_module.alert_incidents.count_documents({"rule_id": rule_id}) == 1


def test_only_one_open_incident_per_rule_at_a_time(client, auth_headers):
    create_resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "metric": "call_count", "comparator": "gt", "threshold_value": 0},
        headers=auth_headers,
    )
    rule_id = create_resp.json()["_id"]
    rule_doc = db_module.alert_rules.find_one({"_id": db_module.alert_rules.find_one({"created_by": "admin@justdial.com"})["_id"]})

    now = datetime.now(timezone.utc)
    _seed_transcript(bot_id="any-bot", created_at=now - timedelta(seconds=1))

    _evaluate_rule(rule_doc, now)
    _evaluate_rule(rule_doc, now)
    _evaluate_rule(rule_doc, now)

    assert db_module.alert_incidents.count_documents({"rule_id": rule_id, "status": "open"}) == 1


def test_editing_a_rule_clears_its_active_incident(client, auth_headers):
    create_resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "metric": "call_count", "comparator": "gt", "threshold_value": 0},
        headers=auth_headers,
    )
    rule_id = create_resp.json()["_id"]
    rule_doc = db_module.alert_rules.find_one({"created_by": "admin@justdial.com"})

    now = datetime.now(timezone.utc)
    _seed_transcript(bot_id="any-bot", created_at=now - timedelta(seconds=1))
    _evaluate_rule(rule_doc, now)
    assert db_module.alert_incidents.find_one({"rule_id": rule_id})["status"] == "open"

    update_resp = client.put(
        f"/api/alerts/rules/{rule_id}",
        json={**VALID_RULE_PAYLOAD, "name": "Renamed"},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200, update_resp.text

    incident = db_module.alert_incidents.find_one({"rule_id": rule_id})
    assert incident["status"] == "resolved"
    assert incident["resolved_at"] is not None


# --------------------------------------------------------------------------------------
# Cross-user IDOR
# --------------------------------------------------------------------------------------


def test_user_b_cannot_get_user_as_rule_by_id(client):
    a_headers, _ = _other_user_headers(client, email="idor-a@justdial.com")
    b_headers, _ = _other_user_headers(client, email="idor-b@justdial.com")

    create_resp = client.post("/api/alerts/rules", json=VALID_RULE_PAYLOAD, headers=a_headers)
    assert create_resp.status_code == 200, create_resp.text
    rule_id = create_resp.json()["_id"]

    # There is no GET /rules/{id} endpoint — ownership is exercised via the list, which
    # must not surface user A's rule to user B.
    b_listing = client.get("/api/alerts/rules", headers=b_headers).json()
    assert b_listing == []

    a_listing = client.get("/api/alerts/rules", headers=a_headers).json()
    assert len(a_listing) == 1
    assert a_listing[0]["_id"] == rule_id


def test_user_b_cannot_update_user_as_rule(client):
    a_headers, _ = _other_user_headers(client, email="idor-upd-a@justdial.com")
    b_headers, _ = _other_user_headers(client, email="idor-upd-b@justdial.com")

    create_resp = client.post("/api/alerts/rules", json=VALID_RULE_PAYLOAD, headers=a_headers)
    rule_id = create_resp.json()["_id"]

    update_resp = client.put(
        f"/api/alerts/rules/{rule_id}",
        json={**VALID_RULE_PAYLOAD, "name": "Hijacked", "threshold_value": 999},
        headers=b_headers,
    )
    assert update_resp.status_code == 404

    # IDOR-regression lock: this is the exact scenario the PR review flagged — update_rule
    # gates on _get_owned_rule() first (which 404s user B here) and ONLY THEN does the
    # find_one_and_update. The write itself is also now scoped by _owner_filter(user), so
    # even if a future refactor dropped the pre-check, user B's write would still match zero
    # documents. Assert the DB row is byte-for-byte untouched by user B's attempt.
    rule_in_db = db_module.alert_rules.find_one({"_id": ObjectId(rule_id)})
    assert rule_in_db["name"] == VALID_RULE_PAYLOAD["name"]
    assert rule_in_db["threshold_value"] == VALID_RULE_PAYLOAD["threshold_value"]


def test_user_b_cannot_delete_user_as_rule(client):
    a_headers, _ = _other_user_headers(client, email="idor-del-a@justdial.com")
    b_headers, _ = _other_user_headers(client, email="idor-del-b@justdial.com")

    create_resp = client.post("/api/alerts/rules", json=VALID_RULE_PAYLOAD, headers=a_headers)
    rule_id = create_resp.json()["_id"]

    delete_resp = client.delete(f"/api/alerts/rules/{rule_id}", headers=b_headers)
    assert delete_resp.status_code == 404

    # Same IDOR-regression lock as the update test above, for delete_rule's delete_one().
    assert db_module.alert_rules.find_one({"_id": ObjectId(rule_id)}) is not None


def test_user_b_cannot_see_user_as_incidents(client):
    a_headers, a_email = _other_user_headers(client, email="idor-inc-a@justdial.com")
    b_headers, _ = _other_user_headers(client, email="idor-inc-b@justdial.com")

    own_bot = _insert_bot(owner=a_email)

    create_resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "metric": "call_count", "comparator": "gt", "threshold_value": 0},
        headers=a_headers,
    )
    rule_id = create_resp.json()["_id"]
    rule_doc = db_module.alert_rules.find_one({"created_by": a_email})

    now = datetime.now(timezone.utc)
    _seed_transcript(bot_id=own_bot, created_at=now - timedelta(seconds=1))
    _evaluate_rule(rule_doc, now)
    assert db_module.alert_incidents.find_one({"rule_id": rule_id}) is not None

    b_incidents = client.get("/api/alerts/incidents", headers=b_headers).json()
    assert b_incidents == []

    a_incidents = client.get("/api/alerts/incidents", headers=a_headers).json()
    assert len(a_incidents) == 1
    assert a_incidents[0]["rule_id"] == rule_id


def test_admin_can_get_update_and_delete_a_non_admins_rule(client, auth_headers):
    """auth_headers is admin@justdial.com per conftest — admin's _owner_filter is {} (see
    alerts.py), so admin must retain full CRUD over rules created by other users."""
    other_headers, other_email = _other_user_headers(client, email="idor-admin@justdial.com")

    create_resp = client.post("/api/alerts/rules", json=VALID_RULE_PAYLOAD, headers=other_headers)
    assert create_resp.status_code == 200, create_resp.text
    rule_id = create_resp.json()["_id"]

    admin_listing = client.get("/api/alerts/rules", headers=auth_headers).json()
    assert any(r["_id"] == rule_id for r in admin_listing)

    update_resp = client.put(
        f"/api/alerts/rules/{rule_id}",
        json={**VALID_RULE_PAYLOAD, "name": "Admin edited"},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["name"] == "Admin edited"

    delete_resp = client.delete(f"/api/alerts/rules/{rule_id}", headers=auth_headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"ok": True}
    assert db_module.alert_rules.find_one({"_id": ObjectId(rule_id)}) is None


# --------------------------------------------------------------------------------------
# _claim_due_rules — starvation regression (a rule with a malformed frequency/window
# must never block other due rules from ever being claimed, tick after tick)
# --------------------------------------------------------------------------------------


def test_claim_due_rules_claims_and_advances_a_valid_rule(client, auth_headers):
    create_resp = client.post("/api/alerts/rules", json=VALID_RULE_PAYLOAD, headers=auth_headers)
    assert create_resp.status_code == 200, create_resp.text
    rule_id = create_resp.json()["_id"]

    now = datetime.now(timezone.utc)
    # Force it due right now — the CRUD router stamps next_eval_at at create time, which
    # may be in the future relative to "now" for this test.
    db_module.alert_rules.update_one({"_id": ObjectId(rule_id)}, {"$set": {"next_eval_at": now}})

    claimed = _claim_due_rules(now)
    assert [str(r["_id"]) for r in claimed] == [rule_id]

    rule_after = db_module.alert_rules.find_one({"_id": ObjectId(rule_id)})
    # mongomock returns naive datetimes on read (even for tz-aware writes) and truncates to
    # millisecond precision; normalize tz and allow sub-millisecond slack before comparing.
    # frequency is "5m" per VALID_RULE_PAYLOAD -> next_eval_at advances by 300s.
    next_eval_at = rule_after["next_eval_at"].replace(tzinfo=timezone.utc)
    last_evaluated_at = rule_after["last_evaluated_at"].replace(tzinfo=timezone.utc)
    assert abs((next_eval_at - (now + timedelta(seconds=300))).total_seconds()) < 0.01
    assert abs((last_evaluated_at - now).total_seconds()) < 0.01
    assert rule_after["enabled"] is True

    # A second immediate call finds nothing due — it was correctly advanced, not
    # reclaimed forever.
    assert _claim_due_rules(now) == []


def test_claim_due_rules_malformed_rule_does_not_starve_a_valid_due_rule(client, auth_headers):
    """Regression lock for the starvation bug: previously, _duration_to_seconds was
    called BEFORE the atomic claim-and-advance, with no per-rule try/except, so a single
    rule with a malformed frequency/window raised out of _claim_due_rules entirely. Its
    next_eval_at never advanced, and since the due-rule query sorts by next_eval_at
    ascending, that same broken rule would be reclaimed first on every subsequent tick
    forever — starving every other rule in the system. This test inserts a malformed rule
    document directly (bypassing Pydantic, simulating a bad document already in Mongo)
    alongside a valid due rule, and asserts the valid rule still gets claimed."""
    now = datetime.now(timezone.utc)

    bad_rule_id = db_module.alert_rules.insert_one({
        "name": "Malformed rule",
        "metric": "call_count",
        "threshold_type": "absolute",
        "comparator": "gt",
        "threshold_value": 0,
        "window": "1h",
        "frequency": "not-a-duration",  # malformed: _duration_to_seconds can't parse this
        "filters": {"bot_ids": [], "status": None, "call_outcome": None},
        "notify_via": "in_app",
        "enabled": True,
        "created_by": "admin@justdial.com",
        "next_eval_at": now - timedelta(seconds=1),  # already due, sorts first
        "last_evaluated_at": None,
        "created_at": now,
    }).inserted_id

    create_resp = client.post("/api/alerts/rules", json=VALID_RULE_PAYLOAD, headers=auth_headers)
    assert create_resp.status_code == 200, create_resp.text
    good_rule_id = create_resp.json()["_id"]
    db_module.alert_rules.update_one({"_id": ObjectId(good_rule_id)}, {"$set": {"next_eval_at": now}})

    # Must not raise, and must still claim the valid rule despite the malformed one
    # sorting first in the due-rule query.
    claimed = _claim_due_rules(now)
    assert [str(r["_id"]) for r in claimed] == [good_rule_id]

    # The malformed rule is disabled (so it stops being picked first every tick) and its
    # next_eval_at was advanced rather than left stuck at the front of the queue forever.
    bad_rule_after = db_module.alert_rules.find_one({"_id": bad_rule_id})
    assert bad_rule_after["enabled"] is False
    # mongomock returns naive datetimes on read even for tz-aware writes; normalize first.
    assert bad_rule_after["next_eval_at"].replace(tzinfo=timezone.utc) > now

    # A subsequent tick claims nothing further — the malformed rule is gone from the
    # due-rule query (disabled) and the valid rule was already advanced into the future.
    assert _claim_due_rules(now) == []


# --------------------------------------------------------------------------------------
# rule_name stored on the incident document at creation time (not just joined at read
# time) — see AlertIncident.rule_name in backend/models.py, which declares it as a real
# field every AlertIncident is supposed to have.
# --------------------------------------------------------------------------------------


def test_incident_created_by_worker_has_rule_name_stored_on_the_document(client, auth_headers):
    create_resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "name": "Stored rule name", "metric": "call_count", "comparator": "gt", "threshold_value": 0},
        headers=auth_headers,
    )
    rule_id = create_resp.json()["_id"]
    rule_doc = db_module.alert_rules.find_one({"created_by": "admin@justdial.com"})

    now = datetime.now(timezone.utc)
    _seed_transcript(bot_id="any-bot", created_at=now - timedelta(seconds=1))
    _evaluate_rule(rule_doc, now)

    # Read the raw Mongo document directly — bypassing list_incidents' in-Python join —
    # to prove rule_name is a real stored field, not only present in the API response.
    incident = db_module.alert_incidents.find_one({"rule_id": rule_id})
    assert incident["rule_name"] == "Stored rule name"

    # The API response still carries it too (via the stored field now, with the join as
    # a defensive fallback for legacy incidents).
    api_incidents = client.get("/api/alerts/incidents", headers=auth_headers).json()
    assert any(i["rule_id"] == rule_id and i["rule_name"] == "Stored rule name" for i in api_incidents)


# --------------------------------------------------------------------------------------
# _evaluate_rule — read-side schema validation. Write-side validation (AlertRuleCreate/
# Update, including the window/frequency compat model_validator) only guarantees a
# document was valid AT WRITE TIME. A document written by an older code version, hand-
# edited, or corrupted, would previously be treated as valid by the worker's raw-dict
# field access (rule["metric"], etc.) and could raise a KeyError/ValueError deep inside
# evaluation instead of failing cleanly. This locks in that _evaluate_rule now parses
# every claimed rule through AlertRule.model_validate first and skips (without raising)
# on failure — same "one bad rule doesn't take down the tick" principle as the malformed-
# frequency starvation fix in _claim_due_rules above, just at the evaluation step instead
# of the claim step.
# --------------------------------------------------------------------------------------


def test_evaluate_rule_with_invalid_schema_skips_without_raising_and_does_not_block_others(client, auth_headers):
    now = datetime.now(timezone.utc)

    # Insert a corrupted/legacy rule document directly (bypassing Pydantic entirely) with
    # a metric value that isn't a valid AlertMetric per the current model — simulating a
    # document that predates a metric being renamed/removed, or was hand-edited in Mongo.
    bad_rule_id = db_module.alert_rules.insert_one({
        "name": "Corrupted rule",
        "metric": "not_a_real_metric",  # invalid per AlertMetric Literal
        "threshold_type": "absolute",
        "comparator": "gt",
        "threshold_value": 0,
        "window": "1h",
        "frequency": "5m",
        "filters": {"bot_ids": [], "status": None, "call_outcome": None},
        "notify_via": "in_app",
        "enabled": True,
        "created_by": "admin@justdial.com",
        "next_eval_at": now,
        "last_evaluated_at": None,
        "created_at": now,
    }).inserted_id
    bad_rule_doc = db_module.alert_rules.find_one({"_id": bad_rule_id})

    # A second, valid, due rule in the same tick — must still evaluate normally.
    create_resp = client.post(
        "/api/alerts/rules",
        json={**VALID_RULE_PAYLOAD, "metric": "call_count", "comparator": "gt", "threshold_value": 0},
        headers=auth_headers,
    )
    good_rule_id = create_resp.json()["_id"]
    good_rule_doc = db_module.alert_rules.find_one({"_id": ObjectId(good_rule_id)})
    _seed_transcript(bot_id="any-bot", created_at=now - timedelta(seconds=1))

    # Evaluating the corrupted rule must not raise.
    _evaluate_rule(bad_rule_doc, now)
    assert db_module.alert_incidents.find_one({"rule_id": str(bad_rule_id)}) is None

    # The valid rule, evaluated in the same tick, still opens its incident normally —
    # the corrupted rule's failure doesn't degrade or block evaluation of a sibling rule.
    _evaluate_rule(good_rule_doc, now)
    incident = db_module.alert_incidents.find_one({"rule_id": good_rule_id})
    assert incident is not None
    assert incident["status"] == "open"
