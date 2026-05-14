---
name: inkbox
description: Use when working with Inkbox — email, phone, text/SMS, contacts, notes, contact rules, vault, identities. Covers the Python SDK (`pip install inkbox`) for live API access from code, and the CLI (`inkbox` / `@inkbox/cli`) for shell scripts and quick manual workflows. Default to the Python SDK; reach for the CLI when the task is a shell command or an ad-hoc check.
user-invocable: false
---

# Inkbox

API-first communication infrastructure for AI agents — email, phone, encrypted vault, and identities. Two surfaces are documented below:

- **Python SDK** (sections below) — primary surface for live API access and automation from Python code.
- **CLI** (last section) — `inkbox` binary on npm. Reach for it when you're writing a shell script or running ad-hoc commands in a terminal.

The **same auth + capabilities** apply to both — they wrap the same REST API.

## Install & Init (Python SDK)

```python
pip install inkbox
```

Always use the context manager — it manages the underlying HTTP session:

```python
from inkbox import Inkbox

with Inkbox(api_key="ApiKey_...") as inkbox:
    ...
```

Constructor: `Inkbox(api_key, base_url="https://inkbox.ai", timeout=30.0)`

## Runtime notes for Inkbox-routed Hermes sessions

When answering factual questions about the live Inkbox state (identity channels, contact records, messages, delivery status), use the Inkbox API/SDK as source of truth, not transcript memory. In Hermes tool contexts, `execute_code` may run in a sandbox without `INKBOX_API_KEY` / `INKBOX_IDENTITY`, and the SDK may not be importable unless the repo SDK path is inserted. If that happens, run via `terminal` with the Hermes venv and local SDK path, for example:

```bash
/home/ec2-user/inkbox-powered-hermes-agent/.venv/bin/python3 - <<'PY'
import os, sys
sys.path.insert(0, '/home/ec2-user/inkbox/sdk/python')
from inkbox import Inkbox
with Inkbox(api_key=os.environ['INKBOX_API_KEY']) as ink:
    identity = ink.get_identity(os.environ.get('INKBOX_IDENTITY', 'inkbox-on-call-agent'))
    print(identity.email_address)
    print(identity.phone_number.number if identity.phone_number else None)
    print(ink.contacts.lookup(phone='+15167251294'))
PY
```

The inbound routing marker already contains the resolved Contact fields; it is acceptable immediate context for conversational identity questions, but verify through the SDK when asked for live/current contact or channel facts.

**Live voice visibility pitfall:** during `[inkbox:voice_call ...]` turns, the prompt-visible marker does not include precise audio latency, transcript timestamps, or any initial adapter greeting that may have happened before the visible transcript context. If the caller asks about latency, timestamps, or “the first thing you said,” answer only from the visible transcript unless you perform a post-call SDK/log inspection; explicitly say when you cannot see the underlying timing or earlier audio in the live turn. Do not infer unseen greetings or claim precise timing during the call.

For Inkbox-routed Hermes runtime pitfalls — forcing SMS rather than email when messaging a contact, checking delivery/bounce status, and scheduling outbound calls — see `references/routed-session-operations.md`.

**Third-party “text X” pitfall:** if the user explicitly asks for an SMS to a contact, prefer `identity.send_text(to="+E164", text=...)` after resolving the contact phone. Do not assume `send_message(target="inkbox:Contact", ...)` will text; contact targets can default to email when the adapter lacks recent SMS modality for that contact. Verify delivery in `identity.get_text_conversation(phone, ...)` before reporting success.

**Voice-call caller identity pitfall:** if a voice turn arrives with `contact=unknown_in_inkbox`, the caller may still exist in the contact book. Use the marker's `call_id` to find the live call via `identity.list_calls(...)`, read `remote_phone_number`, then run `inkbox.contacts.lookup(phone=remote_phone_number)` before saying you do not know who the caller is or what their saved details are. This is especially useful for natural questions like “what's my name?”, “what's my email?”, “do you know my number?”, or “can you look me up?”. If latency makes a live lookup inappropriate, say that you do not see the detail in the visible live-call context and would need to look it up after the call; do not imply the caller has no saved contact record unless you actually checked.

**Voice-call contact creation/update pitfall:** when an unknown voice caller asks to be saved as a contact (for example “my name is Dima; add my phone number”), do not guess or reuse a remembered phone number. Resolve the live call's `remote_phone_number` from the `call_id` first, then lookup/upsert the contact with that E.164 number. Keep the spoken response short; perform the SDK update immediately after a brief acknowledgement if latency matters. If the caller corrects an email spelling, update the contact record immediately and preserve the exact spelling they requested, even for Gmail addresses where dots are delivery-equivalent; contact records should reflect the user's preferred canonical form.

```python
call_id = "59e84d61-..."
with Inkbox(api_key=os.environ["INKBOX_API_KEY"]) as ink:
    identity = ink.get_identity(os.environ.get("INKBOX_IDENTITY", "inkbox-on-call-agent"))
    call = next((c for c in identity.list_calls(limit=50, offset=0) if str(c.id) == call_id), None)
    remote = call.remote_phone_number if call else None
    contact = ink.contacts.lookup(phone=remote) if remote else None
```

## Core Model

```
Inkbox (admin-only client)
├── .create_identity(handle, *, display_name=, description=,
│                    email_local_part=, sending_domain=,
│                    tunnel=, phone_number=, vault_secret_ids=)
│                              → AgentIdentity   (atomically creates mailbox + tunnel)
├── .get_identity(handle)     → AgentIdentity
├── .list_identities()        → list[AgentIdentitySummary]
├── .mailboxes                → MailboxesResource   (list/get/update/search; no create/delete)
├── .phone_numbers            → PhoneNumbersResource
├── .tunnels                  → TunnelsResource     (list/get/update[metadata]/sign_csr)
├── .api_keys                 → ApiKeysResource     (.create — mint identity-scoped keys)
├── .texts                    → TextsResource
├── .mail_contact_rules       → MailContactRulesResource
├── .phone_contact_rules      → PhoneContactRulesResource
├── .contacts                 → ContactsResource  (.access, .vcards)
├── .notes                    → NotesResource     (.access)
├── .vault                    → VaultResource
├── .whoami()                 → WhoamiResponse
└── .create_signing_key()     → SigningKey

AgentIdentity (identity-scoped helper)
├── .mailbox                 → IdentityMailbox          (always populated; 1:1 invariant)
├── .tunnel                  → Tunnel                   (always populated; 1:1 invariant)
├── .phone_number            → IdentityPhoneNumber | None
├── .credentials             → Credentials  (requires vault unlocked)
├── mail methods             (mailbox is always linked)
├── phone methods            (requires assigned phone number)
└── text methods             (requires assigned phone number)
```

