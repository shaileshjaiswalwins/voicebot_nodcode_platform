"""campaign_dialer_worker._promote_scheduled_campaigns — the worker-side half of the
Send Now vs Schedule flow: a campaign parked in "scheduled" status becomes "active" once
its scheduled_at arrives, with no separate cron process (this worker's own poll loop is
the clock). No real TSPL push involved — this only exercises the Mongo status flip."""

import asyncio
from datetime import datetime, timedelta, timezone

from backend.db import campaigns
from campaign_dialer_worker.worker import _promote_scheduled_campaigns


def test_promotes_scheduled_campaign_whose_time_has_passed():
    campaigns.insert_one(
        {
            "campaign_key": "due_camp",
            "status": "scheduled",
            "scheduled_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        }
    )
    loop = asyncio.new_event_loop()
    try:
        promoted = loop.run_until_complete(_promote_scheduled_campaigns(loop))
    finally:
        loop.close()

    assert promoted == 1
    assert campaigns.find_one({"campaign_key": "due_camp"})["status"] == "active"


def test_does_not_promote_a_campaign_scheduled_in_the_future():
    campaigns.insert_one(
        {
            "campaign_key": "future_camp",
            "status": "scheduled",
            "scheduled_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }
    )
    loop = asyncio.new_event_loop()
    try:
        promoted = loop.run_until_complete(_promote_scheduled_campaigns(loop))
    finally:
        loop.close()

    assert promoted == 0
    assert campaigns.find_one({"campaign_key": "future_camp"})["status"] == "scheduled"


def test_leaves_active_and_paused_campaigns_untouched():
    campaigns.insert_one({"campaign_key": "already_active", "status": "active"})
    campaigns.insert_one({"campaign_key": "paused_camp", "status": "paused"})
    loop = asyncio.new_event_loop()
    try:
        promoted = loop.run_until_complete(_promote_scheduled_campaigns(loop))
    finally:
        loop.close()

    assert promoted == 0
    assert campaigns.find_one({"campaign_key": "already_active"})["status"] == "active"
    assert campaigns.find_one({"campaign_key": "paused_camp"})["status"] == "paused"
