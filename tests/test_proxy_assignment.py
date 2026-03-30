import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.register import RegistrationResult
from src.database import crud
from src.database.models import Base
from src.web.routes import registration as registration_routes
from src.web.routes import settings as settings_routes


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return session


def test_round_robin_proxy_selection_rotates():
    db = _make_db()
    try:
        crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8001)
        crud.create_proxy(db, name="p2", type="http", host="127.0.0.1", port=8002)
        crud.create_proxy(db, name="p3", type="http", host="127.0.0.1", port=8003)

        selected_ids = [
            crud.select_proxy_for_registration(db, strategy="round_robin").id
            for _ in range(4)
        ]

        assert selected_ids == [1, 2, 3, 1]
    finally:
        db.close()


def test_least_recently_used_prefers_oldest_last_used():
    db = _make_db()
    try:
        p1 = crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8001)
        p2 = crud.create_proxy(db, name="p2", type="http", host="127.0.0.1", port=8002)
        p3 = crud.create_proxy(db, name="p3", type="http", host="127.0.0.1", port=8003)

        now = datetime.utcnow()
        crud.update_proxy(db, p1.id, last_used=now - timedelta(minutes=2))
        crud.update_proxy(db, p2.id, last_used=now - timedelta(minutes=10))
        crud.update_proxy(db, p3.id, last_used=now - timedelta(minutes=5))

        selected = crud.select_proxy_for_registration(db, strategy="least_recently_used")

        assert selected.id == p2.id
    finally:
        db.close()


def test_proxy_list_exhausted_does_not_fall_back_to_dynamic(monkeypatch):
    db = _make_db()
    try:
        p1 = crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8001)
        p2 = crud.create_proxy(db, name="p2", type="http", host="127.0.0.1", port=8002)

        monkeypatch.setattr(
            registration_routes,
            "get_settings",
            lambda: SimpleNamespace(
                proxy_assignment_strategy="round_robin",
                proxy_url="http://settings-proxy:9000",
            ),
        )

        result = registration_routes.get_proxy_for_registration(
            db,
            exclude_proxy_ids={p1.id, p2.id},
            dynamic_attempt_count=0,
            allow_settings_fallback=True,
        )

        assert result.proxy_source == "proxy_list_exhausted"
        assert result.proxy_url is None
    finally:
        db.close()


def test_update_dynamic_proxy_settings_saves_assignment_strategy(monkeypatch):
    captured = {}

    def fake_update_settings(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(settings_routes, "update_settings", fake_update_settings)

    request = settings_routes.DynamicProxySettings(
        enabled=True,
        api_url="https://proxy.example.test",
        assignment_strategy="least_recently_used",
    )

    result = asyncio.run(settings_routes.update_dynamic_proxy_settings(request))

    assert result["success"] is True
    assert captured["proxy_assignment_strategy"] == "least_recently_used"


def test_run_sync_registration_task_retries_next_proxy_on_proxy_error(monkeypatch):
    logs = []
    task_updates = []
    save_calls = []
    selected_proxies = []

    class DummyTask:
        pass

    class FakeEngine:
        run_count = 0

        def __init__(self, email_service, proxy_url, callback_logger, task_uuid):
            selected_proxies.append(proxy_url)
            self.proxy_url = proxy_url

        def run(self):
            FakeEngine.run_count += 1
            if FakeEngine.run_count == 1:
                return RegistrationResult(success=False, error_message="proxy connect error")
            return RegistrationResult(success=True, email="done@example.com", metadata={})

        def save_to_database(self, result, account_label=None, role_tag=None):
            save_calls.append((result.email, account_label, role_tag))
            return True

    @contextmanager
    def fake_get_db():
        yield object()

    proxies = [
        SimpleNamespace(id=1, proxy_url="http://proxy-1:8001"),
        SimpleNamespace(id=2, proxy_url="http://proxy-2:8002"),
    ]

    def fake_update_registration_task(db, task_uuid, **kwargs):
        task_updates.append(kwargs)
        if kwargs.get("status") == "running":
            return DummyTask()
        return DummyTask()

    monkeypatch.setattr(registration_routes, "get_db", fake_get_db)
    monkeypatch.setattr(registration_routes, "RegistrationEngine", FakeEngine)
    monkeypatch.setattr(registration_routes, "_build_email_service_for_task", lambda *args, **kwargs: object())
    monkeypatch.setattr(registration_routes.task_manager, "is_cancelled", lambda task_uuid: False)
    monkeypatch.setattr(registration_routes.task_manager, "update_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        registration_routes.task_manager,
        "create_log_callback",
        lambda *args, **kwargs: (lambda message: logs.append(message)),
    )
    monkeypatch.setattr(registration_routes.crud, "update_registration_task", fake_update_registration_task)
    monkeypatch.setattr(registration_routes.crud, "update_proxy_last_used", lambda db, proxy_id: True)
    monkeypatch.setattr(registration_routes.crud, "get_enabled_proxies", lambda db: proxies)
    monkeypatch.setattr(
        registration_routes.crud,
        "select_proxy_for_registration",
        lambda db, strategy, exclude_ids=None: next(
            (proxy for proxy in proxies if proxy.id not in set(exclude_ids or [])),
            None,
        ),
    )
    monkeypatch.setattr(
        registration_routes,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_assignment_strategy="round_robin",
            proxy_url=None,
            proxy_dynamic_enabled=False,
            proxy_dynamic_api_url="",
        ),
    )

    registration_routes._run_sync_registration_task(
        task_uuid="task-1",
        email_service_type="tempmail",
        proxy=None,
        email_service_config=None,
    )

    assert selected_proxies == ["http://proxy-1:8001", "http://proxy-2:8002"]
    assert any("切换到下一个代理重试" in item for item in logs)
    assert any(item.get("status") == "completed" for item in task_updates)
    assert save_calls == [("done@example.com", "child", "child")]
