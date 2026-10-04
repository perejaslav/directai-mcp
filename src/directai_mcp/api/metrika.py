"""Транспорт Management API Яндекс Метрики для записи целей (v1.17.0).

Чтение целей и счётчиков раньше шло сокетным транспортом в
`catalog/metrika_goals.py` (принудительный IPv4 к api-metrika.yandex.net:
TLS поверх IPv6 рвётся 10054, см. DECISIONS). Здесь тот же приём на httpx
(`local_address="0.0.0.0"`, как у Аудиторий и Вебмастера), потому что запись
нуждается в POST/PUT/DELETE с телом и должна тестироваться respx.

Заголовок `Authorization: OAuth <токен>` (НЕ Bearer). Тот же токен, что у
Директа: у него должны быть права `metrika:read` (чтение целей) и
`metrika:write` (запись). Токен не попадает в логи и в тексты ошибок.

Таймаут/обрыв на записи — `MetrikaApiError(unverified=True)`: результат
неизвестен, повторять вслепую нельзя, нужен read-back (список целей).

Docs: https://yandex.com/dev/metrika/ru/management/openapi/goal/addGoal ,
https://yandex.com/dev/metrika/ru/management/openapi/goal/editGoal ,
https://yandex.com/dev/metrika/ru/management/openapi/goal/deleteGoal ,
https://yandex.com/dev/metrika/ru/management/openapi/goal/goals .
"""

from __future__ import annotations

from typing import Any

import httpx

from directai_mcp.api.errors import MetrikaApiError, metrika_hint
from directai_mcp.log import redact

METRIKA_BASE = "https://api-metrika.yandex.net"

#: Метка целевого ресурса в плане/журнале (рядом с ("audience", "segment/...")).
METRIKA = "metrika"


def _client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
    return httpx.AsyncClient(timeout=30.0, transport=transport)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": "OAuth " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _fail(status: int, body: str) -> MetrikaApiError:
    message = f"HTTP {status}"
    hint = metrika_hint(status)
    if hint:
        message += f": {hint}"
    detail = (body or "").strip().replace("\n", " ")
    if detail:
        message += f" (ответ: {detail[:200]})"
    return MetrikaApiError(message, status=status)


async def _call(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    write: bool = False,
) -> Any:
    """Один запрос к Management API. write=True — таймаут = unverified."""
    url = f"{METRIKA_BASE}/{path.lstrip('/')}"
    try:
        async with _client() as client:
            resp = await client.request(
                method,
                url,
                headers=_headers(token),
                params=params,
                json=body,
            )
    except httpx.TimeoutException as exc:
        kind = type(exc).__name__
        tail = (
            " Результат записи неизвестен: не повторяйте вслепую, сначала "
            "перечитайте цели счётчика." if write else ""
        )
        raise MetrikaApiError(
            f"таймаут ({kind}).{tail}", unverified=write
        ) from None
    except httpx.HTTPError as exc:
        kind = type(exc).__name__
        tail = (
            " Соединение оборвалось: результат записи неизвестен — не "
            "повторяйте вслепую, перечитайте цели счётчика." if write else ""
        )
        # Текст сетевой ошибки у httpx иногда содержит URL — вычищаем на всякий
        # случай (v1.17.2: секреты в текстах ошибок тоже недопустимы).
        raise MetrikaApiError(
            f"сеть: {kind}: {redact(str(exc)) or 'нет соединения'}.{tail}",
            unverified=write,
        ) from None
    if resp.status_code >= 300:
        raise _fail(resp.status_code, resp.text)
    if not (resp.content or b"").strip():
        return {}
    try:
        return resp.json()
    except ValueError as exc:
        raise MetrikaApiError(f"bad json: {exc}") from None


async def get(
    token: str, path: str, params: dict[str, Any] | None = None
) -> Any:
    return await _call(token, "GET", path, params=params)


async def post(token: str, path: str, body: dict[str, Any]) -> Any:
    return await _call(token, "POST", path, body=body, write=True)


async def put(token: str, path: str, body: dict[str, Any]) -> Any:
    return await _call(token, "PUT", path, body=body, write=True)


async def delete(token: str, path: str) -> Any:
    return await _call(token, "DELETE", path, write=True)


#: Какое приложение выпустило токен (v1.17.1). Нужен, чтобы check/doctor
#: подтверждали, что Метрике достался именно токен приложения Метрики, а не
#: Директа. Права (scopes) эндпоинт НЕ отдаёт — см. DECISIONS v1.17.1,
#: поэтому «есть ли metrika:write» без записи не проверяется.
#:
#: SECURITY (v1.17.2): токен идёт ТОЛЬКО в заголовке `Authorization: OAuth`.
#: В v1.17.1 он был в query (`?oauth_token=…`), и логгер httpx на уровне INFO
#: писал полный URL в лог — токен утекал в `~/.directai/logs/directai.log`.
#: Тот же эндпоинт принимает заголовок (проверено 04.10.2026: 200 и с
#: `OAuth`, и с `Bearer`), поэтому query-параметр не нужен.
OAUTH_INFO_URL = "https://login.yandex.ru/info?format=json"


async def oauth_app_info(token: str) -> dict:
    """client_id и login приложения, выпустившего токен (без токена в URL).

    IPv4 принудительно — как во всех транспортах репозитория (TLS поверх
    IPv6 на login.yandex.ru рвётся, см. DECISIONS v1.2.4).
    """
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as c:
            resp = await c.get(
                OAUTH_INFO_URL, headers={"Authorization": "OAuth " + token}
            )
    except httpx.HTTPError as exc:
        return {"error": f"{type(exc).__name__}"}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}"}
    try:
        payload = resp.json()
    except ValueError:
        return {"error": "bad json"}
    if not isinstance(payload, dict):
        return {"error": "неожиданный ответ"}
    return {
        "client_id": str(payload.get("client_id") or ""),
        "login": str(payload.get("login") or ""),
    }