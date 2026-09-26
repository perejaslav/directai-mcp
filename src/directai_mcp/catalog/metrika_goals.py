"""Metrika Management + Stat API: счётчики и цели (v1.1.29–v1.1.30).

Типы целей счётчиков кампании (management/v1/counter/{id}/goals):
cdp_order_* -> crm, остальное -> conditional; goals.toml — fallback.
v1.1.30: + инфо счётчика (имя/сайт/статус) и визиты Stat API
(stat/v1/data: lastDirectClickOrder по кампании, весь рекламный трафик).

Транспорт: принудительный IPv4 к api-metrika.yandex.net (TLS поверх IPv6
рвётся 10054 — находка PENDING п.3). Кеши — на процесс (сессию).
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
from urllib.parse import urlencode

from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry

METRIKA_HOST = "api-metrika.yandex.net"

# counter_id -> {goal_id: metrika goal type}. Session = process.
COUNTER_GOALS_CACHE: dict[int, dict[str, str]] = {}
# counter_id -> info. Session = process.
COUNTER_INFO_CACHE: dict[int, dict] = {}
# counter_id -> {goal_id: name}. Session = process.
COUNTER_GOAL_NAMES_CACHE: dict[int, dict[str, str]] = {}


class MetrikaError(Exception):
    """Metrika API read error (fallback handled by caller)."""


def _mget(token: str, path: str, timeout: float = 30.0) -> tuple[str, str]:
    try:
        family_addr = socket.getaddrinfo(
            METRIKA_HOST, 443, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise MetrikaError(f"metrika dns ipv4: {e}") from None
    ipv4 = family_addr[0][4][0]
    try:
        raw = socket.create_connection((ipv4, 443), timeout=timeout)
    except OSError as e:
        raise MetrikaError(f"metrika connect {ipv4}: {e}") from None
    try:
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=METRIKA_HOST)
    except (OSError, ssl.SSLError) as e:
        raw.close()
        raise MetrikaError(f"metrika tls: {e}") from None
    try:
        head = "GET " + path + " HTTP/1.1"
        lines = [head, "Host: " + METRIKA_HOST,
                 "Authorization: OAuth " + token,
                 "Content-Type: application/json", "Connection: close", "", ""]
        tls.sendall(("\r\n".join(lines)).encode())
        chunks = []
        while True:
            data = tls.recv(65536)
            if not data:
                break
            chunks.append(data)
    except OSError as e:
        raise MetrikaError(f"metrika read: {e}") from None
    finally:
        tls.close()
    resp = b"".join(chunks)
    h, _, rbody = resp.partition(b"\r\n\r\n")
    status = h.split(b"\r\n", 1)[0].decode("latin-1")
    return status, rbody.decode("utf-8", "replace")


def _check_status(what: str, status: str) -> None:
    if " 200 " not in " " + status + " " and not status.endswith(" 200"):
        raise MetrikaError(f"{what}: {status}")


def _fetch_counter_goals(token: str, counter_id: int, timeout: float = 30.0) -> dict:
    status, body = _mget(
        token, f"/management/v1/counter/{counter_id}/goals", timeout)
    _check_status(f"metrika goals {counter_id}", status)
    try:
        return json.loads(body)
    except ValueError as e:
        raise MetrikaError(
            f"metrika goals {counter_id}: bad json ({e})") from None


async def counter_goal_types(token: str, counter_id: int) -> dict[str, str]:
    """{goal_id: metrika type} (process cache)."""
    cached = COUNTER_GOALS_CACHE.get(counter_id)
    if cached is not None:
        return cached
    payload = await asyncio.to_thread(_fetch_counter_goals, token, counter_id)
    goals = payload.get("goals", []) if isinstance(payload, dict) else []
    out: dict[str, str] = {}
    names: dict[str, str] = {}
    for g in goals:
        if not isinstance(g, dict) or g.get("id") is None:
            continue
        out[str(g["id"])] = str(g.get("type") or "")
        if g.get("name"):
            names[str(g["id"])] = str(g["name"])
    COUNTER_GOALS_CACHE[counter_id] = out
    COUNTER_GOAL_NAMES_CACHE[counter_id] = names
    return out


async def counter_goal_names(token: str, counter_id: int) -> dict[str, str]:
    """v1.1.30: {goal_id: name} (process cache, same fetch as types)."""
    await counter_goal_types(token, counter_id)
    return dict(COUNTER_GOAL_NAMES_CACHE.get(counter_id, {}))


def metrika_type_to_value(mtype: str) -> str:
    """Metrika goal type -> value type (cdp_order_* is CRM money)."""
    return "crm" if str(mtype or "").startswith("cdp_order") else "conditional"


async def counter_info(token: str, counter_id: int) -> dict:
    """v1.1.30: инфо счётчика {id,name,site,status,code_status,activity}
    (process cache)."""
    cached = COUNTER_INFO_CACHE.get(counter_id)
    if cached is not None:
        return cached

    def _fetch() -> dict:
        status, body = _mget(
            token, f"/management/v1/counter/{counter_id}")
        _check_status(f"metrika counter {counter_id}", status)
        try:
            payload = json.loads(body)
        except ValueError as e:
            raise MetrikaError(
                f"metrika counter {counter_id}: bad json ({e})") from None
        c = payload.get("counter", {}) if isinstance(payload, dict) else {}
        site = c.get("site") or ""
        if not site and isinstance(c.get("site2"), dict):
            site = c["site2"].get("site") or c["site2"].get("domain") or ""
        return {
            "id": counter_id,
            "name": str(c.get("name") or ""),
            "site": str(site or ""),
            "status": str(c.get("status") or ""),
            "code_status": str(c.get("code_status") or ""),
            "activity": str(c.get("activity_status") or ""),
        }

    info = await asyncio.to_thread(_fetch)
    COUNTER_INFO_CACHE[counter_id] = info
    return info


async def stat_visits(
    token: str,
    counter_id: int,
    date_from: str,
    date_to: str,
    direct_order: int | None = None,
) -> dict:
    """v1.1.30: визиты Stat API (process-independent, cheap).

    Возвращает {matched, ad_total, total}: matched — визиты с
    lastDirectClickOrder == direct_order (пусто — счётчик не связан
    с Директом: диагностический сигнал); ad_total — весь рекламный
    трафик; total — все визиты.
    """
    def _q(extra: dict) -> dict:
        params = {"ids": counter_id, "date1": date_from, "date2": date_to,
                  "metrics": "ym:s:visits", "limit": 100}
        params.update(extra)
        status, body = _mget(token, "/stat/v1/data?" + urlencode(params))
        _check_status(f"metrika stat {counter_id}", status)
        try:
            payload = json.loads(body)
        except ValueError as e:
            raise MetrikaError(
                f"metrika stat {counter_id}: bad json ({e})") from None
        totals = payload.get("totals") or [0]
        try:
            return {"visits": int(float(totals[0])),
                    "data": payload.get("data") or []}
        except (TypeError, ValueError):
            return {"visits": 0, "data": payload.get("data") or []}

    async def _run() -> dict:
        matched = 0
        matched_empty = True
        if direct_order is not None:
            r = await asyncio.to_thread(
                _q, {"dimensions": "ym:s:lastDirectClickOrder",
                     "filters": "ym:s:lastDirectClickOrder=='" + str(direct_order) + "'"})
            matched = r["visits"]
            matched_empty = not r["data"]
        ad_total = (await asyncio.to_thread(
            _q, {"filters": "ym:s:trafficSource=='ad'"}))["visits"]
        total = (await asyncio.to_thread(_q, {}))["visits"]
        return {"matched": matched, "matched_empty": matched_empty,
                "ad_total": ad_total, "total": total}

    return await _run()


async def campaign_counters(
    ctx: Ctx, entries: list[AccountEntry], campaign_ids: list[int]
) -> tuple[dict[str, list[int]], list[str]]:
    """CounterIds (Campaigns.get v501, read-only)."""
    from directai_mcp.api.errors import DirectError
    from directai_mcp.catalog.common import chunk as _chunk

    counters: dict[str, list[int]] = {}
    problems: list[str] = []
    for entry in entries:
        client = ctx.direct()
        try:
            found: list[int] = []
            for ids in _chunk(list(campaign_ids), 100):
                try:
                    items = await client.get_all(
                        "campaigns",
                        {
                            "SelectionCriteria": {"Ids": ids},
                            "FieldNames": ["Id"],
                            "TextCampaignFieldNames": ["CounterIds"],
                            "UnifiedCampaignFieldNames": ["CounterIds"],
                        },
                        entry.login,
                        "Campaigns",
                        "v501",
                    )
                except DirectError as e:
                    problems.append(
                        f"⚠ {entry.login}: счётчики кампаний: {e.human_message()}"
                    )
                    break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    for block in ("TextCampaign", "UnifiedCampaign"):
                        sub = item.get(block)
                        if not isinstance(sub, dict):
                            continue
                        cids = sub.get("CounterIds")
                        seq = cids.get("Items") if isinstance(cids, dict) else cids
                        if isinstance(seq, list):
                            found.extend(i for i in seq if isinstance(i, int))
            counters[entry.login] = sorted(set(found))
        finally:
            await client.aclose()
    return counters, problems


async def resolve_value_types(
    ctx: Ctx,
    entries: list[AccountEntry],
    campaign_ids: list[int],
    goal_ids: list[str],
) -> tuple[dict[str, str], str]:
    """Value type per goal + source (metrika / fallback goals.toml)."""
    toml_types = dict(ctx.settings.goal_value_types or {})
    if not goal_ids:
        return {}, "goals.toml"
    if not campaign_ids:
        return (
            {g: toml_types.get(g, "conditional") for g in goal_ids},
            "goals.toml",
        )
    counters, _ = await campaign_counters(ctx, entries, campaign_ids)
    metrika_types: dict[str, str] = {}
    metrika_ok = False
    for login_counters in counters.values():
        for cid in login_counters:
            try:
                for gid, mtype in (
                    await counter_goal_types(ctx.token, cid)
                ).items():
                    metrika_types.setdefault(gid, mtype)
                metrika_ok = True
            except MetrikaError:
                continue
    if not metrika_ok:
        return (
            {g: toml_types.get(g, "conditional") for g in goal_ids},
            "goals.toml",
        )
    out = {}
    used_fallback = False
    for gid in goal_ids:
        mtype = metrika_types.get(gid)
        if mtype is None:
            out[gid] = toml_types.get(gid, "conditional")
            if gid not in toml_types:
                used_fallback = True
        else:
            out[gid] = metrika_type_to_value(mtype)
    source = "metrika+goals.toml" if used_fallback else "metrika"
    return out, source
