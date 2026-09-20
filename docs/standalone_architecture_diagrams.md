# Standalone Voice AI Platform Diagrams

This document is for frontend, backend, infra and operations experts joining the project. The no-code platform is a standalone app. The MIS dashboard and the current product orchestration server are external systems that this platform integrates with.

## Current Codebase Map

```mermaid
flowchart LR
    subgraph Repo["voicebot_nodcode_platform repo"]
        FE["React dashboard\nfrontend/"]
        API["FastAPI platform API\nvoicebot_platform/api.py"]
        Store["Mongo config store helpers\nvoicebot_platform/config_store.py"]
        Bot["LiveKit worker runtime\nbot.py"]
        Worker["Callback analysis worker\ncallback_worker/worker.py"]
        Docs["Ops and handoff docs\ndocs/"]
    end

    FE --> API
    API --> Store
    Bot --> Store
    Worker --> Store
    Docs -. guides .-> FE
    Docs -. guides .-> API
    Docs -. guides .-> Bot
```

What has already been implemented:

- `voicebot_platform/api.py`: standalone platform API for bots, versions, campaigns, transcripts, options and templates.
- `voicebot_platform/config_store.py`: Mongo-backed bot definitions, immutable versions, active config lookup, campaign records and transcript search.
- `bot.py`: fetches active published config by `assistant_id`, uses dynamic model/voice/language settings and saves `config_snapshot` per call.
- `callback_worker/worker.py`: emits callback success/failure observability events.
- `frontend/`: React dashboard scaffold with bot list, builder, campaigns, test call, transcripts and observability screens.
- `ops/docker-compose.observability.yml`: starting point for `livekit-monitor` and self-hosted Langfuse.

## System Context

```mermaid
flowchart TB
    PM["PMs"]
    Dev["Developers"]
    Platform["Standalone Voice AI Platform\nReact + FastAPI"]
    Mongo["MongoDB\nbot configs, versions, campaigns, transcripts"]
    Runtime["Voice Bot Agent Processes\nLiveKit worker using Gemini Live"]
    LiveKit["On-prem LiveKit Server\nsingle persistent server"]
    Dialer["In-house SIP Dialer\nexternal orchestration server"]
    MIS["MIS Dashboard / Lead APIs\nexternal system"]
    Gemini["Google Gemini Live\nspeech-to-speech"]
    Sarvam["Sarvam STT\nfallback only"]
    Callback["Existing callback endpoint\nlead outcome update"]
    Langfuse["Self-hosted Langfuse\ntraces and latency"]
    Monitor["livekit-monitor\nrooms and participants"]

    PM --> Platform
    Dev --> Platform
    Platform <--> Mongo
    Runtime --> Mongo
    Dialer --> LiveKit
    LiveKit --> Runtime
    Runtime --> MIS
    Runtime --> Gemini
    Runtime --> Sarvam
    Runtime --> Mongo
    Runtime --> Callback
    Runtime --> Langfuse
    LiveKit --> Monitor
```

Key boundary:

- The standalone platform owns bot configuration, versioning, campaign mapping, transcript browsing and observability views.
- The MIS dashboard remains separate and continues to provide lead/customer/product/question data through APIs.
- The current orchestration/dialer server remains separate and is responsible for starting outbound calls and passing LiveKit room metadata.

## Publish And Config Snapshot Flow

```mermaid
sequenceDiagram
    participant PM as PM / Developer
    participant FE as React Dashboard
    participant API as Platform API
    participant DB as MongoDB
    participant Bot as LiveKit Agent Process
    participant Call as Active Call

    PM->>FE: Edit prompt/settings
    FE->>API: POST /api/bots/{bot_id}/draft
    API->>DB: Insert draft bot_version
    PM->>FE: Publish
    FE->>API: POST /api/bots/{bot_id}/publish
    API->>DB: Mark version published and update active_version_id

    Note over Call,Bot: Existing live calls do not reload config

    Bot->>DB: Fetch active config once at room start
    DB-->>Bot: Published bot_version snapshot
    Bot->>Call: Run full call with that snapshot
    Bot->>DB: Save transcript with config_snapshot

    Note over PM,DB: New calls use the newly published version
```

This enforces the core safety rule: if a PM publishes while 50 calls are live, those 50 calls finish with their original prompt and settings.

## Outbound Call Data Flow

```mermaid
sequenceDiagram
    participant Orch as External Orchestration / Dialer Server
    participant LK as On-prem LiveKit
    participant Bot as Agent Process
    participant DB as MongoDB
    participant MIS as MIS Lead API
    participant Gemini as Gemini Live
    participant Sarvam as Sarvam fallback STT
    participant Callback as Callback API
    participant Langfuse as Langfuse

    Orch->>LK: Create outbound SIP room with metadata
    LK->>Bot: Dispatch room to agent process
    Bot->>DB: Fetch active published config by assistant_id
    Bot->>MIS: Fetch lead by lead_id or mobile
    Bot->>Gemini: Start speech-to-speech session
    Bot->>Langfuse: call_started
    Gemini-->>Bot: Agent speech and transcripts
    Bot->>Langfuse: first_audio_received / first_model_response

    alt Gemini misses final user audio
        Bot->>Sarvam: Fallback transcription
        Sarvam-->>Bot: User text
    end

    Bot->>DB: Save transcript + config_snapshot
    Bot->>Langfuse: transcript_saved / call_ended
    Callback->>DB: Worker polls untagged transcript
    Callback->>Callback: Send outcome payload to existing endpoint
```

