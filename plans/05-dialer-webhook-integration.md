# TSPL dialer webhook integration

## Background

We asked the dialer team (TSPL) for a webhook callback for all call statuses, including the
recording path, configurable at the Service ID level. Their reply:

> This requirement is feasible. We will require an API endpoint from your side. The same can
> be configured at the call completion event to send the required call details, including
> call status, recording path, and other relevant information. The API can be configured
> separately for each Service ID, if required.

So the endpoint is ours to build and hand them a URL for — see
`backend/routers/dialer_webhooks.py` for the scaffolded implementation
(`backend/models.py:DialerCallStatusWebhook`, `backend/dialer_webhooks.py`).

## Deployment model

The backend itself is hosted on an **internal IP**, not exposed directly to the public
internet. A **wrapper/reverse-proxy layer** in front of it accepts requests from the public
network and forwards them to us directly.

Implication: TSPL's webhook calls will arrive at us via that wrapper, not by hitting our
internal IP directly. Before handing TSPL a URL, we need:

1. **Sunny's approval to whitelist the Techinfo team's IP address** at the wrapper level, so
   TSPL's calls (proxied/originating from Techinfo's infra) are actually allowed through to
   reach our internal service.
2. The actual public hostname/path the wrapper exposes for this (not `localhost:8000` —
   that's dev-only). Needs to be confirmed once the wrapper config is in place.

**Action item:** get Sunny's sign-off on the Techinfo IP whitelist before this can go live
end-to-end.

## The endpoint

```
POST https://<public-host-via-wrapper>/api/dialer-webhooks/{service_id}/call-complete?secret=<per-service-secret>
```

- One URL per Service ID, each with its own secret in the query string — this is the auth,
  since TSPL's server can't hold one of our admin JWTs.
- Secrets are set via an authenticated admin endpoint (not exposed to TSPL):
  ```bash
  curl -X PUT https://<host>/api/dialer-webhooks/{service_id}/secret \
    -H "Authorization: Bearer <dashboard JWT>" \
    -H "Content-Type: application/json" \
    -d '{"secret": "<generated secret>"}'
  ```
  (`GET /api/dialer-webhooks/{service_id}/secret` returns whether one is configured, never
  the value itself — same write-only convention as `sip_password`.)

## Payload we expect (placeholder — confirm with TSPL)

```json
{
  "job_id": "optional — our own correlation ID, if we ever hand TSPL one",
  "call_id": "TSPL's call ID (preferred correlation key)",
  "phone_number": "+91XXXXXXXXXX",
  "status": "completed | failed | no_answer | busy | ...",
  "recording_url": "https://...",
  "duration_sec": 123
}
```

Field names and the status vocabulary are our best guess pending TSPL's real sample payload.
Push them toward echoing back a `call_id`/`job_id` we give them when the call is placed —
the phone-number-based fallback match (used when no ID is echoed back) assumes only one call
in flight per number at a time, which holds today but isn't bulletproof at scale.

## Open items

- [ ] Sunny's approval to whitelist Techinfo team's IP at the wrapper
- [ ] Actual public hostname/path from the wrapper layer (replaces the placeholder above)
- [ ] TSPL's real sample payload — adjust `DialerCallStatusWebhook` field names/status
      vocabulary once received
- [ ] Confirm whether TSPL can echo back our own `call_id`/`job_id`, or if we must rely on
      the phone-number fallback match long-term
