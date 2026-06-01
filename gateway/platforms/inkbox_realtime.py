"""Inkbox ↔ OpenAI Realtime API voice bridge.

When ``inkbox.realtime.enabled`` is true and an OpenAI API key is configured,
the call WebSocket handler in :mod:`gateway.platforms.inkbox` delegates inbound
calls to :func:`run_inkbox_realtime_bridge` instead of using Inkbox-side STT/TTS.

The bridge:

1. Accepts the Inkbox call WS with ``x-use-inkbox-text-to-speech: false`` and
   ``x-use-inkbox-speech-to-text: false`` headers, so Inkbox forwards raw
   G.711 μ-law @ 8 kHz frames in both directions.
2. Opens an OpenAI Realtime API WebSocket
   (``wss://api.openai.com/v1/realtime?model=<model>``) and sends
   ``session.update`` configuring tools, instructions, and the
   ``g711_ulaw`` input/output audio format.
3. Bridges audio bidirectionally: Inkbox → OpenAI as
   ``input_audio_buffer.append`` events, OpenAI → Inkbox as
   ``media`` frames carrying ``response.audio.delta`` payloads.
4. Exposes two tools to the realtime model:

   - ``hermes_agent_consult`` — pauses the conversation, dispatches a synthetic
     SMS-mode turn through Hermes' main agent loop, and submits the agent's
     reply as the tool result so the realtime model can speak it.
   - ``register_post_call_action`` — queues a follow-up task. When the call
     ends, all queued actions are dispatched as a single synthetic SMS-mode
     turn so the main agent can execute them (send email, create note, etc.).

The bridge owns the OpenAI Realtime WebSocket for the duration of one call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

try:
    import aiohttp
except ImportError:  # pragma: no cover — aiohttp is a core dep on this fork
    aiohttp = None  # type: ignore

logger = logging.getLogger(__name__)

REALTIME_URL = "wss://api.openai.com/v1/realtime"
# GA Realtime model. The GA models (gpt-realtime, gpt-realtime-2) use the
# *nested* session
# schema (audio.input / audio.output) — NOT the older flat
# input_audio_format / output_audio_format shape used by the beta
# gpt-4o-realtime-preview models. See _send_session_update.
DEFAULT_MODEL = "gpt-realtime-2"
# "cedar" and "marin" are the recommended high-quality GA Realtime voices.
DEFAULT_VOICE = "cedar"

# OpenAI endpoint that exchanges an OAuth/ChatGPT access token for an
# ephemeral Realtime client secret. Lets the bridge use the agent's existing
# Codex/ChatGPT OAuth credentials instead of requiring a separate sk- API key.
REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
# Telephony audio is G.711 μ-law @ 8 kHz. The GA session schema expects an
# audio-format *object*, not the legacy "g711_ulaw" string.
AUDIO_FORMAT_TELEPHONY = {"type": "audio/pcmu"}
# Transcription model for inbound caller audio (nested under audio.input).
INPUT_TRANSCRIPTION_MODEL = "whisper-1"

AGENT_CONSULT_TOOL_NAME = "hermes_agent_consult"
POST_CALL_ACTION_TOOL_NAME = "register_post_call_action"

# How long to wait for the agent_consult tool to complete before giving up and
# returning an error tool result. The realtime model is sitting idle while this
# runs; longer values risk dead air, shorter values cut off legitimate work.
DEFAULT_CONSULT_TIMEOUT_S = 60.0


# ─────────────────────────────────────────────────────────────────────────────
# Tool schema definitions
# ─────────────────────────────────────────────────────────────────────────────


def _agent_consult_tool_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "name": AGENT_CONSULT_TOOL_NAME,
        "description": (
            "Pause the live voice conversation and ask the main Hermes agent to "
            "do tool work that requires the full agent loop (look up an email, "
            "search session history, check the calendar, hit an API, run a "
            "computation, draft a long-form reply, etc.). The result you "
            "receive is the agent's spoken-friendly answer; read it back to "
            "the caller. Use this whenever the caller asks for something that "
            "needs current external data, persistent memory, or a tool call. "
            "Do NOT use it for greetings, small talk, or generic answers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to ask the main agent in plain English. Include "
                        "enough context that the agent can act standalone."
                    ),
                },
            },
            "required": ["query"],
        },
    }


def _post_call_action_tool_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "name": POST_CALL_ACTION_TOOL_NAME,
        "description": (
            "Register work the main Hermes agent must do after this phone "
            "call ends — send an email/SMS follow-up, create a note, update "
            "a contact, etc. Tell the caller the action is queued for after "
            "the call; do NOT claim it's already done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "Plain-English task for the main agent. Include the "
                        "channel, recipient, and outcome."
                    ),
                },
                "details": {
                    "type": "string",
                    "description": "Optional draft text, hints, or constraints.",
                },
            },
            "required": ["action"],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RealtimeCallMeta:
    """Per-call metadata threaded through to tool handlers."""

    call_id: str
    contact_id: str
    contact_name: str
    remote_phone_number: Optional[str]
    direction: str  # "inbound" or "outbound"
    agent_identity_email: Optional[str] = None
    agent_identity_phone: Optional[str] = None
    # Full resolved contact record so the model knows who it's talking to
    # without a mid-call lookup.
    contact_emails: List[str] = field(default_factory=list)
    contact_phones: List[str] = field(default_factory=list)
    contact_company: Optional[str] = None
    contact_notes: Optional[str] = None
    outbound_purpose: Optional[str] = None
    outbound_opening: Optional[str] = None
    # Richer outbound-call context loaded from the call-context file
    # (``$HERMES_HOME/inkbox_call_contexts/<token>.json``). These are the
    # keys the legacy text-mode call handler reads, so realtime calls keep
    # the same "why we called" continuity.
    outbound_reason: Optional[str] = None
    outbound_scheduled_by: Optional[str] = None
    outbound_conversation_summary: Optional[str] = None


@dataclass
class RealtimeConfig:
    """Per-account realtime voice configuration.

    Populated from ``platforms.inkbox.realtime`` in config.yaml, with env
    overrides on a few common fields.
    """

    enabled: bool = False
    # Standard OpenAI Platform API key (sk-...). Used directly as the WS
    # bearer when present.
    api_key: str = ""
    # ChatGPT/Codex OAuth access token. When there's no api_key, this is
    # exchanged for an ephemeral Realtime client secret (see
    # _resolve_realtime_bearer). Lets the agent's existing Codex login drive
    # realtime without a separate API key.
    oauth_token: str = ""
    model: str = DEFAULT_MODEL
    voice: str = DEFAULT_VOICE
    additional_instructions: str = ""
    consult_timeout_s: float = DEFAULT_CONSULT_TIMEOUT_S
    # ``api.openai.com`` by default; override for Azure / proxies.
    base_url: str = REALTIME_URL

    @property
    def has_credential(self) -> bool:
        return bool(self.api_key or self.oauth_token)


@dataclass
class _ToolCallEvent:
    name: str
    call_id: str
    arguments_json: str


@dataclass
class _BridgeState:
    transcript: List[Tuple[str, str]] = field(default_factory=list)
    post_call_actions: List[Dict[str, str]] = field(default_factory=list)
    last_response_id: Optional[str] = None
    closed: bool = False
    greeting_triggered: bool = False
    # Inkbox-assigned stream id from the `start` event; echoed on outbound
    # media / audio_done frames.
    stream_id: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Instruction builder
# ─────────────────────────────────────────────────────────────────────────────


def build_realtime_instructions(
    meta: RealtimeCallMeta,
    additional_instructions: str = "",
) -> str:
    """Compose the system prompt sent to the realtime model.

    Gives the model a clear identity, caller context, when to call the two
    tools, and a directive to keep replies short and spoken-friendly.
    """
    lines: List[str] = [
        "You are the configured Hermes agent speaking on a live Inkbox phone call.",
        "Use natural, concise spoken replies. Keep most answers to one or two short sentences.",
        "Do not mention implementation details unless the caller asks.",
    ]
    if meta.agent_identity_email:
        lines.append(f"Your email identity: {meta.agent_identity_email}.")
    if meta.agent_identity_phone:
        lines.append(f"Your phone number: {meta.agent_identity_phone}.")
    if meta.remote_phone_number:
        lines.append(f"Caller is calling from: {meta.remote_phone_number}.")
    if meta.contact_name and meta.contact_name not in ("unknown", ""):
        lines.append(
            "You already know who this is — do NOT look them up or ask for "
            "details you already have below.",
        )
        lines.append(f"Caller name: {meta.contact_name}.")
        if meta.contact_emails:
            lines.append(f"Caller email(s): {', '.join(meta.contact_emails)}.")
        if meta.contact_phones:
            lines.append(f"Caller phone(s) on file: {', '.join(meta.contact_phones)}.")
        if meta.contact_company:
            lines.append(f"Caller company: {meta.contact_company}.")
        if meta.contact_notes:
            lines.append(f"Notes about the caller: {meta.contact_notes}")
    else:
        lines.append(
            "No matching contact record is loaded; use the phone number or a neutral greeting.",
        )
    if meta.direction == "outbound":
        if meta.outbound_purpose:
            lines.append(f"This is an outbound call you placed. Purpose: {meta.outbound_purpose}")
        if meta.outbound_reason:
            lines.append(f"Reason for the call: {meta.outbound_reason}")
        if meta.outbound_scheduled_by:
            lines.append(f"This call was scheduled by: {meta.outbound_scheduled_by}")
        if meta.outbound_conversation_summary:
            lines.append(
                f"Summary of the prior conversation that led to this call:\n"
                f"{meta.outbound_conversation_summary}",
            )
        if meta.outbound_opening:
            lines.append(
                f"Preferred opening message (say this naturally as your first turn): "
                f"{meta.outbound_opening}",
            )
        lines.append(
            "For outbound calls, do not open with a generic offer to help. "
            "Start by explaining why you are calling, then ask the next specific question.",
        )
    lines.extend([
        "Do not perform a context lookup before greeting the caller. Do not say you "
        "are waiting on a lookup or checking context.",
        f"If the caller asks for work to happen after the call, call "
        f"{POST_CALL_ACTION_TOOL_NAME}. Tell the caller the action is queued for "
        f"after the call; do not claim it has already been completed.",
        f"Call {AGENT_CONSULT_TOOL_NAME} only when the caller asks for current "
        f"external data, session history, a calendar lookup, or other work that "
        f"requires the full Hermes agent. Do not call it for greetings, identity, "
        f"or generic chat.",
    ])
    if additional_instructions.strip():
        lines.append(additional_instructions.strip())
    return "\n".join(lines)


def build_realtime_greeting(meta: RealtimeCallMeta) -> str:
    """Build the instruction for the proactive opening line.

    Realtime calls must not start with silence — the model should greet first.
    Inbound: a short friendly greeting. Outbound: lead with the configured
    opening message / purpose so the callee immediately knows why we called.
    """
    first_name = ""
    if meta.contact_name and meta.contact_name not in ("unknown", ""):
        first_name = meta.contact_name.split()[0]

    if meta.direction == "outbound":
        if meta.outbound_opening:
            return (
                "Open the call by saying this naturally as the very first thing, "
                "with no greeting before it:\n" + meta.outbound_opening
            )
        if meta.outbound_purpose:
            return (
                "Open the call by greeting the person and immediately explaining "
                f"why you are calling: {meta.outbound_purpose}"
            )
        return (
            "Open the call by greeting the person and explaining why you are "
            "calling. Be specific and concise."
        )

    # Inbound.
    who = f" {first_name}" if first_name else ""
    return (
        f"Greet the caller now as the very first thing you say. Say something "
        f"like 'Hi{who}, this is your Hermes agent — how can I help?' Keep it to "
        f"one short sentence and then wait for them to respond."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────────────────────────────────────


# Type alias for the agent-consult callback. The bridge calls this when the
# realtime model invokes hermes_agent_consult; the platform supplies an
# implementation that runs a synthetic SMS-mode turn through the main agent
# and returns the agent's reply as plain text.
AgentConsultCallback = Callable[
    [RealtimeCallMeta, str, List[Tuple[str, str]]],
    Awaitable[str],
]

# Called when the call ends, with the accumulated post-call actions list.
# Platform dispatches them as a synthetic SMS-mode turn to the main agent.
PostCallActionsCallback = Callable[
    [RealtimeCallMeta, List[Dict[str, str]], List[Tuple[str, str]]],
    Awaitable[None],
]

# Called when the realtime call WebSocket has ended, regardless of whether the
# model explicitly registered post-call actions. This mirrors the legacy
# Inkbox STT/TTS path's [call_ended] reflection so commitments made during a
# realtime call can still be followed up safely.
CallEndedCallback = Callable[
    [RealtimeCallMeta, List[Tuple[str, str]]],
    Awaitable[None],
]


async def run_inkbox_realtime_bridge(
    *,
    inkbox_ws: Any,
    config: RealtimeConfig,
    meta: RealtimeCallMeta,
    on_agent_consult: AgentConsultCallback,
    on_post_call_actions: PostCallActionsCallback,
    on_call_ended: CallEndedCallback,
) -> None:
    """Run the bridge for the duration of one call.

    Returns when either side closes the WebSocket. Caller is responsible for
    accepting ``inkbox_ws`` *with* the correct realtime headers before invoking
    this function — see :func:`accept_realtime_inkbox_ws`.

    Errors are logged; the function does not re-raise so a partial failure
    doesn't crash the gateway's WS handler chain.
    """
    if aiohttp is None:
        logger.error("[Inkbox realtime] aiohttp not available; cannot open Realtime API WS")
        return
    if not config.has_credential:
        logger.error("[Inkbox realtime] No OpenAI credential (api_key or oauth_token); refusing to bridge")
        return

    state = _BridgeState()
    url = f"{config.base_url}?model={config.model}"

    session = aiohttp.ClientSession()
    try:
        try:
            bearer = await _resolve_realtime_bearer(session, config)
        except Exception as exc:
            logger.error("[Inkbox realtime] Could not resolve OpenAI bearer: %s", exc)
            return
        if not bearer:
            return

        # GA Realtime drops the OpenAI-Beta: realtime=v1 header.
        headers = {"Authorization": f"Bearer {bearer}"}
        try:
            openai_ws = await session.ws_connect(url, headers=headers, heartbeat=30)
        except Exception as exc:
            logger.error("[Inkbox realtime] Failed to connect to OpenAI Realtime: %s", exc)
            return

        try:
            await _send_session_update(openai_ws, config, meta)
            # Two concurrent pumps:
            inkbox_task = asyncio.create_task(
                _inkbox_to_openai_pump(inkbox_ws, openai_ws, state, meta),
                name=f"realtime-inkbox-pump-{meta.call_id}",
            )
            openai_task = asyncio.create_task(
                _openai_to_inkbox_pump(
                    openai_ws=openai_ws,
                    inkbox_ws=inkbox_ws,
                    state=state,
                    config=config,
                    meta=meta,
                    on_agent_consult=on_agent_consult,
                ),
                name=f"realtime-openai-pump-{meta.call_id}",
            )

            done, pending = await asyncio.wait(
                {inkbox_task, openai_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    logger.warning(
                        "[Inkbox realtime] Pump %s raised: %s",
                        task.get_name(),
                        exc,
                    )
        finally:
            state.closed = True
            try:
                await openai_ws.close()
            except Exception:
                pass

        await _dispatch_post_call(state, meta, on_post_call_actions, on_call_ended)
    finally:
        await session.close()


async def _dispatch_post_call(
    state: _BridgeState,
    meta: RealtimeCallMeta,
    on_post_call_actions: "PostCallActionsCallback",
    on_call_ended: "CallEndedCallback",
) -> None:
    """Dispatch exactly ONE follow-up turn after a call ends.

    Queued post-call actions take priority; otherwise the generic
    [call_ended] reflection runs. Running both can double-execute the same
    commitment (two agent turns each sending the same email/SMS).
    """
    if state.post_call_actions:
        try:
            await on_post_call_actions(meta, state.post_call_actions, list(state.transcript))
        except Exception as exc:
            logger.warning("[Inkbox realtime] Post-call action dispatch failed: %s", exc)
    else:
        try:
            await on_call_ended(meta, list(state.transcript))
        except Exception as exc:
            logger.warning("[Inkbox realtime] Call-ended dispatch failed: %s", exc)


async def _resolve_realtime_bearer(
    session: Any, config: RealtimeConfig,
) -> str:
    """Return the bearer to use on the Realtime WS.

    Prefers a standard ``sk-`` API key. Otherwise exchanges the ChatGPT/Codex
    OAuth token for an ephemeral Realtime client secret.
    """
    if config.api_key:
        return config.api_key

    body = {
        "session": {
            "type": "realtime",
            "model": config.model,
            "audio": {"output": {"voice": config.voice}},
        },
    }
    headers = {
        "Authorization": f"Bearer {config.oauth_token}",
        "Content-Type": "application/json",
    }
    async with session.post(
        REALTIME_CLIENT_SECRETS_URL, headers=headers, json=body,
    ) as resp:
        if resp.status >= 400:
            detail = (await resp.text())[:200]
            raise RuntimeError(f"client_secrets HTTP {resp.status}: {detail}")
        data = await resp.json()
    secret = data.get("value")
    if not secret and isinstance(data.get("client_secret"), dict):
        secret = data["client_secret"].get("value")
    if not secret:
        raise RuntimeError("client_secrets response had no value")
    return str(secret)


async def _maybe_send_greeting(
    openai_ws: Any, state: _BridgeState, meta: RealtimeCallMeta,
) -> None:
    """Fire the proactive opening line once, so calls don't start with silence."""
    if state.greeting_triggered:
        return
    state.greeting_triggered = True
    try:
        # No modalities field here — it inherits the session's
        # output_modalities. Passing output_modalities inside response.create
        # is rejected by GA models (it's a session-level field).
        await openai_ws.send_str(json.dumps({
            "type": "response.create",
            "response": {"instructions": build_realtime_greeting(meta)},
        }))
        logger.info(
            "[Inkbox realtime] greeting sent for call_id=%s direction=%s",
            meta.call_id, meta.direction,
        )
    except Exception as exc:
        logger.debug("[Inkbox realtime] greeting send failed: %s", exc)