Room metadata expected from external orchestration:

```json
{
  "assistant_id": "published bot assistant id",
  "campaign_id": "outbound campaign id",
  "lead_id": "lead id from MIS",
  "call_id": "orchestration call id",
  "mobile": "customer mobile",
  "srchterm": "fallback product/search term",
  "buyer_name": "fallback buyer name",
  "city": "fallback city"
}
```

## Mongo Collections

```mermaid
erDiagram
    BOT_DEFINITIONS ||--o{ BOT_VERSIONS : has
    BOT_DEFINITIONS ||--o{ CAMPAIGNS : runs
    BOT_VERSIONS ||--o{ CALL_TRANSCRIPTS : snapshots
    CAMPAIGNS ||--o{ CALL_TRANSCRIPTS : produces
    CALL_TRANSCRIPTS ||--o{ CALL_EVENTS : explains

    BOT_DEFINITIONS {
        objectId _id
        string assistant_id
        string name
        string status
        objectId active_version_id
        objectId draft_version_id
    }

    BOT_VERSIONS {
        objectId _id
        objectId bot_id
        int version
        string state
        object config
        date published_at
    }

    CAMPAIGNS {
        objectId _id
        string campaign_key
        string bot_id
        object lead_api
        string status
    }

    CALL_TRANSCRIPTS {
        objectId _id
        string bot_id
        string bot_version_id
        string campaign_id
        string assistant_id
        string lead_id
        string call_id
        object config_snapshot
        array transcript
    }

    CALL_EVENTS {
        objectId _id
        string call_id
        string room_name
        string event_type
        string severity
        string message
        object details
        date created_at
    }
```

Important implementation note:

- `bot_versions.config` is the editable/publishable runtime config.
- `tbl_ai_vb_call_transcripts.config_snapshot` is immutable evidence of what the bot used during that call.
- `tbl_ai_vb_call_events` stores the technical call timeline for debugging Gemini, Sarvam, LiveKit, transcript-save, and callback issues.

## Frontend Handoff

Frontend assistance is needed in these areas:

- Replace the current JSON config editor with structured controls for prompt, initial message, closing text, voice, language, model, temperature, VAD, timeout, Sarvam fallback thresholds and tool/API settings.
- Add real transcript filters and a transcript detail page using `GET /api/transcripts` and `GET /api/transcripts/{transcript_id}`.
- Add campaign screens for mapping outbound campaigns to bot versions and existing lead APIs.
- Add a test-call screen that displays the generated LiveKit room metadata and, later, starts a controlled call through the external orchestration server.
- Integrate VoiceDesk SSO and role-aware UI once security provides role group names.
- Add Langfuse trace links and LiveKit room status once infra provides endpoints.

Frontend information needed:

- Design system or visual reference for internal tools.
- SSO integration method and headers/tokens expected by backend.
- Which fields PMs can edit and which are admin/developer-only.
- Exact list of voices and languages approved for business users.
- Whether this app should later be embedded in the master dashboard shell or remain permanently standalone.

## Backend Handoff

Backend assistance is needed in these areas:

- Harden the FastAPI API with Pydantic request/response models and role enforcement.
- Confirm and validate the existing MIS lead API shape for buyer name, mobile, product, city, qualification schema and call id.
- Add direct test-call dispatch only if this standalone platform should call the external orchestration server.
- Add campaign validation around lead API authentication, rate limits and retry behavior.
- Decide whether config changes need approval workflow before publish.
- Expand Langfuse traces with exact Gemini timing, function-call latency, callback latency and error classification.

Backend information needed:

- MongoDB production database and collection naming policy.
- Existing lead API URLs, auth method, request params, response examples and rate limits.
- Existing callback API contract and expected payload changes, if any.
- External orchestration endpoint for starting outbound calls, if the platform should trigger calls.
- LiveKit API URL/key handling and how room metadata is created today.
- SSO group names for PM, Developer, Admin and Viewer.

## Infra And Ops Handoff

Infra assistance is needed in these areas:

- Rotate the SIP credentials shared in chat before production rollout.
- Deploy the standalone platform API and React app on-prem.
- Deploy self-hosted Langfuse and provide `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`.
- Deploy `jossephus/livekit-monitor` and register its webhook with the on-prem LiveKit server.
- Confirm safe concurrency limits for multiple agent processes per bot on the single persistent LiveKit server.
- Define alerts for no greeting, high Time to First Word, Gemini failures, callback failures and LiveKit room failures.

## What To Build Next

```mermaid
flowchart LR
    A["Seed default bot"] --> B["Run standalone API"]
    B --> C["Frontend structured builder"]
    C --> D["Campaign mapping"]
    D --> E["External orchestration test call"]
    E --> F["Transcript detail + Langfuse links"]
    F --> G["Role enforcement + production rollout"]
```

Recommended order:

1. Backend developer confirms MIS and orchestration contracts.
2. Frontend developer replaces JSON editor with forms.
3. Infra deploys Langfuse and LiveKit monitor in staging.
4. Team runs a controlled outbound test campaign.
5. Add SSO/roles before broad PM access.
