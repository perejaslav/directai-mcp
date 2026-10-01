"""Live v4 transport: AccountManagement Get only (read-only balance).

Docs: yandex.ru/dev/direct/doc/dg-v4/ru/live/AccountManagement_Get.md.
Auth: OAuth token goes into the request BODY ("token"), not into
the Authorization header (dg-v4/concepts/JSON). Live answers carry
no Units headers.

Financial token is required ONLY for money-moving operations —
plain Get works with a plain OAuth token (verified live 26.09.2026
on client-v). This module hardcodes method=AccountManagement +
Action=Get, so no other operation can be issued through it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from directai_mcp.api.direct import NetStats

LIVE_V4_URL = "https://api.direct.yandex.com/live/v4/json/"

# Docs (AccountSelectionCriteria.Logins): max 50 for agencies.
MAX_LOGINS_PER_CALL = 50


class LiveError(Exception):
    """Live v4 {error_code, error_str} failure (HTTP 200 envelope)."""

    def __init__(self, code: object, message: str, login: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.login = login

    def human_message(self) -> str:
        prefix = f"[{self.login}] " if self.login else ""
        return f"{prefix}Live v4 ошибка {self.code}: {self.args[0]}"


@dataclass
class LiveClient:
    """Minimal Live v4 client. One instance per call; body carries the token."""

    token: str
    timeout: float = 30.0
    stats: NetStats = field(default_factory=NetStats)
    _http: httpx.AsyncClient | None = field(default=None, repr=False)

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _call(self, method: str, param: object = None) -> object:
        """Generic Live v4 call: token in body, locale ru. Returns `data`."""
        body: dict = {
            "method": method,
            "locale": "ru",
            "token": self.token,
        }
        if param is not None:
            body["param"] = param
        http = await self._http_client()
        try:
            self.stats.requests += 1
            resp = await http.post(
                LIVE_V4_URL,
                headers={
                    "Accept-Language": "ru",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=body,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise LiveError(-1, f"network failure ({type(e).__name__})") from e
        try:
            payload = resp.json()
        except ValueError as e:
            raise LiveError(-1, f"HTTP {resp.status_code} (non-JSON)") from e
        if not isinstance(payload, dict):
            raise LiveError(-1, f"HTTP {resp.status_code} (bad envelope)")
        if "error_code" in payload:
            raise LiveError(
                payload.get("error_code"),
                str(payload.get("error_str") or "Live v4 error"),
            )
        if "data" not in payload:
            raise LiveError(-1, "missing `data` in response")
        return payload["data"]

    # v1.9.0: прогноз показов/кликов/затрат для новых фраз (Live v4).
    # Лимиты справки: ≤100 фраз за отчёт, ≤5 отчётов на пользователя,
    # хранение 5 часов, среднее время готовности до ~60 с.
    async def create_new_forecast(
        self,
        phrases: list[str],
        geo: list[int] | None,
        currency: str,
        auction_bids: bool = True,
    ) -> int:
        param: dict = {
            "Phrases": list(phrases),
            "Currency": currency,
            "AuctionBids": "Yes" if auction_bids else "No",
        }
        if geo:
            param["GeoID"] = list(geo)
        data = await self._call("CreateNewForecast", param)
        try:
            return int(data)  # type: ignore[arg-type]
        except (TypeError, ValueError) as e:
            raise LiveError(-1, f"bad forecast id: {data!r}") from e

    async def get_forecast_list(self) -> list[dict]:
        data = await self._call("GetForecastList")
        return list(data) if isinstance(data, list) else []

    async def get_forecast(self, forecast_id: int) -> dict:
        data = await self._call("GetForecast", int(forecast_id))
        if not isinstance(data, dict):
            raise LiveError(-1, "bad forecast envelope")
        return data

    async def delete_forecast_report(self, forecast_id: int) -> bool:
        data = await self._call("DeleteForecastReport", int(forecast_id))
        return bool(data)

    async def account_management_get(self, logins: list[str]) -> dict:
        """Single AccountManagement/Get call for up to 50 logins.

        Returns the `data` object (Accounts + ActionsResult).
        Raises LiveError on {error_code} envelope or HTTP/JSON failure.
        """
        body = {
            "method": "AccountManagement",
            "locale": "ru",
            "token": self.token,
            "param": {
                "Action": "Get",
                "SelectionCriteria": {"Logins": list(logins)},
            },
        }
        http = await self._http_client()
        try:
            self.stats.requests += 1
            resp = await http.post(
                LIVE_V4_URL,
                headers={
                    "Accept-Language": "ru",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=body,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise LiveError(-1, f"network failure ({type(e).__name__})") from e
        try:
            payload = resp.json()
        except ValueError as e:
            raise LiveError(-1, f"HTTP {resp.status_code} (non-JSON)") from e
        if not isinstance(payload, dict):
            raise LiveError(-1, f"HTTP {resp.status_code} (bad envelope)")
        if "error_code" in payload:
            raise LiveError(
                payload.get("error_code"),
                str(payload.get("error_str") or "Live v4 error"),
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LiveError(-1, "missing `data` in response")
        return data
