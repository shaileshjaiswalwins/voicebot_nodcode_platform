#!/usr/bin/env python3
"""Seed 5 demo bots into OUR OWN Mongo collections (tbl_ai_vb_bots /
tbl_ai_vb_bot_versions — see backend/db.py), each showcasing a distinct platform
capability from the boss's demo requirements email (see
plans/07-nocode-platform-demo-readiness.md):

1. Deepgram STT Showcase (Alex)      — STT provider toggle (Sarvam vs. alt)
2. Justdial In-House TTS (Meera)     — in-house TTS (IndicF5), just wired into
                                        pipeline_providers.py's standard pipeline
3. Zero-Code Action Webhooks (Rohan) — pre_call lead lookup + during_call CRM
                                        category-change function, no code deploy
4. Human Escalation Demo (Kabir)     — workflow bot using a `global` node to
                                        transfer to a human whenever the caller
                                        asks, from any point in the conversation
5. Fine-Tuned Conversational Polish (Zara) — temperature/backchanneling/noise
                                        filter/interruption sensitivity tuning

Idempotent: re-running updates each existing bot's draft version in place
(matched by name), same pattern as seed_appointment_workflow_bot.py.

Run:  cd backend && ../.venv/bin/python3 -m seed_demo_showcase_bots
(or:  uv run python -m backend.seed_demo_showcase_bots   from repo root)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from bson import ObjectId

from .db import bots, bot_versions
from .models import (
    BotConfig,
    CustomFunction,
    FunctionParam,
    StoreVariable,
    WorkflowEdge,
    WorkflowGraphDef,
    WorkflowNode,
    WorkflowNodeData,
    WorkflowTransitionSpec,
)

_BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MOCK_VENDOR_LOOKUP_URL = f"{_BACKEND_URL}/api/mock/vendor-lookup"
MOCK_CATEGORY_CHANGE_URL = f"{_BACKEND_URL}/api/mock/crm/category-change"


# ---------------------------------------------------------------------------
# 1. STT provider toggle — Deepgram (English) vs. the platform's Sarvam default
# ---------------------------------------------------------------------------

def build_deepgram_stt_bot() -> BotConfig:
    return BotConfig(
        organization_name="JustDial",
        agent_name="Alex",
        persona_gender="male",
        language="en",
        system_prompt=(
            "You are Alex, a JustDial partner-success caller reaching out in English to a "
            "business owner about their JustDial listing. Confirm their business category, "
            "ask if their contact details are current, and offer to note down any updates. "
            "Keep turns short and conversational — 1-2 sentences."
        ),
        initial_message="Hi, this is Alex calling from JustDial about your business listing — have you got a minute?",
        call_end_text="Thanks so much for your time, have a great day!",
        stt_provider="deepgram",
        stt_model="nova-3",
        stt_language="en-US",
        tts_provider="sarvam",
        tts_voice="shubh",
        tts_language="en-IN",
        temperature=0.4,
    )


# ---------------------------------------------------------------------------
# 2. In-house TTS (IndicF5) — pipeline_providers.py's own fine-tuned Hindi voice
# ---------------------------------------------------------------------------

def build_indic5_tts_bot() -> BotConfig:
    return BotConfig(
        organization_name="JustDial",
        agent_name="Meera",
        persona_gender="female",
        language="hi",
        system_prompt=(
            "Aap Meera hain, JustDial ki taraf se ek business ko call kar rahi hain. Business "
            "ka JustDial listing check karein, poochein ki details sahi hain ya nahi, aur agar "
            "koi update chahiye to note kar lein. Har baari chhoti aur natural rakhein."
        ),
        initial_message="Namaste, main Meera bol rahi hoon JustDial se — do minute baat kar sakte hain?",
        call_end_text="Dhanyavaad, aapka din shubh ho!",
        stt_provider="sarvam",
        tts_provider="justdial",
        tts_voice="simran",
        tts_options={"nfe_step": 16, "style": "auto", "speed": 1.0},
        temperature=0.4,
    )


# ---------------------------------------------------------------------------
# 3. Zero-code action webhooks — pre_call lead lookup + during_call CRM update
# ---------------------------------------------------------------------------

def build_zero_code_webhook_bot() -> BotConfig:
    lookup_fn = CustomFunction(
        id="fn-lookup-vendor",
        name="lookup_vendor",
        description="Fetches the vendor's current JustDial listing details before the call connects.",
        url=MOCK_VENDOR_LOOKUP_URL,
        method="GET",
        body_mode="json",
        trigger="pre_call",
        store_variables=[
            StoreVariable(variable="business_name", json_path="business_name"),
            StoreVariable(variable="business_category", json_path="business_category"),
            StoreVariable(variable="owner_name", json_path="owner_name"),
        ],
    )
    category_change_fn = CustomFunction(
        id="fn-update-category",
        name="update_category",
        description=(
            "Updates the vendor's JustDial listing category — call this the moment the vendor "
            "asks to be listed under a different category."
        ),
        url=MOCK_CATEGORY_CHANGE_URL,
        method="POST",
        body_mode="json",
        trigger="during_call",
        parameters=[
            FunctionParam(name="lead_id", type="string", description="The vendor's lead/business ID.", required=False),
            FunctionParam(name="new_category", type="string", description="The new business category to list under.", required=True),
        ],
        store_variables=[
            StoreVariable(variable="category_change_ref", json_path="change_ref"),
        ],
    )
    return BotConfig(
        organization_name="JustDial",
        agent_name="Rohan",
        persona_gender="male",
        language="hi",
        system_prompt=(
            "Aap Rohan hain, JustDial ke {{business_name}} ({{business_category}}) business ke "
            "malik {{owner_name}} ko call kar rahe hain. Poochein ki unki listing ki category "
            "sahi hai ya nahi. Agar woh category badalna chahte hain, turant update_category "
            "function call karein — kabhi bhi sirf bol kar mat maan lein ki update ho gaya."
        ),
        initial_message="Namaste {{owner_name}} ji, main Rohan bol raha hoon JustDial se.",
        call_end_text="Dhanyavaad, aapka din shubh ho!",
        functions=[lookup_fn, category_change_fn],
        function_calling=True,
        temperature=0.3,
    )


# ---------------------------------------------------------------------------
# 4. Human escalation — a workflow bot's `global` node, reachable from anywhere
# ---------------------------------------------------------------------------

def build_human_escalation_bot() -> BotConfig:
    nodes = [
        WorkflowNode(id="start", position={"x": 400, "y": 0}, data=WorkflowNodeData(
            kind="start", label="Start — greeting",
            first_message="Namaste, main Kabir bol raha hoon JustDial support se. Aapki kya madad kar sakta hoon?",
        )),
        WorkflowNode(id="conv-faq", position={"x": 400, "y": 160}, data=WorkflowNodeData(
            kind="conversation", label="General support Q&A",
            prompt=(
                "Caller ke JustDial listing ya account se juda sawaal ka jawab dein — billing, "
                "listing status, ya general queries. Agar woh kisi insaan/manager se baat karna "
                "chahte hain, kuch mat kahiye — global escalation trigger khud handle karega."
            ),
            transitions=[WorkflowTransitionSpec(id="t-done", key="done", label="Query resolved, caller satisfied")],
        )),
        WorkflowNode(id="end-resolved", position={"x": 780, "y": 160}, data=WorkflowNodeData(
            kind="end_call", label="End — resolved",
            closing_message="Theek hai, dhanyavaad! Agar aur kuch chahiye to phir call kariyega.",
        )),
        # Global node: no incoming edge, reachable from any point in the call — the
        # platform's "smart human escalation/fallback" mechanism.
        WorkflowNode(id="global-escalate", position={"x": 400, "y": 340}, data=WorkflowNodeData(
            kind="global", label="Global — escalate to human",
            trigger_description="Caller explicitly asks to speak to a human agent, a manager, or a real person.",
            action="transfer",
            transfer_number="+911234567890",
        )),
    ]
    edges = [
        WorkflowEdge(id="e-start", source="start", target="conv-faq"),
        WorkflowEdge(id="e-done", source="conv-faq", target="end-resolved", sourceHandle="t-done"),
    ]
    return BotConfig(
        organization_name="JustDial",
        agent_name="Kabir",
        bot_type="workflow",
        workflow=WorkflowGraphDef(nodes=nodes, edges=edges),
        global_prompt=(
            "You are Kabir, a JustDial support agent. Speak warm, natural Hinglish. Keep every "
            "turn short (1-2 sentences)."
        ),
        temperature=0.4,
        tts_provider="justdial",
        tts_voice="simran",
    )


# ---------------------------------------------------------------------------
# 5. Fine-tuned conversational polish — temperature, backchanneling, noise filter
# ---------------------------------------------------------------------------

def build_conversational_polish_bot() -> BotConfig:
    return BotConfig(
        organization_name="JustDial",
        agent_name="Zara",
        persona_gender="female",
        language="hi",
        system_prompt=(
            "Aap Zara hain, ek warm aur expressive JustDial customer-support agent. Caller ki "
            "baat dhyan se sunein, unki tone match karein, aur helpful lagen — kabhi robotic "
            "nahi. Thoda expressive aur natural rahiye, jaise ek asli insaan baat kar raha ho."
        ),
        initial_message="Hii, main Zara bol rahi hoon JustDial se — sab kuch theek hai na aapke saath?",
        call_end_text="Bahut shukriya baat karne ke liye, apna khayal rakhiyega!",
        temperature=0.9,
        tts_provider="sarvam",
        tts_voice="pooja",
        tts_options={"pace": 1.05, "pitch": 0.1, "loudness": 1.1},
        llm_options={"top_p": 0.95, "presence_penalty": 0.3},
        backchanneling_enabled=True,
        noise_filter_sensitivity="high",
        interruption_sensitivity="responsive",
        post_speech_hold_ms=250,
    )


DEMO_BOTS: list[tuple[str, str, "callable[[], BotConfig]"]] = [
    ("Deepgram STT Showcase (Alex)", "STT provider toggle demo — Deepgram (English) vs. the platform's Sarvam default.", build_deepgram_stt_bot),
    ("Justdial In-House TTS (Meera)", "In-house TTS demo — our own fine-tuned IndicF5 voice (tts_provider=justdial), not a third-party API.", build_indic5_tts_bot),
    ("Zero-Code Action Webhooks (Rohan)", "Zero-code webhook demo — pre-call vendor lookup + a during-call CRM category-change tool.", build_zero_code_webhook_bot),
    ("Human Escalation Demo (Kabir)", "Multi-agent/workflow demo — a global node transfers to a human whenever the caller asks, from anywhere in the call.", build_human_escalation_bot),
    ("Fine-Tuned Conversational Polish (Zara)", "Bot fine-tuning demo — temperature, backchanneling, noise filter, and interruption sensitivity tuned for a warm, expressive persona.", build_conversational_polish_bot),
]


def _seed_one(name: str, description: str, build_config, owner: str = "seed-script") -> dict:
    """Idempotent: re-running updates the existing bot's draft version in place
    (matched by name), rather than creating duplicates on every run."""
    now = datetime.now(timezone.utc).isoformat()
    config = build_config()

    existing_bot = bots.find_one({"name": name, "status": {"$ne": "deleted"}})
    if existing_bot:
        bot_id = existing_bot["_id"]
        version_id = existing_bot.get("draft_version_id")
        version_doc = {
            "bot_id": bot_id,
            "version": 1,
            "state": "draft",
            "config": config.model_dump(),
            "notes": "Re-seeded demo showcase bot",
            "created_at": now,
        }
        if version_id:
            bot_versions.update_one({"_id": ObjectId(version_id)}, {"$set": version_doc})
        else:
            version_id = str(bot_versions.insert_one(version_doc).inserted_id)
            bots.update_one({"_id": bot_id}, {"$set": {"draft_version_id": version_id}})
        bots.update_one({"_id": bot_id}, {"$set": {"updated_at": now, "description": description}})
        return {"name": name, "bot_id": str(bot_id), "version_id": str(version_id), "action": "updated"}

    bot_doc = {
        "name": name,
        "description": description,
        "status": "active",
        "active_version_id": None,
        "owner": owner,
        "created_at": now,
        "updated_at": now,
    }
    bot_id = bots.insert_one(bot_doc).inserted_id

    version_doc = {
        "bot_id": bot_id,
        "version": 1,
        "state": "draft",
        "config": config.model_dump(),
        "notes": "Initial demo showcase seed",
        "created_at": now,
    }
    version_id = bot_versions.insert_one(version_doc).inserted_id

    bots.update_one({"_id": bot_id}, {"$set": {"draft_version_id": str(version_id)}})

    return {"name": name, "bot_id": str(bot_id), "version_id": str(version_id), "action": "created"}


def seed_demo_showcase_bots(owner: str = "seed-script") -> list[dict]:
    return [_seed_one(name, description, build_config, owner) for name, description, build_config in DEMO_BOTS]


def main() -> None:
    results = seed_demo_showcase_bots()
    for r in results:
        print(f"[seed] {r['action']}: {r['name']} — bot_id={r['bot_id']} test_bot_version_id={r['version_id']}")
    print("[seed] Use each bot's ids in a Test Call (POST /api/testcall/start) to run it live.")


if __name__ == "__main__":
    main()
