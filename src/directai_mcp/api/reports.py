"""Reports API: build, poll, parse TSV (SPEC 7.9).

Docs: yandex.ru/dev/direct/doc/ru/{how-to,mode,codes,spec,headers,
type,period,report-format}.md. retryIn — заголовок ответа 201/202.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from directai_mcp.api.direct import NetStats, UnitsInfo
from directai_mcp.api.errors import DirectError, from_response

log = logging.getLogger(__name__)

REPORTS_PROD_BASE = "https://api.direct.yandex.com/json/v5/reports"
REPORTS_SANDBOX_BASE = "https://api-sandbox.direct.yandex.com/json/v5/reports"

POLL_MIN_WAIT = 2.0
POLL_MAX_WAIT = 60.0
POLL_DEFAULT_WAIT = 10.0
POLL_TIMEOUT_TOTAL = 600.0

MISSING = "--"

PERIODS = frozenset(
    {
        "TODAY",
        "YESTERDAY",
        "LAST_3_DAYS",
        "LAST_5_DAYS",
        "LAST_7_DAYS",
        "LAST_14_DAYS",
        "LAST_30_DAYS",
        "LAST_90_DAYS",
        "LAST_365_DAYS",
        "THIS_WEEK_MON_TODAY",
        "THIS_WEEK_SUN_TODAY",
        "LAST_WEEK",
        "LAST_BUSINESS_WEEK",
        "LAST_WEEK_SUN_SAT",
        "THIS_MONTH",
        "LAST_MONTH",
        "ALL_TIME",
        "CUSTOM_DATE",
        "AUTO",
    }
)


def build_report_name(definition: dict[str, Any]) -> str:
    """Deterministic name: 'dai-' + sha1(canonical JSON w/o ReportName)[:20]."""
    canonical = json.dumps(
        {k: v for k, v in definition.items() if k != "ReportName"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "dai-" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:20]


def parse_tsv(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Parse TSV report; '--' becomes None. Accepts dynamic goal columns."""
    lines = [ln for ln in text.splitlines() if ln != ""]
    if not lines:
        return [], []
    columns = lines[0].split("\t")
    rows: list[dict[str, Any]] = []
    for ln in lines[1:]:
        values = ln.split("\t")
        values += [MISSING] * (len(columns) - len(values))
        rows.append(
            {
                col: (None if val == MISSING else val)
                for col, val in zip(columns, values)
            }
        )
    return columns, rows


def _poll_wait(raw: str | None) -> float:
    try:
        wait = float(raw) if raw is not None else POLL_DEFAULT_WAIT
    except ValueError:
        wait = POLL_DEFAULT_WAIT
    return min(max(wait, POLL_MIN_WAIT), POLL_MAX_WAIT)


@dataclass
class ReportsClient:
    """Reports downloader (processingMode auto, TSV, micros off)."""

    token: str
    sandbox: bool = False
    timeout: float = 60.0
    last_units: dict[str, UnitsInfo] = field(default_factory=dict)
    stats: NetStats = field(default_factory=NetStats)
    sleep: Callable[[float], Any] = asyncio.sleep
    _http: httpx.AsyncClient | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return REPORTS_SANDBOX_BASE if self.sandbox else REPORTS_PROD_BASE

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _store_units(self, header_value: str | None, login: str) -> None:
        from directai_mcp.api.direct import _touch_seen

        if not header_value or not login:
            return
        try:
            used_s, rest_s, limit_s = header_value.split("/")
            self.last_units[login] = UnitsInfo(
                used=int(used_s), rest=int(rest_s), limit=int(limit_s)
            )
            self.stats.units_used += self.last_units[login].used
            info = self.last_units[login]
            self.stats.rests[login] = (info.rest, info.limit)
            _touch_seen(login, self.last_units[login])
        except ValueError:
            log.warning("bad Units header %r for login %s", header_value, login)

    async def fetch(
        self,
        client_login: str,
        definition: dict[str, Any],
        timeout_total: float = POLL_TIMEOUT_TOTAL,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """POST report definition, poll 201/202 via retryIn, return (cols, rows)."""
        definition = dict(definition)
        definition["ReportName"] = build_report_name(definition)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Client-Login": client_login,
            "Accept-Language": "ru",
            "processingMode": "auto",
            "returnMoneyInMicros": "false",
            "skipReportHeader": "true",
            "skipReportSummary": "true",
        }
        http = await self._http_client()
        deadline = time.monotonic() + timeout_total
        while True:
            self.stats.requests += 1
            resp = await http.post(
                self.url, headers=headers, json={"params": definition}
            )
            self._store_units(resp.headers.get("Units"), client_login)
            if resp.status_code == 200:
                text = resp.text
                if text.lstrip().startswith("{"):
                    try:
                        payload = resp.json()
                    except ValueError:
                        payload = {}
                    if isinstance(payload, dict) and "error" in payload:
                        raise from_response(payload, login=client_login)
                    raise DirectError(
                        -1, "unexpected JSON in 200 report response", login=client_login
                    )
                return parse_tsv(text)
            if resp.status_code in (201, 202):
                wait = _poll_wait(resp.headers.get("retryIn"))
                if time.monotonic() + wait > deadline:
                    raise DirectError(
                        -1, "report wait timeout (10 min)", login=client_login
                    )
                self.stats.polls += 1
                self.stats.wait_sec += wait
                await self.sleep(wait)
                continue
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            if isinstance(payload, dict) and "error" in payload:
                raise from_response(payload, login=client_login)
            raise DirectError(-1, f"report HTTP {resp.status_code}", login=client_login)
