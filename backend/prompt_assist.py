from .evals import _client, _GEMINI_MODEL

_GENERATE_INSTRUCTIONS = (
    "You are writing a system prompt for a voice call center agent bot. Write only the system "
    "prompt text itself — no preamble, no markdown, no explanation of what you wrote."
)

_REFINE_INSTRUCTIONS = (
    "You are editing an existing system prompt for a voice call center agent bot. Apply ONLY the "
    "requested change below — leave every other part of the prompt exactly as it is. Output the "
    "full updated system prompt text, nothing else: no preamble, no markdown, no explanation."
)


def generate_prompt(mode: str, instruction: str, current_prompt: str) -> str:
    if mode == "refine":
        prompt = (
            f"{_REFINE_INSTRUCTIONS}\n\n"
            f"Current system prompt:\n{current_prompt}\n\n"
            f"Requested change:\n{instruction}"
        )
    else:
        prompt = f"{_GENERATE_INSTRUCTIONS}\n\nWhat the bot should do:\n{instruction}"

    client = _client()
    return client.models.generate_content(model=_GEMINI_MODEL, contents=prompt).text.strip()