**1:1:1 invariant.** Every live identity has exactly one mailbox and exactly one tunnel, created and deleted atomically with it. There are no longer standalone `mailboxes.create`/`delete` or `tunnels.create`/`delete`/`rotate_secret`/`restore` endpoints. Phone numbers remain optional and lifecycle-independent.

**Global handle namespace.** `agent_handle` is globally unique across every Inkbox org and shares its namespace with tunnel names and platform-domain mailbox local-parts. Collisions raise `HandleUnavailableError(blocking_namespace=...)` on `create_identity` (see Error Handling below). The mailbox local part is forced to the handle on the platform domain (`@inkboxmail.com`); on a custom sending domain you can choose freely via `email_local_part=`. Once claimed, a handle is held permanently — soft-deleted identities still hold their slot, and identities on the platform domain cannot be renamed.

## Agent Signup

Public, no API key required: `Inkbox.signup(human_email, note_to_human, agent_handle=None, email_local_part=None)` returns a freshly-provisioned email address, organization, and a one-time API key. The 6-digit verification code from the email goes through `Inkbox.verify_signup(api_key, code)`. Resend the email with `Inkbox.resend_signup_verification(api_key)`; check claim status + restrictions with `Inkbox.get_signup_status(api_key)`. Until verified, the agent is rate-limited and recipient-restricted — verification unlocks full sending capabilities.

## Identities

```python
from inkbox import IdentityTunnelCreateOptions, IdentityPhoneNumberCreateOptions

# Atomically provisions mailbox + tunnel; phone is optional.
identity = inkbox.create_identity(
    "sales-agent",
    display_name="Sales Bot",                                   # optional
    description="Inbound lead handler",                         # optional, org-internal
    tunnel=IdentityTunnelCreateOptions(tls_mode="edge"),        # optional; "edge" is the default
    phone_number=IdentityPhoneNumberCreateOptions(type="local", state="NY"),  # optional
)
identity = inkbox.get_identity("sales-agent")
identities = inkbox.list_identities()  # → list[AgentIdentitySummary]

identity.update(display_name="Sales Bot v2")     # display_name now lives on the identity
identity.update(status="paused")                 # or "active"
identity.refresh()                               # re-fetch from API; updates cached channels
identity.delete()                                # cascades to mailbox + tunnel + scoped API keys
```

**Handles can't be renamed in practice.** Any identity with a platform-domain (`@inkboxmail.com`) mailbox — i.e. every identity created without an explicit custom `sending_domain` — rejects `identity.update(new_handle=...)` with a 409: the handle is load-bearing for the email address. To change a handle, `identity.delete()` and `create_identity(new_handle, ...)`.

## Channel Management

```python
# Mailbox + tunnel are created atomically with the identity.
print(identity.mailbox.email_address)  # e.g. "sales-agent@inkboxmail.com"
print(identity.tunnel.public_host)     # e.g. "sales-agent.inkboxwire.com"

# Provision a phone number (the only optional channel).
phone = identity.provision_phone_number(type="toll_free")       # or type="local", state="NY"
print(phone.number)            # e.g. "+18005551234"

# Link / unlink an existing phone number.
identity.assign_phone_number("phone-number-uuid")
identity.unlink_phone_number()
```

To switch an identity to a different handle/mailbox/tunnel, delete it and recreate — the deletion cascades through mailbox + tunnel + identity-scoped API keys and unlinks the phone:

```python
identity.delete()
new_identity = inkbox.create_identity("new-handle", display_name="...")
```

## Mail

### Send

```python
sent = identity.send_email(
    to=["user@example.com"],
    subject="Hello",
    body_text="Hi there!",          # plain text (optional)
    body_html="<p>Hi there!</p>",   # HTML (optional)
    cc=["cc@example.com"],          # optional
    bcc=["bcc@example.com"],        # optional
    in_reply_to_message_id=sent.id, # for threaded replies
    attachments=[{                  # optional
        "filename": "report.pdf",
        "content_type": "application/pdf",
        "content_base64": "<base64>",
    }],
)
```

### Read

```python
# Iterate all messages — pagination handled automatically (Iterator[Message])
for msg in identity.iter_emails():
    print(msg.subject, msg.from_address, msg.is_read)

# Filter by direction
for msg in identity.iter_emails(direction="inbound"):   # or "outbound"
    ...

# Unread only (client-side filtered)
for msg in identity.iter_unread_emails():
    ...

# Mark as read
ids = [msg.id for msg in identity.iter_unread_emails()]
identity.mark_emails_read(ids)

# Get full thread (oldest-first)
thread = identity.get_thread(msg.thread_id)
for m in thread.messages:
    print(f"[{m.from_address}] {m.subject}")
```

### Thread Folders

Threads carry a `folder` field: `inbox`, `spam`, `archive`, or `blocked` (server-assigned, never client-set).

```python
from inkbox import ThreadFolder
# Thread.folder / ThreadDetail.folder is always one of the four values above.
```

Low-level folder listing / per-thread updates (`list(folder=…)`, `list_folders(email)`, `update(..., folder=…)`) live on `ThreadsResource`. Passing `folder="blocked"` to `update` raises `ValueError` before the HTTP call.

## Phone

```python
# Place outbound call — stream audio via WebSocket
call = identity.place_call(
    to_number="+15167251294",
    client_websocket_url="wss://your-agent.example.com/ws",
)
print(call.status)
print(call.rate_limit.calls_remaining)

# List calls (offset pagination)
calls = identity.list_calls(limit=10, offset=0)
for c in calls:
    print(c.id, c.direction, c.remote_phone_number, c.status)

# Transcript segments (ordered by seq)
for t in identity.list_transcripts(calls[0].id):
    print(f"[{t.party}] {t.text}")   # party: "local" or "remote"
```

### Outbound calls from a Hermes-routed identity

When the identity is wired into Hermes via the Inkbox platform adapter, the gateway already runs the WebSocket that bridges call audio — same `/phone/media/ws` endpoint that handles inbound calls. On every connect the adapter writes the live URLs to an identity-state file so cron-spawned and other follow-up agents can find them without guessing.

**Hosted/development env pitfall:** if `execute_code` lacks `INKBOX_API_KEY`, or a terminal call gets `HTTP 401: Unauthorized`, run the call from a shell that sources the Inkbox Hermes env file and pass the deployment base URL. Example wrapper: `set -a; source "$HERMES_HOME/.env" 2>/dev/null || source /home/ec2-user/.hermes-inkbox/.env; set +a; ...` then instantiate `Inkbox(api_key=os.environ["INKBOX_API_KEY"], base_url=os.environ.get("INKBOX_BASE_URL", "https://inkbox.ai"))`. In development identities, the default production base URL can make an otherwise valid key look unauthorized.

