"""Schema-driven generic post-call analysis extractor for Workflow Builder bots.

This is the non-destructive second path described in Part 2 of the post-call-analysis
revamp plan: `callback_worker/analysis.py::generate_call_analysis` stays exactly as-is
for legacy/campaign bots (its whole prompt is hardcoded around an outbound
lead-qualification `qualification_schema` that's only ever populated for campaign
leads). For a Workflow Builder bot with a PM-defined `analysis_fields` schema
(`backend.models.AnalysisFieldDef`), this module builds one dynamic prompt from that
field list + the call transcript, asks Gemini for a single flat JSON object (one key
per field), and validates each returned value against its declared type before
persisting — mirrors the JSON-extraction/error-tolerance style already established in
backend/prompt_assist.py (using the `google.genai` client wired up in backend/evals.py
and reused by prompt_assist.py) and callback_worker/analysis.py's own Gemini call
pattern, applied to a PM-defined schema instead of a hardcoded one.

Skip-logic mirrors generate_call_analysis: a call that never connected
(`gemini_connect_failed`) or produced no transcript never reaches the model — there is
nothing for it to extract, and it would just waste an API call.
"""

import json
import os

import aiohttp
from loguru import logger

from .models import AnalysisFieldDef

# Same env var callback_worker/config.py reads GEMINI_API_KEY from — read directly here
# rather than importing callback_worker.config, since that module requires several
# production-only env vars (MONGO_URI, CALLBACK_API_URL, ...) at import time that the
# backend API process doesn't set.
GEMINI_API_KEY = os.getenv("GEMINI_ANALYSIS_API_KEY")

_GEMINI_MODEL = "gemini-3.1-flash-lite"

_TYPE_INSTRUCTIONS = {
    "boolean": "a JSON boolean (true or false)",
    "text": "a short free-form JSON string",
    "number": "a JSON number",
    "enum": "a JSON string, exactly one of the listed options",
}


def _build_prompt(fields: list[dict], transcript_lines: str) -> str:
    field_lines = []
    for f in fields:
        desc = f.get("description") or ""
        kind = _TYPE_INSTRUCTIONS.get(f.get("type"), "a short free-form JSON string")
        line = f"- \"{f['key']}\" ({f.get('label', f['key'])}): {kind}."
        if desc:
            line += f" {desc}"
        if f.get("type") == "enum" and f.get("enum_options"):
            line += f" Allowed values: {', '.join(f['enum_options'])}."
        field_lines.append(line)

    return (
        "You are extracting structured post-call analysis fields from a voice call center "
        "agent's call transcript, per a schema defined by the bot's product manager. For "
        "each field below, read the transcript and produce the requested value. If the "
        "transcript gives no basis to determine a field, use your best null-ish default for "
        "its type (false for boolean, 0 for number, \"\" for text/enum) rather than guessing.\n\n"
        "Fields to extract:\n" + "\n".join(field_lines) + "\n\n"
        "Respond with ONLY a raw JSON object with exactly one key per field key listed above, "
        "no markdown fences, no commentary, no extra keys.\n\n"
        f"Transcript:\n{transcript_lines}"
    )


def _coerce_value(raw_value, field: dict):
    """Validate/coerce one extracted value against its declared type. Matches this
    codebase's established error-tolerance philosophy (see analysis.py's DISPOSITION_MAP
    fallback and prompt_assist.py's _extract_json_object callers): a mismatch is logged
    and coerced to a safe default rather than raising and losing the whole analysis over
    one bad field."""
    ftype = field.get("type")
    key = field.get("key")

    if ftype == "boolean":
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str) and raw_value.strip().lower() in ("true", "false"):
            return raw_value.strip().lower() == "true"
        logger.warning(f"[POST_CALL_ANALYSIS] Field '{key}' expected boolean, got {raw_value!r} — defaulting to False")
        return False

    if ftype == "number":
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            try:
                return float(raw_value) if "." in raw_value else int(raw_value)
            except ValueError:
                pass
        logger.warning(f"[POST_CALL_ANALYSIS] Field '{key}' expected number, got {raw_value!r} — defaulting to 0")
        return 0

    if ftype == "enum":
        options = field.get("enum_options") or []
        if isinstance(raw_value, str) and raw_value in options:
            return raw_value
        logger.warning(
            f"[POST_CALL_ANALYSIS] Field '{key}' expected one of {options}, got {raw_value!r} — defaulting to ''"
        )
        return ""

    # "text" — free-form, coerce anything to a string.
    if raw_value is None:
        return ""
    return raw_value if isinstance(raw_value, str) else str(raw_value)


