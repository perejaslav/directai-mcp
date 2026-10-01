"""Транспорт API Яндекс Аудиторий (этапы 1–2: чтение + запись сегментов).

База https://api-audience.yandex.ru/v1/management (RU-доки; EN-примеры дают
зеркало https://api-audience.yandex.com). Заголовок `Authorization: OAuth
<токен>` (НЕ Bearer). Транспорт принудительно IPv4 — как у Вебмастера
(см. DECISIONS.md v1.2.4 про AAAA/TLS).

Чтение: GET segments (список; отдельного GET одного сегмента в API нет —
фильтруем список). Запись этапа 2: multipart POST segments/upload_csv_file,
POST segment/{id}/confirm, DELETE segment/{id}.

Ошибки — AudienceError из api/errors.py; токен не попадает в логи и тексты
ошибок. Тела ответов POST в ошибки не включаем: в них уходит наш payload
(SHA256-хеши), пишем только статус и подсказку. У GET/DELETE тел в запросе
нет — там ответ усечённо прикладываем.

Docs: https://yandex.ru/dev/audience/ru/ (введение, база),
https://yandex.com/dev/audience/en/intro/authorization (OAuth-заголовок),
https://yandex.ru/dev/audience/en/management/formats (GET segments),
https://yandex.ru/dev/audience/ru/intro/data-requirements (форматы данных),
https://yandex.ru/dev/audience/ru/ref/openapi/segments/uploadCsvFile ,
https://yandex.ru/dev/audience/ru/ref/openapi/segments/confirm ,
https://yandex.ru/dev/audience/ru/ref/openapi/segments/delete .
"""

from __future__ import annotations

from typing import Any

import httpx

from directai_mcp.api.errors import AudienceError, audience_hint

AUD_BASE = "https://api-audience.yandex.ru/v1/management"


def _client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
    return httpx.AsyncClient(timeout=30.0, transport=transport)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": "OAuth " + token,
        "Accept": "application/json",
    }


async def _get(
    token: str, path: str, params: dict[str, Any] | None = None
) -> Any:
    """GET https://api-audience.yandex.ru/v1/management/<path> с OAuth-токеном."""
    url = f"{AUD_BASE}/{path.lstrip('/')}"
    try:
        async with _client() as client:
            resp = await client.get(url, headers=_headers(token), params=params)
    except httpx.HTTPError as exc:
        raise AudienceError(
            f"сеть: {type(exc).__name__}: {exc or 'нет соединения'}"
        ) from None
    if resp.status_code != 200:
        raise AudienceError(
            f"HTTP {resp.status_code}{_suffix(resp.status_code)}: "
            f"{resp.text[:200]}"
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise AudienceError(f"bad json: {exc}") from None


async def _post_file(
    token: str, path: str, filename: str, content: bytes
) -> Any:
    """Multipart-загрузка файла (upload_csv_file). Тело ответа в ошибки не пишем."""
    url = f"{AUD_BASE}/{path.lstrip('/')}"
    try:
        async with _client() as client:
            resp = await client.post(
                url,
                headers={"Authorization": "OAuth " + token},
                files={
                    "file": (filename, content, "application/octet-stream")
                },
            )
    except httpx.HTTPError as exc:
        raise AudienceError(
            f"сеть: {type(exc).__name__}: {exc or 'нет соединения'}"
        ) from None
    if resp.status_code != 200:
        raise AudienceError(f"HTTP {resp.status_code}{_suffix(resp.status_code)}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AudienceError(f"bad json: {exc}") from None


async def _post_json(token: str, path: str, body: dict[str, Any]) -> Any:
    """POST JSON (confirm). Тело ответа в ошибки не пишем (рядом наш payload)."""
    url = f"{AUD_BASE}/{path.lstrip('/')}"
    try:
        async with _client() as client:
            resp = await client.post(
                url, headers=_headers(token), json=body
            )
    except httpx.HTTPError as exc:
        raise AudienceError(
            f"сеть: {type(exc).__name__}: {exc or 'нет соединения'}"
        ) from None
    if resp.status_code != 200:
        raise AudienceError(f"HTTP {resp.status_code}{_suffix(resp.status_code)}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AudienceError(f"bad json: {exc}") from None


async def _delete(token: str, path: str) -> Any:
    """DELETE segment/{id}: в запросе только id, тело ответа безопасно."""
    url = f"{AUD_BASE}/{path.lstrip('/')}"
    try:
        async with _client() as client:
            resp = await client.delete(url, headers=_headers(token))
    except httpx.HTTPError as exc:
        raise AudienceError(
            f"сеть: {type(exc).__name__}: {exc or 'нет соединения'}"
        ) from None
    if resp.status_code != 200:
        raise AudienceError(
            f"HTTP {resp.status_code}{_suffix(resp.status_code)}: "
            f"{resp.text[:200]}"
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise AudienceError(f"bad json: {exc}") from None


def _suffix(status: int) -> str:
    hint = audience_hint(status)
    return f" ({hint})" if hint else ""
