---
sidebar_position: 18
title: "Inkbox"
description: "Give your agent a real email address, phone number, and persistent contact list via Inkbox"
---

# Inkbox Setup

Run Hermes Agent on top of [Inkbox](https://inkbox.ai) — API-first communication infrastructure that gives an AI agent a stable email address, phone number, and persistent contact list scoped to a single agent identity. The adapter lives as a bundled platform plugin under `plugins/platforms/inkbox/` — no core edits, just enable it like any other platform.

Inbound email, inbound SMS, and live voice calls all route to **one session per contact**, so email, SMS, and voice from the same human all land in the same conversation thread.

## How the bot responds

| Modality | Behavior |
|----------|----------|
| **Email** | Inbound mail spawns or resumes the contact's session; replies thread by `In-Reply-To` |
| **SMS** | Inbound texts join the contact's session; outbound capped at 1600 chars per send |
| **Voice call** | Inbound calls open a media WebSocket — transcripts arrive as `MessageEvent`s, replies stream as `text` frames for Inkbox-managed TTS |

Outbound `send()` is mode-aware via `metadata['mode']` — `email` / `sms` / `voice`. If unset, the adapter picks: SMS for raw E.164 chat ids, email otherwise.

---

## Step 1: Get an Inkbox account

1. Sign up at [inkbox.ai/console](https://inkbox.ai/console).
2. Create an agent **identity** — this is the handle that owns the mailbox + phone.
3. Provision a mailbox (auto-created with the identity) and a phone number (toll-free or local).
4. Generate an **API key** under [API Keys](https://inkbox.ai/console/api-keys).
5. Create a **webhook signing key** — used to HMAC-verify inbound webhooks.

---

## Step 2: Install the SDK

```bash
pip install 'hermes-agent[inkbox]'
```

This pulls the `inkbox` Python SDK and `aiohttp` (used for the local webhook server + WS bridge).

---

## Step 3: Configure Hermes

Add to `~/.hermes/.env`:

```env
INKBOX_API_KEY=ApiKey_...
INKBOX_IDENTITY=your-agent-handle
INKBOX_SIGNING_KEY=whsec_...

# Allowlist — at least one of these (or INKBOX_ALLOW_ALL_USERS=true for dev)
INKBOX_ALLOWED_USERS=alex@example.com,+15555550101,contact-uuid-...

# Optional — default contact for cron / broadcast delivery
INKBOX_HOME_CHANNEL=contact-uuid-home
```

Then in `~/.hermes/config.yaml`:

```yaml
gateway:
  platforms:
    inkbox:
      enabled: true
```

That's enough — the bundled-plugin scan in `gateway/config.py` picks up `plugins/platforms/inkbox/` automatically. No `Platform.INKBOX` enum edit, no `_create_adapter` registration.

Or run the interactive wizard:

```bash
hermes gateway setup
```

---

## Step 4: Expose the webhook port

Inkbox delivers webhooks over public HTTPS. Two options:

### Option A — SDK tunnel (default, recommended)

Leave `INKBOX_PUBLIC_URL` unset. On `connect()`, the adapter opens an outbound WebSocket to Inkbox's tunnel service and bridges RFC-6455 frames to the local aiohttp server. No port forwarding, no third-party tunnel.

The tunnel name is derived from your identity handle by default; override with `INKBOX_TUNNEL_NAME` to make it stable across restarts.

### Option B — Your own public URL

If you already terminate HTTPS publicly (Cloudflare Tunnel, a load balancer, etc.), set:

```env
INKBOX_PUBLIC_URL=https://my-agent.example.com
INKBOX_LISTEN_PORT=8765   # default; change if 8765 is taken
```

The adapter binds the local server to `INKBOX_HOST:INKBOX_LISTEN_PORT` and tells Inkbox to POST webhooks to `INKBOX_PUBLIC_URL/webhook`.

---

## Step 5: Start the gateway

```bash
hermes gateway start
```

The adapter:

1. Provisions (or reuses) the tunnel, or registers the public URL.
2. PATCHes every mailbox + phone on the identity so their webhook URLs point at it.
3. Starts the aiohttp server with `POST /webhook` (HMAC-verified) and `WS /phone/media/ws` (live call media bridge).

Send the agent an email or text from an allowlisted address — the inbound event spawns a session keyed by `contact_id`.

---

## Session keys

Every inbound event maps to `chat_id = contact_id`, so one Hermes session spans email + SMS + voice for the same remote party:

```
inbound mail   → chat_id=contact_id, thread_id=f"email:{tid}"
inbound SMS    → chat_id=contact_id, thread_id=None
inbound call   → chat_id=contact_id, thread_id=f"call:{call_id}"
outbound call  → chat_id=contact_id, thread_id=None   # joins the contact's main session
```

Unknown senders (lookup miss) still get a session — just keyed by the raw email / phone instead of a merged contact id.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `INKBOX_API_KEY` | yes | — | Admin or agent-scoped key |
| `INKBOX_IDENTITY` | yes | — | Agent identity handle |
| `INKBOX_SIGNING_KEY` | recommended | — | HMAC secret for verifying inbound webhooks |
| `INKBOX_REQUIRE_SIGNATURE` | no | `true` | Fail closed when signing key missing |
| `INKBOX_BASE_URL` | no | `https://inkbox.ai` | Inkbox API endpoint |
| `INKBOX_HOST` | no | `0.0.0.0` | Local webhook server bind |
| `INKBOX_LISTEN_PORT` | no | `8765` | Local webhook server port |
| `INKBOX_PUBLIC_URL` | no | — | Public webhook URL (bypass SDK tunnel) |
| `INKBOX_TUNNEL_NAME` | no | derived from identity | Stable tunnel name for reuse |
| `INKBOX_ALLOWED_USERS` | no | — | Comma-separated allowlist (emails / phones / contact ids) |
| `INKBOX_ALLOW_ALL_USERS` | no | `false` | Allow anyone reaching the address (dev only) |
| `INKBOX_HOME_CHANNEL` | no | — | Default contact for cron / broadcast delivery |
| `INKBOX_HOME_CHANNEL_NAME` | no | `Home` | Display name for the home channel |

---

## Cron delivery

Schedule a job to email or text the home channel:

```bash
hermes cron add "0 9 * * 1" "Send the weekly Monday digest" --deliver inkbox
```

The plugin registers a `standalone_sender_fn`, so cron jobs running in a separate process from the gateway can still deliver — the SDK opens an ephemeral client per send.

---

## Troubleshooting

**`401 Unauthorized` on every webhook** — your `INKBOX_SIGNING_KEY` doesn't match the one configured on the mailbox / phone in the Inkbox console. Rotate the key there, paste the new value, restart the gateway.

**`No live adapter for platform 'inkbox'`** when running `hermes cron run` — the plugin's `standalone_sender_fn` should handle this; if you see it, you're on a Hermes version that predates the plugin registry hook. Update.

**Tunnel won't connect** — wipe `~/.hermes/inkbox_tunnel/` to clear stale tunnel state and try again. The SDK persists tunnel id + connect secret there; if either drifts, the adapter recovers by rotating the secret on the next connect.

**Voice call drops mid-turn** — Inkbox closes the call WS if no `text` frame arrives within ~30s. If your agent takes longer to generate the first token, send a brief "one moment…" `text` frame to keep the connection warm.
