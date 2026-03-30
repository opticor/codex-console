import asyncio

import pytest
from pydantic import SecretStr

import src.config.settings as settings_config
from src.config.constants import EmailServiceType
from src.web.routes import email as email_routes
from src.web.routes import settings as settings_routes


class DummySettings:
    proxy_enabled = False
    proxy_type = "http"
    proxy_host = "127.0.0.1"
    proxy_port = 7890
    proxy_username = None
    proxy_password = None
    proxy_dynamic_enabled = False
    proxy_dynamic_api_url = ""
    proxy_dynamic_api_key_header = "X-API-Key"
    proxy_dynamic_result_field = ""
    proxy_dynamic_api_key = None
    registration_max_retries = 3
    registration_timeout = 120
    registration_default_password_length = 12
    registration_sleep_min = 5
    registration_sleep_max = 30
    registration_entry_flow = "native"
    webui_host = "0.0.0.0"
    webui_port = 8000
    webui_access_password = None
    debug = False
    tempmail_enabled = True
    tempmail_base_url = "https://api.tempmail.lol/v2"
    tempmail_timeout = 30
    tempmail_max_retries = 3
    yyds_mail_enabled = True
    yyds_mail_base_url = "https://maliapi.215.im/v1"
    yyds_mail_api_key = SecretStr("AC-test-key")
    yyds_mail_default_domain = "public.example.com"
    yyds_mail_timeout = 30
    yyds_mail_max_retries = 3
    email_code_timeout = 120
    email_code_poll_interval = 3


def make_settings(**overrides):
    settings = DummySettings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_get_all_settings_includes_yyds_mail(monkeypatch):
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())

    result = asyncio.run(settings_routes.get_all_settings())

    assert result["tempmail"]["enabled"] is True
    assert result["tempmail"]["api_url"] == "https://api.tempmail.lol/v2"
    assert result["yyds_mail"]["enabled"] is True
    assert result["yyds_mail"]["api_url"] == "https://maliapi.215.im/v1"
    assert result["yyds_mail"]["default_domain"] == "public.example.com"
    assert result["yyds_mail"]["has_api_key"] is True


def test_get_tempmail_settings_returns_dual_providers(monkeypatch):
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())

    result = asyncio.run(settings_routes.get_tempmail_settings())

    assert result["tempmail"]["enabled"] is True
    assert result["yyds_mail"]["enabled"] is True
    assert result["yyds_mail"]["has_api_key"] is True


def test_update_tempmail_settings_keeps_saved_yyds_key_when_omitted(monkeypatch):
    captured = {}

    def fake_update_settings(**kwargs):
        captured.update(kwargs)
        return make_settings(
            tempmail_enabled=False,
            tempmail_base_url="https://api.changed.test/v2",
            yyds_mail_enabled=True,
            yyds_mail_base_url="https://maliapi.changed.test/v1",
            yyds_mail_default_domain="changed.example.com",
            yyds_mail_api_key=SecretStr("AC-test-key"),
        )

    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(settings_routes, "update_settings", fake_update_settings)
    monkeypatch.setattr(
        settings_routes,
        "reload_settings",
        lambda: make_settings(
            tempmail_enabled=False,
            tempmail_base_url="https://api.changed.test/v2",
            yyds_mail_enabled=True,
            yyds_mail_base_url="https://maliapi.changed.test/v1",
            yyds_mail_default_domain="changed.example.com",
            yyds_mail_api_key=SecretStr("AC-test-key"),
        ),
    )

    request = settings_routes.TempmailSettings(
        api_url="https://api.changed.test/v2",
        enabled=False,
        yyds_api_url="https://maliapi.changed.test/v1",
        yyds_default_domain="changed.example.com",
        yyds_enabled=True,
    )

    result = asyncio.run(settings_routes.update_tempmail_settings(request))

    assert result["success"] is True
    assert captured["tempmail_base_url"] == "https://api.changed.test/v2"
    assert captured["tempmail_enabled"] is False
    assert captured["yyds_mail_base_url"] == "https://maliapi.changed.test/v1"
    assert captured["yyds_mail_default_domain"] == "changed.example.com"
    assert captured["yyds_mail_enabled"] is True
    assert "yyds_mail_api_key" not in captured
    assert result["tempmail"]["enabled"] is False
    assert result["yyds_mail"]["enabled"] is True
    assert result["yyds_mail"]["has_api_key"] is True