async def _send_session_update(
    openai_ws: Any, config: RealtimeConfig, meta: RealtimeCallMeta,
) -> None:
    """Send the initial ``session.update`` to configure the OpenAI Realtime session.

    Uses the GA session schema (``type: "realtime"``, ``output_modalities``,
    nested ``audio.input`` / ``audio.output``) required by gpt-realtime /
    gpt-realtime-2. The legacy flat ``input_audio_format`` / ``modalities``
    shape is only accepted by the older beta preview models and would be
    rejected by GA.
    """
    instructions = build_realtime_instructions(meta, config.additional_instructions)
    payload = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": config.model,
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": AUDIO_FORMAT_TELEPHONY,
                    "noise_reduction": None,
                    "transcription": {"model": INPUT_TRANSCRIPTION_MODEL},
                    # Server-side VAD — the model auto-detects caller speech
                    # start/stop and decides when to respond. The bridge does
                    # NOT manually trigger response.create per turn.
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {
                    "format": AUDIO_FORMAT_TELEPHONY,
                    "voice": config.voice,
                },
            },
            "tools": [
                _agent_consult_tool_schema(),
                _post_call_action_tool_schema(),
            ],
            "tool_choice": "auto",
        },
    }
    await openai_ws.send_str(json.dumps(payload))


