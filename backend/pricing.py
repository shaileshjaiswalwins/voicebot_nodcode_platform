"""Pure INR (₹) cost calculation and budget-based stack routing. No I/O, no Mongo — same
pattern as custom_functions.py/provider_params.py so it's unit-testable as plain arithmetic
and can't drift between callers (API endpoint, campaign dial-time cost estimate, etc.)."""

from dataclasses import dataclass

# Rates in ₹/minute. Sarvam entries match the hardcoded defaults in provider_params.py —
# any bot with no provider_options configured runs on these exact numbers today.
#
# google_chirp/google_wavenet/google_neural2/google_chirp3_hd/google_studio are catalog-only
# (Admin pricing page + cost estimates) — pipeline_providers.py does not build a Google
# STT/TTS client today, so these are NOT selectable as a live provider anywhere a real call
# gets placed. Converted from Google Cloud's published per-minute (STT, no conversion needed)
# / per-million-character (TTS, assuming ~900 chars/min of spoken audio) rates at ~₹90/$1.
STT_RATES = {
    "sarvam_saras_v3": 0.25,
    "deepgram_nova2": 0.41,
    "whisper_large": 0.96,
    "deepgram_flux": 1.15,
    "google_chirp": 1.44,
}
LLM_RATES = {
    "gemini_2_5_flash": 0.14,
    "gemini_3_1_flash_lite": 0.87,
    "gemini_3_1_pro": 3.60,
    "gpt_4o_mini": 0.29,
    "gpt_4_1": 0.45,
    "gpt_4o": 3.85,
    "claude_3_5_sonnet": 3.85,
}
TTS_RATES = {
    "sarvam_bulbul_v3": 1.60,
    "deepgram_aura": 1.44,
    "cartesia_sonic": 2.40,
    "elevenlabs_turbo": 5.76,
    "google_wavenet": 0.32,
    "google_neural2": 1.30,
    "google_chirp3_hd": 2.43,
    "google_studio": 12.96,
    # Self-hosted on our own infra (INDIC_TTS_WS_URL) — no per-call vendor invoice like the
    # others above, so this is a rough compute-amortization estimate, not a metered rate.
    # Adjust from the Admin pricing page once real infra-cost numbers are in.
    "indic_f5": 0.20,
}
TELEPHONY_RATES = {
    "inhouse_dialer": 0.0,
    "sip_direct": 0.0,
    "plivo": 0.55,
}

# Which company/vendor each catalog key belongs to — purely a labeling/grouping aid for the
# Admin pricing page and the Agent Builder's model pickers, not consulted by the cost math
# above. Keys not listed here render as "Other" in the UI (e.g. a PM-added custom entry).
MODEL_COMPANY: dict[str, str] = {
    "sarvam_saras_v3": "Sarvam",
    "deepgram_nova2": "Deepgram",
    "whisper_large": "OpenAI",
    "deepgram_flux": "Deepgram",
    "google_chirp": "Google",
    "gemini_2_5_flash": "Google",
    "gemini_3_1_flash_lite": "Google",
    "gemini_3_1_pro": "Google",
    "gpt_4o_mini": "OpenAI",
    "gpt_4_1": "OpenAI",
    "gpt_4o": "OpenAI",
    "claude_3_5_sonnet": "Anthropic",
    "sarvam_bulbul_v3": "Sarvam",
    "deepgram_aura": "Deepgram",
    "cartesia_sonic": "Cartesia",
    "elevenlabs_turbo": "ElevenLabs",
    "google_wavenet": "Google",
    "google_neural2": "Google",
    "google_chirp3_hd": "Google",
    "google_studio": "Google",
    "indic_f5": "Justdial",
    "inhouse_dialer": "Platform",
    "sip_direct": "Platform",
    "plivo": "Plivo",
}


@dataclass(frozen=True)
class ProviderStack:
    stt: str
    llm: str
    tts: str
    telephony: str


