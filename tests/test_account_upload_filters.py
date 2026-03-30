import asyncio
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Account, Base
from src.web.routes import accounts as accounts_routes


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def test_list_accounts_filters_by_upload_targets(monkeypatch):
    session = _make_session()
    session.add_all(
        [
            Account(
                email="cpa@example.com",
                email_service="manual",
                status="active",
                cpa_uploaded=True,
                sub2api_uploaded=False,
                tm_uploaded=False,
            ),
            Account(
                email="s2a@example.com",
                email_service="manual",
                status="active",
                cpa_uploaded=False,
                sub2api_uploaded=True,
                tm_uploaded=False,
            ),
            Account(
                email="tm@example.com",
                email_service="manual",
                status="active",
                cpa_uploaded=False,
                sub2api_uploaded=False,
                tm_uploaded=True,
            ),
            Account(
                email="both@example.com",
                email_service="manual",
                status="active",
                cpa_uploaded=True,
                sub2api_uploaded=True,
                tm_uploaded=True,
            ),
            Account(
                email="none@example.com",
                email_service="manual",
                status="active",
                cpa_uploaded=False,
                sub2api_uploaded=False,
                tm_uploaded=False,
            ),
        ]
    )
    session.commit()

    @contextmanager
    def fake_get_db():
        yield session

    monkeypatch.setattr(accounts_routes, "get_db", fake_get_db)

    cpa_only = asyncio.run(
        accounts_routes.list_accounts(
            page=1,
            page_size=20,
            status=None,
            email_service=None,
            role_tag=None,
            upload_targets=["cpa"],
            uploaded=True,
            cpa_uploaded=None,
            sub2api_uploaded=None,
            pool_state=None,
            biz_tag=None,
            search=None,
        )
    )
    assert sorted(item.email for item in cpa_only.accounts) == [
        "both@example.com",
        "cpa@example.com",
    ]

    cpa_and_tm = asyncio.run(
        accounts_routes.list_accounts(
            page=1,
            page_size=20,
            status=None,
            email_service=None,
            role_tag=None,
            upload_targets=["cpa", "tm"],
            uploaded=None,
            cpa_uploaded=None,
            sub2api_uploaded=None,
            pool_state=None,
            biz_tag=None,
            search=None,
        )
    )
    assert cpa_and_tm.total == 1
    assert [item.email for item in cpa_and_tm.accounts] == ["both@example.com"]
    assert cpa_and_tm.accounts[0].tm_uploaded is True

    no_upload = asyncio.run(
        accounts_routes.list_accounts(
            page=1,
            page_size=20,
            status=None,
            email_service=None,
            role_tag=None,
            upload_targets=["none"],
            uploaded=None,
            cpa_uploaded=None,
            sub2api_uploaded=None,
            pool_state=None,
            biz_tag=None,
            search=None,
        )
    )
    assert no_upload.total == 1
    assert [item.email for item in no_upload.accounts] == ["none@example.com"]

    legacy_any_uploaded = asyncio.run(
        accounts_routes.list_accounts(
            page=1,
            page_size=20,
            status=None,
            email_service=None,
            role_tag=None,
            upload_targets=None,
            uploaded=True,
            cpa_uploaded=None,
            sub2api_uploaded=None,
            pool_state=None,
            biz_tag=None,
            search=None,
        )
    )
    assert legacy_any_uploaded.total == 4

    legacy_filter = asyncio.run(
        accounts_routes.list_accounts(
            page=1,
            page_size=20,
            status=None,
            email_service=None,
            role_tag=None,
            upload_targets=None,
            uploaded=None,
            cpa_uploaded=False,
            sub2api_uploaded=True,
            pool_state=None,
            biz_tag=None,
            search=None,
        )
    )
    assert legacy_filter.total == 1
    assert [item.email for item in legacy_filter.accounts] == ["s2a@example.com"]
