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
    "gemini_3_1_pro": {"latency_ms_min": 1150, "latency_ms_max": 1800, "tokens_min": 650, "tokens_max": 1150},
    "gpt_4o_mini": {"latency_ms_min": 900, "latency_ms_max": 1350, "tokens_min": 550, "tokens_max": 950},
    "gpt_4_1": {"latency_ms_min": 950, "latency_ms_max": 1450, "tokens_min": 580, "tokens_max": 980},
    "gpt_4o": {"latency_ms_min": 1100, "latency_ms_max": 1700, "tokens_min": 650, "tokens_max": 1100},
    "claude_3_5_sonnet": {"latency_ms_min": 1100, "latency_ms_max": 1700, "tokens_min": 650, "tokens_max": 1100},
}


# Labels the generic key.replace("_", "-") reconstruction gets wrong (version-number dots,
# or a provider-prefix that shouldn't be part of the model id) — these entries are the exact
# strings passed straight through to pipeline_providers.py / the LLM API, so they must match
# the real provider model name, not just look similar.
_LABEL_OVERRIDES = {
    "gpt_4_1": "gpt-4.1",
    "gemini_2_5_flash": "gemini-2.5-flash",
    "gemini_3_1_flash_lite": "gemini-3.1-flash-lite",
    "gemini_3_1_pro": "gemini-3.1-pro",
    "deepgram_nova2": "nova-2",
    "deepgram_flux": "flux",
}


def _default_entries(rates: dict[str, float], with_latency_tokens: bool = False) -> list[PricingModelEntry]:
    entries = []
    for key, cost in rates.items():
        extra = _DEFAULT_LLM_LATENCY_TOKENS.get(key, {}) if with_latency_tokens else {}
        company = pricing.MODEL_COMPANY.get(key, "")
        label = _LABEL_OVERRIDES.get(key, key.replace("_", "-"))
        entries.append(PricingModelEntry(key=key, label=label, cost_inr_per_min=cost, company=company, **extra))
    return entries


def _default_config() -> PricingConfig:
    return PricingConfig(
        stt=_default_entries(pricing.STT_RATES),
        llm=_default_entries(pricing.LLM_RATES, with_latency_tokens=True),
        tts=_default_entries(pricing.TTS_RATES),
        telephony=_default_entries(pricing.TELEPHONY_RATES),
    )


def _backfill_new_catalog_entries(config: PricingConfig) -> PricingConfig:
    """A saved doc is returned as-is, so a rate key added to pricing.py *after* an admin last
    saved (e.g. today's IndicF5 "indic_f5") would silently never appear until someone manually
    resets the whole config — appends any default entries missing from the saved lists,
    without touching costs an admin already edited for existing keys."""
    def _merge(saved: list[PricingModelEntry], defaults: list[PricingModelEntry]) -> list[PricingModelEntry]:
        saved_keys = {e.key for e in saved}
        return saved + [d for d in defaults if d.key not in saved_keys]

    defaults = _default_config()
    return PricingConfig(
        stt=_merge(config.stt, defaults.stt),
        llm=_merge(config.llm, defaults.llm),
        tts=_merge(config.tts, defaults.tts),
        telephony=_merge(config.telephony, defaults.telephony),
    )


def get_config() -> PricingConfig:
    doc = pricing_config.find_one({"_id": _DOC_ID})
    if not doc:
        return _default_config()
    doc.pop("_id", None)
    return _backfill_new_catalog_entries(PricingConfig(**doc))


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
