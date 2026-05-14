"""Tests for the Inkbox platform plugin (email + SMS + voice)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_sdk(monkeypatch, *, lookup_result=None):
    """Stub out the lazy ``inkbox`` SDK imports inside the plugin module."""
    import plugins.platforms.inkbox.adapter as inkbox_mod

    if lookup_result is None:
        lookup_result = [SimpleNamespace(
            id="contact-uuid-123",
            preferred_name="Alex",
            given_name="Alex",
            emails=[SimpleNamespace(value="alex@example.com", is_primary=True)],
            phones=[SimpleNamespace(value="+15555550101", is_primary=True)],
        )]

    fake_client = MagicMock()
    fake_client.contacts.lookup.return_value = lookup_result
    fake_client.contacts.get.return_value = lookup_result[0] if lookup_result else None
    fake_client.get_identity.return_value = SimpleNamespace(
        agent_handle="inkbox-on-call-agent",
        mailbox=SimpleNamespace(email_address="agent@inkboxmail.com"),
        phone_number=SimpleNamespace(id="phone-uuid", number="+18005550100"),
        send_email=MagicMock(return_value=SimpleNamespace(id="msg-1")),
        send_text=MagicMock(return_value=SimpleNamespace(id="sms-1")),
    )

    InkboxClass = MagicMock(return_value=fake_client)
    InkboxClass.return_value.__enter__ = MagicMock(return_value=fake_client)
    InkboxClass.return_value.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(inkbox_mod, "Inkbox", InkboxClass, raising=False)
    monkeypatch.setattr(inkbox_mod, "INKBOX_AVAILABLE", True, raising=False)
    return fake_client


def _make_adapter(monkeypatch, **extra):
    _patch_sdk(monkeypatch)
    from plugins.platforms.inkbox.adapter import InkboxAdapter

    cfg = PlatformConfig(
        enabled=True,
        api_key="ApiKey_test",
        extra={
            "api_key": "ApiKey_test",
            "identity": "inkbox-on-call-agent",
            "base_url": "https://inkbox.ai",
            **extra,
        },
    )
    adapter = InkboxAdapter(cfg)
    # Pre-construct an SDK client so methods that access self._inkbox work
    # without needing connect().
    from plugins.platforms.inkbox.adapter import Inkbox
    adapter._inkbox = Inkbox()
    adapter._public_host = "tunnel.example"
    return adapter


# ---------------------------------------------------------------------------
# Plugin registration + env enablement
# ---------------------------------------------------------------------------

class TestPluginRegistration:
    def test_register_calls_register_platform_with_expected_shape(self):
        from plugins.platforms.inkbox.adapter import register

        ctx = MagicMock()
        register(ctx)
        ctx.register_platform.assert_called_once()
        kwargs = ctx.register_platform.call_args.kwargs

        assert kwargs["name"] == "inkbox"
        assert kwargs["label"] == "Inkbox"
        assert kwargs["required_env"] == ["INKBOX_API_KEY", "INKBOX_IDENTITY"]
        assert "hermes-agent[inkbox]" in kwargs["install_hint"]
        assert kwargs["cron_deliver_env_var"] == "INKBOX_HOME_CHANNEL"
        assert kwargs["allowed_users_env"] == "INKBOX_ALLOWED_USERS"
        assert kwargs["allow_all_env"] == "INKBOX_ALLOW_ALL_USERS"
        assert callable(kwargs["adapter_factory"])
        assert callable(kwargs["env_enablement_fn"])
        assert callable(kwargs["standalone_sender_fn"])
        assert kwargs["pii_safe"] is True

    def test_env_enablement_returns_none_without_credentials(self, monkeypatch):
        from plugins.platforms.inkbox.adapter import _env_enablement

        monkeypatch.delenv("INKBOX_API_KEY", raising=False)
        monkeypatch.delenv("INKBOX_IDENTITY", raising=False)
        assert _env_enablement() is None

    def test_env_enablement_seeds_extras_and_home_channel(self, monkeypatch):
        from plugins.platforms.inkbox.adapter import _env_enablement

        monkeypatch.setenv("INKBOX_API_KEY", "ApiKey_abc")
        monkeypatch.setenv("INKBOX_IDENTITY", "test-agent")
        monkeypatch.setenv("INKBOX_LISTEN_PORT", "9999")
        monkeypatch.setenv("INKBOX_HOME_CHANNEL", "contact-uuid-home")

        seed = _env_enablement()
        assert seed is not None
        assert seed["api_key"] == "ApiKey_abc"
        assert seed["identity"] == "test-agent"
        assert seed["port"] == 9999
        assert seed["home_channel"]["chat_id"] == "contact-uuid-home"
        assert seed["home_channel"]["name"] == "Home"

    def test_check_requirements_false_without_env(self, monkeypatch):
        from plugins.platforms.inkbox.adapter import check_requirements

        monkeypatch.delenv("INKBOX_API_KEY", raising=False)
        monkeypatch.delenv("INKBOX_IDENTITY", raising=False)
        assert check_requirements() is False

    def test_validate_config_reads_extras_and_env(self, monkeypatch):
        from plugins.platforms.inkbox.adapter import validate_config

        monkeypatch.delenv("INKBOX_API_KEY", raising=False)
        monkeypatch.delenv("INKBOX_IDENTITY", raising=False)
        cfg = PlatformConfig(extra={"api_key": "k", "identity": "i"})
        assert validate_config(cfg) is True

        cfg_empty = PlatformConfig(extra={})
        assert validate_config(cfg_empty) is False


# ---------------------------------------------------------------------------
# Webhook routing
# ---------------------------------------------------------------------------

class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in for the webhook handler."""

    def __init__(self, body: bytes, headers: dict | None = None, query: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self.query = query or {}

    async def read(self):
        return self._body


class TestWebhookRouting:
    @pytest.mark.asyncio
    async def test_mail_webhook_routes_to_message_event(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)

        captured = []

        async def fake_handle_message(event):
            captured.append(event)

        monkeypatch.setattr(adapter, "handle_message", fake_handle_message)

        envelope = {
            "event_type": "message.received",
            "data": {"message": {
                "id": "msg-uuid",
                "from_address": "alex@example.com",
                "subject": "Hi there",
                "snippet": "Hello world",
                "thread_id": "thread-7",
            }},
        }
        body = json.dumps(envelope).encode()
        req = _FakeRequest(body, headers={"X-Inkbox-Request-Id": "rid-1"})
        resp = await adapter._handle_webhook(req)
        assert resp.status == 200

        for task in list(adapter._background_tasks):
            await task

        assert len(captured) == 1
        ev = captured[0]
        assert ev.text == "Hello world"
        assert ev.source.platform == Platform("inkbox")
        # contact_id resolved via lookup() should win over the raw email.
        assert ev.source.chat_id == "contact-uuid-123"
        # email threads mint a sub-session.
        assert ev.source.thread_id == "email:thread-7"
        assert ev.source.chat_topic == "Hi there"

    @pytest.mark.asyncio
    async def test_text_webhook_routes_to_message_event(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)

        captured = []

        async def fake_handle_message(event):
            captured.append(event)

        monkeypatch.setattr(adapter, "handle_message", fake_handle_message)

        envelope = {
            "event_type": "text.received",
            "data": {"text_message": {
                "id": "sms-uuid",
                "remote_phone_number": "+15555550101",
                "local_phone_number": "+18005550100",
                "text": "ping",
                "direction": "inbound",
                "created_at": "2026-04-27T20:00:00Z",
            }},
        }
        body = json.dumps(envelope).encode()
        req = _FakeRequest(body)
        await adapter._handle_webhook(req)

        for task in list(adapter._background_tasks):
            await task

        assert len(captured) == 1
        ev = captured[0]
        assert ev.text == "ping"
        assert ev.source.chat_id == "contact-uuid-123"
        # SMS does NOT mint a sub-session — same chat_id, no thread_id.
        assert ev.source.thread_id is None

    @pytest.mark.asyncio
    async def test_incoming_call_webhook_returns_answer_with_ws_url(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)

        envelope = {
            "id": "call-uuid",
            "phone_number_id": "phone-uuid",
            "remote_phone_number": "+15555550101",
            "local_phone_number": "+18005550100",
            "direction": "inbound",
            "status": "ringing",
            "created_at": "2026-04-27T20:00:00Z",
        }
        body = json.dumps(envelope).encode()
        req = _FakeRequest(body)
        resp = await adapter._handle_webhook(req)

        assert resp.status == 200
        payload = json.loads(resp.body.decode())
        assert payload["action"] == "answer"
        assert payload["client_websocket_url"].startswith("wss://tunnel.example")
        assert "call_id=call-uuid" in payload["client_websocket_url"]

    @pytest.mark.asyncio
    async def test_duplicate_request_id_is_ignored(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        captured = []

        async def fake_handle_message(event):
            captured.append(event)

        monkeypatch.setattr(adapter, "handle_message", fake_handle_message)

        envelope = {
            "event_type": "text.received",
            "data": {"text_message": {
                "id": "sms-1",
                "remote_phone_number": "+15555550101",
                "local_phone_number": "+18005550100",
                "text": "ping",
                "direction": "inbound",
                "created_at": "2026-04-27T20:00:00Z",
            }},
        }
        body = json.dumps(envelope).encode()
        for _ in range(2):
            await adapter._handle_webhook(
                _FakeRequest(body, headers={"X-Inkbox-Request-Id": "rid-dup"}),
            )

        for task in list(adapter._background_tasks):
            await task

        assert len(captured) == 1


# ---------------------------------------------------------------------------
# Contact-resolution cache
# ---------------------------------------------------------------------------

class TestContactCache:
    @pytest.mark.asyncio
    async def test_lookup_cached_within_ttl(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        cid1, name1 = await adapter._resolve_contact(kind="email", value="alex@example.com")
        cid2, name2 = await adapter._resolve_contact(kind="email", value="alex@example.com")
        assert cid1 == cid2 == "contact-uuid-123"
        assert name1 == name2 == "Alex"
        # Two calls, but only one SDK round-trip.
        assert adapter._inkbox.contacts.lookup.call_count == 1

    @pytest.mark.asyncio
    async def test_lookup_negative_result_cached(self, monkeypatch):
        _patch_sdk(monkeypatch, lookup_result=[])
        from plugins.platforms.inkbox.adapter import InkboxAdapter, Inkbox

        cfg = PlatformConfig(extra={
            "api_key": "ApiKey_test",
            "identity": "inkbox-on-call-agent",
            "signing_key": "whsec_test",
        })
        adapter = InkboxAdapter(cfg)
        adapter._inkbox = Inkbox()

        cid, name = await adapter._resolve_contact(kind="phone", value="+15555550999")
        assert cid is None and name is None
        await adapter._resolve_contact(kind="phone", value="+15555550999")
        assert adapter._inkbox.contacts.lookup.call_count == 1


# ---------------------------------------------------------------------------
# Send (outbound)
# ---------------------------------------------------------------------------

class TestSend:
    @pytest.mark.asyncio
    async def test_send_sms_uses_e164_chat_id_directly(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        result = await adapter.send(
            "+15555550101", "hello", metadata={"mode": "sms", "to_phone": "+15555550101"},
        )
        assert result.success is True
        identity = adapter._inkbox.get_identity.return_value
        identity.send_text.assert_called_once_with(to="+15555550101", text="hello")

    @pytest.mark.asyncio
    async def test_send_email_resolves_address_from_contact_when_chat_id_is_uuid(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        result = await adapter.send(
            "contact-uuid-123",
            "hi from hermes",
            metadata={"mode": "email", "subject": "Greetings"},
        )
        assert result.success is True
        identity = adapter._inkbox.get_identity.return_value
        identity.send_email.assert_called_once()
        kwargs = identity.send_email.call_args.kwargs
        assert kwargs["to"] == ["alex@example.com"]
        assert kwargs["subject"] == "Greetings"
        assert kwargs["body_text"] == "hi from hermes"

    @pytest.mark.asyncio
    async def test_send_voice_without_active_ws_fails_cleanly(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        result = await adapter.send(
            "contact-uuid-123", "spoken reply", metadata={"mode": "voice"},
        )
        assert result.success is False
        assert "active call" in (result.error or "").lower()