**Read `ws_url` from the state file — do not hardcode the path or re-derive the URL.** The Inkbox-powered Hermes fork uses an isolated runtime home (typically `HERMES_HOME=~/.hermes-inkbox`), so the state file is **not** at `~/.hermes/`. Always go through `$HERMES_HOME`, which the launcher sets and which subprocess invocations (cron, terminal) inherit.

**Always pass call-purpose context.** A fresh in-call agent session is spawned when the callee picks up — that session has zero memory of why the call was scheduled, so without explicit context it'll greet the user and have no idea what's going on. Bridge this gap by writing a small JSON file under `$HERMES_HOME/inkbox_call_contexts/<token>.json` and including `?context_token=<token>` on the `client_websocket_url`. The adapter reads the file on WS open, deletes it (single-use), and prepends `[outbound_call_context]…[/outbound_call_context]` to the in-call agent's first transcript.

**Outbound-call source-channel pitfall:** call-purpose context can be stale, ambiguous, or wrong about the channel that triggered the call. If the callee asks “where did I tell you to call me?” or challenges “SMS vs email,” do not guess or simply accept their correction. During a live call, either say you need to verify or perform one quick SDK lookup of recent `identity.iter_emails()` and `identity.list_texts(...)`, then answer with the verified channel and evidence. This matters especially when the user is testing cross-channel continuity; incorrect channel claims break trust.

**Terse repeat-call requests:** if the user says “again”, “call again”, or “call me” after an earlier call request and the callee is clear from the current session/user profile, place the call immediately without asking for clarification. Write fresh context that explicitly says this is a repeat call and tells the in-call agent to open with the caller's name and the reason (e.g. “Dima asked me to call again now”).

Known-good recipe:

```python
import os, json, secrets
from pathlib import Path
from urllib.parse import urlparse
from inkbox import Inkbox

home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
state = json.loads((home / "inkbox_identity_state.json").read_text())
ws_url = state["ws_url"]

# Write call-purpose context the in-call agent will see on its first turn.
ctx_dir = home / "inkbox_call_contexts"
ctx_dir.mkdir(parents=True, exist_ok=True)
token = secrets.token_urlsafe(16)
(ctx_dir / f"{token}.json").write_text(json.dumps({
    "reason": "Dima asked via SMS to be called back in 2 minutes",
    "scheduled_by": "cron 8ae42d54deec",
    "conversation_summary": (
        "Earlier in SMS Dima asked the agent to call him in 2 minutes. "
        "No specific topic was given — the call is a check-in / liveness test."
    ),
}))
ws_with_ctx = f"{ws_url}?context_token={token}"

with Inkbox(api_key=os.environ["INKBOX_API_KEY"]) as ink:
    identity = ink.get_identity(os.environ.get("INKBOX_IDENTITY", state["handle"]))
    call = identity.place_call(to_number="+15167251294", client_websocket_url=ws_with_ctx)
    print(f"placed call: id={call.id} status={call.status}")
```

State file shape (written by the adapter on connect):

```json
{
  "handle": "inkbox-on-call-agent",
  "email_address": "inkbox-on-call-agent@inkboxmail.com",
  "phone_number": "+14137240502",
  "phone_number_id": "<uuid>",
  "public_url": "https://<tunnel>.inkboxwire.com",
  "webhook_url": "https://<tunnel>.inkboxwire.com/webhook",
  "ws_url": "wss://<tunnel>.inkboxwire.com/phone/media/ws"
}
```

Common pitfall: a cron job that hardcodes `Path.home() / ".hermes"` will read the upstream Hermes home, not the isolated Inkbox home, and will fail with `No Inkbox call websocket URL found`. The fix is always the `$HERMES_HOME` lookup above.

When the callee picks up, audio bridges to the *same* WS handler as inbound calls — the live-call flow (greeting, transcript-driven turns, two-frame text protocol) is identical. There is no separate outbound code path on the agent side.

## Text Messages (SMS/MMS)

**Outbound SMS limits and gates (current):**

- Allowed only from **local** numbers, not toll-free.
- **15 outbound sends per phone number per rolling 24h.**
- New local numbers need **~10-15 min** for 10DLC carrier propagation. `identity.phone_number.sms_status` is `SmsStatus.PENDING` until ready; sends in this window return `409 sender_sms_pending`.
- Recipient must have texted **`START`** to any number in the org. Unknown → `403 recipient_not_opted_in`. `STOP` → `403 recipient_opted_out`.

**Coming soon:** toll-free SMS sending, customer-managed 10DLC brands/campaigns (drastically higher per-number limits).

```python
# Send an SMS from this identity's phone number.
# Returns a queued TextMessage; final delivery state arrives via the
# incoming_text_webhook_url configured on the sender.
sent = identity.send_text(to="+15167251294", text="Hello from Inkbox")
print(sent.id, sent.delivery_status)   # SmsDeliveryStatus.QUEUED

# List text messages (offset pagination)
texts = identity.list_texts(limit=20, offset=0)
for t in texts:
    print(t.id, t.direction, t.remote_phone_number, t.text, t.is_read)

# Filter by read state
unread = identity.list_texts(is_read=False)

# Get a single text message
text = identity.get_text("text-uuid")
print(text.type)   # "sms" or "mms"
if text.media:     # MMS media attachments (temporary signed URLs)
    for m in text.media:
        print(m.content_type, m.size, m.url)

# List conversation summaries (one row per remote number)
convos = identity.list_text_conversations(limit=20)
for c in convos:
    print(c.remote_phone_number, c.latest_text, c.unread_count, c.total_count)

# Get messages in a specific conversation
msgs = identity.get_text_conversation("+15167251294", limit=50)

# Mark a text as read (identity convenience method)
identity.mark_text_read("text-uuid")

# Mark all messages in a conversation as read
result = identity.mark_text_conversation_read("+15167251294")
print(result["updated_count"])

# Admin-only: search, update, delete
results = inkbox.texts.search(phone.id, q="invoice", limit=20)
inkbox.texts.update(phone.id, "text-uuid", status="deleted")
```

## Vault

Encrypted credential vault with client-side Argon2id key derivation and AES-256-GCM encryption. The server never sees plaintext secrets. Requires `argon2-cffi` and `cryptography` (included as dependencies).

### Initialize

```python
# Initialize a new vault (org ID is fetched automatically from the API key)
result = inkbox.vault.initialize("my-Vault-key-01!")
print(result.vault_id, result.vault_key_id)
for code in result.recovery_codes:
    print(code)  # save these immediately — they cannot be retrieved again
```

### Unlock & Read

