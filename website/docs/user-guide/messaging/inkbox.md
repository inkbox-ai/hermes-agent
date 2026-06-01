---
sidebar_position: 2
sidebar_label: "Inkbox"
title: "Inkbox Messaging"
description: "Use Hermes Agent with Inkbox email, SMS/MMS, and voice"
---

# Inkbox Messaging

Inkbox gives one Hermes identity a mailbox, a phone number, and an edge tunnel. The Hermes Inkbox adapter maps inbound email, SMS/MMS, and live voice calls into normal gateway sessions.

## Protocol Model

Inkbox is a multi-channel platform adapter, not just an SMS adapter:

- `message.received` webhooks become email turns.
- Inbound `text.received` webhooks are grouped into SMS/MMS turns.
- Other `text.*` webhooks are delivery/lifecycle callbacks and are logged without starting an agent turn.
- Incoming calls are answered by the adapter and bridged over `/phone/media/ws`.

Hermes uses the resolved Inkbox Contact as the primary `chat_id` when possible. That means email, SMS, and outbound voice can share one contact-scoped session. Unknown senders fall back to the raw email address or E.164 phone number.

## Startup Registration

On startup, Hermes registers a webhook receiver with your Inkbox organisation for the mailbox and phone number on the configured identity. Hermes refreshes the receiver when the public URL changes (e.g., on tunnel reconnect), and cleans up the previous URL it installed. Receivers you set up by other means are not touched.

## SMS Behavior

Inbound SMS can be buffered per contact before Hermes starts an agent turn. The quiet window is disabled by default so upstream deployments keep immediate SMS behavior; set `sms_text_batch_delay_seconds` or `INKBOX_SMS_TEXT_BATCH_DELAY_SECONDS` to a positive number when a deployment wants rapid human fragments, corrections, and follow-ups to arrive as one prompt instead of several competing prompts. A multi-message turn uses an `inkbox:sms_burst` routing marker and includes relative timestamps for each fragment.

If an SMS turn arrives while Hermes is already running for that contact, the gateway queues it for the next turn and merges later SMS follow-ups into the same pending prompt. SMS follow-ups do not interrupt the active run by default.

Slash commands bypass SMS batching and routing markers, so a text such as `/approve` or `/deny` reaches the Hermes command parser as a command rather than tagged SMS body text. Carrier protocol words such as `START`, `STOP`, `HELP`, `YES`, `SUBSCRIBE`, `INFO`, and `UNSUBSCRIBE` are treated as SMS control traffic and are acknowledged at the webhook layer without starting an agent turn.

Outbound SMS is queued with `identity.send_text(to=..., text=...)`. The adapter returns a `SendResult` with the Inkbox text id and non-body metadata such as `delivery_status`. Hermes does not chunk long SMS replies automatically; content over the Inkbox SMS limit fails before send with `sms_too_long` rather than being silently truncated.

Important SMS gates are enforced by Inkbox and carriers:

- New local numbers may need 10-15 minutes of carrier propagation before sending.
- Recipients must opt in by texting `START` to an org number.
- `STOP` opts a recipient out until they opt in again.
- Per-number SMS sending is rate limited.
- MMS media on inbound text records is surfaced as attachment metadata and a prompt-visible attachment marker.

Common structured send errors:

| Error code | Meaning | Retry behavior |
|------------|---------|----------------|
| `sender_sms_pending` | Sender number is still provisioning | Do not immediate-retry |
| `messaging_profile_disabled` | Sender messaging profile is disabled upstream/provider-side | Do not retry content variants; check provisioning |
| `recipient_not_opted_in` | Recipient has not opted in with `START` | Do not retry |
| `recipient_opted_out` | Recipient sent `STOP` | Do not retry |
| `rate_limited` / send-limit errors | Per-number or provider send limit | Wait for the provider window |
| `sms_too_long` | Hermes response exceeds the SMS size limit | Shorten the response or use another channel |
| HTTP 5xx / transient provider errors | Provider temporarily unavailable | Retry with backoff |

Hermes preserves these failures in `SendResult.raw_response` with `status_code`, `error_code`, `category`, and `retryable`. Provider-gated SMS failures disable the generic plain-text fallback because changing message formatting does not fix provisioning, consent, or carrier state.

## Webhook Safety

By default the adapter requires Inkbox webhook signatures:

```bash
INKBOX_REQUIRE_SIGNATURE=true
INKBOX_SIGNING_KEY=...
```

For local-only testing you can set `INKBOX_REQUIRE_SIGNATURE=false`, but production deployments should keep signature verification enabled.

The adapter deduplicates webhooks by `X-Inkbox-Request-Id` and also deduplicates inbound SMS by Inkbox text id. This protects against duplicate agent runs when webhook retries arrive with a new request id.

## Configuration

The installer configures Inkbox automatically. Manual deployments use:

```bash
INKBOX_API_KEY=...
INKBOX_IDENTITY=your-agent-handle
INKBOX_SIGNING_KEY=...
INKBOX_PUBLIC_URL=https://your-public-host.example   # optional when not using the SDK tunnel
INKBOX_LISTEN_PORT=8765
INKBOX_HOME_CHANNEL=contact-or-phone
```

Use `INKBOX_BASE_URL` only for staging or development environments.

## Realtime Voice (OpenAI Realtime API)

By default, inbound phone calls use Inkbox's server-side STT + TTS — the agent exchanges text events on the call WebSocket and Inkbox handles audio in both directions. This works without any OpenAI dependency but adds latency and isn't truly interactive.

