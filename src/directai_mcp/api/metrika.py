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
        raise MetrikaApiError(
            f"сеть: {kind}: {exc or 'нет соединения'}.{tail}", unverified=write
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