"""Tests for conversation-centric SMS: 1:1 conversation routing + group chats.

Covers the new Inkbox text API where outbound replies address a conversation
UUID (not a bare phone number), group texts are detected and keyed by
conversation, the group response policy + [SILENT] sentinel let the agent stay
quiet on unrelated group chatter, and group sends route via conversation_id.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.gateway.test_inkbox import _FakeRequest, _make_adapter


def _set_conversations(adapter, summaries):
    """Make the fake identity return the given conversation summaries."""
    identity = adapter._inkbox.get_identity.return_value
    identity.list_text_conversations = lambda **_k: summaries


def _text_envelope(**msg):
    base = {
        "id": "sms-uuid",
        "direction": "inbound",
        "local_phone_number": "+18005550100",
        "created_at": "2026-04-27T20:00:00Z",
    }
    base.update(msg)
    return {"event_type": "text.received", "data": {"text_message": base}}


async def _capture_inbound(adapter, monkeypatch, envelope):
    captured = []

    async def fake_handle_message(event):
        captured.append(event)

    monkeypatch.setattr(adapter, "handle_message", fake_handle_message)
    await adapter._handle_webhook(_FakeRequest(json.dumps(envelope).encode()))
    for task in list(adapter._background_tasks):
        await task
    return captured


# ─── 1:1 conversation routing ───────────────────────────────────────────────


class TestDirectConversation:
    @pytest.mark.asyncio
    async def test_inbound_remembers_conversation_id_for_reply(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        _set_conversations(adapter, [])  # not a group
        env = _text_envelope(
            remote_phone_number="+15555550101",
            text="hi",
            conversation_id="conv-direct-1",
        )
        captured = await _capture_inbound(adapter, monkeypatch, env)
        assert len(captured) == 1
        ev = captured[0]
        # 1:1 stays keyed on the contact, dm chat_type.
        assert ev.source.chat_type == "dm"
        assert ev.source.chat_id == "contact-uuid-123"
        assert ev.text.startswith("[inkbox:sms")
        # The conversation id is remembered for the reply.
        assert adapter._sms_conversation["contact-uuid-123"] == "conv-direct-1"

    @pytest.mark.asyncio
    async def test_send_routes_via_remembered_conversation_id(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._sms_conversation["contact-uuid-123"] = "conv-direct-1"
        adapter._last_inbound_modality["contact-uuid-123"] = "sms"
        identity = adapter._inkbox.get_identity.return_value

        res = await adapter.send("contact-uuid-123", "hello back")
        assert res.success
        # Sent by conversation_id, not by phone number.
        kwargs = identity.send_text.call_args.kwargs
        assert kwargs.get("conversation_id") == "conv-direct-1"
        assert "to" not in kwargs


# ─── group chats ────────────────────────────────────────────────────────────


class TestGroupConversation:
    def _group_summary(self, conv_id="conv-group-1"):
        return [SimpleNamespace(
            id=conv_id,
            is_group=True,
            participants=["+15555550101", "+15555550102", "+18005550100"],
        )]

    @pytest.mark.asyncio
    async def test_group_inbound_keyed_by_conversation_with_policy(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        _set_conversations(adapter, self._group_summary())
        env = _text_envelope(
            remote_phone_number="+15555550101",
            sender_phone_number="+15555550102",
            text="anyone around?",
            conversation_id="conv-group-1",
        )
        captured = await _capture_inbound(adapter, monkeypatch, env)
        assert len(captured) == 1
        ev = captured[0]
        # Group session is keyed by the conversation UUID, chat_type=group.
        assert ev.source.chat_id == "conv-group-1"
        assert ev.source.chat_type == "group"
        # Marker + policy present.
        assert ev.text.startswith("[inkbox:group_sms conversation_id=conv-group-1")
        assert "reply_mode=conversation_id" in ev.text
        assert "participants=" in ev.text
        assert "Group SMS response policy" in ev.text
        assert "return exactly [SILENT]" in ev.text
        # Remembers conv id for reply.
        assert adapter._sms_conversation["conv-group-1"] == "conv-group-1"

    @pytest.mark.asyncio
    async def test_group_detected_from_multiple_contacts_without_summary(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        _set_conversations(adapter, [])  # summary says not group...
        env = {
            "event_type": "text.received",
            "data": {
                "text_message": {
                    "id": "sms-uuid",
                    "direction": "inbound",
                    "remote_phone_number": "+15555550101",
                    "conversation_id": "conv-group-2",
                    "text": "hey all",
                    "created_at": "2026-04-27T20:00:00Z",
                },
                # ...but the webhook matched two contacts -> group.
                "contacts": [{"id": "c1", "name": "A"}, {"id": "c2", "name": "B"}],
            },
        }
        captured = await _capture_inbound(adapter, monkeypatch, env)
        assert captured[0].source.chat_type == "group"
        assert captured[0].source.chat_id == "conv-group-2"

    @pytest.mark.asyncio
    async def test_group_send_routes_via_conversation_id(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._sms_conversation["conv-group-1"] = "conv-group-1"
        adapter._last_inbound_modality["conv-group-1"] = "sms"
        identity = adapter._inkbox.get_identity.return_value

        res = await adapter.send("conv-group-1", "replying to the group")
        assert res.success
        kwargs = identity.send_text.call_args.kwargs
        assert kwargs.get("conversation_id") == "conv-group-1"
        assert "to" not in kwargs

    @pytest.mark.asyncio
    async def test_silent_sentinel_is_suppressed(self, monkeypatch):
        adapter = _make_adapter(monkeypatch)
        adapter._sms_conversation["conv-group-1"] = "conv-group-1"
        adapter._last_inbound_modality["conv-group-1"] = "sms"
        identity = adapter._inkbox.get_identity.return_value

        res = await adapter.send("conv-group-1", "[SILENT]")
        assert res.success
        # Nothing was sent.
        identity.send_text.assert_not_called()
