from .evals import _client, _GEMINI_MODEL
from .models import ChatTurn

_ENDMARK = "[END]"


def _substitute_dynamic_variables(text: str, dynamic_variables: dict[str, str]) -> str:
    """{{var}} substitution for the Test LLM sandbox — a deliberately simpler mechanism than
    bot.py's build_system_prompt() record-based compiler, since this panel never runs a real
    call and only needs to let the tester see how seeded values read in the prompt."""
    for name, value in dynamic_variables.items():
        text = text.replace("{{" + name + "}}", value).replace("{" + name + "}", value)
    return text


def _mocks_block(function_mocks: dict[str, str]) -> str:
    """Function-calling is not actually wired into this sandbox (Gemini tool-calling requires
    per-function JSON schemas and a full call/response loop) — instead, mocked responses are
    surfaced to the bot as context it's told to use if the conversation calls for them. This
    is intentionally simpler than real execution: it validates prompt/data shape, not the
    function-calling loop itself."""
    if not function_mocks:
        return ""
    lines = [
        "\n\nYou have the following functions available. If the conversation requires calling "
        "one, act as though you called it and got this mocked result — use it naturally, don't "
        "mention it's mocked:"
    ]
    for name, mock_json in function_mocks.items():
        lines.append(f"- {name}() -> {mock_json}")
    return "\n".join(lines)


def _compile_system_prompt(system_prompt: str, dynamic_variables: dict[str, str], function_mocks: dict[str, str]) -> str:
    compiled = _substitute_dynamic_variables(system_prompt, dynamic_variables)
    return compiled + _mocks_block(function_mocks)


def _format_history(history: list[ChatTurn]) -> str:
    return "\n".join(f"{'Caller' if turn.role == 'user' else 'Bot'}: {turn.text}" for turn in history)


def bot_reply(system_prompt: str, history: list[ChatTurn], dynamic_variables: dict[str, str], function_mocks: dict[str, str]) -> str:
    compiled_prompt = _compile_system_prompt(system_prompt, dynamic_variables, function_mocks)
    transcript = _format_history(history)
    prompt = f"{compiled_prompt}\n\nConversation so far:\n{transcript}\n\nRespond as the bot, one turn only."
    client = _client()
    return client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text.strip()


def simulate_turn(
    system_prompt: str,
    caller_persona: str,
    history: list[ChatTurn],
    dynamic_variables: dict[str, str],
    function_mocks: dict[str, str],
) -> dict:
    """One caller-then-bot turn, mirroring evals.run_scenario's per-iteration body but
    returning after a single pair instead of looping to a final transcript — lets the
    frontend render turns as they're generated."""
    client = _client()
    caller_history = _format_history(history)
    caller_prompt = (
        f"{caller_persona}\n\n"
        f"Conversation so far (you are the CALLER):\n{caller_history or '(call is just starting — the bot will greet you)'}\n\n"
        "Reply with only what the caller says next, one short natural sentence. "
        f"If the bot already said goodbye or you have nothing left to say, reply exactly: {_ENDMARK}"
    )
    caller_text = client.models.generate_content(model=_GEMINI_MODEL, contents=caller_prompt).text.strip()
    if _ENDMARK in caller_text:
        return {"ended": True, "caller_text": None, "bot_text": None}

    caller_turn = ChatTurn(role="user", text=caller_text)
    bot_text = bot_reply(system_prompt, history + [caller_turn], dynamic_variables, function_mocks)
    return {"ended": False, "caller_text": caller_text, "bot_text": bot_text}