def test_update_tempmail_settings_returns_error_when_reload_mismatches(monkeypatch):
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(settings_routes, "update_settings", lambda **kwargs: None)
    monkeypatch.setattr(
        settings_routes,
        "reload_settings",
        lambda: make_settings(
            yyds_mail_enabled=False,
            yyds_mail_api_key=None,
        ),
    )

    request = settings_routes.TempmailSettings(
        yyds_api_url="https://maliapi.changed.test/v1",
        yyds_api_key="AC-custom-key",
        yyds_enabled=True,
    )

    with pytest.raises(settings_routes.HTTPException) as exc_info:
        asyncio.run(settings_routes.update_tempmail_settings(request))

    assert exc_info.value.status_code == 500


def test_update_settings_reloads_from_database_after_save(monkeypatch):
    captured = {}
    reloaded = make_settings(
        yyds_mail_enabled=True,
        yyds_mail_base_url="https://maliapi.persisted.test/v1",
        yyds_mail_api_key=SecretStr("AC-persisted-key"),
        yyds_mail_default_domain="persisted.example.com",
    )

    monkeypatch.setattr(settings_config, "_settings", make_settings())

    def fake_save_settings_to_db(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(settings_config, "_save_settings_to_db", fake_save_settings_to_db)
    monkeypatch.setattr(settings_config, "reload_settings", lambda: reloaded)

    result = settings_config.update_settings(
        yyds_mail_enabled=True,
        yyds_mail_base_url="https://maliapi.persisted.test/v1",
        yyds_mail_api_key="AC-persisted-key",
        yyds_mail_default_domain="persisted.example.com",
    )

    assert result is reloaded
    assert captured["yyds_mail_enabled"] is True
    assert captured["yyds_mail_base_url"] == "https://maliapi.persisted.test/v1"
    assert captured["yyds_mail_default_domain"] == "persisted.example.com"
    assert captured["yyds_mail_api_key"].get_secret_value() == "AC-persisted-key"


def test_reload_settings_restores_yyds_state_after_cache_reset(monkeypatch):
    monkeypatch.setattr(settings_config, "_settings", None)
    monkeypatch.setattr(settings_config, "init_default_settings", lambda: None)
    monkeypatch.setattr(
        settings_config,
        "_load_settings_from_db",
        lambda: {
            "tempmail_enabled": True,
            "yyds_mail_enabled": True,
            "yyds_mail_base_url": "https://maliapi.persisted.test/v1",
            "yyds_mail_api_key": SecretStr("AC-persisted-key"),
            "yyds_mail_default_domain": "persisted.example.com",
        },
    )

    settings = settings_config.reload_settings()

    assert settings.yyds_mail_enabled is True
    assert settings.yyds_mail_api_key.get_secret_value() == "AC-persisted-key"

    monkeypatch.setattr(settings_routes, "get_settings", lambda: settings)
    all_settings = asyncio.run(settings_routes.get_all_settings())
    tempmail_settings = asyncio.run(settings_routes.get_tempmail_settings())

    assert all_settings["yyds_mail"]["enabled"] is True
    assert all_settings["yyds_mail"]["has_api_key"] is True
    assert tempmail_settings["yyds_mail"]["enabled"] is True
    assert tempmail_settings["yyds_mail"]["has_api_key"] is True


def test_test_tempmail_service_uses_tempmail_provider(monkeypatch):
    captured = {}

    class FakeService:
        def check_health(self):
            return True

    def fake_create(service_type, config, name=None):
        captured["service_type"] = service_type
        captured["config"] = config
        return FakeService()

    monkeypatch.setattr(email_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(email_routes.EmailServiceFactory, "create", fake_create)

    request = email_routes.TempmailTestRequest(provider="tempmail", api_url="https://api.tempmail.custom/v2")
    result = asyncio.run(email_routes.test_tempmail_service(request))

    assert result["success"] is True
    assert captured["service_type"] == EmailServiceType.TEMPMAIL
    assert captured["config"]["base_url"] == "https://api.tempmail.custom/v2"


def test_test_tempmail_service_uses_yyds_provider(monkeypatch):
    captured = {}

    class FakeService:
        def check_health(self):
            return True

    def fake_create(service_type, config, name=None):
        captured["service_type"] = service_type
        captured["config"] = config
        return FakeService()

    monkeypatch.setattr(email_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(email_routes.EmailServiceFactory, "create", fake_create)

    request = email_routes.TempmailTestRequest(
        provider="yyds_mail",
        api_url="https://maliapi.custom.test/v1",
        api_key="AC-custom-key",
    )
    result = asyncio.run(email_routes.test_tempmail_service(request))

    assert result["success"] is True
    assert captured["service_type"] == EmailServiceType.YYDS_MAIL
    assert captured["config"]["base_url"] == "https://maliapi.custom.test/v1"
    assert captured["config"]["api_key"] == "AC-custom-key"
    assert captured["config"]["default_domain"] == "public.example.com"