def _empty_result(fields: list[dict]) -> dict:
    return {f["key"]: _coerce_value(None if f.get("type") != "boolean" else False, f) for f in fields}


def empty_analysis_result(analysis_fields: list[dict] | list[AnalysisFieldDef]) -> dict:
    """Public wrapper around `_empty_result` for callers (e.g. bot.py's outer except
    block around `generate_generic_analysis`) that need the same per-type-correct empty
    defaults without duplicating the type-default logic themselves."""
    fields = [f.model_dump() if isinstance(f, AnalysisFieldDef) else dict(f) for f in analysis_fields]
    return _empty_result(fields)


async def generate_generic_analysis(
    transcript: list[dict],
    analysis_fields: list[dict] | list[AnalysisFieldDef],
    http_session: aiohttp.ClientSession,
    gemini_connect_failed: bool = False,
    model: str = _GEMINI_MODEL,
) -> tuple[str, dict]:
    """Extract a PM-defined set of `analysis_fields` from a call transcript via Gemini.

    Returns a `(status, result)` tuple. `result` is a flat {key: value} dict, one entry
    per configured field, every value already coerced to its declared type. `status` is
    one of:
      - "ok": a real result — either no fields were configured (nothing to extract), or
        Gemini was called and returned a parseable, type-coerced response.
      - "skipped": skip-logic short-circuited before ever calling Gemini (the call never
        connected, or produced no/blank transcript) — there was nothing to analyze.
      - "failed": Gemini was called but the request/response pipeline broke (non-2xx HTTP
        status, network/timeout error, or a malformed/unparseable response).

    This status lets callers (bot.py) persist a sibling `analysis_fields_status` field
    next to `analysis_fields_result` so a PM viewing the dashboard can distinguish "the
    model determined this is false/empty" from "nothing was ever actually analyzed for
    this call" — both cases would otherwise produce the exact same per-type-empty shape.

    Never raises — on any failure (skip conditions, API error, malformed JSON) falls back
    to per-type empty defaults so a bad call never blocks the transcript save."""
    fields = [f.model_dump() if isinstance(f, AnalysisFieldDef) else dict(f) for f in analysis_fields]
    if not fields:
        return "ok", {}

    # Mirror generate_call_analysis's skip-logic: a call that never connected or produced
    # no transcript has nothing to extract from — don't waste a Gemini call on it.
    if gemini_connect_failed or not transcript:
        return "skipped", _empty_result(fields)

    lines = "\n".join(
        f"{t.get('role', '?').upper()}: {t.get('text', '')}" for t in transcript if t.get("text", "").strip()
    )
    if not lines.strip():
        return "skipped", _empty_result(fields)

    prompt = _build_prompt(fields, lines)

    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        async with http_session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            # Check the HTTP status BEFORE parsing the body. A non-2xx here (401/429/500/...)
            # almost always signals a systemic problem (bad/expired key, rate limiting,
            # Gemini outage) rather than a one-off bad response — tag it distinctly
            # (error_category=http_status) so it's greppable apart from the parse/network
            # failures below, letting someone debugging a production incident immediately
            # tell "Gemini was down for an hour" from "a handful of calls got weird output".
            if not (200 <= resp.status < 300):
                body_text = await resp.text()
                logger.error(
                    f"[POST_CALL_ANALYSIS] error_category=http_status Gemini HTTP call failed with "
                    f"status={resp.status}: {body_text[:500]}"
                )
                return "failed", _empty_result(fields)

            data = await resp.json()
            if "candidates" not in data or not data["candidates"]:
                logger.error(
                    f"[POST_CALL_ANALYSIS] error_category=parse No candidates in Gemini response: "
                    f"{data.get('error') or data}"
                )
                return "failed", _empty_result(fields)
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(
                    f"[POST_CALL_ANALYSIS] error_category=parse Malformed JSON from Gemini: "
                    f"{type(e).__name__}: {e}"
                )
                return "failed", _empty_result(fields)
            if not isinstance(parsed, dict):
                logger.error(
                    f"[POST_CALL_ANALYSIS] error_category=parse Expected a JSON object, got "
                    f"{type(parsed).__name__}: {parsed!r}"
                )
                return "failed", _empty_result(fields)

            result = {}
            for f in fields:
                key = f["key"]
                result[key] = _coerce_value(parsed.get(key), f)
            return "ok", result
    except Exception as e:
        logger.error(
            f"[POST_CALL_ANALYSIS] error_category=network Generic extraction failed: "
            f"{type(e).__name__}: {e}"
        )
        return "failed", _empty_result(fields)
