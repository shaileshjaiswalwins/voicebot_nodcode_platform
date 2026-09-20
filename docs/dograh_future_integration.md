# Dograh Future Integration

V1 stays prompt-and-settings based. To keep Dograh easy to add later, bot definitions now include an `orchestration` object:

```json
{
  "orchestration": {
    "mode": "prompt_settings",
    "flow_provider": null,
    "flow_id": null
  }
}
```

Later, when visual drag-and-drop flows are approved, the same field can point to Dograh:

```json
{
  "orchestration": {
    "mode": "visual_flow",
    "flow_provider": "dograh",
    "flow_id": "dograh-flow-id"
  }
}
```

Recommended future path:

1. Fork `dograh-hq/dograh`.
2. Keep this dashboard as the operational shell.
3. Embed or deep-link Dograh only inside the Builder screen.
4. Store the published Dograh flow id on the bot version/config snapshot.
5. Keep transcripts, campaigns, callbacks, and Langfuse tracing in this platform.

This prevents Dograh from owning the whole product. It becomes the visual flow editor, while this app remains the VoiceDesk control plane.
