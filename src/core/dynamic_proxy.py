"""
动态代理获取模块
支持通过外部 API 获取动态代理 URL
"""

from dataclasses import dataclass
import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass
class AutoProxySelectionResult:
    proxy_url: Optional[str]
    proxy_id: Optional[int]
    proxy_source: str
    strategy: str


def fetch_dynamic_proxy(api_url: str, api_key: str = "", api_key_header: str = "X-API-Key", result_field: str = "") -> Optional[str]:
    """
    从代理 API 获取代理 URL

    Args:
        api_url: 代理 API 地址，响应应为代理 URL 字符串或含代理 URL 的 JSON
        api_key: API 密钥（可选）
        api_key_header: API 密钥请求头名称
        result_field: 从 JSON 响应中提取代理 URL 的字段路径，支持点号分隔（如 "data.proxy"），留空则使用响应原文

    Returns:
        代理 URL 字符串（如 http://user:pass@host:port），失败返回 None
    """
    try:
        from curl_cffi import requests as cffi_requests

        headers = {}
        if api_key:
            headers[api_key_header] = api_key

        response = cffi_requests.get(
            api_url,
            headers=headers,
            timeout=10,
            impersonate="chrome110"
        )

        if response.status_code != 200:
            logger.warning(f"动态代理 API 返回错误状态码: {response.status_code}")
            return None

        text = response.text.strip()

        # 尝试解析 JSON
        if result_field or text.startswith("{") or text.startswith("["):
            try:
                import json
                data = json.loads(text)
                if result_field:
                    # 按点号路径逐层提取
                    for key in result_field.split("."):
                        if isinstance(data, dict):
                            data = data.get(key)
                        elif isinstance(data, list) and key.isdigit():
                            data = data[int(key)]
                        else:
                            data = None
                        if data is None:
                            break
                    proxy_url = str(data).strip() if data is not None else None
                else:
                    # 无指定字段，尝试常见键名
                    for key in ("proxy", "url", "proxy_url", "data", "ip"):
                        val = data.get(key) if isinstance(data, dict) else None
                        if val:
                            proxy_url = str(val).strip()
                            break
                    else:
                        proxy_url = text
            except (ValueError, AttributeError):
                proxy_url = text
        else:
            proxy_url = text

        if not proxy_url:
            logger.warning("动态代理 API 返回空代理 URL")
            return None

        # 若未包含协议头，默认加 http://
        if not re.match(r'^(http|socks5)://', proxy_url):
            proxy_url = "http://" + proxy_url

        logger.info(f"动态代理获取成功: {proxy_url[:40]}..." if len(proxy_url) > 40 else f"动态代理获取成功: {proxy_url}")
        return proxy_url

    except Exception as e:
        logger.error(f"获取动态代理失败: {e}")
        return None


def resolve_auto_proxy_for_task(
    *,
    settings=None,
    db=None,
    exclude_proxy_ids: Optional[Iterable[int]] = None,
    dynamic_attempt_count: int = 0,
    allow_settings_fallback: bool = True,
) -> AutoProxySelectionResult:
    """
    为自动任务解析代理来源。

    策略顺序：
    1. 根据 assignment strategy 选择代理列表中的代理
    2. 代理列表不可用或被跳过时，尝试动态代理
    3. 动态代理不可用时，按需回退到全局静态代理
    4. no_proxy 且无任何代理可用时，允许直连
    """
    from ..config.settings import get_settings, normalize_proxy_assignment_strategy

    resolved_settings = settings or get_settings()
    strategy = normalize_proxy_assignment_strategy(
        getattr(resolved_settings, "proxy_assignment_strategy", "round_robin")
    )

    if db is not None and strategy != "no_proxy":
        from ..database import crud

        enabled_proxies = crud.get_enabled_proxies(db)
        if enabled_proxies:
            selected_proxy = crud.select_proxy_for_registration(
                db,
                strategy=strategy,
                exclude_ids=exclude_proxy_ids or set(),
            )
            if selected_proxy and str(selected_proxy.proxy_url or "").strip():
                return AutoProxySelectionResult(
                    proxy_url=str(selected_proxy.proxy_url).strip(),
                    proxy_id=selected_proxy.id,
                    proxy_source="proxy_list",
                    strategy=strategy,
                )
            if strategy not in {"default_only"}:
                return AutoProxySelectionResult(
                    proxy_url=None,
                    proxy_id=None,
                    proxy_source="proxy_list_exhausted",
                    strategy=strategy,
                )

    if dynamic_attempt_count < 2:
        dynamic_proxy = get_dynamic_proxy_url(settings=resolved_settings)
        if dynamic_proxy:
            return AutoProxySelectionResult(
                proxy_url=dynamic_proxy,
                proxy_id=None,
                proxy_source="dynamic",
                strategy=strategy,
            )

    if allow_settings_fallback:
        static_proxy = resolved_settings.proxy_url
        if static_proxy:
            return AutoProxySelectionResult(
                proxy_url=static_proxy,
                proxy_id=None,
                proxy_source="settings",
                strategy=strategy,
            )

    if strategy == "no_proxy" and allow_settings_fallback:
        return AutoProxySelectionResult(
            proxy_url=None,
            proxy_id=None,
            proxy_source="direct",
            strategy=strategy,
        )

    return AutoProxySelectionResult(
        proxy_url=None,
        proxy_id=None,
        proxy_source="none",
        strategy=strategy,
    )


def get_proxy_url_for_task(settings=None) -> Optional[str]:
    """
    获取动态代理 / 全局静态代理兜底结果，不包含代理列表分配。

    Returns:
        代理 URL 或 None
    """
    from ..config.settings import get_settings
    resolved_settings = settings or get_settings()

    # 优先使用动态代理
    if resolved_settings.proxy_dynamic_enabled and resolved_settings.proxy_dynamic_api_url:
        api_key = (
            resolved_settings.proxy_dynamic_api_key.get_secret_value()
            if resolved_settings.proxy_dynamic_api_key else ""
        )
        proxy_url = fetch_dynamic_proxy(
            api_url=resolved_settings.proxy_dynamic_api_url,
            api_key=api_key,
            api_key_header=resolved_settings.proxy_dynamic_api_key_header,
            result_field=resolved_settings.proxy_dynamic_result_field,
        )
        if proxy_url:
            return proxy_url
        logger.warning("动态代理获取失败，回退到静态代理")

    # 使用静态代理
    return resolved_settings.proxy_url


def get_dynamic_proxy_url(settings=None) -> Optional[str]:
    """仅获取动态代理，不回退静态代理。"""
    from ..config.settings import get_settings

    resolved_settings = settings or get_settings()
    if not resolved_settings.proxy_dynamic_enabled or not resolved_settings.proxy_dynamic_api_url:
        return None

    api_key = (
        resolved_settings.proxy_dynamic_api_key.get_secret_value()
        if resolved_settings.proxy_dynamic_api_key else ""
    )
    return fetch_dynamic_proxy(
        api_url=resolved_settings.proxy_dynamic_api_url,
        api_key=api_key,
        api_key_header=resolved_settings.proxy_dynamic_api_key_header,
        result_field=resolved_settings.proxy_dynamic_result_field,
    )
