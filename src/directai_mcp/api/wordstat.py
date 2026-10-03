"""Small async client for the Yandex Cloud Wordstat REST API v2.

The Wordstat API is a separate Yandex Cloud service.  It uses an AI Studio
API key (``Authorization: Api-Key ...``) and a configured folder ID, so it is
kept separate from the OAuth based Yandex Direct clients.
"""

from __future__ import annotations

from typing import Any

import httpx

WORDSTAT_BASE = "https://searchapi.api.cloud.yandex.net/v2/wordstat"


class WordstatError(Exception):
    """A safe, user-facing Wordstat API error.

    The API key is never included in this exception.  Response text is
    redacted defensively because an upstream diagnostic should not be able to
    echo a credential back into an MCP response.
    """


class WordstatClient:
    """Configured Wordstat client used by catalog actions."""

    def __init__(self, api_key: str, folder_id: str):
        self.api_key = api_key
        self.folder_id = folder_id

    async def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(body or {})
        payload.setdefault("folderId", self.folder_id)
        return await post(self.api_key, path, payload)


def _redact(text: str, secret: str) -> str:
    if not text:
        return ""
    return text.replace(secret, "[REDACTED]")[:500]


async def post(
    api_key: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST one Wordstat method and return its JSON object response."""

    url = f"{WORDSTAT_BASE}/{path.lstrip('/')}"
    headers = {
        "Authorization": "Api-Key " + api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        # As in the Audience client, bind IPv4 to avoid failed TLS connections
        # on Windows hosts whose IPv6 route to Yandex Cloud is unavailable.
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
            response = await client.post(url, headers=headers, json=body or {})
    except httpx.HTTPError as exc:
        raise WordstatError(
            f"сеть: {type(exc).__name__}: "
            f"{_redact(str(exc), api_key) or 'нет соединения'}"
        ) from None

    if response.status_code != 200:
        detail = _redact(response.text, api_key)
        suffix = f": {detail}" if detail else ""
        raise WordstatError(f"HTTP {response.status_code}{suffix}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise WordstatError(f"bad json: {exc}") from None
    if not isinstance(payload, dict):
        raise WordstatError("неожиданный ответ API: ожидался JSON-объект")
    return payload
