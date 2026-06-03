"""Tests for the Inkbox Realtime-calls step of the gateway setup wizard.

Mirrors the plugin's realtime wizard coverage: key detection priority, reuse of
Hermes' own OpenAI credentials, the failed-key retry loop, and opt-out. GA
Realtime is API-key-only, so there is no OAuth/Codex path to exercise here.
"""

import types

import hermes_cli.setup as setup


def _clear_realtime_env(monkeypatch):
    for name in ("OPENAI_API_KEY", "INKBOX_REALTIME_API_KEY", "INKBOX_REALTIME_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def _identity():
    return types.SimpleNamespace(phone_number=types.SimpleNamespace(number="+15551234567"))


# ─── detection priority ─────────────────────────────────────────────────────


def test_detect_realtime_key_prefers_inkbox_specific_env(monkeypatch):
    _clear_realtime_env(monkeypatch)
    values = {"OPENAI_API_KEY": "sk-openai", "INKBOX_REALTIME_API_KEY": "sk-realtime"}
    monkeypatch.setattr(setup, "_config_realtime_api_key", lambda: "")
    monkeypatch.setattr(setup, "_hermes_openai_api_key", lambda: ("credential_pool:openai-api", "sk-pool"))
    monkeypatch.setattr(setup, "get_env_value", lambda name: values.get(name, ""))
    # INKBOX_REALTIME_API_KEY beats both the Hermes pool and OPENAI_API_KEY.
    assert setup._detect_openai_realtime_key() == ("INKBOX_REALTIME_API_KEY", "sk-realtime")


def test_detect_realtime_key_prefers_config(monkeypatch):
    _clear_realtime_env(monkeypatch)
    monkeypatch.setattr(setup, "_config_realtime_api_key", lambda: "sk-config")
    monkeypatch.setattr(setup, "_hermes_openai_api_key", lambda: ("credential_pool:openai-api", "sk-pool"))
    monkeypatch.setattr(setup, "get_env_value", lambda name: "sk-realtime" if name == "INKBOX_REALTIME_API_KEY" else "")
    assert setup._detect_openai_realtime_key() == ("platforms.inkbox.realtime.api_key", "sk-config")


def test_detect_realtime_key_uses_hermes_openai_credentials(monkeypatch):
    _clear_realtime_env(monkeypatch)
    monkeypatch.setattr(setup, "_config_realtime_api_key", lambda: "")
    monkeypatch.setattr(setup, "_hermes_openai_api_key", lambda: ("credential_pool:openai-api", "sk-pool"))
    monkeypatch.setattr(setup, "get_env_value", lambda _name: "")
    # No config / INKBOX env -> fall back to Hermes' own OpenAI credentials.
    assert setup._detect_openai_realtime_key() == ("credential_pool:openai-api", "sk-pool")


# ─── _configure_realtime_calls flow ─────────────────────────────────────────


def test_configure_realtime_existing_key_success(monkeypatch):
    _clear_realtime_env(monkeypatch)
    saved, tested = [], []
    monkeypatch.setattr(setup, "_config_realtime_api_key", lambda: "")
    monkeypatch.setattr(setup, "_hermes_openai_api_key", lambda: None)
    monkeypatch.setattr(setup, "get_env_value", lambda name: "sk-existing" if name == "OPENAI_API_KEY" else "")
    monkeypatch.setattr(setup, "prompt_yes_no", lambda *_a, **_k: True)
    monkeypatch.setattr(setup, "save_env_value", lambda name, value: saved.append((name, value)))
    monkeypatch.setattr(
        setup, "_test_openai_realtime_api_key",
        lambda key, model: tested.append((key, model)) or (True, "ok"),
    )

    setup._configure_realtime_calls(_identity())

    assert tested == [("sk-existing", "gpt-realtime-2")]
    assert ("INKBOX_REALTIME_ENABLED", "true") in saved
    assert ("INKBOX_REALTIME_MODEL", "gpt-realtime-2") in saved
    assert ("INKBOX_REALTIME_API_KEY", "sk-existing") in saved


def test_configure_realtime_reuses_hermes_credentials_without_prompt(monkeypatch):
    _clear_realtime_env(monkeypatch)
    saved, tested = [], []
    monkeypatch.setattr(setup, "_config_realtime_api_key", lambda: "")
    monkeypatch.setattr(setup, "_hermes_openai_api_key", lambda: ("credential_pool:openai-api", "sk-pool"))
    monkeypatch.setattr(setup, "get_env_value", lambda _name: "")
    monkeypatch.setattr(setup, "prompt_yes_no", lambda *_a, **_k: True)

    def _no_prompt(*_a, **_k):
        raise AssertionError("prompted for a key when one was already detected")

    monkeypatch.setattr(setup, "prompt", _no_prompt)
    monkeypatch.setattr(setup, "save_env_value", lambda name, value: saved.append((name, value)))
    monkeypatch.setattr(
        setup, "_test_openai_realtime_api_key",
        lambda key, model: tested.append((key, model)) or (True, "ok"),
    )

    setup._configure_realtime_calls(_identity())

    assert tested == [("sk-pool", "gpt-realtime-2")]
    assert ("INKBOX_REALTIME_API_KEY", "sk-pool") in saved


def test_configure_realtime_retries_after_failed_key(monkeypatch):
    _clear_realtime_env(monkeypatch)
    saved, attempts = [], []
    # Nothing detected -> wizard prompts for a key each pass.
    monkeypatch.setattr(setup, "_config_realtime_api_key", lambda: "")
    monkeypatch.setattr(setup, "_hermes_openai_api_key", lambda: None)
    monkeypatch.setattr(setup, "get_env_value", lambda _name: "")
    monkeypatch.setattr(setup, "prompt_yes_no", lambda *_a, **_k: True)
    keys = iter(["sk-bad", "sk-good"])
    monkeypatch.setattr(setup, "prompt", lambda *_a, **_k: next(keys))

    def _test(key, model):
        attempts.append(key)
        return (key == "sk-good", "ok" if key == "sk-good" else "invalid key")

    monkeypatch.setattr(setup, "_test_openai_realtime_api_key", _test)
    monkeypatch.setattr(setup, "save_env_value", lambda name, value: saved.append((name, value)))

    setup._configure_realtime_calls(_identity())

    # Bad key is rejected, wizard re-prompts, good key enables realtime.
    assert attempts == ["sk-bad", "sk-good"]
    assert ("INKBOX_REALTIME_ENABLED", "true") in saved
    assert ("INKBOX_REALTIME_API_KEY", "sk-good") in saved


def test_configure_realtime_opt_out_disables(monkeypatch):
    _clear_realtime_env(monkeypatch)
    saved = []
    monkeypatch.setattr(setup, "_detect_openai_realtime_key", lambda: None)
    monkeypatch.setattr(setup, "prompt_yes_no", lambda *_a, **_k: False)
    monkeypatch.setattr(setup, "save_env_value", lambda name, value: saved.append((name, value)))

    setup._configure_realtime_calls(_identity())

    assert ("INKBOX_REALTIME_ENABLED", "false") in saved


def test_configure_realtime_skipped_without_phone(monkeypatch):
    saved = []
    monkeypatch.setattr(setup, "save_env_value", lambda name, value: saved.append((name, value)))
    # Identity with no phone number -> realtime step is a no-op.
    setup._configure_realtime_calls(types.SimpleNamespace(phone_number=None))
    assert saved == []