```python
from inkbox import LoginPayload, APIKeyPayload, SSHKeyPayload, OtherPayload

# Unlock with a vault key — derives key via Argon2id, decrypts all secrets
unlocked = inkbox.vault.unlock("my-Vault-key-01!")

# Optionally filter to secrets an agent identity has access to
unlocked = inkbox.vault.unlock("my-Vault-key-01!", identity_id="agent-uuid")

# All decrypted secrets from the unlock bundle
for secret in unlocked.secrets:
    print(secret.name, secret.secret_type)
    print(secret.payload)   # LoginPayload, APIKeyPayload, SSHKeyPayload, or OtherPayload

# Fetch and decrypt a single secret by ID
secret = unlocked.get_secret("secret-uuid")
print(secret.payload.username, secret.payload.password)   # for login type
```

### Create & Update

```python
# Create a login secret (secret_type inferred from payload type)
unlocked.create_secret(
    "AWS Production",
    LoginPayload(password="s3cret", username="admin", url="https://aws.amazon.com"),
    description="Production IAM user",
)

# Create an API key secret
unlocked.create_secret(
    "GitHub PAT",
    APIKeyPayload(api_key="ghp_xxx"),
)

# Create an SSH key secret
unlocked.create_secret(
    "Deploy Key",
    SSHKeyPayload(private_key="-----BEGIN OPENSSH PRIVATE KEY-----..."),
)

# Create a freeform secret
unlocked.create_secret("Misc", OtherPayload(data="any freeform content"))

# Update name/description and/or re-encrypt payload
unlocked.update_secret("secret-uuid", name="New Name")
unlocked.update_secret("secret-uuid", payload=LoginPayload(password="new", username="new"))

# Delete
unlocked.delete_secret("secret-uuid")
```

### Metadata (no unlock needed)

```python
info = inkbox.vault.info()                                   # VaultInfo
keys = inkbox.vault.list_keys()                              # list[VaultKey]
keys = inkbox.vault.list_keys(key_type="recovery")           # filter by type
secrets = inkbox.vault.list_secrets()                         # list[VaultSecret] (metadata only)
secrets = inkbox.vault.list_secrets(secret_type="login")     # filter by type
inkbox.vault.delete_secret("secret-uuid")                    # delete without unlocking
```

### Payload Types

| Type | Class | Fields |
|------|-------|--------|
| `login` | `LoginPayload` | `password`, `username?`, `email?`, `url?`, `notes?` |
| `api_key` | `APIKeyPayload` | `api_key`, `endpoint?`, `notes?` |
| `key_pair` | `KeyPairPayload` | `access_key`, `secret_key`, `endpoint?`, `notes?` |
| `ssh_key` | `SSHKeyPayload` | `private_key`, `public_key?`, `fingerprint?`, `passphrase?`, `notes?` |
| `other` | `OtherPayload` | `data` |

`secret_type` is immutable after creation. To change it, delete and recreate.

### Agent Credentials (identity-scoped)

Agent-facing credential access — typed, identity-scoped. The vault stays as the admin surface; `identity.credentials` is the agent runtime surface.

```python
from inkbox import Credentials

# Unlock the vault first (stores state on the client)
inkbox.vault.unlock("my-Vault-key-01!")

identity = inkbox.get_identity("support-bot")

# Discovery — returns list[DecryptedVaultSecret] with name/metadata
all_creds = identity.credentials.list()
logins    = identity.credentials.list_logins()
api_keys  = identity.credentials.list_api_keys()
ssh_keys  = identity.credentials.list_ssh_keys()
key_pairs = identity.credentials.list_key_pairs()

# Access by UUID — returns typed payload directly
login    = identity.credentials.get_login("secret-uuid")      # → LoginPayload
api_key  = identity.credentials.get_api_key("secret-uuid")    # → APIKeyPayload
ssh_key  = identity.credentials.get_ssh_key("secret-uuid")    # → SSHKeyPayload
key_pair = identity.credentials.get_key_pair("secret-uuid")   # → KeyPairPayload

# Generic access — returns DecryptedVaultSecret
secret = identity.credentials.get("secret-uuid")
```

- Requires `inkbox.vault.unlock()` first — raises `InkboxError` if vault is not unlocked
- Results are filtered to secrets the identity has access to (via access rules)
- Cached after first access; call `identity.refresh()` to clear the cache
- `get_*` raises `KeyError` if not found, `TypeError` if wrong secret type

## One-Time Passwords (TOTP)

TOTP secrets are stored inside `LoginPayload.totp` in the encrypted vault. Codes are generated client-side — no server call needed.

### From an agent identity (recommended)

```python
from inkbox.vault.totp import parse_totp_uri
from inkbox.vault.types import LoginPayload

# Create a login with TOTP
secret = identity.create_secret(
    name="GitHub",
    payload=LoginPayload(
        username="user@example.com",
        password="s3cret",
        totp=parse_totp_uri("otpauth://totp/GitHub:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=GitHub"),
    ),
)

# Generate TOTP code
code = identity.get_totp_code(str(secret.id))
print(code.code)              # e.g. "482901"
print(code.seconds_remaining) # e.g. 17

# Add/replace TOTP on existing login
identity.set_totp(secret_id, "otpauth://totp/...?secret=...")

# Remove TOTP
identity.remove_totp(secret_id)
```

### From the unlocked vault (admin-only)

```python
unlocked = inkbox.vault.unlock("my-Vault-key-01!")

# Same methods available on UnlockedVault
unlocked.set_totp(secret_id, totp_config_or_uri)
unlocked.remove_totp(secret_id)
code = unlocked.get_totp_code(secret_id)
```

### TOTPCode fields

| Field | Type | Description |
|---|---|---|
| `code` | `str` | The OTP code (e.g. `"482901"`) |
| `period_start` | `int` | Unix timestamp when the code became valid |
| `period_end` | `int` | Unix timestamp when the code expires |
| `seconds_remaining` | `int` | Seconds until expiry |

## Admin-only Resources

### Mailboxes (`inkbox.mailboxes`)

Mailbox lifecycle is owned by the identity: `create_identity()` provisions one atomically, `identity.delete()` cascades. There is no `mailboxes.create()` or `mailboxes.delete()` — those endpoints return 404.