async def _inkbox_to_openai_pump(
    inkbox_ws: Any, openai_ws: Any, state: _BridgeState, meta: RealtimeCallMeta,
) -> None:
    """Forward caller audio from Inkbox to OpenAI; fire the opening greeting.

    Inkbox sends frames as ``{"event": "media", "media": {"payload": "<b64>"}}``.
    We re-emit as ``input_audio_buffer.append``; server-side VAD handles turns.
    The proactive greeting fires once on the ``start`` event, or on first media
    if no ``start`` is sent.
    """
    async for msg in inkbox_ws:
        if state.closed:
            return
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                frame = json.loads(msg.data)
            except (TypeError, ValueError):
                continue
            event = (frame.get("event") or "").lower()
            if event == "start":
                state.stream_id = frame.get("stream_id") or state.stream_id
                await _maybe_send_greeting(openai_ws, state, meta)
            elif event == "media":
                if not state.greeting_triggered:
                    await _maybe_send_greeting(openai_ws, state, meta)
                payload_b64 = (frame.get("media") or {}).get("payload")
                if payload_b64:
                    await openai_ws.send_str(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": payload_b64,
                    }))
            elif event in {"stop", "closed", "hangup"}:
                logger.info("[Inkbox realtime] Inkbox WS signaled %s", event)
                return
        elif msg.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
            return


