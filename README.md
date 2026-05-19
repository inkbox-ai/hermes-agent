<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Inkbox-powered Hermes Agent ☤

<p align="center">
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/Upstream-NousResearch%2Fhermes--agent-blueviolet?style=for-the-badge" alt="Upstream"></a>
  <a href="https://inkbox.ai"><img src="https://img.shields.io/badge/Powered%20by-Inkbox-FFD700?style=for-the-badge" alt="Inkbox"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
</p>

A fork of [Nous Research's Hermes Agent](https://github.com/NousResearch/hermes-agent) wired up to [Inkbox](https://inkbox.ai). The agent runtime is the same — the difference is every install ships with a **real email address, real phone number, and tunnel runtime** out of the box.

## Quick Install

One command. Installs deps, then walks you through Inkbox self-signup (no account prerequisite), provisions email + phone, and installs the gateway as a background service.

**Linux / macOS / WSL2 / Termux:**

```bash
curl -fsSL https://raw.githubusercontent.com/inkbox-ai/hermes-agent/inkbox/scripts/install.sh | bash
```

**Windows (early beta):**

```powershell
<<<<<<< HEAD
irm https://raw.githubusercontent.com/inkbox-ai/hermes-agent/inkbox/scripts/install.ps1 | iex
=======
iex (irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1)
>>>>>>> origin/main
```

**By the end your agent has:**

- A real email address (`yourname@inkboxmail.com`) it can send and receive from
- A real phone number that handles inbound SMS and voice
- A persistent identity + contact list that survives across sessions
- A background service that auto-starts on boot, with HMAC-verified webhooks

## What this fork adds

| Capability | Upstream Hermes | This fork |
| --- | --- | --- |
| Chat platforms (Telegram, Slack, Discord, …) | 🟢 | 🟢 |
| Real email address | 🟡 DIY (SMTP) | 🟢 |
| Real phone (SMS + voice) | 🟡 DIY (Twilio + SIP) | 🟢 |
| Public webhook tunnel | 🟡 DIY (ngrok / server) | 🟢 |
| Persistent contact list | 🟡 DIY (agent memory) | 🟢 |

Specifically:

- The `inkbox` SDK and `aiohttp` are **core dependencies** (no `[inkbox]` extra to remember)
- `hermes gateway setup` runs Inkbox self-signup inline, mints the API key, writes it to `~/.hermes/.env`
- Tunnel runtime + gateway adapter built in — the agent receives webhooks without you hosting a server
- Gateway service installer wires the runtime into systemd / launchd / Scheduled Task
- HMAC signature verification on inbound webhooks + tunnel traffic

If you want **just the Hermes runtime** and prefer to bring your own SMTP / Twilio / SIP plumbing, use upstream at [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).

## Synced daily with upstream

We try to sync from [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) daily.

## Hermes itself

Everything beyond the Inkbox wiring — the CLI, supported models, skills, memory, tools, terminal backends, scheduled jobs, RL training, contributing — is unchanged from upstream:

<<<<<<< HEAD
- **Repo:** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
- **Docs:** [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/)
- **Discord:** [NousResearch](https://discord.gg/NousResearch)
=======
| Action | CLI | Messaging platforms |
|---------|-----|---------------------|
| Start chatting | `hermes` | Run `hermes gateway setup` + `hermes gateway start`, then send the bot a message |
| Start fresh conversation | `/new` or `/reset` | `/new` or `/reset` |
| Change model | `/model [provider:model]` | `/model [provider:model]` |
| Set a personality | `/personality [name]` | `/personality [name]` |
| Retry or undo the last turn | `/retry`, `/undo` | `/retry`, `/undo` |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]` |
| Browse skills | `/skills` or `/<skill-name>` | `/<skill-name>` |
| Interrupt current work | `Ctrl+C` or send a new message | `/stop` or send a new message |
| Platform-specific status | `/platforms` | `/status`, `/sethome` |

For the full command lists, see the [CLI guide](https://hermes-agent.nousresearch.com/docs/user-guide/cli) and the [Messaging Gateway guide](https://hermes-agent.nousresearch.com/docs/user-guide/messaging).

---

## Documentation

All documentation lives at **[hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs/)**:

| Section | What's Covered |
|---------|---------------|
| [Quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart) | Install → setup → first conversation in 2 minutes |
| [CLI Usage](https://hermes-agent.nousresearch.com/docs/user-guide/cli) | Commands, keybindings, personalities, sessions |
| [Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) | Config file, providers, models, all options |
| [Messaging Gateway](https://hermes-agent.nousresearch.com/docs/user-guide/messaging) | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://hermes-agent.nousresearch.com/docs/user-guide/security) | Command approval, DM pairing, container isolation |
| [Tools & Toolsets](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools) | 40+ tools, toolset system, terminal backends |
| [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) | Procedural memory, Skills Hub, creating skills |
| [Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) | Persistent memory, user profiles, best practices |
| [MCP Integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) | Connect any MCP server for extended capabilities |
| [Cron Scheduling](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) | Scheduled tasks with platform delivery |
| [Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files) | Project context that shapes every conversation |
| [Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture) | Project structure, agent loop, key classes |
| [Contributing](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing) | Development setup, PR process, code style |
| [CLI Reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) | All commands and flags |
| [Environment Variables](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) | Complete env var reference |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, Hermes can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`hermes setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
hermes claw migrate              # Interactive migration (full preset)
hermes claw migrate --dry-run    # Preview what would be migrated
hermes claw migrate --preset user-data   # Migrate without secrets
hermes claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:
- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.hermes/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `hermes claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See the [Contributing Guide](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing) for development setup, code style, and PR process.

Quick start for contributors — clone and go with `setup-hermes.sh`:

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
./setup-hermes.sh     # installs uv, creates venv, installs .[all], symlinks ~/.local/bin/hermes
./hermes              # auto-detects the venv, no need to `source` first
```

Manual path (equivalent to the above):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

---

## Community

- 💬 [Discord](https://discord.gg/NousResearch)
- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://github.com/NousResearch/hermes-agent/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Linux desktop-control MCP server for Hermes and other MCP hosts, with AT-SPI accessibility trees, Wayland/X11 input, screenshots, and compositor window targeting.
- 🔌 [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — Community WeChat bridge: Run Hermes Agent and OpenClaw on the same WeChat account.

---
>>>>>>> origin/main

## License

MIT — same as upstream. See [LICENSE](LICENSE).
