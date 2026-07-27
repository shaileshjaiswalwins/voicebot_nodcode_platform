import os

from google import genai

from .models import EvalScenario

_GEMINI_MODEL = os.getenv("EVALS_GEMINI_MODEL", "gemini-3.1-flash-lite")

DEFAULT_SCENARIOS: list[EvalScenario] = [
    EvalScenario(
        name="Interested buyer",
        caller_persona="You are a small business owner in Bangalore who is genuinely interested in the product/service being offered. Answer questions naturally and provide your city and requirement when asked.",
        max_turns=4,
        must_contain=[],
        must_not_contain=["error", "undefined", "{agent_name}", "{organization_name}"],
    ),
    EvalScenario(
        name="Not interested — should close politely",
        caller_persona="You are not interested in what is being offered. Say so clearly in your first reply and end the conversation.",
        max_turns=3,
        must_contain=[],
        must_not_contain=["error", "undefined"],
    ),
]


def _client() -> genai.Client:
    api_key = os.getenv("GEMINI_LIVE_API_KEY") or os.getenv("GEMINI_ANALYSIS_API_KEY")
    if not api_key:
        raise RuntimeError("No Gemini API key configured (GEMINI_LIVE_API_KEY / GEMINI_ANALYSIS_API_KEY)")
    return genai.Client(api_key=api_key)


def run_scenario(client: genai.Client, system_prompt: str, scenario: EvalScenario) -> dict:
    """LLM-vs-LLM simulation: one model plays the caller (scenario.caller_persona), the other
    plays the bot (the real system_prompt being evaluated). No live telephony required, so
    this can run pre-publish against any draft version."""
    transcript: list[dict] = []
    caller_history = ""
    bot_history = ""

    for _ in range(scenario.max_turns):
        caller_prompt = (
            f"{scenario.caller_persona}\n\n"
            f"Conversation so far (you are the CALLER):\n{caller_history or '(call is just starting — the bot will greet you)'}\n\n"
            "Reply with only what the caller says next, one short natural sentence. "
            "If the bot already said goodbye or you have nothing left to say, reply exactly: [END]"
        )
        caller_reply = client.models.generate_content(model=_GEMINI_MODEL, contents=caller_prompt).text.strip()
        if "[END]" in caller_reply:
            break
        transcript.append({"role": "caller", "text": caller_reply})
        caller_history += f"Caller: {caller_reply}\n"

        bot_prompt = f"{system_prompt}\n\nConversation so far:\n{bot_history}Caller: {caller_reply}\n\nRespond as the bot, one turn only."
        bot_reply = client.models.generate_content(model=_GEMINI_MODEL, contents=bot_prompt).text.strip()
        transcript.append({"role": "bot", "text": bot_reply})
        bot_history += f"Caller: {caller_reply}\nBot: {bot_reply}\n"
        caller_history += f"Bot: {bot_reply}\n"

    full_bot_text = " ".join(t["text"] for t in transcript if t["role"] == "bot").lower()
    missing = [phrase for phrase in scenario.must_contain if phrase.lower() not in full_bot_text]
    forbidden_hit = [phrase for phrase in scenario.must_not_contain if phrase.lower() in full_bot_text]
    # An empty transcript (the caller-simulator ended the call before the bot ever spoke)
    # trivially satisfies "no missing/forbidden phrases found" — without this check that
    # silently reports as a pass, giving false confidence that the scenario was actually
    # exercised when the simulation may have short-circuited for an unrelated reason.
    passed = bool(transcript) and not missing and not forbidden_hit

    return {
        "scenario": scenario.name,
        "passed": passed,
        "transcript": transcript,
        "missing_required_phrases": missing,
        "forbidden_phrases_found": forbidden_hit,
    }


def run_evals(system_prompt: str, scenarios: list[EvalScenario]) -> dict:
    client = _client()
    results = [run_scenario(client, system_prompt, scenario) for scenario in scenarios]
    return {
        "results": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
    }
