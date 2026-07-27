"""Tests for dialer_client.py — the push side of TSPL's outbound-dialer integration.
No real network calls: push_lead_to_dialer is exercised against a fake aiohttp session."""

import asyncio
import json

from dialer_client import STATIC_CAMPAIGN_TYPE, STATIC_TRIGGER_REASON, build_dialer_payload, push_lead_to_dialer


def _lead(jduid="9092012171200001572", **vars_overrides):
    vars_ = {
        "buyer_city": "kolkata",
        "buyer_area": "999999",
        "searched_keyword": "biscuit Dealer",
        "ncatid": "10551087",
        **vars_overrides,
    }
    return {"_id": "lead1", "campaign_id": "camp1", "jduid": jduid, "vars": vars_}


def _campaign(**dialer_config_overrides):
    dialer_config = {
        "channel_name": "DVN Missed Call",
        "channel_id": 43,
        "bd": 0,
        "service_id": "302",
        "service_source": "lq_staging",
        **dialer_config_overrides,
    }
    return {"campaign_key": "camp1", "dialer_config": dialer_config}


def test_static_fields_are_always_present():
    payload = build_dialer_payload(_lead(), _campaign(), job_id="job123")
    assert payload["campaign_type"] == STATIC_CAMPAIGN_TYPE == "TARGET_AI_CALL"
    assert payload["trigger_reason"] == STATIC_TRIGGER_REASON == "15_MIN_INACTIVITY"


def test_per_lead_fields_mapped_from_jduid_and_vars():
    payload = build_dialer_payload(_lead(), _campaign(), job_id="job123")
    assert payload["jduid"] == "9092012171200001572"
    assert payload["buyer_details"]["jduid"] == "9092012171200001572"
    assert payload["buyer_details"]["buyer_city"] == "kolkata"
    assert payload["buyer_details"]["buyer_area"] == "999999"
    assert payload["buyer_city"] == "kolkata"
    assert payload["search_context"]["searched_keyword"] == "biscuit Dealer"
    assert payload["ncatid"] == 10551087  # coerced to int, matching TSPL's sample shape


def test_ncatid_falls_back_to_raw_string_when_not_numeric():
    payload = build_dialer_payload(_lead(ncatid="not-a-number"), _campaign(), job_id="job123")
    assert payload["ncatid"] == "not-a-number"


def test_per_campaign_dialer_config_fields_mapped():
    payload = build_dialer_payload(_lead(), _campaign(), job_id="job123")
    assert payload["channel_name"] == "DVN Missed Call"
    assert payload["channel_id"] == 43
    assert payload["bd"] == 0
    assert payload["service_id"] == "300"
    assert payload["service_source"] == "lq_staging"


def test_ref_obj_id_carries_our_own_job_id_for_correlation():
    payload = build_dialer_payload(_lead(), _campaign(), job_id="job123")
    assert payload["ref_obj"] == {"_id": "job123"}


def test_missing_dialer_config_defaults_gracefully():
    campaign_without_config = {"campaign_key": "camp1"}
    payload = build_dialer_payload(_lead(), campaign_without_config, job_id="job123")
    assert payload["channel_name"] == ""
    assert payload["service_id"] == ""
    assert payload["meta"]["country"] == "IN"
    assert payload["meta"]["language"] == "en"
    assert payload["meta"]["page_type"] == "gallery_image"


class _FakeResponse:
    def __init__(self, status: int, body: dict | str):
        self.status = status
        self._body = body if isinstance(body, str) else json.dumps(body)

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse | None = None, raise_error: Exception | None = None):
        self._response = response
        self._raise_error = raise_error
        self.last_call = None

    def post(self, url, json=None, timeout=None):
        self.last_call = {"url": url, "json": json}
        if self._raise_error:
            raise self._raise_error
        return self._response


def test_push_success_returns_true_and_parsed_body():
    session = _FakeSession(_FakeResponse(200, {"status": "queued"}))
    ok, body = asyncio.run(push_lead_to_dialer({"jduid": "x"}, session, "http://fake/push"))
    assert ok is True
    assert body == {"status": "queued"}
    assert session.last_call == {"url": "http://fake/push", "json": {"jduid": "x"}}


def test_push_non_2xx_returns_false_with_status_and_body():
    session = _FakeSession(_FakeResponse(500, "internal error"))
    ok, body = asyncio.run(push_lead_to_dialer({"jduid": "x"}, session, "http://fake/push"))
    assert ok is False
    assert body["status_code"] == 500


def test_push_network_error_returns_false_without_raising():
    session = _FakeSession(raise_error=ConnectionError("boom"))
    ok, body = asyncio.run(push_lead_to_dialer({"jduid": "x"}, session, "http://fake/push"))
    assert ok is False
    assert "error" in body
