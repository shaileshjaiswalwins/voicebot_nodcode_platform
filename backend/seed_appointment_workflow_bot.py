#!/usr/bin/env python3
"""Seed the "Justdial Vendor Appointment Scheduling (Ishita)" workflow bot into
OUR OWN Mongo collections (tbl_ai_vb_bots / tbl_ai_vb_bot_versions in the
ai_voice_bot_management DB — see backend/db.py), for the "Live Feature Showcase:
Appointment Flow" demo item.

Script content (node prompts/labels/closing messages, built from
Revised_Script_30062024.pdf) reproduced verbatim from the reference platform's
seed_justdial_appointment_workflow_bot.py — this is Justdial's own internal
outbound call script, shared across both teams' platforms for the same demo, not
third-party content. Graph shape/schema and storage are entirely our own
(WorkflowGraphDef in backend/models.py, workflow_engine.py's runtime), and vendor
lookup now points at our own mock endpoint (backend/routers/mock_appointment_data.py)
instead of the other repo's.

New relative to the reference version: `fn-book-appointment` + `booking-check` —
a REAL calendar-booking webhook call once a time is agreed (backend/routers/
mock_appointment_data.py's /api/mock/calendar/book), branching on whether the
slot was actually confirmed, rather than just having the LLM record a free-text
appointment_time string and never doing anything with it. This is a genuine
capability neither platform had before.

Two things to know about this bot's current state (same caveats as the reference
version):

1. Vendor-context fields ({{mis.business_name}}, {{mis.owner_name}},
   {{mis.business_category}}, {{mis.category_searches}}, {{mis.competitor_name_1}},
   {{mis.competitor_name_2}}) are fetched by the `fn-vendor-lookup` function node
   right after Start, stored under `output_key: "mis"`. Points at our own
   /api/mock/vendor-lookup (backend/routers/mock_appointment_data.py) — THROWAWAY
   TEST DATA. Swap for a real MIS/CRM vendor-lookup API before using this bot for
   real calls.
2. "Transfer" (end-transfer) is NOT a real SIP/telephony transfer — it just speaks
   a line announcing the handoff and ends the call (see workflow_engine.py's
   module docstring on the `global` node "transfer" action, which has the same
   limitation).

Run:  cd backend && ../.venv/bin/python3 -m seed_appointment_workflow_bot
(or:  uv run python -m backend.seed_appointment_workflow_bot   from repo root)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from bson import ObjectId

from .db import bots, bot_versions
from .models import (
    BotConfig,
    WorkflowConditionSpec,
    WorkflowEdge,
    WorkflowFunctionSpec,
    WorkflowGraphDef,
    WorkflowNode,
    WorkflowNodeData,
    WorkflowTransitionSpec,
    WorkflowVariableSpec,
)

BOT_NAME = "Justdial Vendor Appointment Scheduling (Ishita)"

_BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MOCK_MIS_API_URL = f"{_BACKEND_URL}/api/mock/vendor-lookup"
MOCK_CALENDAR_BOOK_URL = f"{_BACKEND_URL}/api/mock/calendar/book"


def _transition(tid: str, key: str, label: str, condition: str) -> WorkflowTransitionSpec:
    return WorkflowTransitionSpec(id=tid, key=key, label=label, condition=condition)


# ---------------------------------------------------------------------------
# Graph — nodes (prompts/labels/closing_messages verbatim from the reference
# platform's script; graph shape/schema is ours)
# ---------------------------------------------------------------------------

NODES: list[WorkflowNode] = [
    WorkflowNode(id="start", position={"x": 400, "y": 0}, data=WorkflowNodeData(
        kind="start", label="Start — opening line",
        first_message="Hello, kya aap {{mis.business_name}} se baat kar rahe hain?",
    )),

    WorkflowNode(id="fn-vendor-lookup", position={"x": 400, "y": 75}, data=WorkflowNodeData(
        kind="function", label="Fetch Vendor Details (MOCK — test data)",
        output_key="mis",
        function=WorkflowFunctionSpec(url=MOCK_MIS_API_URL, method="GET", body_format="json"),
    )),

    WorkflowNode(id="conv-1a", position={"x": 400, "y": 150}, data=WorkflowNodeData(
        kind="conversation", label="Stage 1A — Greeting + Identity",
        prompt=(
            "You just asked the caller to confirm they're connected with {{mis.business_name}}. "
            "Judge their reply: if they confirm (haan/ji/yes), call the 'confirmed' transition. "
            "If they deny it, say it's a wrong number, or seem confused about {{mis.business_name}}, "
            "call the 'wrong_number' transition. Don't ask anything else in this step."
        ),
        transitions=[
            _transition("t-1a-confirmed", "confirmed", "Confirmed identity",
                        "Caller confirms they are connected with {{mis.business_name}}"),
            _transition("t-1a-wrong", "wrong_number", "Wrong number / denies",
                        "Caller denies being from {{mis.business_name}}, or it's a wrong number"),
        ],
    )),
    WorkflowNode(id="end-wrong-number", position={"x": 780, "y": 150}, data=WorkflowNodeData(
        kind="end_call", label="End — wrong number",
        closing_message="Oh, koi baat nahi. Maaf kijiyega, samay dene ke liye dhanyavaad!",
    )),

    WorkflowNode(id="conv-1b", position={"x": 400, "y": 320}, data=WorkflowNodeData(
        kind="conversation", label="Stage 1B — Owner + Decision Maker Check",
        prompt=(
            "Say: 'Main Ishita bol rahi hoon JustDial se. Kya meri baat {{mis.owner_name}} ji se ho "
            "rahi hai?' If they confirm being {{mis.owner_name}}, ask: 'Toh iss business ke decisions "
            "jaise ki advertisement ya promotion aap hi lete honge?' If they confirm they ARE the "
            "decision-maker for ads/promotion, call the 'decision_maker' transition.\n"
            "If they say they are NOT {{mis.owner_name}}, or are an employee who doesn't make these "
            "decisions, say: 'Theek hai, kya main jaan sakti hoon ki decisions kaun leta hai?' — if "
            "they offer a name/number for the real decision-maker, record it with set_variable, then "
            "call the 'not_decision_maker' transition either way."
        ),
        variables=[WorkflowVariableSpec(name="alt_contact_name", type="string", required=False,
                                         description="Name/number of the real decision-maker, if the current speaker offers one")],
        transitions=[
            _transition("t-1b-yes", "decision_maker", "Confirmed owner/decision-maker",
                        "Caller confirms being {{mis.owner_name}} and confirms they decide on ads/promotion"),
            _transition("t-1b-no", "not_decision_maker", "Not owner/decision-maker",
                        "Caller is not {{mis.owner_name}}, is an employee, or doesn't decide on ads/promotion"),
        ],
    )),
    WorkflowNode(id="end-not-decision-maker", position={"x": 780, "y": 320}, data=WorkflowNodeData(
        kind="end_call", label="End — not the decision-maker",
        closing_message="Theek hai, samay dene ke liye dhanyavaad. Hum decision-maker se sampark karne ki koshish karenge.",
    )),

    WorkflowNode(id="conv-2", position={"x": 400, "y": 490}, data=WorkflowNodeData(
        kind="conversation", label="Stage 2 — The Free Lead Hook",
        prompt=(
            "Say: 'Humne aapko WhatsApp par ek interested customer ki free enquiry bheji thi "
            "{{mis.business_category}} se related. Kya aap chahenge ki aisi genuine enquiries aapko "
            "regularly milti rahein?'\n"
            "- If they agree/acknowledge positively: move on.\n"
            "- If NOT interested or mention a bad experience: say 'I understand, par pichle mahine "
            "aapke area mein {{mis.category_searches}} se zyada customer enquiries aayi hain. Hum nahi "
            "chahte ki aap genuine business opportunities miss karein. Just check karne mein kya "
            "burai hai?' and continue once they acknowledge.\n"
            "- If they say they never received the WhatsApp message: say 'Koi baat nahi "
            "{{mis.owner_name}} ji — kabhi kabhi messages filter ho jaate hain. Hum aapko abhi dobara "
            "bhej dete hain. Par uss enquiry se bhi zyada important baat yeh hai —' and continue.\n"
            "As soon as the vendor acknowledges via ANY of these paths, call the 'acknowledged' "
            "transition — it's the only way forward from this step."
        ),
        transitions=[_transition("t-2-ack", "acknowledged", "Vendor acknowledges",
                                  "Vendor acknowledges interest in receiving leads, after any needed rebuttal")],
    )),

    WorkflowNode(id="conv-3", position={"x": 400, "y": 660}, data=WorkflowNodeData(
        kind="conversation", label="Stage 3 — Competitor Proof + Urgency",
        prompt=(
            "Say: 'Dekhiye, pichle mahine aapke area mein {{mis.business_category}} ki "
            "{{mis.category_searches}} se zyada enquiries aayi thin. Yeh saari enquiries aapke "
            "competitors jaise {{mis.competitor_name_1}} aur {{mis.competitor_name_2}} ko ja rahi hain "
            "kyunki woh JustDial se jude hain. Aap jude nahi hain toh yeh leads aapke paas nahi aa "
            "rahi. Aap chahenge ki aapko bhi aisi leads milne lagein?' Once the vendor acknowledges "
            "or agrees, call the 'acknowledged' transition."
        ),
        transitions=[_transition("t-3-ack", "acknowledged", "Vendor acknowledges",
                                  "Vendor acknowledges wanting similar leads")],
    )),

    WorkflowNode(id="conv-3-1", position={"x": 400, "y": 830}, data=WorkflowNodeData(
        kind="conversation", label="Stage 3.1 — Verification + Free Visit",
        prompt=(
            "Say: 'Hamare Marketing Manager aaj aapke area mein hi visit kar rahe hain. Woh aakar "
            "aapki JustDial profile verify kar lenge aur aapke business ke photos, location, timing "
            "sab update karenge. Yeh visit bilkul free hai, koi charge nahi.' After saying this line, "
            "call the 'continue' transition."
        ),
        transitions=[_transition("t-31-continue", "continue", "Continue to pricing",
                                  "After delivering the free-visit line")],
    )),

    WorkflowNode(id="conv-3-2", position={"x": 400, "y": 1000}, data=WorkflowNodeData(
        kind="conversation", label="Stage 3.2 — Price + Appointment",
        prompt=(
            "Say: 'Iske baad agar aap membership lena chahein, toh cost sirf ₹133 per day hai — "
            "yaani ₹4000 mahina. Toh kya main aaj aapke liye ek free meeting book kar doon?'\n"
            "- If they agree: ask 'Aap kis time free hain aaj?' and record their answer with "
            "set_variable('appointment_time', ...).\n"
            "- If they hesitate / say they have no time: say 'Sir, sirf 20 minute lagenge aur aaj "
            "aapki category ke liye special discount coupons bhi hain jo limited hain,' pitch the "
            "next available slot, then record the time they accept.\n"
            "- If they say it's expensive: say 'Bilkul samajh sakti hoon — isliye hi manager free "
            "demo denge taaki aap khud dekh sakein JustDial kaise kaam karta hai. Abhi sirf meeting "
            "book ho rahi hai, koi payment nahi. Aaj ka time fix kar lein?' then record the time.\n"
            "As soon as a specific time is agreed AND recorded via set_variable, call the "
            "'time_agreed' transition."
        ),
        variables=[WorkflowVariableSpec(name="appointment_time", type="string", required=True,
                                         description="The time today the vendor agreed to for the free meeting/visit")],
        transitions=[_transition("t-32-agreed", "time_agreed", "Appointment time agreed",
                                  "Vendor has agreed to and stated a specific time for the free meeting")],
    )),

    # --- NEW: a real calendar-booking webhook, rather than just recording a string ---
    WorkflowNode(id="fn-book-appointment", position={"x": 400, "y": 1085}, data=WorkflowNodeData(
        kind="function", label="Book appointment (real webhook — new)",
        output_key="calendar_booking",
        function=WorkflowFunctionSpec(
            url=MOCK_CALENDAR_BOOK_URL, method="POST", body_format="json",
            custom_body='{"business_name": "{{mis.business_name}}", "requested_time": "{{appointment_time}}"}',
        ),
    )),
    WorkflowNode(id="booking-check", position={"x": 400, "y": 1130}, data=WorkflowNodeData(
        kind="condition", label="Booking confirmed?",
        conditions=[
            WorkflowConditionSpec(id="booked", path="calendar_booking.status", op="eq", value="confirmed"),
            WorkflowConditionSpec(id="booking-failed", is_fallback=True),
        ],
    )),
    WorkflowNode(id="end-booking-issue", position={"x": 780, "y": 1130}, data=WorkflowNodeData(
        kind="end_call", label="End — booking system unavailable",
        closing_message=(
            "Ek chhoti si dikkat aa rahi hai booking system mein — koi baat nahi, hamara manager aapko "
            "thodi der mein call karke {{appointment_time}} ke aas-paas ka time confirm kar lenge. "
            "Dhanyavaad!"
        ),
    )),

    WorkflowNode(id="conv-4", position={"x": 400, "y": 1170}, data=WorkflowNodeData(
        kind="conversation", label="Stage 4 — Closing & Hot Transfer (ask 1)",
        prompt=(
            "Say: 'Done! Ab main aapki call apne manager ko transfer karti hoon. Aaj limited time "
            "ke liye discounts available hain. Woh aapke details ek baar verify kar denge aur "
            "discount coupon bhi apply kar denge. Isme bas 2 minute lagega. Theek hai?' "
            "If the vendor agrees, call 'agrees'. If they decline or hesitate, call 'declines'."
        ),
        transitions=[
            _transition("t-4-agree", "agrees", "Agrees to transfer", "Vendor agrees to be transferred"),
            _transition("t-4-decline", "declines", "Declines transfer", "Vendor declines or hesitates"),
        ],
    )),
    WorkflowNode(id="conv-4-retry1", position={"x": 400, "y": 1340}, data=WorkflowNodeData(
        kind="conversation", label="Stage 4 — Closing & Hot Transfer (retry 1)",
        prompt=(
            "Say: 'Bilkul samajh sakti hoon — bas 2 minute ki baat hai. Manager sirf address aur "
            "time confirm karenge taaki aapka discount coupon apply ho sake.' Then ask again if "
            "they're okay with the transfer. If they agree, call 'agrees'. If they still decline, "
            "call 'declines'."
        ),
        transitions=[
            _transition("t-4r1-agree", "agrees", "Agrees to transfer", "Vendor now agrees to be transferred"),
            _transition("t-4r1-decline", "declines", "Still declines", "Vendor still declines"),
        ],
    )),
    WorkflowNode(id="conv-4-retry2", position={"x": 400, "y": 1510}, data=WorkflowNodeData(
        kind="conversation", label="Stage 4 — Closing & Hot Transfer (retry 2)",
        prompt=(
            "Say: 'Ek aakhri baat batati hoon — hum aapko ek aur free lead {{mis.business_category}} "
            "se related jald hi bhej sakte hain jab Marketing Manager aapke office visit karein toh. "
            "Bas address aur time confirm karna hai, kya main aapki call transfer karu?' If they "
            "agree, call 'agrees'. If they still decline, call 'declines'."
        ),
        transitions=[
            _transition("t-4r2-agree", "agrees", "Agrees to transfer", "Vendor now agrees to be transferred"),
            _transition("t-4r2-decline", "declines", "Still declines", "Vendor still declines"),
        ],
    )),
    WorkflowNode(id="conv-4-callback", position={"x": 400, "y": 1680}, data=WorkflowNodeData(
        kind="conversation", label="Stage 4 — Callback fallback",
        prompt=(
            "Say: 'Koi baat nahi — main aapko kal ek baar call karti hoon. Aapki meeting abhi bhi "
            "confirmed hai.' If the vendor gives a preferred callback day/time, record it with "
            "set_variable('callback_time', ...). Then call the 'done' transition."
        ),
        variables=[WorkflowVariableSpec(name="callback_time", type="string", required=False,
                                         description="When to call the vendor back, if they specify")],
        transitions=[_transition("t-4cb-done", "done", "Callback noted", "After confirming the callback plan")],
    )),

    WorkflowNode(id="end-transfer", position={"x": 780, "y": 1170}, data=WorkflowNodeData(
        kind="end_call", label="End — transferred to manager",
        closing_message="Ek moment, aapki call abhi humare manager ko transfer ho rahi hai. Dhanyavaad!",
    )),
    WorkflowNode(id="end-callback", position={"x": 400, "y": 1850}, data=WorkflowNodeData(
        kind="end_call", label="End — callback scheduled",
        closing_message="Dhanyavaad, hum kal aapse sampark karenge. Aapki meeting confirmed hai.",
    )),
]

EDGES: list[WorkflowEdge] = [
    WorkflowEdge(id="e-start", source="start", target="fn-vendor-lookup"),
    WorkflowEdge(id="e-vendor-lookup", source="fn-vendor-lookup", target="conv-1a"),
    WorkflowEdge(id="e-1a-confirmed", source="conv-1a", target="conv-1b", sourceHandle="t-1a-confirmed"),
    WorkflowEdge(id="e-1a-wrong", source="conv-1a", target="end-wrong-number", sourceHandle="t-1a-wrong"),
    WorkflowEdge(id="e-1b-yes", source="conv-1b", target="conv-2", sourceHandle="t-1b-yes"),
    WorkflowEdge(id="e-1b-no", source="conv-1b", target="end-not-decision-maker", sourceHandle="t-1b-no"),
    WorkflowEdge(id="e-2-ack", source="conv-2", target="conv-3", sourceHandle="t-2-ack"),
    WorkflowEdge(id="e-3-ack", source="conv-3", target="conv-3-1", sourceHandle="t-3-ack"),
    WorkflowEdge(id="e-31-continue", source="conv-3-1", target="conv-3-2", sourceHandle="t-31-continue"),
    # was conv-3-2 -> conv-4 directly in the reference script; now routes through the
    # new real calendar-booking webhook + condition branch first.
    WorkflowEdge(id="e-32-agreed", source="conv-3-2", target="fn-book-appointment", sourceHandle="t-32-agreed"),
    WorkflowEdge(id="e-book-check", source="fn-book-appointment", target="booking-check"),
    WorkflowEdge(id="e-check-booked", source="booking-check", target="conv-4", sourceHandle="booked"),
    WorkflowEdge(id="e-check-failed", source="booking-check", target="end-booking-issue", sourceHandle="booking-failed"),
    WorkflowEdge(id="e-4-agree", source="conv-4", target="end-transfer", sourceHandle="t-4-agree"),
    WorkflowEdge(id="e-4-decline", source="conv-4", target="conv-4-retry1", sourceHandle="t-4-decline"),
    WorkflowEdge(id="e-4r1-agree", source="conv-4-retry1", target="end-transfer", sourceHandle="t-4r1-agree"),
    WorkflowEdge(id="e-4r1-decline", source="conv-4-retry1", target="conv-4-retry2", sourceHandle="t-4r1-decline"),
    WorkflowEdge(id="e-4r2-agree", source="conv-4-retry2", target="end-transfer", sourceHandle="t-4r2-agree"),
    WorkflowEdge(id="e-4r2-decline", source="conv-4-retry2", target="conv-4-callback", sourceHandle="t-4r2-decline"),
    WorkflowEdge(id="e-4cb-done", source="conv-4-callback", target="end-callback", sourceHandle="t-4cb-done"),
]

GLOBAL_PROMPT = (
    "You are Ishita, an outbound telecaller for JustDial. You are calling a business (vendor) to pitch "
    "JustDial's paid listing membership and book a free manager visit. Speak natural, warm, persistent "
    "Hinglish — like a real telecaller, never robotic or formal. Keep each turn short (1-2 sentences). "
    "Follow this call's stages in order, but adapt phrasing naturally to how the vendor actually responds — "
    "don't recite lines verbatim if the vendor has already answered part of it."
)

DESCRIPTION = (
    "Outbound vendor-outreach flow built from Revised_Script_30062024.pdf: confirms identity and "
    "decision-maker, pitches free leads, builds competitor-FOMO urgency, books a free manager visit via "
    "a real calendar webhook, then hands off. A function node right after Start fetches vendor-context "
    "fields ({{mis.business_name}}, {{mis.owner_name}}, {{mis.business_category}}, "
    "{{mis.category_searches}}, {{mis.competitor_name_1}}, {{mis.competitor_name_2}}) — currently our "
    "own MOCK test endpoint (backend/routers/mock_appointment_data.py), swap for a real MIS/CRM lookup "
    "before going live. 'Transfer' only announces the handoff and ends the call — there is no live SIP "
    "transfer."
)


def build_appointment_bot_config() -> BotConfig:
    return BotConfig(
        agent_name="Ishita",
        bot_type="workflow",
        workflow=WorkflowGraphDef(nodes=NODES, edges=EDGES),
        global_prompt=GLOBAL_PROMPT,
        tts_provider="sarvam",
        tts_voice="simran",
        temperature=0.5,
        max_call_duration=480,
        interruption_sensitivity="balanced",
    )


def seed_appointment_workflow_bot(owner: str = "seed-script") -> dict:
    """Idempotent: re-running updates the existing bot's draft version in place
    (matched by name), rather than creating duplicates on every run."""
    now = datetime.now(timezone.utc).isoformat()
    config = build_appointment_bot_config()

    existing_bot = bots.find_one({"name": BOT_NAME, "status": {"$ne": "deleted"}})
    if existing_bot:
        bot_id = existing_bot["_id"]
        version_id = existing_bot.get("draft_version_id")
        version_doc = {
            "bot_id": bot_id,
            "version": 1,
            "state": "draft",
            "config": config.model_dump(),
            "notes": "Re-seeded appointment-flow demo bot",
            "created_at": now,
        }
        if version_id:
            bot_versions.update_one({"_id": ObjectId(version_id)}, {"$set": version_doc})
        else:
            version_id = str(bot_versions.insert_one(version_doc).inserted_id)
            bots.update_one({"_id": bot_id}, {"$set": {"draft_version_id": version_id}})
        bots.update_one({"_id": bot_id}, {"$set": {"updated_at": now, "description": "Demo bot for the appointment-flow live showcase — books a manager visit via a real calendar webhook."}})
        return {"bot_id": str(bot_id), "version_id": str(version_id), "action": "updated"}

    bot_doc = {
        "name": BOT_NAME,
        "description": "Demo bot for the appointment-flow live showcase — books a manager visit via a real calendar webhook.",
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
        "notes": "Initial appointment-flow demo seed",
        "created_at": now,
    }
    version_id = bot_versions.insert_one(version_doc).inserted_id

    bots.update_one({"_id": bot_id}, {"$set": {"draft_version_id": str(version_id)}})

    return {"bot_id": str(bot_id), "version_id": str(version_id), "action": "created"}


def main() -> None:
    result = seed_appointment_workflow_bot()
    print(f"[seed] {result['action']}: bot_id={result['bot_id']} test_bot_version_id={result['version_id']}")
    print("[seed] Use these two ids in a Test Call (POST /api/testcall/start) to run it live.")


if __name__ == "__main__":
    main()
