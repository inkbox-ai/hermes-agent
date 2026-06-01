"""Tests for the Inkbox realtime voice bridge.

These tests exercise the pure-Python pieces of the bridge — config parsing,
instruction building, tool schema, and the dispatch logic that picks between
agent_consult / post_call_action / unknown-tool paths. The actual WebSocket
plumbing (OpenAI Realtime API + Inkbox audio frames) is mocked out; an
end-to-end test against the real Realtime API would need an OpenAI key and
live audio fixtures, which belongs in a separate integration suite.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from gateway.platforms.inkbox_realtime import (
    AGENT_CONSULT_TOOL_NAME,
    AUDIO_FORMAT_TELEPHONY,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    POST_CALL_ACTION_TOOL_NAME,
    RealtimeCallMeta,
    RealtimeConfig,
    _BridgeState,
    _agent_consult_tool_schema,
    _dispatch_tool_call,
    _maybe_send_greeting,
    _post_call_action_tool_schema,
    _resolve_realtime_bearer,
    _send_session_update,
    build_realtime_greeting,
    build_realtime_instructions,
)


# ─── helpers ──────────────────────────────────────────────────────────────


def _meta(**overrides) -> RealtimeCallMeta:
    base = dict(
        call_id="call-abc",
        contact_id="contact-uuid-123",
        contact_name="Alex",
        remote_phone_number="+15555550101",
        direction="inbound",
        agent_identity_email="agent@inkboxmail.com",
        agent_identity_phone="+18005550100",
    )
    base.update(overrides)
    return RealtimeCallMeta(**base)


class _FakeWS:
    """Minimal stand-in for an aiohttp ClientWebSocketResponse / WSResponse.

    Records every JSON payload sent via ``send_str``; tests assert against
    the captured frames.
    """

    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    async def send_str(self, payload: str) -> None:
        try:
            self.sent.append(json.loads(payload))
        except (TypeError, ValueError):
            self.sent.append({"_raw": payload})


# ─── schema sanity ────────────────────────────────────────────────────────


class TestToolSchemas:
    def test_agent_consult_schema_has_required_query(self):
        schema = _agent_consult_tool_schema()
        assert schema["name"] == AGENT_CONSULT_TOOL_NAME
        assert schema["parameters"]["required"] == ["query"]
        assert "query" in schema["parameters"]["properties"]

    def test_post_call_action_schema_has_required_action(self):
        schema = _post_call_action_tool_schema()
        assert schema["name"] == POST_CALL_ACTION_TOOL_NAME
        assert schema["parameters"]["required"] == ["action"]


# ─── instruction builder ───────────────────────────────────────────────────


class TestBuildInstructions:
    def test_includes_identity_and_caller_info(self):
        text = build_realtime_instructions(_meta())
        assert "agent@inkboxmail.com" in text
        assert "+18005550100" in text
        assert "+15555550101" in text
        assert "Alex" in text

    def test_unknown_contact_uses_neutral_greeting_directive(self):
        text = build_realtime_instructions(_meta(contact_name="unknown"))
        assert "No matching contact record" in text

    def test_outbound_call_includes_purpose_and_opening(self):
        text = build_realtime_instructions(_meta(
            direction="outbound",
            outbound_purpose="Confirm the 3pm meeting",
            outbound_opening="Hi Alex, this is Hermes confirming our 3pm.",
        ))
        assert "Confirm the 3pm meeting" in text
        assert "Hi Alex, this is Hermes" in text
        # Outbound calls must include the directive not to offer generic help
        assert "do not open with a generic offer" in text

    def test_outbound_call_includes_reason_scheduled_by_summary(self):
        text = build_realtime_instructions(_meta(
            direction="outbound",
            outbound_reason="Follow up on the overdue invoice",
            outbound_scheduled_by="the billing workflow",
            outbound_conversation_summary="Customer promised to pay by Friday.",
        ))
        assert "Follow up on the overdue invoice" in text
        assert "the billing workflow" in text
        assert "Customer promised to pay by Friday." in text

    def test_inbound_call_does_not_include_outbound_directives(self):
        text = build_realtime_instructions(_meta(direction="inbound"))
        assert "do not open with a generic offer" not in text

    def test_additional_instructions_are_appended(self):
        text = build_realtime_instructions(
            _meta(),
            additional_instructions="Always use a friendly tone.",
        )
        assert "Always use a friendly tone." in text

    def test_tool_names_are_mentioned(self):
        text = build_realtime_instructions(_meta())
        assert AGENT_CONSULT_TOOL_NAME in text
        assert POST_CALL_ACTION_TOOL_NAME in text


# ─── greeting ──────────────────────────────────────────────────────────────


class TestGreeting:
    def test_default_voice_is_cedar(self):
        assert DEFAULT_VOICE == "cedar"

    def test_inbound_greeting_uses_first_name(self):
        g = build_realtime_greeting(_meta(contact_name="Alex Wilcox"))
        assert "Alex" in g
        assert "Wilcox" not in g

    def test_inbound_greeting_unknown_contact_has_no_name(self):
        g = build_realtime_greeting(_meta(contact_name="unknown"))
        assert "Greet the caller" in g

    def test_outbound_greeting_prefers_opening_message(self):
        g = build_realtime_greeting(_meta(
            direction="outbound",
            outbound_opening="Hi, calling about your order.",
        ))
        assert "Hi, calling about your order." in g

    def test_outbound_greeting_falls_back_to_purpose(self):
        g = build_realtime_greeting(_meta(
            direction="outbound",
            outbound_purpose="confirm the appointment",
        ))
        assert "confirm the appointment" in g

    @pytest.mark.asyncio
    async def test_maybe_send_greeting_fires_once(self):
        ws = _FakeWS()
        state = _BridgeState()
        await _maybe_send_greeting(ws, state, _meta())
        await _maybe_send_greeting(ws, state, _meta())  # second call is a no-op
        assert len(ws.sent) == 1
        assert ws.sent[0]["type"] == "response.create"
        assert "output_modalities" not in ws.sent[0]["response"]
        assert "instructions" in ws.sent[0]["response"]
        assert state.greeting_triggered is True


# ─── bearer resolution (api key vs OAuth client-secret mint) ────────────────


class _FakePostCtx:
    def __init__(self, status, payload):
        self._status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def status(self):
        return self._status

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _FakeSession:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload or {}
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakePostCtx(self.status, self.payload)


class TestBearerResolution:
    @pytest.mark.asyncio
    async def test_api_key_used_directly_no_mint(self):
        sess = _FakeSession()
        cfg = RealtimeConfig(enabled=True, api_key="sk-direct")
        bearer = await _resolve_realtime_bearer(sess, cfg)
        assert bearer == "sk-direct"
        assert sess.calls == []  # no mint call when api_key present

    @pytest.mark.asyncio
    async def test_oauth_mints_client_secret(self):
        sess = _FakeSession(status=200, payload={"value": "ek-ephemeral"})
        cfg = RealtimeConfig(enabled=True, oauth_token="oauth-tok", model="gpt-realtime-2")
        bearer = await _resolve_realtime_bearer(sess, cfg)
        assert bearer == "ek-ephemeral"
        assert len(sess.calls) == 1
        assert sess.calls[0]["headers"]["Authorization"] == "Bearer oauth-tok"
        assert sess.calls[0]["json"]["session"]["type"] == "realtime"

    @pytest.mark.asyncio
    async def test_oauth_mint_nested_client_secret_value(self):
        sess = _FakeSession(status=200, payload={"client_secret": {"value": "ek-nested"}})
        cfg = RealtimeConfig(enabled=True, oauth_token="oauth-tok")
        bearer = await _resolve_realtime_bearer(sess, cfg)
        assert bearer == "ek-nested"

    @pytest.mark.asyncio
    async def test_oauth_mint_http_error_raises(self):
        sess = _FakeSession(status=401, payload={"error": "bad token"})
        cfg = RealtimeConfig(enabled=True, oauth_token="oauth-tok")
        with pytest.raises(RuntimeError):
            await _resolve_realtime_bearer(sess, cfg)

    def test_has_credential_property(self):
        assert RealtimeConfig(api_key="sk-x").has_credential is True
        assert RealtimeConfig(oauth_token="tok").has_credential is True
        assert RealtimeConfig().has_credential is False


# ─── GA session.update protocol ────────────────────────────────────────────


class TestSessionUpdate:
    def test_default_model_is_ga_v2(self):
        # We default to the GA gpt-realtime-2 model.
        assert DEFAULT_MODEL == "gpt-realtime-2"

    def test_telephony_audio_format_is_ga_object(self):
        # GA expects an audio-format object, not the legacy "g711_ulaw" string.
        assert AUDIO_FORMAT_TELEPHONY == {"type": "audio/pcmu"}

    @pytest.mark.asyncio
    async def test_session_update_uses_ga_nested_schema(self):
        ws = _FakeWS()
        config = RealtimeConfig(
            enabled=True, api_key="sk-test", model="gpt-realtime-2", voice="cedar",
        )
        await _send_session_update(ws, config, _meta())

        assert len(ws.sent) == 1
        sess = ws.sent[0]["session"]
        # GA markers — must NOT use the legacy flat shape.
        assert sess["type"] == "realtime"
        assert sess["model"] == "gpt-realtime-2"
        assert sess["output_modalities"] == ["audio"]
        assert "modalities" not in sess
        assert "input_audio_format" not in sess
        assert "output_audio_format" not in sess
        # Nested audio config.
        assert sess["audio"]["input"]["format"] == {"type": "audio/pcmu"}
        assert sess["audio"]["output"]["format"] == {"type": "audio/pcmu"}
        assert sess["audio"]["output"]["voice"] == "cedar"
        assert sess["audio"]["input"]["turn_detection"]["type"] == "server_vad"
        assert sess["audio"]["input"]["transcription"]["model"]
        # Tools live at the top of session in GA shape.
        tool_names = {t["name"] for t in sess["tools"]}
        assert tool_names == {AGENT_CONSULT_TOOL_NAME, POST_CALL_ACTION_TOOL_NAME}
        assert sess["tool_choice"] == "auto"


# ─── tool dispatch ─────────────────────────────────────────────────────────


class TestDispatchPostCallAction:
    @pytest.mark.asyncio
    async def test_queues_action_and_acknowledges(self):
        ws = _FakeWS()
        state = _BridgeState()
        config = RealtimeConfig(enabled=True, api_key="sk-test")

        async def _unused_consult(*_a, **_k):
            raise AssertionError("agent_consult should not be invoked for this tool")

        await _dispatch_tool_call(
            openai_ws=ws,
            call_id="tc-1",
            name=POST_CALL_ACTION_TOOL_NAME,
            arguments_json=json.dumps({
                "action": "Email Alex the summary",
                "details": "Cover decisions + next steps.",
            }),
            state=state,
            config=config,
            meta=_meta(),
            on_agent_consult=_unused_consult,
        )

        assert len(state.post_call_actions) == 1
        assert state.post_call_actions[0]["action"] == "Email Alex the summary"
        # Two frames: conversation.item.create (function_call_output) +
        # response.create.
        types = [frame.get("type") for frame in ws.sent]
        assert types == ["conversation.item.create", "response.create"]
        result_payload = json.loads(ws.sent[0]["item"]["output"])
        assert result_payload["status"] == "queued"

    @pytest.mark.asyncio
    async def test_missing_action_returns_error(self):
        ws = _FakeWS()
        state = _BridgeState()
        config = RealtimeConfig(enabled=True, api_key="sk-test")

        async def _unused(*_a, **_k):
            raise AssertionError("not used")

        await _dispatch_tool_call(
            openai_ws=ws,
            call_id="tc-1",
            name=POST_CALL_ACTION_TOOL_NAME,
            arguments_json="{}",
            state=state,
            config=config,
            meta=_meta(),
            on_agent_consult=_unused,
        )
        # No action queued, error result returned.
        assert state.post_call_actions == []
        out = json.loads(ws.sent[0]["item"]["output"])
        assert "error" in out


class TestDispatchAgentConsult:
    @pytest.mark.asyncio
    async def test_runs_consult_and_returns_answer(self):
        ws = _FakeWS()
        state = _BridgeState()
        config = RealtimeConfig(
            enabled=True, api_key="sk-test", consult_timeout_s=5.0,
        )
        seen: Dict[str, Any] = {}

        async def _consult(meta, query, transcript):
            seen["query"] = query
            seen["transcript"] = transcript
            return "The next meeting is at 3pm."

        await _dispatch_tool_call(
            openai_ws=ws,
            call_id="tc-2",
            name=AGENT_CONSULT_TOOL_NAME,
            arguments_json=json.dumps({"query": "When's my next meeting?"}),
            state=state,
            config=config,
            meta=_meta(),
            on_agent_consult=_consult,
        )

        assert seen["query"] == "When's my next meeting?"
        # The bridge fires an interim "one moment" response.create + then
        # conversation.item.create with the agent's answer + then a final
        # response.create.
        types = [frame.get("type") for frame in ws.sent]
        assert types[0] == "response.create"  # interim "one moment"
        assert "One moment" in ws.sent[0]["response"]["instructions"]
        assert types[1] == "conversation.item.create"
        assert types[2] == "response.create"
        result = json.loads(ws.sent[1]["item"]["output"])
        assert result["answer"] == "The next meeting is at 3pm."
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_consult_timeout_returns_error_result(self):
        ws = _FakeWS()
        state = _BridgeState()
        config = RealtimeConfig(
            enabled=True, api_key="sk-test", consult_timeout_s=0.05,
        )

        async def _slow_consult(*_a, **_k):
            await asyncio.sleep(0.5)
            return "too late"

        await _dispatch_tool_call(
            openai_ws=ws,
            call_id="tc-3",
            name=AGENT_CONSULT_TOOL_NAME,
            arguments_json=json.dumps({"query": "expensive thing"}),
            state=state,
            config=config,
            meta=_meta(),
            on_agent_consult=_slow_consult,
        )

        # Should produce: interim "one moment" + error tool result + response.create
        types = [frame.get("type") for frame in ws.sent]
        assert "conversation.item.create" in types
        item_idx = types.index("conversation.item.create")
        result = json.loads(ws.sent[item_idx]["item"]["output"])
        assert "error" in result
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_consult_exception_returns_error_result(self):
        ws = _FakeWS()
        state = _BridgeState()
        config = RealtimeConfig(enabled=True, api_key="sk-test")

        async def _boom(*_a, **_k):
            raise RuntimeError("consult exploded")

        await _dispatch_tool_call(
            openai_ws=ws,
            call_id="tc-4",
            name=AGENT_CONSULT_TOOL_NAME,
            arguments_json=json.dumps({"query": "anything"}),
            state=state,
            config=config,
            meta=_meta(),
            on_agent_consult=_boom,
        )
        types = [frame.get("type") for frame in ws.sent]
        item_idx = types.index("conversation.item.create")
        result = json.loads(ws.sent[item_idx]["item"]["output"])
        assert "error" in result
        assert "consult exploded" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_query_returns_error(self):
        ws = _FakeWS()
        state = _BridgeState()
        config = RealtimeConfig(enabled=True, api_key="sk-test")

        async def _unused(*_a, **_k):
            raise AssertionError("not used")

        await _dispatch_tool_call(
            openai_ws=ws,
            call_id="tc-5",
            name=AGENT_CONSULT_TOOL_NAME,
            arguments_json="{}",
            state=state,
            config=config,
            meta=_meta(),
            on_agent_consult=_unused,
        )
        out = json.loads(ws.sent[0]["item"]["output"])
        assert "error" in out


class TestDispatchUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        ws = _FakeWS()
        state = _BridgeState()
        config = RealtimeConfig(enabled=True, api_key="sk-test")

        async def _unused(*_a, **_k):
            raise AssertionError("not used")

        await _dispatch_tool_call(
            openai_ws=ws,
            call_id="tc-6",
            name="unrecognized_tool",
            arguments_json="{}",
            state=state,
            config=config,
            meta=_meta(),
            on_agent_consult=_unused,
        )
        out = json.loads(ws.sent[0]["item"]["output"])
        assert "error" in out
        assert "unrecognized_tool" in out["error"]


# ─── adapter integration: realtime config is parsed from extra ─────────────


class TestAdapterRealtimeConfig:
    def _make(self, monkeypatch, extra_overrides=None, codex_token=""):
        # Reuse the same _patch_sdk helper used by the broader inkbox test
        # suite so we don't double-mock the SDK here.
        from tests.gateway.test_inkbox import _patch_sdk
        from gateway.config import Platform, PlatformConfig
        from gateway.platforms.inkbox import InkboxAdapter

        _patch_sdk(monkeypatch)
        # Stub the Codex OAuth token lookup so tests don't read real auth.json.
        import hermes_cli.auth as _auth
        monkeypatch.setattr(_auth, "_pool_codex_access_token", lambda: codex_token)
        cfg = PlatformConfig(
            enabled=True,
            api_key="ApiKey_test",
            extra={
                "api_key": "ApiKey_test",
                "identity": "inkbox-on-call-agent",
                "base_url": "https://inkbox.ai",
                **(extra_overrides or {}),
            },
        )
        return InkboxAdapter(cfg)

    def test_realtime_off_when_no_key_and_unset(self, monkeypatch):
        # No key anywhere + no explicit flag -> auto-detect finds nothing -> off.
        monkeypatch.delenv("INKBOX_REALTIME_ENABLED", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("INKBOX_REALTIME_API_KEY", raising=False)
        adapter = self._make(monkeypatch)
        assert adapter._realtime_config.enabled is False

    def test_realtime_auto_enables_on_openai_key(self, monkeypatch):
        # Auto-detect: a generic OPENAI_API_KEY with NO
        # explicit flag turns realtime on.
        monkeypatch.delenv("INKBOX_REALTIME_ENABLED", raising=False)
        monkeypatch.delenv("INKBOX_REALTIME_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-generic")
        adapter = self._make(monkeypatch)
        assert adapter._realtime_config.enabled is True
        assert adapter._realtime_config.api_key == "sk-generic"

    def test_realtime_explicit_disable_wins_over_key(self, monkeypatch):
        # Explicit disable must beat an auto-detect that would otherwise
        # enable from the present key.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
        monkeypatch.setenv("INKBOX_REALTIME_ENABLED", "false")
        adapter = self._make(monkeypatch)
        assert adapter._realtime_config.enabled is False

    def test_realtime_config_disable_wins_over_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
        adapter = self._make(monkeypatch, extra_overrides={
            "realtime": {"enabled": False},
        })
        assert adapter._realtime_config.enabled is False

    def test_realtime_enabled_with_api_key_via_env(self, monkeypatch):
        monkeypatch.setenv("INKBOX_REALTIME_ENABLED", "true")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-abc")
        adapter = self._make(monkeypatch)
        assert adapter._realtime_config.enabled is True
        assert adapter._realtime_config.api_key == "sk-test-abc"

    def test_realtime_explicit_enable_without_any_credential_falls_back(self, monkeypatch):
        # enabled=true but no API key AND no Codex OAuth — stays disabled.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("INKBOX_REALTIME_API_KEY", raising=False)
        adapter = self._make(monkeypatch, extra_overrides={
            "realtime": {"enabled": True},
        }, codex_token="")
        assert adapter._realtime_config.enabled is False

    def test_realtime_auto_enables_on_codex_oauth(self, monkeypatch):
        # No sk- key but the agent has a Codex OAuth token -> realtime on,
        # oauth_token populated (the "proper OpenAI method" path).
        monkeypatch.delenv("INKBOX_REALTIME_ENABLED", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("INKBOX_REALTIME_API_KEY", raising=False)
        adapter = self._make(monkeypatch, codex_token="codex-oauth-token")
        assert adapter._realtime_config.enabled is True
        assert adapter._realtime_config.api_key == ""
        assert adapter._realtime_config.oauth_token == "codex-oauth-token"

    def test_realtime_api_key_preferred_over_codex_oauth(self, monkeypatch):
        monkeypatch.delenv("INKBOX_REALTIME_ENABLED", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-wins")
        adapter = self._make(monkeypatch, codex_token="codex-oauth-token")
        assert adapter._realtime_config.api_key == "sk-wins"
        # When an API key is present we don't bother reading the OAuth token.
        assert adapter._realtime_config.oauth_token == ""

    def test_config_extras_take_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        monkeypatch.setenv("INKBOX_REALTIME_MODEL", "from-env-model")
        adapter = self._make(monkeypatch, extra_overrides={
            "realtime": {
                "enabled": True,
                "api_key": "sk-config",
                "model": "from-config-model",
                "voice": "verse",
            },
        })
        assert adapter._realtime_config.api_key == "sk-config"
        assert adapter._realtime_config.model == "from-config-model"
        assert adapter._realtime_config.voice == "verse"
