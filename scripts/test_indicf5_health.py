"""Standalone health check for the IndicF5 TTS WebSocket server (INDIC_TTS_WS_URL).

Talks to the raw ws protocol directly (see livekit_indic5_tts.py's docstring) rather than
importing the livekit-agents TTS wrapper, so this runs with zero LiveKit/room/job context —
just "is the server up and actually producing audio for each of our 3 fine-tuned speakers."

Usage:
    .venv/bin/python3 scripts/test_indicf5_health.py
    .venv/bin/python3 scripts/test_indicf5_health.py --text "अपना कस्टम टेक्स्ट यहाँ"
    .venv/bin/python3 scripts/test_indicf5_health.py --speaker simran --save

Writes one .wav per speaker into scripts/indicf5_health_out/ only when --save is passed,
so a routine health check doesn't leave files behind.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

import websockets

WS_URL = os.getenv("INDIC_TTS_WS_URL", "ws://10.10.0.14:8404/ws")
SPEAKERS = ["simran", "anushka", "niharika"]
DEFAULT_TEXT = "नमस्ते, मैं जस्टडायल की तरफ़ से बोल रही हूँ। क्या आप मुझे सुन पा रहे हैं?"
SAMPLE_RATE = 24000


async def synth_once(text: str, speaker: str, timeout_s: float = 20.0) -> dict:
    """Sends one synthesis request; returns a result dict — never raises for expected
    failure modes (unreachable server, error event, suppressed/zero-audio line) so the
    caller can report a clean pass/fail table instead of a stack trace per speaker."""
    result = {
        "speaker": speaker, "ok": False, "bytes": 0, "ttfb_ms": None,
        "total_ms": None, "detail": "", "audio": b"",
    }
    t0 = time.monotonic()
    try:
        async with websockets.connect(WS_URL, max_size=None, open_timeout=timeout_s) as ws:
            await ws.send(json.dumps({
                "text": text, "speaker": speaker, "nfe_step": 16, "style": "auto",
                "transliterate": True, "speed": 1.0, "sample_rate": SAMPLE_RATE,
            }))
            chunks = []
            ttfb = None
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
                if isinstance(msg, (bytes, bytearray)):
                    if ttfb is None:
                        ttfb = (time.monotonic() - t0) * 1000
                    chunks.append(bytes(msg))
                else:
                    ev = json.loads(msg)
                    if ev.get("event") == "error":
                        result["detail"] = f"server error: {ev.get('detail')}"
                        return result
                    if ev.get("event") == "end":
                        if ev.get("suppressed"):
                            result["detail"] = "server suppressed this line (0 audio) — matches the known anushka-suppression issue livekit_indic5_tts.py works around with a fallback speaker"
                        break
            audio = b"".join(chunks)
            result["bytes"] = len(audio)
            result["audio"] = audio
            result["ttfb_ms"] = round(ttfb, 1) if ttfb is not None else None
            result["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
            result["ok"] = len(audio) > 0
            if not result["ok"] and not result["detail"]:
                result["detail"] = "connected fine, but zero audio bytes came back"
            return result
    except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as exc:
        result["detail"] = f"{type(exc).__name__}: {exc}"
        result["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return result


def save_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # s16le
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text to synthesize")
    parser.add_argument("--speaker", choices=SPEAKERS, help="Test only this speaker (default: all 3)")
    parser.add_argument("--save", action="store_true", help="Write .wav files to scripts/indicf5_health_out/")
    args = parser.parse_args()

    speakers = [args.speaker] if args.speaker else SPEAKERS
    print(f"IndicF5 health check — {WS_URL}")
    print(f"text: {args.text!r}\n")

    results = []
    for speaker in speakers:
        print(f"  [{speaker}] synthesizing...", end=" ", flush=True)
        r = await synth_once(args.text, speaker)
        results.append(r)
        if r["ok"]:
            print(f"OK — {r['bytes']} bytes, ttfb={r['ttfb_ms']}ms, total={r['total_ms']}ms")
            if args.save:
                out = Path(__file__).parent / "indicf5_health_out" / f"{speaker}.wav"
                save_wav(out, r["audio"])
                print(f"      saved -> {out}")
        else:
            print(f"FAILED — {r['detail']}")

    print()
    ok_count = sum(1 for r in results if r["ok"])
    print(f"Result: {ok_count}/{len(results)} speakers produced audio.")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
