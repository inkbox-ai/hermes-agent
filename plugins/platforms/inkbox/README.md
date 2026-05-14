# Inkbox Platform Plugin

[Inkbox](https://inkbox.ai) is API-first communication infrastructure for AI agents — a stable email address, phone number, and persistent contact list scoped to a single agent identity.

This plugin routes the three Inkbox modalities — **inbound email**, **inbound SMS**, and **live voice calls** — into a single contact-keyed Hermes session per remote party. Email + SMS + voice from the same human all land in the same conversation thread.

## Install

```bash
pip install 'hermes-agent[inkbox]'
```

## Configure

Set the required env vars in `~/.hermes/.env` (or run `hermes config`):

| Variable | Required | Description |
|---|---|---|
| `INKBOX_API_KEY` | yes | Admin or agent-scoped key from [inkbox.ai/console/api-keys](https://inkbox.ai/console/api-keys) |
| `INKBOX_IDENTITY` | yes | The agent's Inkbox identity handle |
| `INKBOX_SIGNING_KEY` | recommended | HMAC secret for verifying inbound webhooks |
| `INKBOX_PUBLIC_URL` | no | Set if you have a public URL; otherwise the SDK opens a tunnel automatically |
| `INKBOX_ALLOWED_USERS` | no | Comma-separated allowlist of emails / phones / contact ids |
| `INKBOX_HOME_CHANNEL` | no | Default contact for cron / broadcast delivery |

Or interactively:

```bash
hermes gateway setup
```

## Architecture

```
Caller / Sender ──► Inkbox edge ──► tunnel (or your public URL)
                                            │
                                            ▼
                              local aiohttp on :8765
                                  ├─ POST /webhook       (email / SMS / call events)
                                  └─ WS   /phone/media/ws (live voice transcripts ↔ TTS)
                                            │
                                            ▼
                                     Hermes gateway runner
```

On `connect()` the adapter:

1. Provisions (or reuses) an Inkbox edge tunnel — the SDK opens an outbound WS to Inkbox and bridges RFC-6455 frames to a local aiohttp server. Skipped if `INKBOX_PUBLIC_URL` is set.
2. PATCHes every mailbox + phone on the identity so their webhook URLs point at the tunnel.
3. Starts the aiohttp server with two routes — `POST /webhook` (HMAC-verified) and `WS /phone/media/ws` (live call media bridge).

## Session keys

Every inbound event maps to `chat_id = contact_id`, so one Hermes session spans email + SMS + voice for the same remote party:

```
inbound mail   → chat_id=contact_id, thread_id=f"email:{tid}"
inbound SMS    → chat_id=contact_id, thread_id=None
inbound call   → chat_id=contact_id, thread_id=f"call:{call_id}"
outbound call  → chat_id=contact_id, thread_id=None   # joins the contact's main session
```

Unknown senders (lookup miss) still get a session — just keyed by the raw email/phone instead of a merged contact id.

## Outbound

`send()` is mode-aware via `metadata['mode']`:

- `email` → `identity.send_email(to=..., subject=..., body_text=...)`
- `sms` → `identity.send_text(to=..., text=...)`
- `voice` → push a `text` frame onto the contact's active call WS (Inkbox handles TTS)

Streaming agent replies (gateway calls `edit_message()` repeatedly) forward voice deltas as incremental `text` events; email + SMS edits are no-ops.

## See also

- [Inkbox docs](https://inkbox.ai/docs)
- [User guide page](../../../website/docs/user-guide/messaging/inkbox.md)
- [SKILL.md](skills/SKILL.md) — runtime guidance for the agent
