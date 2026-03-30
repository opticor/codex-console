import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import src.config.settings as settings_config
from src.core import dynamic_proxy as dynamic_proxy_module
from src.core import system_selfcheck
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.register import RegistrationResult
from src.database import crud
from src.database.models import Base
from src.web.routes import accounts as accounts_routes
from src.web.routes import registration as registration_routes
from src.web.routes import settings as settings_routes


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    return session


def _make_settings_stub(**overrides):
    data = {
        "proxy_enabled": False,
        "proxy_type": "http",
        "proxy_host": "127.0.0.1",
        "proxy_port": 7890,
        "proxy_username": None,
        "proxy_password": None,
        "proxy_dynamic_enabled": False,
        "proxy_dynamic_api_url": "",
        "proxy_dynamic_api_key": None,
        "proxy_dynamic_api_key_header": "X-API-Key",
        "proxy_dynamic_result_field": "",
        "proxy_assignment_strategy": "round_robin",
        "proxy_url": None,
        "registration_max_retries": 3,
        "registration_timeout": 120,
        "registration_default_password_length": 12,
        "registration_sleep_min": 5,
        "registration_sleep_max": 30,
        "registration_entry_flow": "native",
        "webui_host": "0.0.0.0",
        "webui_port": 8000,
        "debug": False,
        "webui_access_password": None,
        "auto_quick_refresh_enabled": False,
        "auto_quick_refresh_interval_minutes": 30,
        "auto_quick_refresh_retry_limit": 2,
        "circuit_breaker_enabled": True,
        "circuit_breaker_failure_threshold": 5,
        "circuit_breaker_cooldown_seconds": 180,
        "circuit_breaker_probe_interval_seconds": 30,
        "tempmail_enabled": True,
        "tempmail_base_url": "https://api.tempmail.test",
        "tempmail_timeout": 30,
        "tempmail_max_retries": 3,
        "yyds_mail_enabled": False,
        "yyds_mail_base_url": "https://api.yyds.test",
        "yyds_mail_default_domain": "",
        "yyds_mail_timeout": 30,
        "yyds_mail_max_retries": 3,
        "yyds_mail_api_key": None,
        "email_code_timeout": 120,
        "email_code_poll_interval": 3,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_normalize_proxy_assignment_strategy_accepts_new_values():
    assert settings_config.normalize_proxy_assignment_strategy("default_only") == "default_only"
    assert settings_config.normalize_proxy_assignment_strategy("no_proxy") == "no_proxy"
    assert settings_config.normalize_proxy_assignment_strategy("unknown") == "round_robin"


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


def test_default_only_proxy_selection_returns_enabled_default():
    db = _make_db()
    try:
        default_proxy = crud.create_proxy(db, name="default", type="http", host="127.0.0.1", port=8011)
        crud.create_proxy(db, name="other", type="http", host="127.0.0.1", port=8012)

        selected = crud.select_proxy_for_registration(db, strategy="default_only")

        assert selected is not None
        assert selected.id == default_proxy.id
    finally:
        db.close()


def test_default_only_proxy_selection_does_not_fall_back_to_other_proxy():
    db = _make_db()
    try:
        default_proxy = crud.create_proxy(db, name="default", type="http", host="127.0.0.1", port=8021)
        other_proxy = crud.create_proxy(db, name="other", type="http", host="127.0.0.1", port=8022)
        crud.update_proxy(db, default_proxy.id, enabled=False)

        selected = crud.select_proxy_for_registration(db, strategy="default_only")

        assert selected is None
        assert crud.get_proxy_by_id(db, other_proxy.id).enabled is True
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


def test_no_proxy_strategy_skips_proxy_list_and_allows_direct(monkeypatch):
    db = _make_db()
    try:
        crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8031)
        monkeypatch.setattr(
            crud,
            "select_proxy_for_registration",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not select proxy list")),
        )

        result = dynamic_proxy_module.resolve_auto_proxy_for_task(
            settings=_make_settings_stub(
                proxy_assignment_strategy="no_proxy",
            ),
            db=db,
        )

        assert result.proxy_source == "direct"
        assert result.proxy_url is None
    finally:
        db.close()


def test_default_only_strategy_falls_back_to_settings_when_default_missing():
    db = _make_db()
    try:
        proxy = crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8041)
        crud.update_proxy(db, proxy.id, is_default=False)

        result = dynamic_proxy_module.resolve_auto_proxy_for_task(
            settings=_make_settings_stub(
                proxy_assignment_strategy="default_only",
                proxy_url="http://settings-proxy:9041",
            ),
            db=db,
        )

        assert result.proxy_source == "settings"
        assert result.proxy_url == "http://settings-proxy:9041"
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


