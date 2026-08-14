#!/usr/bin/env python3
"""Seed a demo Workflow Builder bot ("Post-Call Analysis Feature Demo") that exercises
every `AnalysisFieldDef` type (boolean, text, number, enum) so the generic extractor in
backend/post_call_analysis.py has something concrete to run against — a short single-node
customer-satisfaction survey flow, not meant to be a realistic production bot.

Run:  cd backend && ../.venv/bin/python3 -m seed_post_call_analysis_demo_bot
(or:  uv run python -m backend.seed_post_call_analysis_demo_bot   from repo root)
"""

from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId

from .db import bots, bot_versions
from .models import (
    AnalysisFieldDef,
    BotConfig,
    WorkflowEdge,
    WorkflowGraphDef,
    WorkflowNode,
    WorkflowNodeData,
)

BOT_NAME = "Post-Call Analysis Feature Demo"

NODES: list[WorkflowNode] = [
    WorkflowNode(id="start", position={"x": 400, "y": 0}, data=WorkflowNodeData(
        kind="start", label="Start — opening line",
        first_message="Hi, this is a quick survey call about your recent support ticket. Do you have two minutes?",
    )),
    WorkflowNode(id="conv-survey", position={"x": 400, "y": 150}, data=WorkflowNodeData(
        kind="conversation", label="Survey — satisfaction, issue, follow-up",
        prompt=(
            "Ask the caller: (1) whether their issue was resolved, (2) to describe the issue in their "
            "own words, (3) to rate their satisfaction from 1 to 5, and (4) whether they'd like a "
            "follow-up call. Keep it conversational, one question at a time. Once you've asked all "
            "four, thank them and call the 'done' transition."
        ),
        transitions=[
            {"id": "t-survey-done", "key": "done", "label": "Survey complete", "condition": "All four survey questions have been asked and answered"},
        ],
    )),
    WorkflowNode(id="end-survey", position={"x": 400, "y": 300}, data=WorkflowNodeData(
        kind="end_call", label="End — survey complete",
        closing_message="Thanks so much for your time today — have a great day!",
    )),
]

EDGES: list[WorkflowEdge] = [
    WorkflowEdge(id="e-start", source="start", target="conv-survey"),
    WorkflowEdge(id="e-survey-done", source="conv-survey", target="end-survey", sourceHandle="t-survey-done"),
]

GLOBAL_PROMPT = (
    "You are a friendly customer-support follow-up caller running a brief post-ticket satisfaction "
    "survey. Speak naturally, keep turns short, and don't rush the caller."
)

DESCRIPTION = (
    "Demo bot for the Post-Call Analysis feature: a short satisfaction-survey flow whose transcript "
    "gives the generic extractor (backend/post_call_analysis.py) real material for all four field "
    "types below — issue_resolved (boolean), issue_summary (text), satisfaction_score (number), and "
    "follow_up_preference (enum)."
)

ANALYSIS_FIELDS: list[AnalysisFieldDef] = [
    AnalysisFieldDef(
        key="issue_resolved",
        label="Issue resolved?",
        type="boolean",
        description="True if the caller confirms their original support ticket/issue was resolved.",
    ),
    AnalysisFieldDef(
        key="issue_summary",
        label="Issue summary",
        type="text",
        description="One or two sentence summary, in the caller's own words, of what their issue was.",
    ),
    AnalysisFieldDef(
        key="satisfaction_score",
        label="Satisfaction score (1-5)",
        type="number",
        description="The satisfaction rating the caller gave, from 1 (very dissatisfied) to 5 (very satisfied).",
    ),
    AnalysisFieldDef(
        key="follow_up_preference",
        label="Follow-up preference",
        type="enum",
        description="Whether the caller wants a follow-up call.",
        enum_options=["Yes", "No", "Undecided"],
    ),
]


def build_demo_bot_config() -> BotConfig:
    return BotConfig(
        agent_name="Survey Bot",
        bot_type="workflow",
        workflow=WorkflowGraphDef(nodes=NODES, edges=EDGES),
        global_prompt=GLOBAL_PROMPT,
        analysis_fields=ANALYSIS_FIELDS,
        tts_provider="sarvam",
        tts_voice="simran",
        temperature=0.5,
        max_call_duration=300,
        interruption_sensitivity="balanced",
    )


def seed_post_call_analysis_demo_bot(owner: str = "seed-script") -> dict:
    """Idempotent: re-running updates the existing bot's draft version in place
    (matched by name), rather than creating duplicates on every run."""
    now = datetime.now(timezone.utc).isoformat()
    config = build_demo_bot_config()

    existing_bot = bots.find_one({"name": BOT_NAME, "status": {"$ne": "deleted"}})
    if existing_bot:
        bot_id = existing_bot["_id"]
        version_id = existing_bot.get("draft_version_id")
        version_doc = {
            "bot_id": bot_id,
            "version": 1,
            "state": "draft",
            "config": config.model_dump(),
            "notes": "Re-seeded post-call-analysis demo bot",
            "created_at": now,
        }
        if version_id:
            bot_versions.update_one({"_id": ObjectId(version_id)}, {"$set": version_doc})
        else:
            version_id = str(bot_versions.insert_one(version_doc).inserted_id)
            bots.update_one({"_id": bot_id}, {"$set": {"draft_version_id": version_id}})
        bots.update_one({"_id": bot_id}, {"$set": {"updated_at": now, "description": DESCRIPTION}})
        return {"bot_id": str(bot_id), "version_id": str(version_id), "action": "updated"}

    bot_doc = {
        "name": BOT_NAME,
        "description": DESCRIPTION,
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
        "notes": "Initial post-call-analysis demo seed",
        "created_at": now,
    }
    version_id = bot_versions.insert_one(version_doc).inserted_id

    bots.update_one({"_id": bot_id}, {"$set": {"draft_version_id": str(version_id)}})

    return {"bot_id": str(bot_id), "version_id": str(version_id), "action": "created"}


def main() -> None:
    result = seed_post_call_analysis_demo_bot()
    print(f"[seed] {result['action']}: bot_id={result['bot_id']} version_id={result['version_id']}")
    print("[seed] Open this bot in the Builder UI → Post-Call Analysis tab to see the 4 field types,")
    print("[seed] or POST /api/testcall/start with these ids to run it live and see analysis_fields_result.")


if __name__ == "__main__":
    main()
