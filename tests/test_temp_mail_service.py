from src.services.temp_mail import TempMailService


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class FakeHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({
            "method": method,
            "url": url,
            "kwargs": kwargs,
        })
        if not self.responses:
            raise AssertionError(f"未准备响应: {method} {url}")
        return self.responses.pop(0)


def test_get_verification_code_fallbacks_to_admin_when_user_endpoints_fail():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(status_code=401, payload={"error": "unauthorized"}),
        FakeResponse(status_code=401, payload={"error": "unauthorized"}),
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "OpenAI verification",
                        "text": "Your OpenAI verification code is 654321",
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    email = "tester@example.com"
    service._email_cache[email] = {"jwt": "jwt-abc"}

    code = service.get_verification_code(email=email, timeout=1)

    assert code == "654321"
    assert fake_client.calls[0]["url"].endswith("/api/mails")
    assert fake_client.calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer jwt-abc"
    assert fake_client.calls[1]["url"].endswith("/user_api/mails")
    assert fake_client.calls[1]["kwargs"]["headers"]["x-user-token"] == "jwt-abc"
    assert fake_client.calls[2]["url"].endswith("/admin/mails")
    assert fake_client.calls[2]["kwargs"]["params"]["address"] == email


def test_get_verification_code_without_jwt_uses_admin_only():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "Code",
                        "text": "123456 is your verification code",
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="nojwt@example.com", timeout=1)

    assert code == "123456"
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["url"].endswith("/admin/mails")


def test_get_verification_code_skips_last_used_mail_id_between_calls():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "Code #1",
                        "text": "111111 is your verification code",
                    }
                ],
                "total": 1,
            }
        ),
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "Code #1",
                        "text": "111111 is your verification code",
                    },
                    {
                        "id": "mail-2",
                        "source": "noreply@openai.com",
                        "subject": "Code #2",
                        "text": "222222 is your verification code",
                    },
                ],
                "total": 2,
            }
        ),
    ])
    service.http_client = fake_client

    code_1 = service.get_verification_code(email="reuse@example.com", timeout=1)
    code_2 = service.get_verification_code(email="reuse@example.com", timeout=1)

    assert code_1 == "111111"
    assert code_2 == "222222"


def test_get_verification_code_filters_old_mails_by_otp_sent_at():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    otp_sent_at = 1_700_000_000.0
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-old",
                        "source": "noreply@openai.com",
                        "subject": "Old Code",
                        "text": "333333 is your verification code",
                        "createdAt": otp_sent_at - 30,
                    },
                    {
                        "id": "mail-new",
                        "source": "noreply@openai.com",
                        "subject": "New Code",
                        "text": "444444 is your verification code",
                        "createdAt": otp_sent_at + 5,
                    },
                ],
                "total": 2,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(
        email="filter@example.com",
        timeout=1,
        otp_sent_at=otp_sent_at,
    )

    assert code == "444444"


def test_get_verification_code_accepts_mails_key_and_missing_mail_id():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "mails": [
                    {
                        # 没有 id/mail_id 字段，验证回退 ID 逻辑
                        "source": "noreply@openai.com",
                        "subject": "OpenAI verification",
                        "text": "Your verification code is 987654",
                        "createdAt": "2026-03-23 10:00:00",
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="format@example.com", timeout=1)

    assert code == "987654"


def test_get_verification_code_fetches_mail_detail_when_list_has_no_body():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-100",
                        "source": "noreply@openai.com",
                        "subject": "OpenAI verification",
                        "createdAt": "2026-03-23T10:00:00Z",
                    }
                ]
            }
        ),
        FakeResponse(
            payload={
                "id": "mail-100",
                "source": "noreply@openai.com",
                "subject": "OpenAI verification",
                "text": "Your OpenAI verification code is 246810",
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="detail@example.com", timeout=1)

    assert code == "246810"


def test_get_verification_code_admin_unfiltered_fallback():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        # /admin/mails?address=... 返回空
        FakeResponse(payload={"results": []}),
        # /admin/mails 不带地址过滤，返回包含目标邮箱邮件
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-200",
                        "address": "target@example.com",
                        "source": "noreply@openai.com",
                        "subject": "Code",
                        "text": "135790 is your verification code",
                    },
                    {
                        "id": "mail-201",
                        "address": "other@example.com",
                        "source": "noreply@openai.com",
                        "subject": "Code",
                        "text": "111111 is your verification code",
                    },
                ]
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="target@example.com", timeout=1)

    assert code == "135790"


def test_cleanup_failed_task_resources_deletes_address_when_enabled():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": True,
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={}),
    ])
    service.http_client = fake_client
    service._email_cache["cleanup@example.com"] = {
        "email": "cleanup@example.com",
        "address_id": "addr-1",
        "service_id": "cleanup@example.com",
    }
    service._last_used_mail_ids["cleanup@example.com"] = "mail-1"
    service._task_mail_ids_by_email["cleanup@example.com"] = {"mail-1", "mail-2"}

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com", "address_id": "addr-1"},
        mailbox_email="cleanup@example.com",
    )

    assert result["address_deleted"] is True
    assert result["lookup_attempted"] is False
    assert result["lookup_matched"] is False
    assert fake_client.calls[0]["method"] == "DELETE"
    assert fake_client.calls[0]["url"].endswith("/admin/delete_address/addr-1")
    assert "cleanup@example.com" not in service._email_cache
    assert "cleanup@example.com" not in service._last_used_mail_ids
    assert "cleanup@example.com" not in service._task_mail_ids_by_email