def test_update_dynamic_proxy_settings_saves_default_only(monkeypatch):
    captured = {}

    def fake_update_settings(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(settings_routes, "update_settings", fake_update_settings)

    request = settings_routes.DynamicProxySettings(
        enabled=True,
        api_url="https://proxy.example.test",
        assignment_strategy="default_only",
    )

    result = asyncio.run(settings_routes.update_dynamic_proxy_settings(request))

    assert result["success"] is True
    assert captured["proxy_assignment_strategy"] == "default_only"


def test_update_dynamic_proxy_settings_saves_no_proxy(monkeypatch):
    captured = {}

    def fake_update_settings(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(settings_routes, "update_settings", fake_update_settings)

    request = settings_routes.DynamicProxySettings(
        enabled=False,
        api_url="",
        assignment_strategy="no_proxy",
    )

    result = asyncio.run(settings_routes.update_dynamic_proxy_settings(request))

    assert result["success"] is True
    assert captured["proxy_assignment_strategy"] == "no_proxy"


def test_get_all_settings_returns_new_proxy_strategy(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "get_settings",
        lambda: _make_settings_stub(proxy_assignment_strategy="default_only"),
    )

    result = asyncio.run(settings_routes.get_all_settings())

    assert result["proxy"]["assignment_strategy"] == "default_only"


def test_proxy_to_dict_exposes_success_metrics():
    db = _make_db()
    try:
        proxy = crud.create_proxy(db, name="stats", type="http", host="127.0.0.1", port=9001)
        crud.update_proxy(db, proxy.id, success_count=3, failure_count=1)

        result = crud.get_proxy_by_id(db, proxy.id).to_dict()

        assert result["success_count"] == 3
        assert result["failure_count"] == 1
        assert result["success_rate"] == 75.0
    finally:
        db.close()


def test_increment_proxy_success_updates_last_used_timestamp():
    db = _make_db()
    try:
        proxy = crud.create_proxy(db, name="success", type="http", host="127.0.0.1", port=9002)

        updated = crud.increment_proxy_success(db, proxy.id)
        refreshed = crud.get_proxy_by_id(db, proxy.id)

        assert updated is True
        assert refreshed.success_count == 1
        assert refreshed.failure_count == 0
        assert refreshed.last_used is not None
    finally:
        db.close()


def test_increment_proxy_failure_does_not_touch_last_used():
    db = _make_db()
    try:
        proxy = crud.create_proxy(db, name="failure", type="http", host="127.0.0.1", port=9003)

        updated = crud.increment_proxy_failure(db, proxy.id)
        refreshed = crud.get_proxy_by_id(db, proxy.id)

        assert updated is True
        assert refreshed.success_count == 0
        assert refreshed.failure_count == 1
        assert refreshed.last_used is None
    finally:
        db.close()


def test_get_proxies_list_returns_success_metrics(monkeypatch):
    db = _make_db()
    try:
        proxy = crud.create_proxy(db, name="list", type="http", host="127.0.0.1", port=9004)
        crud.update_proxy(db, proxy.id, success_count=2, failure_count=2)

        @contextmanager
        def fake_get_db():
            yield db

        monkeypatch.setattr(settings_routes, "get_db", fake_get_db)

        result = asyncio.run(settings_routes.get_proxies_list())

        assert result["total"] == 1
        assert result["proxies"][0]["success_count"] == 2
        assert result["proxies"][0]["failure_count"] == 2
        assert result["proxies"][0]["success_rate"] == 50.0
    finally:
        db.close()


def test_batch_proxy_routes_return_counts_and_missing_ids(monkeypatch):
    db = _make_db()
    try:
        proxy_a = crud.create_proxy(db, name="a", type="http", host="127.0.0.1", port=9101)
        proxy_b = crud.create_proxy(db, name="b", type="http", host="127.0.0.1", port=9102)
        crud.update_proxy(db, proxy_b.id, enabled=False)

        @contextmanager
        def fake_get_db():
            yield db

        monkeypatch.setattr(settings_routes, "get_db", fake_get_db)

        enable_result = asyncio.run(
            settings_routes.batch_enable_proxies(
                settings_routes.ProxyBatchActionRequest(ids=[proxy_a.id, proxy_b.id, 999])
            )
        )
        delete_result = asyncio.run(
            settings_routes.batch_delete_proxies(
                settings_routes.ProxyBatchActionRequest(ids=[proxy_a.id, 999])
            )
        )

        remaining = crud.get_proxy_by_id(db, proxy_b.id)

        assert enable_result == {
            "success": True,
            "requested": 3,
            "affected": 2,
            "missing_ids": [999],
        }
        assert delete_result == {
            "success": True,
            "requested": 2,
            "affected": 1,
            "missing_ids": [999],
        }
        assert remaining is not None
        assert remaining.enabled is True
        assert remaining.is_default is True
    finally:
        db.close()


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
    monkeypatch.setattr(registration_routes.crud, "increment_proxy_success", lambda db, proxy_id: True)
    monkeypatch.setattr(registration_routes.crud, "increment_proxy_failure", lambda db, proxy_id: True)
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


def test_registration_no_proxy_strategy_allows_direct(monkeypatch):
    db = _make_db()
    try:
        crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8051)
        monkeypatch.setattr(
            registration_routes,
            "get_settings",
            lambda: _make_settings_stub(
                proxy_assignment_strategy="no_proxy",
            ),
        )

        result = registration_routes.get_proxy_for_registration(
            db,
            exclude_proxy_ids=set(),
            dynamic_attempt_count=0,
            allow_settings_fallback=True,
        )

        assert result.proxy_source == "direct"
        assert result.proxy_url is None
    finally:
        db.close()


def test_registration_default_only_without_default_falls_back_to_settings(monkeypatch):
    db = _make_db()
    try:
        proxy = crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8061)
        crud.update_proxy(db, proxy.id, is_default=False)
        monkeypatch.setattr(
            registration_routes,
            "get_settings",
            lambda: _make_settings_stub(
                proxy_assignment_strategy="default_only",
                proxy_url="http://settings-proxy:9061",
            ),
        )

        result = registration_routes.get_proxy_for_registration(
            db,
            exclude_proxy_ids=set(),
            dynamic_attempt_count=0,
            allow_settings_fallback=True,
        )

        assert result.proxy_source == "settings"
        assert result.proxy_url == "http://settings-proxy:9061"
    finally:
        db.close()


