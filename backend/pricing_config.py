"""Admin-configurable pricing (INR) — Mongo-backed, singleton-doc pattern identical to
backend/routers/settings.py's PlatformSettings. This is the only file that touches Mongo
for pricing; backend/pricing.py's math stays pure and reads whatever rates are passed in.
"""

from .db import db
from .models import PricingConfig, PricingModelEntry
from . import pricing

pricing_config = db["tbl_ai_vb_pricing_config"]

_DOC_ID = "pricing_config"

# Default latency/token estimates shown on a bot's "Agent details" card, keyed by the same
# LLM keys as pricing.py's LLM_RATES. Only a rough shape (matches the screenshot's example
# numbers) — admins should tune these once real call data is available.
_DEFAULT_LLM_LATENCY_TOKENS = {
    "gemini_2_5_flash": {"latency_ms_min": 850, "latency_ms_max": 1300, "tokens_min": 500, "tokens_max": 900},
    "gemini_3_1_flash_lite": {"latency_ms_min": 970, "latency_ms_max": 1450, "tokens_min": 597, "tokens_max": 1000},
    "gpt_4o_mini": {"latency_ms_min": 900, "latency_ms_max": 1350, "tokens_min": 550, "tokens_max": 950},
    "gpt_4o": {"latency_ms_min": 1100, "latency_ms_max": 1700, "tokens_min": 650, "tokens_max": 1100},
    "claude_3_5_sonnet": {"latency_ms_min": 1100, "latency_ms_max": 1700, "tokens_min": 650, "tokens_max": 1100},
}


def _default_entries(rates: dict[str, float], with_latency_tokens: bool = False) -> list[PricingModelEntry]:
    entries = []
    for key, cost in rates.items():
        extra = _DEFAULT_LLM_LATENCY_TOKENS.get(key, {}) if with_latency_tokens else {}
        entries.append(PricingModelEntry(key=key, label=key.replace("_", "-"), cost_inr_per_min=cost, **extra))
    return entries


def _default_config() -> PricingConfig:
    return PricingConfig(
        stt=_default_entries(pricing.STT_RATES),
        llm=_default_entries(pricing.LLM_RATES, with_latency_tokens=True),
        tts=_default_entries(pricing.TTS_RATES),
        telephony=_default_entries(pricing.TELEPHONY_RATES),
    )


def get_config() -> PricingConfig:
    doc = pricing_config.find_one({"_id": _DOC_ID})
    if not doc:
        return _default_config()
    doc.pop("_id", None)
    return PricingConfig(**doc)


def update_config(payload: PricingConfig) -> PricingConfig:
    pricing_config.update_one({"_id": _DOC_ID}, {"$set": payload.model_dump()}, upsert=True)
    return payload


def get_effective_rates() -> dict[str, dict[str, float]]:
    """Flattens the current config back into the {stt:{key:cost}, llm:{...}, ...} shape
    backend/pricing.py's functions expect, for /matrix, /tiers, /budget-route."""
    config = get_config()
    return {
        "stt": {e.key: e.cost_inr_per_min for e in config.stt},
        "llm": {e.key: e.cost_inr_per_min for e in config.llm},
        "tts": {e.key: e.cost_inr_per_min for e in config.tts},
        "telephony": {e.key: e.cost_inr_per_min for e in config.telephony},
    }