```python
mailboxes = inkbox.mailboxes.list()
mailbox   = inkbox.mailboxes.get("abc@inkboxmail.com")

inkbox.mailboxes.update(mailbox.email_address, webhook_url="https://example.com/hook")
inkbox.mailboxes.update(mailbox.email_address, webhook_url=None)   # remove webhook

# `display_name` lives on the identity now — passing it to mailboxes.update
# returns 422 with a hint. Use identity.update(display_name=...) instead.

# Switch contact-rule filter mode (admin-only — agent-scoped keys get 403)
updated = inkbox.mailboxes.update(mailbox.email_address, filter_mode="whitelist")
if updated.filter_mode_change_notice:
    # Populated when filter_mode actually changed — tells you how many
    # rules are now redundant under the new mode.
    n = updated.filter_mode_change_notice
    print(n.redundant_rule_count, n.redundant_rule_action, n.new_filter_mode)

# Mailbox responses carry mailbox.agent_identity_id (always populated for
# live customer mailboxes; null only on tombstones or system mailboxes).

results = inkbox.mailboxes.search(mailbox.email_address, q="invoice", limit=20)
```

### Tunnels (`inkbox.tunnels`)

Like mailboxes, tunnel lifecycle is owned by the identity. The surviving control-plane operations are read + metadata-only update + CSR signing.

```python
tunnels = inkbox.tunnels.list()
tunnel  = inkbox.tunnels.get("tunnel-uuid")

# Metadata-only update — the only PATCH-able field is `metadata`.
inkbox.tunnels.update("tunnel-uuid", metadata={"env": "prod"})

# Sign a CSR (passthrough tunnels only; edge tunnels return 409 TunnelTLSModeMismatch)
signed = inkbox.tunnels.sign_csr("tunnel-uuid", csr_pem=csr_bytes)
```

To open the data plane from your own code use `inkbox.tunnels.connect(...)` (or `inkbox.tunnels.client.connect`); auth is the SDK client's API key sent as `x-api-key`. No per-tunnel connect secret to mint or rotate. The key must be admin-scoped in the tunnel's org, or agent-scoped to the tunnel's owning identity.

### API Keys (`inkbox.api_keys`)

```python
# Mint a fresh API key. Admin-scoped callers MUST pass scoped_identity_id
# (admin keys cannot mint other admin keys — JWT only). Agent-scoped keys
# are bound to a single identity for the life of the key.
created = inkbox.api_keys.create(
    label="Hermes gateway · sales-agent",
    description="Auto-minted by hermes setup",
    scoped_identity_id=identity.id,        # None → admin-scoped (JWT only)
)
print(created.api_key)     # the full secret — shown once, save immediately
print(created.record.id)   # ApiKey record metadata
```

The minted key is what `tunnels.connect()` sends as `x-api-key` on the data plane, so the per-identity key from this call is exactly what a gateway running as one identity should hold on disk.

### Phone Numbers (`inkbox.phone_numbers`)

```python
numbers = inkbox.phone_numbers.list()
number  = inkbox.phone_numbers.get("phone-number-uuid")
number  = inkbox.phone_numbers.provision(agent_handle="my-agent", type="toll_free")
local   = inkbox.phone_numbers.provision(agent_handle="my-agent", type="local", state="NY")

inkbox.phone_numbers.update(
    number.id,
    incoming_call_action="webhook",            # "webhook", "auto_accept", or "auto_reject"
    incoming_call_webhook_url="https://...",
)
inkbox.phone_numbers.update(
    number.id,
    incoming_call_action="auto_accept",
    client_websocket_url="wss://...",
)

hits = inkbox.phone_numbers.search_transcripts(number.id, q="refund", party="remote", limit=50)
inkbox.phone_numbers.release(number.id)
```

Phone numbers carry the same `filter_mode` / `agent_identity_id` / `filter_mode_change_notice` fields as mailboxes; flipping `filter_mode` is admin-only and returns a change-notice when the value actually changed.

## Contact Rules

Per-mailbox or per-phone-number allow/block lists, enforced server-side. The active `filter_mode` on the owning resource controls whether the rules are interpreted as a whitelist or blacklist. Mail matches by exact email or domain; phone matches by exact E.164 number.

```python
from inkbox import (
    MailRuleAction, MailRuleMatchType, PhoneRuleAction, PhoneRuleMatchType,
    DuplicateContactRuleError,
)

# Mail rules — scoped to a single mailbox. New rules always start active;
# call `update(..., status="paused")` afterwards to pause one.
rule = inkbox.mail_contact_rules.create(
    mailbox.email_address,
    action=MailRuleAction.ALLOW,         # or BLOCK
    match_type=MailRuleMatchType.DOMAIN, # or EXACT_EMAIL
    match_target="example.com",
)
inkbox.mail_contact_rules.list(mailbox.email_address)
inkbox.mail_contact_rules.get(mailbox.email_address, rule.id)
inkbox.mail_contact_rules.update(mailbox.email_address, rule.id, status="paused")  # admin-only
inkbox.mail_contact_rules.delete(mailbox.email_address, rule.id)                   # admin-only

# Admin-only list; optionally narrow to a single mailbox_id
all_rules = inkbox.mail_contact_rules.list_all(mailbox_id=str(mailbox.id))

# Duplicate (match_type, match_target) on the same mailbox raises 409:
try:
    inkbox.mail_contact_rules.create(
        mailbox.email_address,
        action="allow", match_type="domain", match_target="example.com",
    )
except DuplicateContactRuleError as e:
    print(e.existing_rule_id)   # UUID of the rule that already matched

# Phone rules — same shape, only match_type="exact_number" is supported.
inkbox.phone_contact_rules.create(
    number.id,
    action=PhoneRuleAction.BLOCK,
    match_type=PhoneRuleMatchType.EXACT_NUMBER,
    match_target="+15551234567",
)
inkbox.phone_contact_rules.list(number.id)
inkbox.phone_contact_rules.list_all(phone_number_id=str(number.id))
```

## Contacts

Admin-only address book with per-identity access grants and vCard import/export. For routed-session contact-book upsert patterns, transport-sender-vs-described-person pitfalls, and user-facing distinctions between the Inkbox contact book and Hermes memory/user profile, see `references/contact-book-upserts.md`.

