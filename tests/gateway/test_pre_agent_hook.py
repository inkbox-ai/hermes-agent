"""Tests for the generic gateway pre-agent-turn hook."""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import (
    GatewayConfig,
    Platform,
    PlatformConfig,
    PreAgentHookConfig,
    _apply_env_overrides,
    load_gateway_config,
)
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource, build_session_key


def _source(
    *,
    platform: Platform = Platform.TELEGRAM,
    chat_id: str = "chat-1",
    user_id: str = "user-1",
    user_name: str = "Tester",
    user_id_alt: str | None = None,
) -> SessionSource:
    return SessionSource(
        platform=platform,
        chat_id=chat_id,
        chat_type="dm",
        user_id=user_id,
        user_name=user_name,
        user_id_alt=user_id_alt,
    )


def _session_entry(source: SessionSource, *, session_id: str = "sess-1") -> SessionEntry:
    now = datetime.now(timezone.utc)
    return SessionEntry(
        session_key=build_session_key(source),
        session_id=session_id,
        created_at=now,
        updated_at=now,
        platform=source.platform,
        chat_type=source.chat_type,
    )


def _event(
    text: str = "hello",
    *,
    source: SessionSource | None = None,
    message_id: str = "msg-1",
    message_type: MessageType = MessageType.TEXT,
    raw_message=None,
) -> MessageEvent:
    source = source or _source()
    return MessageEvent(
        text=text,
        source=source,
        message_type=message_type,
        raw_message=raw_message,
        message_id=message_id,
        timestamp=datetime(2026, 5, 16, 21, 20, tzinfo=timezone.utc),
    )


def _runner(
    *,
    config: GatewayConfig | None = None,
    source: SessionSource | None = None,
    session_entry: SessionEntry | None = None,
):
    from gateway.run import GatewayRunner

    source = source or _source()
    session_entry = session_entry or _session_entry(source)
    runner = object.__new__(GatewayRunner)
    runner.config = config or GatewayConfig(
        platforms={source.platform: PlatformConfig(enabled=True, token="***")}
    )
    runner.adapters = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), emit_collect=AsyncMock())
    runner.hooks.loaded_hooks = []
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store._entries = {session_entry.session_key: session_entry}
    runner.session_store._ensure_loaded = MagicMock()
    runner.session_store.load_transcript.return_value = []
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()
    runner._session_sources = OrderedDict()
    runner._session_sources_max = 512
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner._session_db = None
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._busy_ack_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._pending_skills_reload_notes = {}
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._prefill_messages = None
    runner._ephemeral_system_prompt = ""
    runner._show_reasoning = False
    runner._set_session_env = lambda _context: []
    runner._clear_session_env = MagicMock()
    runner._prepare_inbound_message_text = AsyncMock(
        side_effect=lambda *, event, source, history: event.text
    )
    runner._bind_adapter_run_generation = MagicMock()
    runner._is_session_run_current = lambda _quick_key, _generation: True
    runner._should_send_voice_reply = lambda *_args, **_kwargs: False
    runner._send_voice_reply = AsyncMock()
    runner._deliver_media_from_response = AsyncMock()
    runner._clear_restart_failure_count = MagicMock()
    runner._draining = False
    runner._get_proxy_url = lambda: None
    runner._resolve_session_agent_runtime = lambda **_kwargs: ("test-model", {})
    runner._resolve_session_reasoning_config = lambda **_kwargs: None
    runner._resolve_turn_agent_config = lambda _message, model, runtime: {
        "model": model,
        "runtime": runtime,
        "request_overrides": None,
    }
    runner._load_service_tier = lambda: None
    runner._agent_cache_lock = None
    runner._agent_cache = None
    runner._init_cached_agent_for_turn = MagicMock()
    runner._enforce_agent_cache_cap = MagicMock()
    runner._consume_pending_native_image_paths = MagicMock(return_value=[])
    runner._evict_cached_agent = MagicMock()
    runner._is_intentional_model_switch = lambda *_args, **_kwargs: True
    runner._is_telegram_topic_lane = lambda *_args, **_kwargs: False
    runner._thread_metadata_for_source = lambda *_args, **_kwargs: None
    runner._reply_anchor_for_event = lambda event: getattr(event, "message_id", None)
    return runner