def test_accounts_get_proxy_honors_no_proxy_strategy_with_direct(monkeypatch):
    db = _make_db()
    try:
        crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8071)

        @contextmanager
        def fake_get_db():
            yield db

        monkeypatch.setattr(accounts_routes, "get_db", fake_get_db)
        monkeypatch.setattr(
            accounts_routes,
            "get_settings",
            lambda: _make_settings_stub(
                proxy_assignment_strategy="no_proxy",
            ),
        )

        result = accounts_routes._get_proxy()

        assert result is None
    finally:
        db.close()


def test_selfcheck_proxy_resolver_honors_default_only_fallback(monkeypatch):
    db = _make_db()
    try:
        proxy = crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8081)
        crud.update_proxy(db, proxy.id, is_default=False)

        @contextmanager
        def fake_get_db():
            yield db

        monkeypatch.setattr(system_selfcheck, "get_db", fake_get_db)
        monkeypatch.setattr(
            system_selfcheck,
            "get_settings",
            lambda: _make_settings_stub(
                proxy_assignment_strategy="default_only",
                proxy_url="http://settings-proxy:9081",
            ),
        )

        result = system_selfcheck._resolve_selfcheck_proxy_url()

        assert result == "http://settings-proxy:9081"
    finally:
        db.close()


def test_selfcheck_proxy_resolver_honors_no_proxy_direct(monkeypatch):
    db = _make_db()
    try:
        crud.create_proxy(db, name="p1", type="http", host="127.0.0.1", port=8091)

        @contextmanager
        def fake_get_db():
            yield db

        monkeypatch.setattr(system_selfcheck, "get_db", fake_get_db)
        monkeypatch.setattr(
            system_selfcheck,
            "get_settings",
            lambda: _make_settings_stub(
                proxy_assignment_strategy="no_proxy",
            ),
        )

        result = system_selfcheck._resolve_selfcheck_proxy_url()

        assert result is None
    finally:
        db.close()


def test_run_sync_registration_task_allows_direct_when_no_proxy_strategy(monkeypatch):
    logs = []
    task_updates = []
    selected_proxies = []

    class DummyTask:
        pass

    class FakeEngine:
        def __init__(self, email_service, proxy_url, callback_logger, task_uuid):
            selected_proxies.append(proxy_url)

        def run(self):
            return RegistrationResult(success=True, email="direct@example.com", metadata={})

        def save_to_database(self, result, account_label=None, role_tag=None):
            return True

    @contextmanager
    def fake_get_db():
        yield object()

    def fake_update_registration_task(db, task_uuid, **kwargs):
        task_updates.append(kwargs)
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
    monkeypatch.setattr(registration_routes.crud, "increment_proxy_success", lambda db, proxy_id: True)
    monkeypatch.setattr(registration_routes.crud, "increment_proxy_failure", lambda db, proxy_id: True)
    monkeypatch.setattr(
        registration_routes,
        "get_settings",
        lambda: SimpleNamespace(
            proxy_assignment_strategy="no_proxy",
            proxy_url=None,
            proxy_dynamic_enabled=False,
            proxy_dynamic_api_url="",
            proxy_dynamic_api_key=None,
            proxy_dynamic_api_key_header="X-API-Key",
            proxy_dynamic_result_field="",
        ),
    )

    registration_routes._run_sync_registration_task(
        task_uuid="task-direct",
        email_service_type="tempmail",
        proxy=None,
        email_service_config=None,
    )

    assert selected_proxies == [None]
    assert any("直连运行" in item for item in logs)
    assert any(item.get("status") == "completed" for item in task_updates)