```python
from inkbox import (
    Contact, ContactEmail, ContactPhone, ContactAddress,
    RedundantContactAccessGrantError,
)

# CRUD
contact = inkbox.contacts.create(
    given_name="Ada",
    family_name="Lovelace",
    emails=[ContactEmail(label="work", value="ada@example.com")],
    phones=[ContactPhone(label="mobile", value="+15551234567")],
    # access_identity_ids defaults to "wildcard" (every active identity);
    # pass [] for admin-only, or a list of identity UUIDs for explicit grants.
)
inkbox.contacts.get(str(contact.id))
inkbox.contacts.list(q="ada", order="recent", limit=50, offset=0)
inkbox.contacts.update(str(contact.id), job_title="Analyst")       # JSON-merge-patch via kwargs
inkbox.contacts.delete(str(contact.id))

# Reverse-lookup — exactly one filter required (else ValueError before HTTP)
inkbox.contacts.lookup(email="ada@example.com")
inkbox.contacts.lookup(email_domain="example.com")
inkbox.contacts.lookup(phone="+15551234567")
inkbox.contacts.lookup(email_contains="ada")
inkbox.contacts.lookup(phone_contains="555")

# Contact-book updates in Inkbox-routed sessions:
# - If the inbound marker resolves to a contact and the user gives details for that same person,
#   update that contact_id directly and verify the returned record.
# - If the user asks the agent to “remember” a role/title for a known or lookupable third party,
#   save durable memory if appropriate AND update that person's contact record; do not treat
#   Hermes memory as a substitute for the Inkbox contact book.
# - If the user says “add Ray too” (or otherwise names a different person) while texting from
#   someone else's channel, do NOT overwrite the sender's contact. Require/use the new person's
#   email or phone, lookup by email then phone, and update-or-create a separate contact.
# - Normalize phones to E.164 before lookup/create/update. Dict payloads work for email/phone
#   collections when importing ContactEmail/ContactPhone is inconvenient.
# Example upsert for a distinct person:
email = "ray@example.com"
phone = "+18573008599"
existing = None
for kwargs in ({"email": email}, {"phone": phone}):
    try:
        match = inkbox.contacts.lookup(**kwargs)
        existing = match[0] if isinstance(match, list) and match else match
        if existing:
            break
    except Exception:
        pass
payload = dict(
    preferred_name="Ray",
    given_name="Ray",
    company_name="Inkbox",
    job_title="Cofounder",
    emails=[{"label": "work", "value": email, "is_primary": True}],
    phones=[{"label": "mobile", "value_e164": phone, "is_primary": True}],
)
contact = inkbox.contacts.update(str(existing.id), **payload) if existing else inkbox.contacts.create(**payload)

# Access grants (admin + JWT only; agents can self-revoke)
inkbox.contacts.access.list(str(contact.id))
inkbox.contacts.access.grant(str(contact.id), identity_id="agent-uuid")
inkbox.contacts.access.grant(str(contact.id), wildcard=True)       # every active identity
inkbox.contacts.access.revoke(str(contact.id), "agent-uuid")

# Redundant grants (e.g. per-identity on top of wildcard) raise 409
try:
    inkbox.contacts.access.grant(str(contact.id), identity_id="agent-uuid")
except RedundantContactAccessGrantError as e:
    print(e.error, e.detail_message)

# vCards
result = inkbox.contacts.vcards.import_vcards(vcf_text)   # bulk, ≤5 MiB, ≤1000 cards
print(result.created_ids)     # list[UUID]
for item in result.errors:    # list[ContactImportResultItem]
    print(item.index, item.error)

vcf = inkbox.contacts.vcards.export_vcard(str(contact.id))  # vCard 4.0 string
```

## Notes

Admin-only free-form notes with per-identity access grants. Identities must be granted access explicitly — there is no wildcard for notes.

```python
note = inkbox.notes.create(body="Customer prefers email follow-up.", title="Ada")
inkbox.notes.get(str(note.id))
inkbox.notes.list(q="email", identity_id="agent-uuid", order="recent", limit=50)
inkbox.notes.update(str(note.id), body="Updated body")
inkbox.notes.update(str(note.id), title=None)   # clear title (body cannot be null)
inkbox.notes.delete(str(note.id))

# Access grants (admin + JWT only)
inkbox.notes.access.list(str(note.id))
inkbox.notes.access.grant(str(note.id), identity_id="agent-uuid")
inkbox.notes.access.revoke(str(note.id), "agent-uuid")
```

## Whoami

```python
# Check the authenticated caller's identity
info = inkbox.whoami()
print(info.auth_type)        # "api_key" or "jwt"
print(info.organization_id)
```

Returns `WhoamiApiKeyResponse` (with `key_id`, `label`, `creator_type`, `auth_subtype`, etc.) or `WhoamiJwtResponse` (with `email`, `org_role`, etc.) based on `auth_type`.

For branching on API-key scope, compare against the exported constants:

```python
from inkbox import (
    AUTH_SUBTYPE_API_KEY_ADMIN_SCOPED,
    AUTH_SUBTYPE_API_KEY_AGENT_SCOPED_CLAIMED,
    AUTH_SUBTYPE_API_KEY_AGENT_SCOPED_UNCLAIMED,
)

if info.auth_type == "api_key" and info.auth_subtype == AUTH_SUBTYPE_API_KEY_ADMIN_SCOPED:
    ...   # admin-only operations (filter_mode flips, rule updates/deletes, etc.)
```

## Webhooks & Signature Verification

Webhooks are configured directly on the mailbox or phone number — no separate registration.

```python
from inkbox import verify_webhook

# Rotate signing key (plaintext returned once — save it)
key = inkbox.create_signing_key()

# Verify an incoming webhook request
is_valid = verify_webhook(
    payload=raw_body,                                    # bytes
    headers=request.headers,
    secret="whsec_...",
)
```

Algorithm: HMAC-SHA256 over `"{request_id}.{timestamp}.{body}"`.

## Error Handling

```python
from inkbox import (
    InkboxAPIError,
    DuplicateContactRuleError,
    RedundantContactAccessGrantError,
)

try:
    identity = inkbox.get_identity("unknown")
except InkboxAPIError as e:
    print(e.status_code)   # HTTP status (e.g. 404)
    print(e.detail)        # str for legacy errors, dict for structured ones
```

`InkboxAPIError.detail` can now be a `dict` for structured responses (e.g. contact-rule / access conflicts). Catch the narrower subclasses when you need the parsed fields:

- `DuplicateContactRuleError` — 409 when creating a contact rule with an already-taken `(match_type, match_target)` on the same resource. Exposes `.existing_rule_id: UUID`.
- `RedundantContactAccessGrantError` — 409 when a contact-access grant is redundant (e.g. per-identity grant on top of an active wildcard). Exposes `.error` and `.detail_message`.
- `HandleUnavailableError` (from `inkbox.identities.exceptions`) — 409 from `create_identity()` when the requested `agent_handle` collides in the unified global namespace. Exposes `.blocking_namespace: Literal["identities","tunnels","mail",None]` so the caller can tell whether the collision was against an existing identity, an existing tunnel, or a platform-domain mailbox local-part. (Identities on the platform domain cannot be renamed, so this error effectively only fires at create time on this fork.)
- `TunnelNotProvisioned` (from `inkbox.tunnels.exceptions`) — raised by `inkbox.tunnels.connect(...)` when no tunnel exists for the supplied handle. Recovery: recreate the identity (tunnels are atomically provisioned with it).
- `TunnelRemoved` (from `inkbox.tunnels.exceptions`) — raised when the local `state.json` references a `tunnel_id` that the server returned 404 for. Clear the state directory and call `create_identity(...)`.