async def _openai_to_inkbox_pump(
    *,
    openai_ws: Any,
    inkbox_ws: Any,
    state: _BridgeState,
    config: RealtimeConfig,
    meta: RealtimeCallMeta,
    on_agent_consult: AgentConsultCallback,
) -> None:
    """Forward audio + handle tool calls from OpenAI back to Inkbox."""
    # Accumulator for streaming function call arguments. The Realtime API
    # delivers args as a sequence of `response.function_call_arguments.delta`
    # events terminated by `response.function_call_arguments.done`; we collect
    # by call_id then dispatch on done.
    pending_calls: Dict[str, str] = {}

    async for msg in openai_ws:
        if state.closed:
            return
        if msg.type != aiohttp.WSMsgType.TEXT:
            if msg.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                return
            continue
        try:
            frame = json.loads(msg.data)
        except (TypeError, ValueError):
            continue
        if not isinstance(frame, dict):
            continue
        ftype = frame.get("type", "")

        # GA emits ``response.output_audio.delta``; beta ``response.audio.delta``.
        if ftype in ("response.output_audio.delta", "response.audio.delta"):
            # Already μ-law base64. Forward as an outbound Inkbox media frame,
            # echoing the stream_id and tagging the track per the Inkbox media
            # protocol.
            delta_b64 = frame.get("delta") or ""
            if delta_b64:
                out = {
                    "event": "media",
                    "media": {"payload": delta_b64, "track": "outbound"},
                }
                if state.stream_id:
                    out["stream_id"] = state.stream_id
                try:
                    await inkbox_ws.send_str(json.dumps(out))
                except Exception as exc:
                    logger.debug("[Inkbox realtime] Inkbox WS send failed: %s", exc)
                    return

        # Outbound audio for a response finished — tell Inkbox to flush/play.
        elif ftype in ("response.output_audio.done", "response.audio.done"):
            done = {"event": "audio_done"}
            if state.stream_id:
                done["stream_id"] = state.stream_id
            try:
                await inkbox_ws.send_str(json.dumps(done))
            except Exception:
                pass

        # Caller started speaking (barge-in) — drop any queued outbound audio.
        elif ftype == "input_audio_buffer.speech_started":
            try:
                await inkbox_ws.send_str(json.dumps({"event": "clear"}))
            except Exception:
                pass

        # GA: response.output_audio_transcript.done; beta: response.audio_transcript.done
        elif ftype in (
            "response.output_audio_transcript.done",
            "response.audio_transcript.done",
        ):
            text = (frame.get("transcript") or "").strip()
            if text:
                state.transcript.append(("agent", text))

        elif ftype == "conversation.item.input_audio_transcription.completed":
            text = (frame.get("transcript") or "").strip()
            if text:
                state.transcript.append(("caller", text))

        elif ftype == "response.function_call_arguments.delta":
            call_id = frame.get("call_id") or ""
            delta = frame.get("delta") or ""
            if call_id:
                pending_calls[call_id] = pending_calls.get(call_id, "") + delta

        elif ftype == "response.function_call_arguments.done":
            call_id = frame.get("call_id") or ""
            name = frame.get("name") or ""
            args_json = (
                frame.get("arguments")
                or pending_calls.pop(call_id, "")
                or "{}"
            )
            await _dispatch_tool_call(
                openai_ws=openai_ws,
                call_id=call_id,
                name=name,
                arguments_json=args_json,
                state=state,
                config=config,
                meta=meta,
                on_agent_consult=on_agent_consult,
            )

        elif ftype == "response.done":
            resp = frame.get("response") or {}
            rid = resp.get("id")
            if rid:
                state.last_response_id = rid

        elif ftype == "error":
            err = frame.get("error") or frame
            logger.warning("[Inkbox realtime] OpenAI error event: %s", err)

        # All other event types (session.created, session.updated,
        # rate_limits.updated, response.output_item.added, …) are ignored.