def _hook_config(
    *,
    platform: str = "telegram",
    channel: str = "text",
    command: str = "python hook.py",
) -> PreAgentHookConfig:
    return PreAgentHookConfig(
        enabled=True,
        command=command,
        timeout_seconds=1.0,
        platforms=(platform,),
        channels=(channel,),
    )


def test_pre_agent_hook_disabled_by_default():
    source = _source()
    runner = _runner(source=source)

    assert runner._pre_agent_hook_enabled(source, _event(source=source)) is False


def test_pre_agent_hook_env_config(monkeypatch):
    monkeypatch.setenv("HERMES_PRE_AGENT_HOOK_ENABLED", "true")
    monkeypatch.setenv("HERMES_PRE_AGENT_HOOK_COMMAND", "python bridge.py")
    monkeypatch.setenv("HERMES_PRE_AGENT_HOOK_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("HERMES_PRE_AGENT_HOOK_PLATFORMS", "inkbox, telegram")
    monkeypatch.setenv("HERMES_PRE_AGENT_HOOK_CHANNELS", "sms,text")

    config = GatewayConfig()
    _apply_env_overrides(config)

    assert config.pre_agent_hook.enabled is True
    assert config.pre_agent_hook.command == "python bridge.py"
    assert config.pre_agent_hook.timeout_seconds == 2.5
    assert config.pre_agent_hook.platforms == ("inkbox", "telegram")
    assert config.pre_agent_hook.channels == ("sms", "text")


def test_load_gateway_config_reads_nested_pre_agent_hook(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "gateway:\n"
        "  pre_agent_hook:\n"
        "    enabled: true\n"
        "    command: python bridge.py\n"
        "    timeout_seconds: 2\n"
        "    platforms: [inkbox]\n"
        "    channels: sms\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    config = load_gateway_config()

    assert config.pre_agent_hook.enabled is True
    assert config.pre_agent_hook.command == "python bridge.py"
    assert config.pre_agent_hook.timeout_seconds == 2.0
    assert config.pre_agent_hook.platforms == ("inkbox",)
    assert config.pre_agent_hook.channels == ("sms",)


@pytest.mark.asyncio
async def test_hook_command_receives_session_payload_before_auto_skill(tmp_path):
    seen_path = tmp_path / "seen.json"
    script = tmp_path / "hook.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "payload = json.load(sys.stdin)\n"
        f"pathlib.Path({str(seen_path)!r}).write_text(json.dumps(payload), encoding='utf-8')\n"
        "print(json.dumps({'ok': True, 'action': 'hold', 'outbound': {'mode': 'hold_no_reply'}}))\n",
        encoding="utf-8",
    )
    source = _source()
    config = GatewayConfig(
        pre_agent_hook=_hook_config(command=f"{sys.executable} {script}")
    )
    runner = _runner(config=config, source=source)
    runner._run_agent = AsyncMock(side_effect=AssertionError("agent should not run"))
    event = _event("Need a plumber", source=source)
    event.auto_skill = "inkbox"

    result = await runner._handle_message_with_agent(
        event, source, _quick_key="q", run_generation=1
    )

    payload = json.loads(seen_path.read_text(encoding="utf-8"))
    assert result is None
    assert payload["schema_version"] == "hermes.pre_agent_turn.v1"
    assert payload["hermes_session"]["session_key"] == build_session_key(source)
    assert payload["hermes_session"]["session_id"] == "sess-1"
    assert payload["message"]["body_text"] == "Need a plumber"
    assert event.text == "Need a plumber"
    runner._run_agent.assert_not_called()


@pytest.mark.asyncio
async def test_hold_skips_run_agent():
    source = _source()
    config = GatewayConfig(pre_agent_hook=_hook_config())
    runner = _runner(config=config, source=source)
    runner._run_pre_agent_hook = AsyncMock(
        return_value={"ok": True, "action": "hold", "outbound": {"mode": "hold_no_reply"}}
    )
    runner._run_agent = AsyncMock(side_effect=AssertionError("agent should not run"))

    result = await runner._handle_message_with_agent(
        _event(source=source), source, _quick_key="q", run_generation=1
    )

    assert result is None
    runner._run_agent.assert_not_called()


@pytest.mark.asyncio
async def test_reply_skips_run_agent_and_returns_exact_text():
    source = _source()
    config = GatewayConfig(pre_agent_hook=_hook_config())
    runner = _runner(config=config, source=source)
    runner._run_pre_agent_hook = AsyncMock(
        return_value={
            "ok": True,
            "action": "reply",
            "outbound": {"mode": "send_exact", "text": "Exact acknowledgement."},
        }
    )
    runner._run_agent = AsyncMock(side_effect=AssertionError("agent should not run"))

    result = await runner._handle_message_with_agent(
        _event(source=source), source, _quick_key="q", run_generation=1
    )

    assert result == "Exact acknowledgement."
    runner._run_agent.assert_not_called()


@pytest.mark.asyncio
async def test_non_live_continue_appends_hydration_to_context_prompt():
    source = _source()
    config = GatewayConfig(pre_agent_hook=_hook_config())
    runner = _runner(config=config, source=source)
    runner._run_pre_agent_hook = AsyncMock(
        return_value={
            "ok": True,
            "action": "continue",
            "hydration": {"prompt_context": "[External context]\nKnown fact."},
            "outbound": {"mode": "hold_no_reply"},
        }
    )
    runner._run_agent = AsyncMock(
        return_value={"final_response": "agent response", "messages": [], "api_calls": 1}
    )

    result = await runner._handle_message_with_agent(
        _event(source=source), source, _quick_key="q", run_generation=1
    )

    assert result == "agent response"
    context_prompt = runner._run_agent.await_args.kwargs["context_prompt"]
    assert "[External context]\nKnown fact." in context_prompt


def _inkbox_sms_source(chat_id: str, phone: str, name: str = "Alex") -> SessionSource:
    return _source(
        platform=Platform.INKBOX,
        chat_id=chat_id,
        user_id=chat_id,
        user_name=name,
        user_id_alt=phone,
    )


def _inkbox_raw(text_id: str, phone: str, text: str) -> dict:
    return {
        "event_type": "text.received",
        "data": {
            "text_message": {
                "id": text_id,
                "remote_phone_number": phone,
                "local_phone_number": "+18005550100",
                "text": text,
                "direction": "inbound",
                "created_at": "2026-05-16T21:20:00Z",
            }
        },
    }


class _QueueDrainAdapter:
    def __init__(self):
        self._pending_messages = {}
        self._active_sessions = {}
        self._post_delivery_callbacks = {}
        self.sent = []
        self.exact_replies = []

    def get_pending_message(self, session_key):
        return self._pending_messages.pop(session_key, None)

    def has_pending_interrupt(self, session_key):
        return session_key in self._pending_messages

    async def send(self, chat_id, content=None, *args, **kwargs):
        text = content if content is not None else (args[0] if args else "")
        self.sent.append((chat_id, text))
        return SimpleNamespace(success=True, message_id=f"send-{len(self.sent)}")

    async def _send_with_retry(self, *, chat_id, content, **kwargs):
        self.exact_replies.append((chat_id, content))
        return SimpleNamespace(success=True, message_id=f"reply-{len(self.exact_replies)}")

    async def send_typing(self, chat_id, **kwargs):
        return None


def _install_fast_agent(monkeypatch, responses, agent_calls):
    import gateway.run as run_mod
    import hermes_cli.tools_config as tools_config
    import run_agent

    monkeypatch.setattr(
        run_mod,
        "_load_gateway_config",
        lambda: {
            "agent": {"gateway_notify_interval": 0},
            "display": {"tool_progress": "off", "interim_assistant_messages": False},
        },
    )
    monkeypatch.setattr(run_mod, "_resolve_gateway_model", lambda: "test-model")
    monkeypatch.setattr(
        run_mod, "_reload_runtime_env_preserving_config_authority", lambda: None
    )
    monkeypatch.setattr(tools_config, "_get_platform_tools", lambda *_args, **_kwargs: set())

    class FastAgent:
        def __init__(self, *args, **kwargs):
            self.model = kwargs.get("model") or "test-model"
            self.session_id = kwargs.get("session_id")
            self.tools = []
            self.context_compressor = SimpleNamespace(
                last_prompt_tokens=0,
                context_length=0,
            )
            self.session_prompt_tokens = 0
            self.session_completion_tokens = 0
            self.is_interrupted = False
            self.ephemeral_system_prompt = kwargs.get("ephemeral_system_prompt") or ""

        def run_conversation(self, message, conversation_history=None, task_id=None):
            agent_calls.append((message, self.ephemeral_system_prompt))
            if not responses:
                raise AssertionError("unexpected extra agent run")
            return responses.pop(0)

        def interrupt(self, _reason=None):
            self.is_interrupted = True

        def get_activity_summary(self):
            return {
                "seconds_since_activity": 0,
                "api_call_count": 1,
                "max_iterations": 90,
                "last_activity_desc": "test",
            }

    monkeypatch.setattr(run_agent, "AIAgent", FastAgent)


async def _run_inline_executor(func, *args):
    return func(*args)


@pytest.mark.asyncio
async def test_live_inkbox_sms_continue_is_coerced_to_hold(caplog):
    caplog.set_level("INFO")
    source = _inkbox_sms_source("contact-1", "+15555550101")
    config = GatewayConfig(
        pre_agent_hook=_hook_config(platform="inkbox", channel="sms")
    )
    runner = _runner(config=config, source=source)
    runner._run_pre_agent_hook = AsyncMock(
        return_value={
            "ok": True,
            "action": "continue",
            "hydration": {"prompt_context": "context that must not be sent live"},
            "outbound": {"mode": "hold_no_reply"},
        }
    )
    runner._run_agent = AsyncMock(side_effect=AssertionError("agent should not run"))
    event = _event(
        "[inkbox:sms from=+15555550101 | contact_id=contact-1]\nNeed help",
        source=source,
        raw_message=_inkbox_raw("sms-1", "+15555550101", "Need help"),
        message_id="sms-1",
    )

    result = await runner._handle_message_with_agent(
        event, source, _quick_key="q", run_generation=1
    )

    assert result is None
    runner._run_agent.assert_not_called()
    assert "Need help" not in caplog.text
    assert "context that must not be sent live" not in caplog.text


@pytest.mark.asyncio
async def test_queued_inkbox_sms_continue_is_coerced_to_hold():
    source = _inkbox_sms_source("contact-1", "+15555550101")
    config = GatewayConfig(
        pre_agent_hook=_hook_config(platform="inkbox", channel="sms")
    )
    runner = _runner(config=config, source=source)
    runner._run_pre_agent_hook = AsyncMock(
        return_value={
            "ok": True,
            "action": "continue",
            "hydration": {"prompt_context": "context that must not be sent live"},
            "outbound": {"mode": "hold_no_reply"},
        }
    )
    event = _event(
        "[inkbox:sms from=+15555550101 | contact_id=contact-1]\nNeed help",
        source=source,
        raw_message=_inkbox_raw("sms-queued", "+15555550101", "Need help"),
        message_id="sms-queued",
    )

    action, _context, exact = await runner._apply_pre_agent_hook_for_turn(
        event=event,
        source=source,
        session_entry=_session_entry(source),
        session_key=build_session_key(source),
        context_prompt="base",
    )

    assert action == "hold"
    assert exact is None
    runner._run_pre_agent_hook.assert_awaited_once()


@pytest.mark.asyncio
async def test_queued_followup_drain_runs_hook_before_recursive_agent(monkeypatch):
    source = _source()
    session_entry = _session_entry(source)
    session_key = build_session_key(source)
    adapter = _QueueDrainAdapter()
    queued = _event("queued follow-up", source=source, message_id="queued-1")
    adapter._pending_messages[session_key] = queued
    config = GatewayConfig(pre_agent_hook=_hook_config())
    runner = _runner(config=config, source=source, session_entry=session_entry)
    runner.adapters[source.platform] = adapter
    runner._run_in_executor_with_context = _run_inline_executor

    agent_calls = []
    _install_fast_agent(
        monkeypatch,
        responses=[
            {
                "final_response": "first response",
                "messages": [{"role": "assistant", "content": "first response"}],
                "api_calls": 1,
            },
            {
                "final_response": "second response",
                "messages": [{"role": "assistant", "content": "second response"}],
                "api_calls": 1,
            },
        ],
        agent_calls=agent_calls,
    )

    hook_seen = []

    async def hook(payload):
        hook_seen.append(payload["message"]["body_text"])
        return {
            "ok": True,
            "action": "continue",
            "hydration": {"prompt_context": "[hook context for queued follow-up]"},
            "outbound": {"mode": "hold_no_reply"},
        }

    runner._run_pre_agent_hook = AsyncMock(side_effect=hook)

    result = await runner._run_agent(
        message="initial",
        context_prompt="base context",
        history=[],
        source=source,
        session_id=session_entry.session_id,
        session_key=session_key,
        run_generation=1,
    )

    assert result["final_response"] == "second response"
    assert hook_seen == ["queued follow-up"]
    assert [call[0] for call in agent_calls] == ["initial", "queued follow-up"]
    assert "[hook context for queued follow-up]" in agent_calls[1][1]


@pytest.mark.asyncio
async def test_multiple_queued_inkbox_sms_followups_keep_order_and_hit_hook(
    monkeypatch, caplog
):
    caplog.set_level("DEBUG")
    source = _inkbox_sms_source("contact-1", "+15555550101")
    session_entry = _session_entry(source)
    session_key = build_session_key(source)
    adapter = _QueueDrainAdapter()
    first = _event(
        "[inkbox:sms from=+15555550101 | contact_id=contact-1]\nPRIVATE FIRST BODY",
        source=source,
        raw_message=_inkbox_raw("sms-queued-1", "+15555550101", "PRIVATE FIRST BODY"),
        message_id="sms-queued-1",
    )
    second = _event(
        "[inkbox:sms from=+15555550101 | contact_id=contact-1]\nPRIVATE SECOND BODY",
        source=source,
        raw_message=_inkbox_raw("sms-queued-2", "+15555550101", "PRIVATE SECOND BODY"),
        message_id="sms-queued-2",
    )
    adapter._pending_messages[session_key] = first
    config = GatewayConfig(
        pre_agent_hook=_hook_config(platform="inkbox", channel="sms")
    )
    runner = _runner(config=config, source=source, session_entry=session_entry)
    runner.adapters[source.platform] = adapter
    runner._queued_events = {session_key: [second]}
    runner._run_in_executor_with_context = _run_inline_executor

    agent_calls = []
    _install_fast_agent(
        monkeypatch,
        responses=[
            {
                "final_response": "base one",
                "messages": [{"role": "assistant", "content": "base one"}],
                "api_calls": 1,
            },
            {
                "final_response": "base two",
                "messages": [{"role": "assistant", "content": "base two"}],
                "api_calls": 1,
            },
        ],
        agent_calls=agent_calls,
    )

    hook_ids = []

    async def hook(payload):
        hook_ids.append(payload["source"]["message_id"])
        return {
            "ok": True,
            "action": "reply",
            "outbound": {
                "mode": "send_exact",
                "text": f"exact reply for {payload['source']['message_id']}",
            },
        }

    runner._run_pre_agent_hook = AsyncMock(side_effect=hook)

    await runner._run_agent(
        message="initial one",
        context_prompt="base context",
        history=[],
        source=source,
        session_id=session_entry.session_id,
        session_key=session_key,
        run_generation=1,
    )
    await runner._run_agent(
        message="initial two",
        context_prompt="base context",
        history=[],
        source=source,
        session_id=session_entry.session_id,
        session_key=session_key,
        run_generation=1,
    )

    assert hook_ids == ["sms-queued-1", "sms-queued-2"]
    assert [reply[1] for reply in adapter.exact_replies] == [
        "exact reply for sms-queued-1",
        "exact reply for sms-queued-2",
    ]
    assert "PRIVATE FIRST BODY" not in caplog.text
    assert "PRIVATE SECOND BODY" not in caplog.text


def test_live_inkbox_sms_generate_draft_is_coerced_to_hold():
    source = _inkbox_sms_source("contact-1", "+15555550101")
    runner = _runner(source=source)
    event = _event(
        "[inkbox:sms from=+15555550101 | contact_id=contact-1]\nNeed help",
        source=source,
        raw_message=_inkbox_raw("sms-1", "+15555550101", "Need help"),
        message_id="sms-1",
    )

    action, _context, exact = runner._apply_pre_agent_hook_result(
        {
            "ok": True,
            "action": "reply",
            "outbound": {
                "mode": "generate_draft",
                "text": "This must not be sent live.",
            },
        },
        context_prompt="base",
        source=source,
        event=event,
        session_key=build_session_key(source),
    )

    assert action == "hold"
    assert exact is None


@pytest.mark.asyncio
async def test_auto_reset_flags_are_persisted_before_hook_hold():
    source = _source()
    session_entry = _session_entry(source)
    session_entry.was_auto_reset = True
    session_entry.auto_reset_reason = "idle"
    session_entry.is_fresh_reset = True
    config = GatewayConfig(pre_agent_hook=_hook_config())
    runner = _runner(config=config, source=source, session_entry=session_entry)
    runner._run_pre_agent_hook = AsyncMock(
        return_value={"ok": True, "action": "hold", "outbound": {"mode": "hold_no_reply"}}
    )
    runner._run_agent = AsyncMock(side_effect=AssertionError("agent should not run"))

    result = await runner._handle_message_with_agent(
        _event(source=source), source, _quick_key="q", run_generation=1
    )

    assert result is None
    assert session_entry.was_auto_reset is False
    assert session_entry.auto_reset_reason is None
    assert session_entry.is_fresh_reset is False
    runner.session_store._save.assert_called()
    runner._run_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("script_body", "expected_failure"),
    [
        ("import json; print('{')\n", "malformed_json"),
        ("import sys; sys.exit(7)\n", "nonzero_exit"),
        ("import time; time.sleep(1)\n", "timeout"),
    ],
)
async def test_hook_failures_hold_and_do_not_log_full_body(
    tmp_path, caplog, script_body, expected_failure
):
    script = tmp_path / "hook.py"
    script.write_text(script_body, encoding="utf-8")
    source = _source()
    config = GatewayConfig(
        pre_agent_hook=PreAgentHookConfig(
            enabled=True,
            command=f"{sys.executable} {script}",
            timeout_seconds=0.1,
            platforms=("telegram",),
            channels=("text",),
        )
    )
    runner = _runner(config=config, source=source)
    payload = {
        "platform": "telegram",
        "channel": "text",
        "source": {"message_id": "msg-private"},
        "hermes_session": {"session_key": "session-private"},
        "message": {"body_text": "PRIVATE BODY SHOULD NOT LOG"},
    }

    result = await runner._run_pre_agent_hook(payload)

    assert result["action"] == "hold"
    assert result["_hermes_failure"] == expected_failure
    assert "PRIVATE BODY SHOULD NOT LOG" not in caplog.text


