import asyncio
from contextlib import contextmanager

from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Base, EmailService
from src.web.routes import accounts as accounts_routes


class DummySettings:
    def __init__(
        self,
        *,
        tempmail_enabled=False,
        yyds_mail_enabled=False,
        yyds_mail_api_key=None,
    ):
        self.tempmail_enabled = tempmail_enabled
        self.yyds_mail_enabled = yyds_mail_enabled
        self.yyds_mail_api_key = yyds_mail_api_key


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _patch_db(monkeypatch, session):
    @contextmanager
    def fake_get_db():
        yield session

    monkeypatch.setattr(accounts_routes, "get_db", fake_get_db)


def test_account_filter_options_returns_tempmail_when_enabled(monkeypatch):
    session = _make_session()
    _patch_db(monkeypatch, session)
    monkeypatch.setattr(
        accounts_routes,
        "get_settings",
        lambda: DummySettings(tempmail_enabled=True),
    )

    result = asyncio.run(accounts_routes.get_account_filter_options())

    assert result["email_services"] == [
        {
            "value": "tempmail",
            "label": "Tempmail.lol",
            "source": "settings",
        }
    ]


def test_account_filter_options_returns_yyds_mail_only_with_api_key(monkeypatch):
    session = _make_session()
    _patch_db(monkeypatch, session)
    monkeypatch.setattr(
        accounts_routes,
        "get_settings",
        lambda: DummySettings(yyds_mail_enabled=True),
    )

    missing_key_result = asyncio.run(accounts_routes.get_account_filter_options())

    assert missing_key_result["email_services"] == []

    monkeypatch.setattr(
        accounts_routes,
        "get_settings",
        lambda: DummySettings(
            yyds_mail_enabled=True,
            yyds_mail_api_key=SecretStr("AC-test-key"),
        ),
    )

    result = asyncio.run(accounts_routes.get_account_filter_options())

    assert result["email_services"] == [
        {
            "value": "yyds_mail",
            "label": "YYDS Mail",
            "source": "settings",
        }
    ]


def test_account_filter_options_returns_enabled_database_services_once(monkeypatch):
    session = _make_session()
    session.add_all(
        [
            EmailService(service_type="outlook", name="Outlook A", config={}, enabled=True),
            EmailService(service_type="outlook", name="Outlook B", config={}, enabled=True),
            EmailService(service_type="moe_mail", name="Moe A", config={}, enabled=True),
            EmailService(service_type="temp_mail", name="Temp A", config={}, enabled=True),
            EmailService(service_type="cloudmail", name="Cloud A", config={}, enabled=True),
            EmailService(service_type="duck_mail", name="Duck A", config={}, enabled=True),
            EmailService(service_type="freemail", name="Free A", config={}, enabled=True),
            EmailService(service_type="imap_mail", name="Imap A", config={}, enabled=True),
            EmailService(service_type="imap_mail", name="Imap Disabled", config={}, enabled=False),
            EmailService(service_type="yyds_mail", name="YYDS Custom", config={}, enabled=True),
            EmailService(service_type="temp_mail", name="Temp Disabled", config={}, enabled=False),
        ]
    )
    session.commit()

    _patch_db(monkeypatch, session)
    monkeypatch.setattr(accounts_routes, "get_settings", lambda: DummySettings())

    result = asyncio.run(accounts_routes.get_account_filter_options())

    assert result["email_services"] == [
        {"value": "outlook", "label": "Outlook", "source": "database", "count": 2},
        {"value": "moe_mail", "label": "MoeMail", "source": "database", "count": 1},
        {"value": "temp_mail", "label": "Temp-Mail（自部署）", "source": "database", "count": 1},
        {"value": "cloudmail", "label": "CloudMail（自部署）", "source": "database", "count": 1},
        {"value": "duck_mail", "label": "DuckMail", "source": "database", "count": 1},
        {"value": "freemail", "label": "Freemail", "source": "database", "count": 1},
        {"value": "imap_mail", "label": "IMAP 邮箱", "source": "database", "count": 1},
    ]


def test_account_filter_options_combines_global_and_database_services(monkeypatch):
    session = _make_session()
    session.add(EmailService(service_type="cloudmail", name="Cloud A", config={}, enabled=True))
    session.commit()

    _patch_db(monkeypatch, session)
    monkeypatch.setattr(
        accounts_routes,
        "get_settings",
        lambda: DummySettings(
            tempmail_enabled=True,
            yyds_mail_enabled=True,
            yyds_mail_api_key=SecretStr("AC-test-key"),
        ),
    )

    result = asyncio.run(accounts_routes.get_account_filter_options())

    assert result["email_services"] == [
        {"value": "tempmail", "label": "Tempmail.lol", "source": "settings"},
        {"value": "yyds_mail", "label": "YYDS Mail", "source": "settings"},
        {"value": "cloudmail", "label": "CloudMail（自部署）", "source": "database", "count": 1},
    ]
