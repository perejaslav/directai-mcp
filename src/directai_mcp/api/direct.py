"""Direct API transport: call, get_all, Units, retries (SPEC 7.1-7.7)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from directai_mcp.api.errors import (
    READ_METHODS,
    RETRY_ANY_CODES,
    RETRY_GET_ONLY_CODES,
    RETRY_PAUSES,
    DirectError,
    DirectUnverifiedError,
    from_response,
)

PROD_HOST = "https://api.direct.yandex.com"
SANDBOX_HOST = "https://api-sandbox.direct.yandex.com"

PAGE_LIMIT = 10_000

log = logging.getLogger(__name__)


@dataclass
class UnitsInfo:
    used: int
    rest: int
    limit: int


@dataclass
class NetStats:
    """Шаг 1.1-5: учёт запросов/повторов/Units на action."""

    requests: int = 0
    direct_requests: int = 0
    retries_code: int = 0
    retries_net: int = 0
    units_used: int = 0
    polls: int = 0
    wait_sec: float = 0.0
    rests: dict[str, tuple[int, int]] = field(default_factory=dict)

    def merge(self, other: NetStats) -> None:
        self.requests += other.requests
        self.direct_requests += other.direct_requests
        self.retries_code += other.retries_code
        self.retries_net += other.retries_net
        self.units_used += other.units_used
        self.polls += other.polls
        self.wait_sec += other.wait_sec
        self.rests.update(other.rests)


# Шаг 1.1-4 (Q4): последний известный остаток + время (для list_accounts).
# Обновляется из обоих транспортов (Direct + Reports).
LAST_SEEN_UNITS: dict[str, tuple[UnitsInfo, str]] = {}


def _touch_seen(login: str, info: UnitsInfo) -> None:
    from datetime import datetime

    LAST_SEEN_UNITS[login] = (
        info,
        datetime.now().astimezone().strftime("%d.%m %H:%M"),
    )


@dataclass
class DirectClient:
    """Async JSON v5 client. One instance per token; safe for 3 concurrent calls."""

    token: str
    sandbox: bool = False
    timeout: float = 30.0
    max_concurrent: int = 3
    last_units: dict[str, UnitsInfo] = field(default_factory=dict)
    stats: NetStats = field(default_factory=NetStats)
    _sem: asyncio.Semaphore | None = field(default=None, repr=False)
    _http: httpx.AsyncClient | None = field(default=None, repr=False)

    @property
    def base_url(self) -> str:
        host = SANDBOX_HOST if self.sandbox else PROD_HOST
        return f"{host}/json/v5"

    def _url(self, service: str, version: str = "v5") -> str:
        host = SANDBOX_HOST if self.sandbox else PROD_HOST
        return f"{host}/json/{version}/{service}"

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_concurrent)
        return self._sem

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _store_units(self, header_value: str | None, login: str) -> None:
        if not header_value or not login:
            return
        try:
            used_s, rest_s, limit_s = header_value.split("/")
            info = UnitsInfo(
                used=int(used_s), rest=int(rest_s), limit=int(limit_s)
            )
            self.last_units[login] = info
            self.stats.units_used += info.used
            self.stats.rests[login] = (info.rest, info.limit)
            _touch_seen(login, info)
        except ValueError:
            log.warning("bad Units header %r for login %s", header_value, login)

    async def call(
        self,
        service: str,
        method: str,
        params: dict,
        client_login: str | None,
        version: str = "v5",
    ) -> dict:
        """POST /{service} {method}. Returns the `result` object.

        client_login=None omits Client-Login (token-level services like
        Dictionaries). version selects json/v5 vs json/v501 (SPEC 7.1).
        Raises DirectError; ambiguous write failures raise
        DirectUnverifiedError (no auto-retry of add/update/...).
        """
        url = self._url(service, version)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }
        if client_login:
            headers["Client-Login"] = client_login
        login = client_login or ""
        body = {"method": method, "params": params}
        is_read = method in READ_METHODS

        async with self._semaphore():
            http = await self._http_client()
            for attempt in range(len(RETRY_PAUSES) + 1):
                try:
                    self.stats.requests += 1
                    self.stats.direct_requests += 1
                    resp = await http.post(url, headers=headers, json=body)
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    if is_read and attempt < len(RETRY_PAUSES):
                        self.stats.retries_net += 1
                        await asyncio.sleep(RETRY_PAUSES[attempt])
                        continue
                    raise DirectUnverifiedError(
                        -1, f"network failure ({type(e).__name__})", login=login
                    ) from e

                self._store_units(resp.headers.get("Units"), login)

                payload: dict = {}
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {}

                if "error" in payload:
                    err = from_response(payload, login=login)
                    action = self._retry_action(err.code, is_read)
                    if action == "retry" and attempt < len(RETRY_PAUSES):
                        self.stats.retries_code += 1
                        await asyncio.sleep(RETRY_PAUSES[attempt])
                        continue
                    if action == "unverified":
                        raise DirectUnverifiedError(
                            err.code, err.message, err.detail, err.request_id, err.login
                        ) from None
                    raise err

                if resp.status_code != 200:
                    raise DirectError(-1, f"HTTP {resp.status_code}", login=login)
                if not isinstance(payload.get("result"), dict):
                    raise DirectError(-1, "missing `result` in response", login=login)
                return payload["result"]

            raise DirectError(-1, "retry exhausted", login=login)

    @staticmethod
    def _retry_action(code: int, is_read: bool) -> str:
        """Return 'retry', 'unverified' or 'fail'."""
        if code in RETRY_ANY_CODES:
            return "retry"
        if code in RETRY_GET_ONLY_CODES:
            return "retry" if is_read else "unverified"
        return "fail"

    async def get_all(
        self,
        service: str,
        params: dict,
        client_login: str | None,
        items_key: str,
        version: str = "v5",
    ) -> list[dict]:
        """Paginate get via Page/LimitedBy (SPEC 7.6)."""
        items: list[dict] = []
        offset = 0
        while True:
            page_params = dict(params)
            page_params["Page"] = {"Limit": PAGE_LIMIT, "Offset": offset}
            result = await self.call(service, "get", page_params, client_login, version)
            batch = result.get(items_key, [])
            if isinstance(batch, list):
                items.extend(batch)
            limited_by = result.get("LimitedBy")
            if limited_by is None:
                return items
            offset = int(limited_by)
