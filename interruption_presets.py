"""Named interruption-sensitivity presets — PM-facing knob for "how easily can
a caller interrupt the bot mid-sentence", resolved here into concrete
per-runtime parameters. See backend/schemas.py's `interruption_sensitivity`
field (a plain string: "patient" | "balanced" | "responsive") — every bot
config (Assistant and Workflow Bot, both bot_type values) carries it.

Two runtimes, two genuinely different underlying mechanisms — deliberately
NOT sharing one raw parameter set between them (see the "why two `min_words`
fields" note below, that's not an accident):

  - LiveKit (bot_dev.py, workflow_engine.py): AgentSession's current, non-
    deprecated `turn_handling={"interruption": {...}}` option (confirmed by
    reading the installed livekit-agents source,
    .venv/.../livekit/agents/voice/turn.py — the bare kwargs like
    `min_interruption_words=` are deprecated in favor of this dict, and
    mixing the two styles in one AgentSession() call silently drops whichever
    one isn't `turn_handling` itself — confirmed by reading
    agent_session.py's constructor, not assumed). Fields used:
    `livekit_min_duration` (seconds of continuous speech before treating it
    as a real interruption), `livekit_min_words` (STT word count), and
    `backchannel_boundary` (a window at the start/end of the bot's turn where
    adaptive interruption handling is suppressed, specifically to let
    "हाँ"/"ठीक"-style backchannels through without cutting the bot off).
    "balanced" intentionally matches the SDK's own documented defaults
    (min_duration=0.5, min_words=0, backchannel_boundary=(1.0, 3.5)) rather
    than inventing new numbers, since that's the behavior every existing bot
    already has today (neither bot_dev.py nor workflow_engine.py previously
    overrode these). `hold_secs` additionally drives bot_dev.py's own
    hand-rolled mic-mute timer (_delayed_unmute) — a belt-and-suspenders
    mechanism, separate from the SDK's interruption logic, that stops audio
    from reaching STT at all for a window after the bot starts speaking
    (avoids wasted STT calls and TTS-echo pickup, not just a "should we act
    on it" decision like InterruptionOptions is). "balanced" = 4.0s matches
    its pre-existing hardcoded value.
  - Pipecat (webrtc_bot.py's InterruptionWordGate, built earlier this
    project): a word count is the ONLY axis available here — there is no
    equivalent of min_duration/backchannel_boundary backing it up. This is
    why `pipecat_min_words` is a SEPARATE field from `livekit_min_words`
    rather than the same number reused: LiveKit's "balanced" min_words=0 is
    fine precisely because min_duration=0.5s is doing the real protective
    work there, but on Pipecat a min_words=0 would mean NO gating at all —
    exactly the "fillers cutting the bot off" bug this session already fixed
    once for the default case (InterruptionWordGate(min_words=2)). Reusing
    the LiveKit number here would have silently reintroduced that bug for
    every bot on the "balanced"/"responsive" presets.

NOT implemented (deliberately, this pass): per-conversation-node overrides.
Whether LiveKit's AgentSession/turn_handling can be reconfigured mid-call
(as opposed to fixed at construction) wasn't verified against a live call,
so workflow bots get one bot-level preset for now, same as regular
assistants — not a per-node one. Revisit if per-node granularity is needed.
"""

from __future__ import annotations

from typing import TypedDict


class InterruptionPreset(TypedDict):
    hold_secs: float           # bot_dev.py's hand-rolled mic-mute duration
    livekit_min_duration: float        # LiveKit turn_handling.interruption.min_duration
    livekit_min_words: int             # LiveKit turn_handling.interruption.min_words
    backchannel_boundary: tuple[float, float]  # LiveKit turn_handling.interruption.backchannel_boundary
    pipecat_min_words: int      # Pipecat InterruptionWordGate.min_words — see module docstring for why this is NOT the same number as livekit_min_words


DEFAULT_INTERRUPTION_SENSITIVITY = "balanced"

INTERRUPTION_PRESETS: dict[str, InterruptionPreset] = {
    "patient": {
        "hold_secs": 7.0,
        "livekit_min_duration": 1.2,
        "livekit_min_words": 4,
        "backchannel_boundary": (1.5, 5.0),
        "pipecat_min_words": 4,
    },
    "balanced": {
        "hold_secs": 4.0,
        "livekit_min_duration": 0.5,
        "livekit_min_words": 0,
        "backchannel_boundary": (1.0, 3.5),
        "pipecat_min_words": 2,
    },
    "responsive": {
        "hold_secs": 1.5,
        "livekit_min_duration": 0.2,
        "livekit_min_words": 0,
        "backchannel_boundary": (0.5, 2.0),
        "pipecat_min_words": 1,
    },
}


def resolve_interruption_preset(sensitivity: str | None) -> InterruptionPreset:
    """Resolve a stored `interruption_sensitivity` string to its concrete
    parameters. Falls back to "balanced" for None/unknown values so callers
    always get a valid config."""
    return INTERRUPTION_PRESETS.get(sensitivity or "", INTERRUPTION_PRESETS[DEFAULT_INTERRUPTION_SENSITIVITY])