def test_two_inkbox_sms_contacts_produce_distinct_payloads():
    runner = _runner()
    source_a = _inkbox_sms_source("contact-a", "+15555550101", "Alex")
    source_b = _inkbox_sms_source("contact-b", "+15555550102", "Blair")
    event_a = _event(
        "[inkbox:sms from=+15555550101 | contact_id=contact-a]\nNeed help",
        source=source_a,
        raw_message=_inkbox_raw("sms-a", "+15555550101", "Need help"),
        message_id="sms-a",
    )
    event_b = _event(
        "[inkbox:sms from=+15555550102 | contact_id=contact-b]\nNeed help",
        source=source_b,
        raw_message=_inkbox_raw("sms-b", "+15555550102", "Need help"),
        message_id="sms-b",
    )

    payload_a = runner._build_pre_agent_hook_payload(
        event=event_a,
        source=source_a,
        session_entry=_session_entry(source_a, session_id="sess-a"),
        session_key=build_session_key(source_a),
        was_auto_reset=False,
        auto_reset_reason=None,
    )
    payload_b = runner._build_pre_agent_hook_payload(
        event=event_b,
        source=source_b,
        session_entry=_session_entry(source_b, session_id="sess-b"),
        session_key=build_session_key(source_b),
        was_auto_reset=False,
        auto_reset_reason=None,
    )

    assert payload_a["hermes_session"]["session_key"] != payload_b["hermes_session"]["session_key"]
    assert payload_a["provider"]["phone_alias"] == "+15555550101"
    assert payload_b["provider"]["phone_alias"] == "+15555550102"
    assert payload_a["provider"]["contact_id"] == "contact-a"
    assert payload_b["provider"]["contact_id"] == "contact-b"
