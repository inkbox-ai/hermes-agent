"""Tests for the generic gateway pre-agent-turn hook."""

from __future__ import annotations

import inspect
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
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
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
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
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


def test_queued_followup_drain_uses_pre_agent_hook_gate():
    from gateway.run import GatewayRunner

    source_text = inspect.getsource(GatewayRunner._run_agent)
    gate = "hook_action, context_prompt, exact_reply = await self._apply_pre_agent_hook_for_turn"
    recursive_run = "followup_result = await self._run_agent"

    assert gate in source_text
    assert source_text.index(gate) < source_text.index(recursive_run)


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
