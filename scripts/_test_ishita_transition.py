"""Isolates whether Ishita's "stuck after first message" bug is an LLM/prompt problem or a
LiveKit-plumbing problem: sends the EXACT system instructions workflow_engine.py's
compile_instructions() builds for conv-1a, plus the exact two transition tools
_build_transition_tool() would register, straight to the real Gemini API — no LiveKit
AgentSession, no room, no audio. If Gemini calls one of the tools here, the bug is in how
the tool-call result gets applied to the live session, not in the prompt/model. If it
doesn't, the bug is upstream (prompt wording, model choice, or tool schema)."""
from __future__ import annotations

import os

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

from backend.db import bots, bot_versions
import bson

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

b = bots.find_one({"name": {"$regex": "Ishita", "$options": "i"}})
vid = b.get("draft_version_id") or b.get("active_version_id")
v = bot_versions.find_one({"_id": bson.ObjectId(vid)})
cfg = v["config"]
wf = cfg["workflow"]
node = next(n for n in wf["nodes"] if n["id"] == "conv-1a")
data = node["data"]

# Mirrors WorkflowGraph.compile_instructions() for this node — global_prompt + node prompt +
# transliteration hint (tts_provider defaults to "justdial" here, matching Ishita's config).
business_name = "Sunrise Electronics"
prompt = data["prompt"].replace("{{mis.business_name}}", business_name)
global_prompt = cfg.get("global_prompt", "")
TRANSLITERATION_HINT = (
    "Write all Hindi/Hinglish speech in Devanagari script, not Latin transliteration "
    "(e.g. write 'हाँ जी' not 'haan ji') — our TTS can only pronounce Devanagari correctly."
)
instructions = "\n\n".join(p for p in [global_prompt, prompt, TRANSLITERATION_HINT] if p)

tools = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=t["key"], description=t["condition"].replace("{{mis.business_name}}", business_name),
            parameters=types.Schema(type="OBJECT", properties={}),
        )
        for t in data["transitions"]
    ])
]

TEST_REPLIES = ["haan ji, bilkul", "yes, this is Sunrise Electronics", "no, wrong number", "haan"]

client = genai.Client(api_key=GEMINI_API_KEY)

print("=== System instructions sent ===")
print(instructions)
print("\n=== Tools registered ===")
for t in data["transitions"]:
    print(f"  - {t['key']}: {t['condition']}")
print()

for reply in TEST_REPLIES:
    print(f"--- user says: {reply!r} ---")
    resp = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[types.Content(role="user", parts=[types.Part(text=reply)])],
        config=types.GenerateContentConfig(
            system_instruction=instructions, tools=tools, temperature=0.4,
        ),
    )
    cand = resp.candidates[0]
    called_tool = None
    text_reply = None
    for part in cand.content.parts:
        if getattr(part, "function_call", None):
            called_tool = part.function_call.name
        if getattr(part, "text", None):
            text_reply = part.text
    if called_tool:
        print(f"  -> CALLED TOOL: {called_tool}")
    else:
        print(f"  -> NO TOOL CALLED. Text reply: {text_reply!r}")
    print()