## Key Conventions

- All method and property names are **snake_case**
- `iter_emails()` / `iter_unread_emails()` return `Iterator[Message]` — auto-paginated, lazy
- `list_calls()` returns `list[PhoneCall]` — offset pagination, not an iterator
- To clear a nullable field (e.g. webhook URL), pass `field=None`
- The `Inkbox` client **must** be used as a context manager (`with` statement) or `.close()` called manually
- Mail/phone methods on `AgentIdentity` raise `InkboxError` if the relevant channel isn't assigned

---

# Inkbox CLI


Command-line interface for the Inkbox API — identities, email, phone, text/SMS, encrypted vault, mailboxes, phone numbers, signing keys, and webhook utilities.

## Auth & Runtime

Set credentials via env vars or global flags:

```bash
export INKBOX_API_KEY="ApiKey_..."
export INKBOX_VAULT_KEY="my-vault-key"   # only needed for vault decrypt/create flows
```

Global options:

```text
--api-key <key>      Inkbox API key (or set INKBOX_API_KEY)
--vault-key <key>    Vault key for decrypt operations (or set INKBOX_VAULT_KEY)
--base-url <url>     Override API base URL
--json               Output as JSON instead of formatted tables
```

If `INKBOX_API_KEY` is missing and `--api-key` is not passed, the CLI exits with an error.

Prefer `--json` when the result will be parsed or fed into another tool. Use the default table/record output when the user wants a quick human-readable summary.

## Install & Local Repo Usage

Published package:

```bash
npm install -g @inkbox/cli
```

Or run without a global install:

```bash
npx @inkbox/cli <command>
```

Requires Node.js >= 18.

Inside this repository, prefer running the local source instead of assuming a global install:

```bash
npm --prefix cli run dev -- <command>
```

Examples:

```bash
npm --prefix cli run dev -- --json identity list
npm --prefix cli run dev -- email list -i support-bot --limit 10
```

## High-Risk Operations

These commands can send real traffic or mutate real resources. Confirm with the user before running them:

- `signup create`
- `email send`
- `text send`
- `phone call`
- `identity delete` (cascades to its mailbox + tunnel + scoped API keys)
- `email delete`
- `email delete-thread`
- `vault delete`
- `mailbox update --filter-mode ...` (admin-only; flips allow/block semantics for that mailbox)
- `number release`
- `number update --filter-mode ...` (admin-only; same caveat as mailbox)
- `signing-key create`

`contacts delete`, `notes delete`, `mailbox rules delete`, `number rules delete` affect downstream filtering and access — confirm intent before running.

Also confirm before creating or rotating secrets if the values were not explicitly provided by the user.

## Agent Signup

```bash
inkbox signup create
inkbox signup verify --code <code>
inkbox signup resend-verification
inkbox signup status
```

`signup create` is the only one that does not require an API key. The follow-up commands need the signup-issued API key passed back via `--api-key` or `INKBOX_API_KEY` — the CLI does not persist it automatically. Until verified, the agent is rate-limited and recipient-restricted; verification unlocks full sending.

## Identities

```bash
inkbox identity list
inkbox identity get <handle>
inkbox identity create <handle> [--display-name <name>] [--description <text>]
inkbox identity delete <handle>
inkbox identity update <handle> [--display-name <name>] [--description <text>] [--status active|paused]
inkbox identity refresh <handle>
```

Notes:

- `identity create` atomically provisions the agent identity, its mailbox, and its tunnel. The handle is globally unique across all Inkbox orgs and is reused as the platform-domain mailbox local part and the tunnel name. A 409 means the handle collides somewhere in that shared namespace.
- `identity get` and `identity refresh` return mailbox, tunnel, and phone number assignments.
- `identity delete` cascades: mailbox + tunnel are tombstoned and any identity-scoped API keys are revoked.
- Most email, phone, and text commands require `-i, --identity <handle>`.

### Identity-Scoped Secrets

These require a vault key:

```bash
inkbox identity create-secret <handle> --name <name> --type <type> ...
inkbox identity get-secret <handle> <secret-id>
inkbox identity delete-secret <handle> <secret-id>
inkbox identity revoke-access <handle> <secret-id>
inkbox identity set-totp <handle> <secret-id> --uri <otpauth-uri>
inkbox identity remove-totp <handle> <secret-id>
inkbox identity totp-code <handle> <secret-id>
```

Secret types:

```text
login, api_key, ssh_key, key_pair, other
```

## Email

All email commands are identity-scoped and require `-i <handle>`.

```bash
inkbox email send -i <handle> \
  --to user@example.com \
  --subject "Hello" \
  --body-text "Hi"

inkbox email list -i <handle> --limit 10
inkbox email get <message-id> -i <handle>
inkbox email search -i <handle> -q "invoice"
inkbox email unread -i <handle> --limit 10
inkbox email mark-read <ids...> -i <handle>
inkbox email delete <message-id> -i <handle>
inkbox email delete-thread <thread-id> -i <handle>
inkbox email star <message-id> -i <handle>
inkbox email unstar <message-id> -i <handle>
inkbox email thread <thread-id> -i <handle>
```

Use `email search` only when the identity already has a mailbox assigned.

Before sending, confirm recipients, subject, and body with the user.

## Phone

All phone commands are identity-scoped and require `-i <handle>`.

```bash
inkbox phone call -i <handle> --to +15167251294 --ws-url wss://example.com/ws
inkbox phone calls -i <handle> --limit 10 --offset 0
inkbox phone transcripts <call-id> -i <handle>
inkbox phone search-transcripts -i <handle> -q "refund" --party remote
```

Before placing a call, confirm the destination number and websocket URL with the user.

## Text Messages

All text commands are identity-scoped and require `-i <handle>`.

**Outbound SMS limits and gates (current):**

- Allowed only from **local** numbers, not toll-free.
- **15 sends per phone number per rolling 24h.**
- A freshly provisioned local number needs **~10-15 min** for 10DLC carrier propagation. Inspect with `inkbox number get <id>`; sending is gated until `smsStatus` reads `ready` (otherwise `409 sender_sms_pending`).
- Recipient must have texted **`START`** to any number in the org. Unknown → `403 recipient_not_opted_in`. `STOP` → `403 recipient_opted_out`.

**Coming soon:** toll-free SMS sending, customer-managed 10DLC brands/campaigns (drastically higher per-number limits).

```bash
inkbox text send -i <handle> --to +15167251294 --text "Hello from Inkbox"
inkbox text list -i <handle> --limit 20
inkbox text get <text-id> -i <handle>
inkbox text conversations -i <handle> --limit 20
inkbox text conversation <remote-number> -i <handle> --limit 50
inkbox text search -i <handle> -q "invoice"
inkbox text mark-read <text-id> -i <handle>
inkbox text mark-conversation-read <remote-number> -i <handle>
```