def test_cleanup_failed_task_resources_looks_up_address_id_then_deletes_address():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": True,
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={"results": [{"id": 506, "name": "cleanup@example.com"}]}),
        FakeResponse(payload={}),
    ])
    service.http_client = fake_client
    service._email_cache["cleanup@example.com"] = {
        "email": "cleanup@example.com",
        "service_id": "cleanup@example.com",
    }
    service._task_mail_ids_by_email["cleanup@example.com"] = {"mail-1", "mail-2"}

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com"},
        mailbox_email="cleanup@example.com",
    )

    assert result["address_deleted"] is True
    assert result["address_id"] == "506"
    assert result["lookup_attempted"] is True
    assert result["lookup_matched"] is True
    assert [call["url"] for call in fake_client.calls] == [
        "https://mail.example.com/admin/address",
        "https://mail.example.com/admin/delete_address/506",
    ]
    assert fake_client.calls[0]["kwargs"]["params"] == {
        "limit": 1,
        "offset": 0,
        "query": "cleanup@example.com",
    }
    assert "cleanup@example.com" not in service._email_cache


def test_cleanup_failed_task_resources_does_not_treat_email_info_id_as_address_id():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": True,
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={"results": [{"id": 506, "name": "cleanup@example.com"}]}),
        FakeResponse(payload={}),
    ])
    service.http_client = fake_client

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com", "id": "cleanup@example.com"},
        mailbox_email="cleanup@example.com",
    )

    assert result["address_deleted"] is True
    assert result["address_id"] == "506"
    assert fake_client.calls[0]["url"] == "https://mail.example.com/admin/address"
    assert fake_client.calls[1]["url"] == "https://mail.example.com/admin/delete_address/506"


def test_cleanup_failed_task_resources_swallows_delete_errors_and_clears_local_state():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": True,
    })
    fake_client = FakeHTTPClient([
        FakeResponse(status_code=500, payload={"error": "boom"}),
    ])
    service.http_client = fake_client
    service._email_cache["cleanup@example.com"] = {
        "email": "cleanup@example.com",
        "service_id": "cleanup@example.com",
    }
    service._task_mail_ids_by_email["cleanup@example.com"] = {"mail-1"}
    service._last_used_mail_ids["cleanup@example.com"] = "mail-1"

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com"},
        mailbox_email="cleanup@example.com",
    )

    assert result["address_deleted"] is False
    assert result["address_id"] == ""
    assert result["lookup_attempted"] is True
    assert result["lookup_matched"] is False
    assert "cleanup@example.com" not in service._email_cache
    assert "cleanup@example.com" not in service._last_used_mail_ids
    assert "cleanup@example.com" not in service._task_mail_ids_by_email


def test_cleanup_failed_task_resources_uses_exact_email_match_from_address_lookup():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": True,
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={
            "results": [
                {"id": 100, "name": "other@example.com"},
                {"id": 506, "name": "cleanup@example.com"},
            ]
        }),
        FakeResponse(payload={}),
    ])
    service.http_client = fake_client

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com"},
        mailbox_email="cleanup@example.com",
    )

    assert result["address_deleted"] is True
    assert result["address_id"] == "506"
    assert fake_client.calls[1]["url"].endswith("/admin/delete_address/506")


def test_cleanup_failed_task_resources_does_not_delete_when_address_lookup_misses():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": True,
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={"results": []}),
    ])
    service.http_client = fake_client

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com"},
        mailbox_email="cleanup@example.com",
    )

    assert result["address_deleted"] is False
    assert result["address_id"] == ""
    assert result["lookup_attempted"] is True
    assert result["lookup_matched"] is False
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["url"] == "https://mail.example.com/admin/address"


def test_cleanup_failed_task_resources_swallows_delete_address_errors_after_lookup():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": True,
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={"results": [{"id": 506, "name": "cleanup@example.com"}]}),
        FakeResponse(status_code=500, payload={"error": "boom"}),
    ])
    service.http_client = fake_client
    service._email_cache["cleanup@example.com"] = {"email": "cleanup@example.com"}

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com"},
        mailbox_email="cleanup@example.com",
    )

    assert result["address_deleted"] is False
    assert result["address_id"] == "506"
    assert result["lookup_attempted"] is True
    assert result["lookup_matched"] is True
    assert "cleanup@example.com" not in service._email_cache


def test_cleanup_failed_task_resources_is_noop_when_disabled():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
        "cleanup_on_task_failure": False,
    })
    fake_client = FakeHTTPClient([])
    service.http_client = fake_client

    result = service.cleanup_failed_task_resources(
        email_info={"email": "cleanup@example.com", "address_id": "addr-1"},
        mailbox_email="cleanup@example.com",
    )

    assert result["skipped"] is True
    assert fake_client.calls == []