def estimate_cost_per_min(
    stack: ProviderStack,
    stt_rates: dict[str, float] = STT_RATES,
    llm_rates: dict[str, float] = LLM_RATES,
    tts_rates: dict[str, float] = TTS_RATES,
    telephony_rates: dict[str, float] = TELEPHONY_RATES,
) -> float:
    """Sum of the four per-minute rates. Raises KeyError for an unknown provider key —
    callers should validate against the rate tables before calling, same contract as
    provider_params.py's ParamSpec coercion.

    Rate dicts default to this module's hardcoded tables (so existing callers/tests are
    unaffected) but callers can pass admin-configured rates instead — see
    backend/pricing_config.py, the only place those come from Mongo."""
    return round(
        stt_rates[stack.stt] + llm_rates[stack.llm] + tts_rates[stack.tts] + telephony_rates[stack.telephony],
        2,
    )


def pricing_matrix() -> dict:
    """Full rate table for the frontend to render dropdowns/cost widgets without
    hardcoding numbers a second time in TypeScript."""
    return {"stt": STT_RATES, "llm": LLM_RATES, "tts": TTS_RATES, "telephony": TELEPHONY_RATES}


# Named tiers, cheapest first — the same three tiers from the PRD's cost table
# (Budget / Performance / Ultra), plus the platform's current Sarvam+Gemini default.
_NAMED_TIERS: list[tuple[str, ProviderStack]] = [
    ("Budget", ProviderStack("deepgram_nova2", "gemini_2_5_flash", "deepgram_aura", "plivo")),
    ("Current default", ProviderStack("sarvam_saras_v3", "gemini_3_1_flash_lite", "sarvam_bulbul_v3", "sip_direct")),
    ("Performance", ProviderStack("whisper_large", "gpt_4o_mini", "cartesia_sonic", "plivo")),
    ("Ultra", ProviderStack("deepgram_flux", "gpt_4o", "elevenlabs_turbo", "plivo")),
]
_TIERS: list[ProviderStack] = [stack for _, stack in _NAMED_TIERS]


def named_tiers(
    stt_rates: dict[str, float] = STT_RATES,
    llm_rates: dict[str, float] = LLM_RATES,
    tts_rates: dict[str, float] = TTS_RATES,
    telephony_rates: dict[str, float] = TELEPHONY_RATES,
) -> list[dict]:
    """The same four tiers select_stack_for_budget picks from, with labels — for a
    frontend comparison table (Budget/Default/Performance/Ultra side by side)."""
    def _cost(stack: ProviderStack) -> float:
        return estimate_cost_per_min(stack, stt_rates, llm_rates, tts_rates, telephony_rates)

    return [
        {
            "name": name,
            "stt": stack.stt,
            "llm": stack.llm,
            "tts": stack.tts,
            "telephony": stack.telephony,
            "cost_per_min": _cost(stack),
        }
        for name, stack in sorted(_NAMED_TIERS, key=lambda nt: _cost(nt[1]))
    ]


def select_stack_for_budget(
    max_inr_per_min: float,
    stt_rates: dict[str, float] = STT_RATES,
    llm_rates: dict[str, float] = LLM_RATES,
    tts_rates: dict[str, float] = TTS_RATES,
    telephony_rates: dict[str, float] = TELEPHONY_RATES,
) -> ProviderStack:
    """Highest-performing (= most expensive) stack that still fits under the cap.
    Falls back to the cheapest available tier if even that exceeds the budget —
    never raises, since a too-low budget shouldn't 500 the campaign UI."""
    def _cost(stack: ProviderStack) -> float:
        return estimate_cost_per_min(stack, stt_rates, llm_rates, tts_rates, telephony_rates)

    tiers_by_cost = sorted(_TIERS, key=_cost)
    affordable = [t for t in tiers_by_cost if _cost(t) <= max_inr_per_min]
    return affordable[-1] if affordable else tiers_by_cost[0]