## Vault

Vault decryption and secret creation require a vault key via `INKBOX_VAULT_KEY` or `--vault-key`.

```bash
inkbox vault init --vault-key <key>
inkbox vault info
inkbox vault secrets
inkbox vault get <secret-id>
inkbox vault create --name <name> --type <type> ...
inkbox vault delete <secret-id>
inkbox vault keys
inkbox vault grant-access <secret-id> -i <handle>
inkbox vault revoke-access <secret-id> -i <handle>
inkbox vault access-list <secret-id>
inkbox vault logins -i <handle>
inkbox vault api-keys -i <handle>
inkbox vault ssh-keys -i <handle>
inkbox vault key-pairs -i <handle>
```

Secret type flags:

```bash
# login
--password <pass> [--username <user>] [--email <email>] [--url <url>] [--totp-uri <uri>] [--notes <text>]

# api_key
--key <key> [--endpoint <url>] [--notes <text>]

# key_pair
--access-key <key> --secret-key <key> [--endpoint <url>] [--notes <text>]

# ssh_key
--private-key <key> [--public-key <key>] [--fingerprint <fp>] [--passphrase <pass>] [--notes <text>]

# other
--data <json> [--notes <text>]
```

## Admin-Only Mailboxes

```bash
inkbox mailbox list
inkbox mailbox get <email-address>
inkbox mailbox update <email-address> [--webhook-url <url>] [--filter-mode whitelist|blacklist]
```

Mailbox creation and deletion are owned by the identity now — use `inkbox identity create` / `inkbox identity delete`. `mailbox update` no longer accepts `--display-name`; that field moved to the identity (`inkbox identity update <handle> --display-name <name>`).

`mailbox list` / `get` / `update` rows include `filterMode` and `agentIdentityId`. `--filter-mode` is admin-only; when the value actually changes, a note is printed to **stderr** telling you how many existing rules are now redundant under the new mode.

### Mailbox Contact Rules (`inkbox mailbox rules …`)

Per-mailbox allow/block rules (combined with the mailbox's `filterMode`).

```bash
inkbox mailbox rules list --mailbox <email> [--action allow|block] [--match-type exact_email|domain] [--limit <n>] [--offset <n>]
inkbox mailbox rules list --all-mailboxes [--mailbox-id <id>] [--action …] [--match-type …]    # admin-only
inkbox mailbox rules get <rule-id> --mailbox <email>
inkbox mailbox rules create --mailbox <email> --action allow|block --match-type exact_email|domain --match-target <value> [--status active|paused]
inkbox mailbox rules update <rule-id> --mailbox <email> [--action allow|block] [--status active|paused]   # admin-only
inkbox mailbox rules delete <rule-id> --mailbox <email>                                                    # admin-only
```

## Admin-Only Phone Numbers

```bash
inkbox number list
inkbox number get <id>
inkbox number provision --handle <handle> [--type toll_free|local] [--state NY]
inkbox number update <id> [--incoming-call-action auto_accept|auto_reject|webhook] [--filter-mode whitelist|blacklist] ...
inkbox number release <number-id>
```

Use `--state` only when provisioning a local number. Phone-number rows also carry `filterMode` / `agentIdentityId`; `--filter-mode` is admin-only and prints a stderr note when the value changes.

### Number Contact Rules (`inkbox number rules …`)

Per-number allow/block rules (combined with the number's `filterMode`).

```bash
inkbox number rules list --number <id> [--action allow|block] [--match-type exact_number] [--limit <n>] [--offset <n>]
inkbox number rules list --all-numbers [--phone-number-id <id>] [--action …] [--match-type …]   # admin-only
inkbox number rules get <rule-id> --number <id>
inkbox number rules create --number <id> --action allow|block --match-target <e164> [--match-type exact_number] [--status active|paused]
inkbox number rules update <rule-id> --number <id> [--action allow|block] [--status active|paused]   # admin-only
inkbox number rules delete <rule-id> --number <id>                                                    # admin-only
```

## Contacts

Admin-only address book. All commands hit the admin endpoints; agents see contacts they've been granted access to.

```bash
inkbox contacts list [--q <query>] [--order name|recent] [--limit <n>] [--offset <n>]
inkbox contacts get <contact-id>
inkbox contacts create --json <payload>            # JSON matching CreateContactOptions
inkbox contacts update <contact-id> --json <patch>  # JSON-merge-patch
inkbox contacts delete <contact-id>
inkbox contacts lookup (--email <email> | --email-contains <s> | --email-domain <d> | --phone <e164> | --phone-contains <s>)
inkbox contacts import <file.vcf>                  # bulk vCard import (≤5 MiB, ≤1000 cards)
inkbox contacts export <contact-id> [--out <file>] # vCard 4.0 to stdout or file

# Per-contact access grants
inkbox contacts access list <contact-id>
inkbox contacts access grant <contact-id> (--identity <uuid> | --wildcard)   # admin + JWT only
inkbox contacts access revoke <contact-id> <identity-id>
```

`contacts lookup` requires exactly one filter flag. For `create` / `update`, construct the payload carefully — fields include `preferredName`, `givenName`, `familyName`, `companyName`, `jobTitle`, `birthday`, `notes`, and lists `emails` / `phones` / `websites` / `dates` / `addresses` / `customFields` (each list item has `label` / `value`).

## Notes

Admin-only free-form notes with per-identity grants (no wildcard).

```bash
inkbox notes list [--q <query>] [--identity <uuid>] [--order recent|created] [--limit <n>] [--offset <n>]
inkbox notes get <note-id>
inkbox notes create --body <text> [--title <text>]
inkbox notes update <note-id> [--title <text>] [--body <text>]   # pass --title "" to clear
inkbox notes delete <note-id>

# Per-note access grants
inkbox notes access list <note-id>
inkbox notes access grant <note-id> <identity-id>    # admin + JWT only
inkbox notes access revoke <note-id> <identity-id>
```

## Whoami, Signing Keys, Webhooks

```bash
inkbox whoami
inkbox signing-key create
inkbox webhook verify --payload <payload> --secret <secret> -H "X-Header: value"
```

Use `whoami --json` when you need the authenticated caller shape exactly.

## Practical Guidance

- Prefer the local repo command `npm --prefix cli run dev -- ...` when working in this codebase.
- Prefer `--json` for anything that needs stable parsing.
- Use the identity handle, not mailbox address or phone number, for identity-scoped commands.
- If a command fails because the identity lacks a mailbox or phone number, inspect it first with `inkbox identity get <handle>`.