async def _dispatch_tool_call(
    *,
    openai_ws: Any,
    call_id: str,
    name: str,
    arguments_json: str,
    state: _BridgeState,
    config: RealtimeConfig,
    meta: RealtimeCallMeta,
    on_agent_consult: AgentConsultCallback,
) -> None:
    """Handle a function-call event from the realtime model."""
    try:
        args = json.loads(arguments_json or "{}")
    except (TypeError, ValueError):
        args = {}

    if name == POST_CALL_ACTION_TOOL_NAME:
        action = (args.get("action") or "").strip()
        if not action:
            await _submit_tool_result(
                openai_ws, call_id, {"error": "missing action argument"},
            )
            return
        state.post_call_actions.append({
            "action": action,
            "details": (args.get("details") or "").strip(),
        })
        await _submit_tool_result(openai_ws, call_id, {
            "status": "queued",
            "action_count": len(state.post_call_actions),
            "message": (
                "Action queued for after the call. Tell the caller the action "
                "is queued; do not claim it is already done."
            ),
        })
        return

    if name == AGENT_CONSULT_TOOL_NAME:
        query = (args.get("query") or "").strip()
        if not query:
            await _submit_tool_result(
                openai_ws, call_id, {"error": "missing query argument"},
            )
            return

        # OpenAI Realtime doesn't have a native "I'll continue later" mechanism
        # for one tool call, but we can ALSO inject an interim instruction so
        # the model says "one moment" while the agent thinks. The final tool
        # result is what the model uses to compose the actual spoken answer.
        try:
            # Override instructions for just this turn so the model says a
            # short filler line while the agent runs. No modalities field —
            # it inherits the session's output_modalities (GA rejects
            # output_modalities inside response.create).
            await openai_ws.send_str(json.dumps({
                "type": "response.create",
                "response": {
                    "instructions": (
                        "Say only 'One moment.' Do not mention waiting for "
                        "context or checking a lookup."
                    ),
                },
            }))
        except Exception:
            # The interim cue is best-effort; the final tool result is
            # authoritative.
            pass

        try:
            answer = await asyncio.wait_for(
                on_agent_consult(meta, query, list(state.transcript)),
                timeout=config.consult_timeout_s,
            )
        except asyncio.TimeoutError:
            await _submit_tool_result(openai_ws, call_id, {
                "error": "agent_consult timed out",
                "message": (
                    "Tell the caller you couldn't get an answer right now. "
                    "Offer to follow up after the call."
                ),
            })
            return
        except Exception as exc:
            logger.warning("[Inkbox realtime] agent_consult failed: %s", exc)
            await _submit_tool_result(openai_ws, call_id, {
                "error": f"agent_consult error: {exc}",
                "message": "Apologize briefly and ask if you can help another way.",
            })
            return

        await _submit_tool_result(openai_ws, call_id, {
            "status": "ok",
            "answer": answer,
            "instructions": (
                "Read the answer back to the caller in your own spoken voice. "
                "Keep it natural and concise."
            ),
        })
        return

    # Unknown tool — refuse politely.
    await _submit_tool_result(openai_ws, call_id, {
        "error": f"Tool '{name}' is not available on live calls.",
    })


async def _submit_tool_result(
    openai_ws: Any, call_id: str, output: Dict[str, Any],
) -> None:
    """Submit a function call output and trigger a model response.

    The OpenAI Realtime protocol takes function call output via a
    ``conversation.item.create`` event of type ``function_call_output``,
    followed by ``response.create`` so the model speaks based on the result.
    """
    try:
        await openai_ws.send_str(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(output),
            },
        }))
        # Bare response.create — let the session's configured output
        # modalities + audio settings apply. Passing a beta-style
        # ``modalities`` field here would be rejected by GA models.
        await openai_ws.send_str(json.dumps({
            "type": "response.create",
        }))
    except Exception as exc:
        logger.debug("[Inkbox realtime] submit_tool_result failed: %s", exc)
