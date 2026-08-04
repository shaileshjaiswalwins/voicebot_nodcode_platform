import json
import re

from .analysis_prompts import CALL_ANALYSIS_KEY, DEFAULT_PROMPTS, REQUIRED_PLACEHOLDERS
from .evals import _client, _GEMINI_MODEL

# Per-target instruction pairs for the builder's "Generate/Refine with AI" (Sparkles) button —
# same widget reused across System prompt, Closing line, and Analysis prompt override, each
# with its own generate/refine framing since they're very different kinds of text.
_GENERATE_INSTRUCTIONS = {
    "system_prompt": (
        "You are writing a system prompt for a voice call center agent bot. Write only the system "
        "prompt text itself — no preamble, no markdown, no explanation of what you wrote."
    ),
    "closing_line": (
        "You are writing the closing line a voice call center agent bot says right after it has "
        "collected everything it needs from the caller and is ending the call. Write ONE short, warm, "
        "natural spoken line — no preamble, no markdown, no explanation, no quotation marks around it. "
        "Match the language/script implied by the instruction (e.g. Hindi-in-Devanagari, Hinglish, "
        "English) — do not silently translate to English."
    ),
}

_REFINE_INSTRUCTIONS = {
    "system_prompt": (
        "You are editing an existing system prompt for a voice call center agent bot. Apply ONLY the "
        "requested change below — leave every other part of the prompt exactly as it is. Output the "
        "full updated system prompt text, nothing else: no preamble, no markdown, no explanation."
    ),
    "closing_line": (
        "You are editing the closing line a voice call center agent bot says at the end of a call. "
        "Apply ONLY the requested change below. Output just the updated line, nothing else: no "
        "preamble, no markdown, no explanation, no quotation marks around it."
    ),
    "analysis_prompt": (
        "You are editing the post-call ANALYSIS prompt for a voice AI platform — this is not spoken to "
        "the caller; it's fed to a separate LLM after the call ends, with the transcript, to produce a "
        "structured JSON classification. Apply ONLY the requested change below (e.g. add/adjust a "
        "classification rule or disposition option) — leave everything else exactly as it is, "
        "including formatting and section structure.\n\n"
        "CRITICAL — this template is substituted with runtime values via Python str.format(), so it "
        "MUST keep every one of these placeholders present, spelled exactly as shown, wrapped in a "
        "single pair of curly braces, and MUST NOT introduce any other {{...}} placeholder: "
        f"{', '.join('{' + p + '}' for p in sorted(REQUIRED_PLACEHOLDERS[CALL_ANALYSIS_KEY]))}\n\n"
        "Output the full updated analysis prompt text, nothing else: no preamble, no markdown fences, "
        "no explanation."
    ),
}


def generate_prompt(mode: str, instruction: str, current_prompt: str, target: str = "system_prompt") -> str:
    """Powers the builder's per-field "Generate/Refine with AI" button.

    `target` selects which framing to use (system_prompt / closing_line / analysis_prompt) —
    see the instruction dicts above. analysis_prompt has no from-scratch "generate" mode: with
    18 mandatory runtime placeholders (see analysis_prompts.REQUIRED_PLACEHOLDERS) a from-nothing
    rewrite is too likely to drop one and fail the save-time validator
    (bots._validate_config_analysis_prompt), so it's always treated as a refine of the current
    text — or the shared default template (analysis_prompts.DEFAULT_PROMPTS) when the bot has no
    override yet, which the frontend passes as current_prompt in that case anyway.
    """
    if target == "analysis_prompt":
        base_text = current_prompt or DEFAULT_PROMPTS[CALL_ANALYSIS_KEY]
        prompt = (
            f"{_REFINE_INSTRUCTIONS['analysis_prompt']}\n\n"
            f"Current analysis prompt:\n{base_text}\n\n"
            f"Requested change:\n{instruction}"
        )
    elif mode == "refine":
        prompt = (
            f"{_REFINE_INSTRUCTIONS.get(target, _REFINE_INSTRUCTIONS['system_prompt'])}\n\n"
            f"Current text:\n{current_prompt}\n\n"
            f"Requested change:\n{instruction}"
        )
    else:
        prompt = (
            f"{_GENERATE_INSTRUCTIONS.get(target, _GENERATE_INSTRUCTIONS['system_prompt'])}\n\n"
            f"What it should do:\n{instruction}"
        )

    client = _client()
    return client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text.strip()


_CREATE_AGENT_INSTRUCTIONS = (
    "You are helping a non-technical user create a voice call center agent from a plain-English "
    "description of what they want. Given their description, produce exactly three things:\n"
    "1. agent_name — a short, plausible spoken persona first name for the bot (e.g. \"Priya\", \"Rahul\").\n"
    "2. initial_message — the bot's opening line when the call connects. One or two short "
    "conversational sentences, in the bot's own persona, that greet the caller and state why "
    "it's calling/what it can help with.\n"
    "3. system_prompt — a complete system prompt for the bot: its role, the conversation's goal, "
    "a rough step-by-step flow, tone/style guidance, and 2-3 explicit constraints.\n\n"
    "Respond with ONLY a raw JSON object with exactly these three keys (agent_name, "
    "initial_message, system_prompt), no markdown fences, no commentary before or after."
)


def _extract_json_object(text: str) -> dict:
    """Gemini is instructed to return raw JSON but sometimes wraps it in a ```json fence
    anyway — strip that before parsing rather than failing on it."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(cleaned)


def generate_agent_from_description(description: str) -> dict:
    """Create Agent > Create with AI: derives agent_name/initial_message/system_prompt from a
    free-text description in one call, so a non-technical user never has to write a prompt by
    hand. Everything else (TTS/STT/language/etc.) is left to the platform's own defaults —
    this only fills in the fields an LLM can reasonably infer from a short description."""
    prompt = f"{_CREATE_AGENT_INSTRUCTIONS}\n\nUser's description of the agent they want:\n{description}"
    client = _client()
    raw = client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text
    try:
        parsed = _extract_json_object(raw)
        return {
            "agent_name": str(parsed.get("agent_name", "")).strip(),
            "initial_message": str(parsed.get("initial_message", "")).strip(),
            "system_prompt": str(parsed.get("system_prompt", "")).strip(),
        }
    except (json.JSONDecodeError, AttributeError):
        # Model didn't follow the JSON format — fall back to treating the whole response as
        # the system prompt rather than losing the generation entirely; agent_name/
        # initial_message just stay blank for the user to fill in themselves.
        return {"agent_name": "", "initial_message": "", "system_prompt": raw.strip()}
