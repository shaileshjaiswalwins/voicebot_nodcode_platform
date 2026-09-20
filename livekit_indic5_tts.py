"""
LiveKit TTS adapter backed by our fine-tuned IndicF5 WebSocket server.

Protocol:
  1. Client → Server: {"text": "...", "nfe_step": 8, "style": "auto",
                       "transliterate": true, "speed": 1.0}
  2. Server → Client: <binary frames> — raw s16le PCM, mono, 24 kHz
  3. Server → Client: {"event": "end"} or {"event": "error", "detail": "..."}

English brand/product words are transliterated to Devanagari server-side.
nfe_step trades latency vs quality: 8 ≈ 0.8 s TTFB, 16 balanced, 32 best.
"""

from __future__ import annotations

import json
import logging
import os

import websockets
from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

_log = logging.getLogger("indic5-tts")

_DEFAULT_WS_URL = os.getenv("INDIC_TTS_WS_URL", "ws://tts.internal:8404/ws")


class IndicF5TTS(tts.TTS):
    def __init__(
        self,
        ws_url: str = _DEFAULT_WS_URL,
        sample_rate: int = 24000,
        nfe_step: int = 16,
        style: str = "auto",
        transliterate: bool = True,
        speed: float = 1.0,
        speaker: str = "simran",
        fallback_speaker: str | None = "simran",
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._ws_url = ws_url
        self._opts = {
            "nfe_step": nfe_step,
            "style": style,
            "transliterate": transliterate,
            "speed": speed,
            "sample_rate": sample_rate,
            "speaker": speaker,
        }
        # Some fine-tuned speakers (e.g. "anushka") intermittently suppress synthesis
        # for a line — server returns {"event": "end", "sentences": 0, "suppressed": true}
        # with zero audio frames instead of an error. Retry once with fallback_speaker so
        # a suppressed line never turns into dead air on a live call. None disables this.
        self._fallback_speaker = fallback_speaker if fallback_speaker != speaker else None

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "IndicF5ChunkedStream":
        return IndicF5ChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            ws_url=self._ws_url,
            sample_rate=self._sample_rate,
            opts=self._opts,
            fallback_speaker=self._fallback_speaker,
        )


class IndicF5ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: IndicF5TTS,
        input_text: str,
        conn_options: APIConnectOptions,
        ws_url: str,
        sample_rate: int,
        opts: dict,
        fallback_speaker: str | None = None,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._ws_url = ws_url
        self._sample_rate = sample_rate
        self._opts = opts
        self._fallback_speaker = fallback_speaker

    async def _synthesize_once(self, output_emitter: tts.AudioEmitter, opts: dict) -> bool:
        """Sends one synthesis request. Returns True iff at least one audio frame arrived."""
        pushed = False
        async with websockets.connect(self._ws_url, max_size=None) as ws:
            await ws.send(json.dumps({"text": self._input_text, **opts}))
            while True:
                msg = await ws.recv()
                if isinstance(msg, (bytes, bytearray)):
                    output_emitter.push(bytes(msg))
                    pushed = True
                else:
                    ev = json.loads(msg)
                    if ev.get("event") == "end":
                        break
                    if ev.get("event") == "error":
                        raise RuntimeError(ev.get("detail"))
        return pushed

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )

        try:
            pushed = await self._synthesize_once(output_emitter, self._opts)
        except RuntimeError:
            if not self._fallback_speaker:
                raise
            pushed = False

        if not pushed and self._fallback_speaker:
            _log.warning(
                f"[TTS] speaker={self._opts.get('speaker')!r} produced no audio for "
                f"text={self._input_text!r} — retrying with fallback speaker={self._fallback_speaker!r}"
            )
            pushed = await self._synthesize_once(
                output_emitter, {**self._opts, "speaker": self._fallback_speaker}
            )

        output_emitter.flush()
