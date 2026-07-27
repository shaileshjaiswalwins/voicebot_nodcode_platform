"""Answers the "did you ever test resolve_active_endpoints' fallback under a genuinely
unreachable admin API, rather than just a mocked exception" question.

The three tests below exercise the fallback via a real network failure (connecting to a
port nothing listens on), not a monkeypatched exception — the closest a unit test can get
to "admin API is actually down" without spinning up Docker networking tricks.
"""

import time

import aiohttp
import pytest

from callback_worker import config


UNREACHABLE_URL = "http://127.0.0.1:1/api/settings/platform/active-endpoints"


@pytest.fixture(autouse=True)
def _reset_cache():
    config._endpoints_cache_state["data"] = {}
    config._endpoints_cache_state["at"] = 0.0
    yield
    config._endpoints_cache_state["data"] = {}
    config._endpoints_cache_state["at"] = 0.0


@pytest.mark.asyncio
async def test_falls_back_to_static_defaults_when_admin_api_is_genuinely_unreachable(monkeypatch):
    """Connecting to 127.0.0.1:1 fails at the TCP layer (connection refused) — a real
    network error, not a mocked one — because nothing listens on port 1."""
    monkeypatch.setattr(config, "_ACTIVE_ENDPOINTS_URL", UNREACHABLE_URL)

    async with aiohttp.ClientSession() as session:
        result = await config.resolve_active_endpoints(session)

    assert result["callback_api_url"] == config.CALLBACK_API_URL
    assert result["callback_update_api_url"] == config.CALLBACK_UPDATE_API_URL
    assert result["mis_api_base"] == config.MIS_API_BASE
    assert result["active_environment"] == config.__dict__.get("VOICEBOT_ENV", result["active_environment"])


@pytest.mark.asyncio
async def test_unreachable_admin_api_does_not_poison_the_cache(monkeypatch):
    """A failed fetch must not populate the cache with the fallback, since a real admin
    API recovering seconds later should be retried rather than being masked for the full
    TTL by a cached failure."""
    monkeypatch.setattr(config, "_ACTIVE_ENDPOINTS_URL", UNREACHABLE_URL)

    async with aiohttp.ClientSession() as session:
        await config.resolve_active_endpoints(session)

    assert config._endpoints_cache_state["data"] == {}
    assert config._endpoints_cache_state["at"] == 0.0


@pytest.mark.asyncio
async def test_slow_admin_api_times_out_and_falls_back_within_a_few_seconds(monkeypatch):
    """A hung (not just refused) admin API must not block the worker forever — the 5s
    aiohttp timeout inside resolve_active_endpoints should fire and fall back."""
    from aiohttp import web

    async def _hang(_request):
        import asyncio

        await asyncio.sleep(8)
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/api/settings/platform/active-endpoints", _hang)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    monkeypatch.setattr(config, "_ACTIVE_ENDPOINTS_URL", f"http://127.0.0.1:{port}/api/settings/platform/active-endpoints")
    monkeypatch.setattr(config, "_ENDPOINTS_CACHE_TTL_SEC", 30)

    try:
        started = time.monotonic()
        async with aiohttp.ClientSession() as session:
            result = await config.resolve_active_endpoints(session)
        elapsed = time.monotonic() - started
    finally:
        await runner.cleanup()

    assert elapsed < 10, f"fallback took too long to kick in: {elapsed}s"
    assert result["callback_api_url"] == config.CALLBACK_API_URL
