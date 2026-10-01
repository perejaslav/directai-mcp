"""Read actions for Yandex.Webmaster API v4 (hosts, summary, generic read).

Чтение Яндекс.Вебмастера (API v4) в составе directai-mcp.

Токену нужно право `webmaster:hostinfo` (и `webmaster:verify` — только для
POST-подтверждения прав). Токен Яндекс.OAuth живёт 6 месяцев.
Docs: https://yandex.ru/dev/webmaster/doc/ru/

Токен берётся так: отдельный (`directai-mcp set-token --webmaster`, ключ
`directai-mcp-webmaster`, env `DIRECTAI_WEBMASTER_TOKEN`) → иначе основной.
Отдельный нужен потому, что приложения Яндекс.OAuth «для авторизации
пользователей» дают не более 3 групп разрешений, и Вебмастер в них не влезает;
приложения «для доступа к API» лимита не имеют.

Только чтение: GET-ресурсы. Запись в Вебмастере (добавление/удаление сайта,
переобход, sitemap) сознательно не реализована — если понадобится, её нужно
заводить через registry.write_action(...) с планом и guard, как у Директа.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from directai_mcp.catalog.common import finalize
from directai_mcp.catalog.registry import Ctx, action

WM_BASE = "https://api.webmaster.yandex.net/v4"


class WebmasterError(Exception):
    """Webmaster API read error."""


def _hint(status: int) -> str:
    if status == 401:
        return " (токен не принят — перевыпустите: directai-mcp set-token)"
    if status == 403:
        return " (у приложения/токена нет права webmaster:hostinfo)"
    if status == 404:
        return " (сайта нет в списке пользователя либо права не подтверждены)"
    return ""


async def _get(
    token: str, path: str, params: dict[str, Any] | None = None
) -> Any:
    """GET https://api.webmaster.yandex.net/v4/<path> с OAuth-токеном.

    Транспорт принудительно IPv4: у api.webmaster.yandex.net в DNS первым идёт
    AAAA-адрес, а TLS поверх IPv6 на этой машине рвётся (curl без -4 даёт 000,
    с -4 — нормальный ответ). Та же находка, что в metrika_goals.py.
    """
    url = f"{WM_BASE}/{path.lstrip('/')}"
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
    try:
        async with httpx.AsyncClient(
            timeout=30.0, transport=transport
        ) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": "OAuth " + token,
                    "Accept": "application/json",
                },
                params=params,
            )
    except httpx.HTTPError as exc:
        raise WebmasterError(
            f"сеть: {type(exc).__name__}: {exc or 'нет соединения'}"
        ) from None
    if resp.status_code != 200:
        raise WebmasterError(
            f"HTTP {resp.status_code}{_hint(resp.status_code)}: "
            f"{resp.text[:200]}"
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise WebmasterError(f"bad json: {exc}") from None


def _token(ctx: Ctx) -> str:
    """Токен Вебмастера: отдельный (своё приложение) или основной.

    У приложений Яндекс.OAuth «для авторизации пользователей» лимит 3 группы
    разрешений, поэтому Вебмастер обычно выносят в отдельное приложение
    «для доступа к API» и хранят его токен под своим ключом
    (`directai-mcp set-token --webmaster`). Если отдельного токена нет —
    используется основной, как раньше.
    """
    from directai_mcp.config import get_webmaster_token

    return get_webmaster_token(ctx.settings.auth_login) or ctx.token


async def _user_id(ctx: Ctx) -> int:
    """user_id владельца токена (нужен для всех ресурсов /v4/user/{user-id}/)."""
    payload = await _get(_token(ctx), "user")
    uid = payload.get("user_id") if isinstance(payload, dict) else None
    if uid is None:
        raise WebmasterError("нет user_id в ответе GET /v4/user")
    return int(uid)


class _WmParams(BaseModel):
    """Общие параметры вывода (account тут не применим — Вебмастер не кабинет)."""

    limit: int | None = None
    save_as: Literal["csv", "md"] | None = Field(
        default=None,
        description="Устарел, используйте output/format: save_as=X ≡ output=file, format=X.",
    )
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"


class WebmasterHostsParams(_WmParams):
    pass


@action(
    "webmaster_hosts",
    "read",
    "Вебмастер: список сайтов пользователя и статус подтверждения прав",
    (
        "вебмастер",
        "webmaster",
        "сайты",
        "hosts",
        "подтверждение",
        "зеркало",
        "seo",
        "индексация",
    ),
    WebmasterHostsParams,
)
async def _hosts(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, WebmasterHostsParams)
    try:
        uid = await _user_id(ctx)
        payload = await _get(_token(ctx), f"user/{uid}/hosts")
    except WebmasterError as exc:
        return f"Ошибка Вебмастера: {exc}"
    hosts = payload.get("hosts") or [] if isinstance(payload, dict) else []
    rows: list[dict] = []
    for host in hosts:
        if not isinstance(host, dict):
            continue
        mirror = host.get("main_mirror") or {}
        rows.append(
            {
                "Сайт": host.get("unicode_host_url")
                or host.get("ascii_host_url")
                or "—",
                "Host ID": host.get("host_id") or "—",
                "Подтверждён": "да" if host.get("verified") else "нет",
                "Главное зеркало": mirror.get("unicode_host_url") or "—",
            }
        )
    return finalize(
        ctx,
        f"webmaster_hosts: сайтов {len(rows)}, user_id {uid}.",
        "webmaster_hosts",
        ["Сайт", "Host ID", "Подтверждён", "Главное зеркало"],
        rows,
        params.limit,
        params.save_as,
        [],
        money_cols=(),
        output=params.output,
        format=params.format,
    )


class WebmasterSummaryParams(_WmParams):
    host_id: str = Field(
        min_length=1,
        description=(
            "Host ID из webmaster_hosts, например https:example.ru:443 "
            "(без завершающего слэша)"
        ),
    )


@action(
    "webmaster_summary",
    "read",
    "Вебмастер: ИКС, страницы в поиске/исключённые, проблемы сайта",
    (
        "вебмастер",
        "webmaster",
        "икс",
        "sqi",
        "страницы в поиске",
        "исключено",
        "проблемы",
        "сводка",
    ),
    WebmasterSummaryParams,
)
async def _summary(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, WebmasterSummaryParams)
    try:
        uid = await _user_id(ctx)
        payload = await _get(
            _token(ctx), f"user/{uid}/hosts/{params.host_id}/summary"
        )
    except WebmasterError as exc:
        return f"Ошибка Вебмастера: {exc}"
    data = payload if isinstance(payload, dict) else {}
    problems = data.get("site_problems") or {}
    row = {
        "Сайт": params.host_id,
        "ИКС": data.get("sqi"),
        "Страниц в поиске": data.get("searchable_pages_count"),
        "Исключено": data.get("excluded_pages_count"),
        "FATAL": problems.get("FATAL"),
        "CRITICAL": problems.get("CRITICAL"),
        "POSSIBLE_PROBLEM": problems.get("POSSIBLE_PROBLEM"),
        "RECOMMENDATION": problems.get("RECOMMENDATION"),
    }
    return finalize(
        ctx,
        f"webmaster_summary: {params.host_id}, user_id {uid}.",
        "webmaster_summary",
        list(row),
        [row],
        params.limit,
        params.save_as,
        [],
        money_cols=(),
        output=params.output,
        format=params.format,
    )


class WebmasterQueryParams(_WmParams):
    resource: str = Field(
        min_length=1,
        description=(
            "Ресурс API v4 относительно /user/{user-id}/hosts/{host-id}/: "
            "diagnostics, indexing/history, search-queries/popular, "
            "query-analytics/list, links/internal/broken/samples, "
            "search-urls/in-search/history, sqi-history и т.п. "
            "Только GET-ресурсы. У search-queries/popular обязателен "
            "query={'order_by': 'TOTAL_SHOWS'|'TOTAL_CLICKS'}, "
            "полезно добавить query_indicator и limit."
        ),
    )
    host_id: str | None = Field(
        default=None, description="Host ID; для ресурсов без host-id не нужен"
    )
    query: dict[str, str] | None = Field(
        default=None, description="Query-параметры, например {'limit': '100'}"
    )


@action(
    "webmaster_query",
    "read",
    "Вебмастер: произвольный read-ресурс API v4 (диагностика, индексация, запросы)",
    (
        "вебмастер",
        "webmaster",
        "диагностика",
        "индексация",
        "поисковые запросы",
        "ссылки",
        "sitemap",
        "переобход",
        "любой ресурс",
    ),
    WebmasterQueryParams,
)
async def _query(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, WebmasterQueryParams)
    try:
        uid = await _user_id(ctx)
        if params.host_id:
            path = f"user/{uid}/hosts/{params.host_id}/{params.resource.strip('/')}"
        else:
            path = f"user/{uid}/{params.resource.strip('/')}"
        payload = await _get(_token(ctx), path, params.query)
    except WebmasterError as exc:
        return f"Ошибка Вебмастера: {exc}"
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    limit = params.limit or 4000
    shown = body[:limit]
    cut = "\n… (обрезано, увеличьте limit)" if len(body) > limit else ""
    return f"webmaster_query: /v4/{path}\n\n{shown}{cut}"
