"""Read-транспорт API Яндекс Аудиторий (этап 1, только GET).

База https://api-audience.yandex.ru/v1/management (RU-доки; EN-примеры дают
зеркало https://api-audience.yandex.com). Заголовок `Authorization: OAuth
<токен>` (НЕ Bearer). Транспорт принудительно IPv4 — как у Вебмастера
(см. DECISIONS.md v1.2.4 про AAAA/TLS).

Только чтение: список сегментов и один сегмент (фильтром по списку —
отдельного GET одного сегмента в API нет). Ошибки — AudienceError из
api/errors.py; токен не попадает в логи и тексты ошибок.

Docs: https://yandex.ru/dev/audience/ru/ (введение, база),
https://yandex.com/dev/audience/en/intro/authorization (OAuth-заголовок),
https://yandex.ru/dev/audience/en/management/formats (GET segments).
"""

from __future__ import annotations

from typing import Any

import httpx

from directai_mcp.api.errors import AudienceError, audience_hint

AUD_BASE = "https://api-audience.yandex.ru/v1/management"


async def _get(token: str, path: str) -> Any:
    """GET https://api-audience.yandex.ru/v1/management/<path> с OAuth-токеном."""
    url = f"{AUD_BASE}/{path.lstrip('/')}"
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
    try:
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": "OAuth " + token,
                    "Accept": "application/json",
                },
            )
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