When an OpenAI credential is available, inbound calls are streamed end-to-end through the [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime) instead. The caller talks to an OpenAI GA Realtime voice model (default `gpt-realtime-2` with the `cedar` voice) in real time, with G.711 μ-law audio bridged through Hermes' Inkbox WS handler. The agent greets the caller proactively, so the line never opens with silence.

The realtime model has access to two tools:

- **`hermes_agent_consult`** — pauses the live conversation, dispatches a one-shot `hermes -z PROMPT` invocation of the main Hermes agent (with full tool access), and reads the agent's reply back to the caller. Use for anything that needs current external data, session search, calendar lookups, or other agentic work mid-call.
- **`register_post_call_action`** — queues a follow-up task. When the call ends, all queued actions are dispatched as a single synthetic SMS-mode turn so the main agent executes them with its full toolset (send email, update contact, create note, etc.).

### Credentials

The bridge accepts either:

1. A standard OpenAI Platform **API key** (`sk-...`) — used directly.
2. The agent's existing **ChatGPT/Codex OAuth** login — exchanged at `POST /v1/realtime/client_secrets` for an ephemeral client secret per call. This is the zero-config path: if the agent is already logged in via `hermes auth add openai-codex`, realtime works with no extra key.

### Enablement (auto-detect)

Realtime is **tri-state** ("auto unless explicitly disabled"):

| `INKBOX_REALTIME_ENABLED` / `realtime.enabled` | OpenAI credential present? | Result |
|---|---|---|
| unset | yes | **realtime on** (auto) |
| unset | no | Inkbox STT/TTS (no credential) |
| `true` | yes | realtime on |
| `true` | no | Inkbox STT/TTS + startup warning |
| `false` | either | Inkbox STT/TTS (explicit opt-out) |

"Credential present" means an API key (`realtime.api_key`, `INKBOX_REALTIME_API_KEY`, or `OPENAI_API_KEY`) **or** a Codex OAuth login. API key is preferred when both exist. Set `INKBOX_REALTIME_ENABLED=false` to opt out.

```bash
# Optional overrides (none required if the agent is Codex-authed)
INKBOX_REALTIME_ENABLED=false                    # explicit opt-out
OPENAI_API_KEY=sk-...                            # or INKBOX_REALTIME_API_KEY
INKBOX_REALTIME_MODEL=gpt-realtime-2             # optional, default shown
INKBOX_REALTIME_VOICE=cedar                      # optional, default shown
INKBOX_REALTIME_CONSULT_TIMEOUT_S=60             # optional, default shown
```

Or under `platforms.inkbox.realtime` in `~/.hermes/config.yaml`:

```yaml
platforms:
  inkbox:
    realtime:
      # enabled: false                           # omit for auto; set false to opt out
      api_key: sk-...                            # optional; falls back to OPENAI_API_KEY / Codex OAuth
      model: gpt-realtime-2
      voice: cedar
      additional_instructions: |
        Always end the call with "Anything else?"
      consult_timeout_s: 60
```

If realtime is explicitly enabled but no credential is found, the bridge falls back to the legacy Inkbox-side STT/TTS path and logs a warning at startup — calls still work, just without the realtime voice model.

### How it works

1. The call WS handler in `gateway/platforms/inkbox.py:_handle_call_ws` accepts the Inkbox WebSocket with `x-use-inkbox-text-to-speech: false` and `x-use-inkbox-speech-to-text: false` so Inkbox forwards raw μ-law frames.
2. `gateway/platforms/inkbox_realtime.py:run_inkbox_realtime_bridge` resolves the bearer (the `sk-` API key directly, or an ephemeral client secret minted from the Codex/ChatGPT OAuth token), opens a WS to `wss://api.openai.com/v1/realtime?model=<model>`, sends the **GA-schema** `session.update` (nested `audio.input` / `audio.output`, `output_modalities: ["audio"]`, audio format object `{"type": "audio/pcmu"}`) required by the GA models, and starts two concurrent pumps. The legacy flat `input_audio_format` shape used by the older `gpt-4o-realtime-preview` beta models is **not** sent — GA rejects it.
3. Caller audio: Inkbox → Hermes (μ-law base64 in `media` events) → OpenAI (`input_audio_buffer.append`). The `start` event's `stream_id` is captured for outbound frames.
4. Model audio: OpenAI (`response.output_audio.delta`) → Hermes → Inkbox `media` frames tagged `track: "outbound"` with the `stream_id`. `response.output_audio.done` emits an `audio_done` frame; OpenAI `input_audio_buffer.speech_started` (caller barge-in) emits a `clear` frame to drop queued audio.
5. The full resolved contact (name, emails, phones, company, notes) is loaded into the model's instructions at call start (only when a contact actually matched), so it knows who's calling without a mid-call lookup.
6. Tool calls: accumulated by `item_id` (name from `response.output_item.added`, args from `response.function_call_arguments.delta/.done`, with `response.output_item.done` as a fallback), then dispatched once → adapter callback → `submitToolResult` via `conversation.item.create` + `response.create`.
6. On `hermes_agent_consult`, the bridge fires an interim "Say only 'One moment.'" instruction so the model fills dead air while the spawned `hermes -z` invocation runs.

### Limitations

- **Subprocess-based agent consult.** The mid-call agent invocation spawns `hermes -z PROMPT` rather than dispatching in-process. Adds ~2s startup latency per consult but gives clean isolation from concurrent calls and full agent tooling. In-process dispatch is on the roadmap.
- **No barge-in customization.** The bridge uses OpenAI server-side VAD with default thresholds (`silence_duration_ms: 500`, `interrupt_response: true`). Tuning hooks are not exposed yet.
