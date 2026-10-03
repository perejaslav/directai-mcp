"""Metrika Reporting API: отчёты по трафику/целям/динамике (v1.12.0, Часть 1).

Только ЧТЕНИЕ. Основа: catalog/metrika_goals.py (_mget, campaign_counters,
counter_goal_names) — HTTP-слой не дублируется.

API: GET /stat/v1/data (+ /stat/v1/data/bytime для динамики).
Параметры: ids, date1/date2, dimensions, metrics, attribution,
accuracy=full, filters, sort, limit, lang=ru.
Токен — тот же (metrika:read).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from urllib.parse import urlencode

from pydantic import BaseModel, Field, field_validator

from directai_mcp.catalog.common import GetActionParams, finalize
from directai_mcp.catalog.metrika_goals import (
    MetrikaError,
    _mget,
    campaign_counters,
    counter_goal_names,
    counter_goal_types,
)
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import ConfigError, resolve_primary_goal

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# v1.14.1: потолок строк stat/v1/data (все страницы); сверх — truncated.
STAT_MAX_ROWS = 100_000

GROUP_VALUES = ("source", "utm", "utm_full", "direct", "landing", "device", "region")

BASE_METRICS = (
    "ym:s:visits",
    "ym:s:users",
    "ym:s:bounceRate",
    "ym:s:pageDepth",
    "ym:s:avgVisitDurationSeconds",
)

# group_by -> человеческие имена колонок группировки.
GROUP_COLUMNS: dict[str, tuple[str, ...]] = {
    "source": ("Source",),
    "utm": ("UTMSource", "UTMMedium", "UTMCampaign"),
    "utm_full": ("UTMSource", "UTMMedium", "UTMCampaign", "UTMContent", "UTMTerm"),
    "direct": ("DirectOrder",),
    "landing": ("Landing",),
    "device": ("Device",),
    "region": ("Region",),
}


def group_dimensions(group_by: str, attribution: str) -> list[str]:
    """Группировка -> dimensions Reporting API."""
    attr = (attribution or "lastsign").strip() or "lastsign"
    if group_by == "source":
        return [f"ym:s:{attr}TrafficSource"]
    if group_by == "utm":
        return ["ym:s:UTMSource", "ym:s:UTMMedium", "ym:s:UTMCampaign"]
    if group_by == "utm_full":
        return [
            "ym:s:UTMSource",
            "ym:s:UTMMedium",
            "ym:s:UTMCampaign",
            "ym:s:UTMContent",
            "ym:s:UTMTerm",
        ]
    if group_by == "direct":
        return ["ym:s:lastDirectClickOrder"]
    if group_by == "landing":
        return ["ym:s:startURL"]
    if group_by == "device":
        return ["ym:s:deviceCategory"]
    if group_by == "region":
        return ["ym:s:regionCityName"]
    raise ValueError(f"unknown group_by '{group_by}'")


def metrics_for(goal_ids: list[str]) -> list[str]:
    out = list(BASE_METRICS)
    for gid in goal_ids:
        out.append(f"ym:s:goal{gid}reaches")
    return out


def build_filters(
    filter_utm_source: str, filter_utm_campaign: str
) -> str | None:
    parts: list[str] = []

    def _q(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    if filter_utm_source:
        parts.append(f"ym:s:UTMSource=='{_q(filter_utm_source)}'")
    if filter_utm_campaign:
        parts.append(f"ym:s:UTMCampaign=='{_q(filter_utm_campaign)}'")
    return " AND ".join(parts) if parts else None


def default_period() -> tuple[str, str]:
    """Последние 14 полных дней (вчера −13 .. вчера)."""
    today = datetime.datetime.now().astimezone().date()
    to = today - datetime.timedelta(days=1)
    start = to - datetime.timedelta(days=13)
    return start.isoformat(), to.isoformat()


def resolve_dates(date_from: str, date_to: str) -> tuple[str, str] | str:
    if not date_from and not date_to:
        return default_period()
    if not date_from or not date_to:
        return "Ошибка: укажите date_from и date_to вместе (YYYY-MM-DD)."
    if not DATE_RE.fullmatch(date_from) or not DATE_RE.fullmatch(date_to):
        return "Ошибка: даты должны быть YYYY-MM-DD."
    if date_from > date_to:
        return "Ошибка: date_from позже date_to."
    return date_from, date_to


def truncation_note(payload: dict) -> str | None:
    """Текст о неполноте, если stat_table упёрся в STAT_MAX_ROWS."""
    if payload.get("truncated"):
        return (f"Отчёт неполный: строк больше {STAT_MAX_ROWS}, "
                "показаны первые — сузьте период или фильтр.")
    return None


def human_metrika_error(counter_id: int, err: Exception) -> str:
    text = str(err)
    if "403" in text:
        return (
            f"Нет доступа к счётчику {counter_id} (403): "
            "проверьте права токена (metrika:read) и доступ к счётчику."
        )
    if "429" in text:
        return f"Метрика: превышен лимит запросов (429), счётчик {counter_id}."
    if " 400" in " " + text or ": 400" in text or " 400 " in text:
        return f"Метрика: неверный запрос (400), счётчик {counter_id}: {text}."
    return f"Метрика, счётчик {counter_id}: {text}."


def _stat_fetch(
    token: str, path: str, counter_id: int, what: str
) -> dict:
    """Синхронный GET с одним повтором при 429. Возвращает payload dict."""
    import time as _time

    for attempt in (1, 2):
        status, body = _mget(token, path)
        if " 429" in " " + status + " " or status.rstrip().endswith(" 429"):
            if attempt == 1:
                _time.sleep(2)
                continue
            raise MetrikaError(f"{what}: {status} (повтор не помог)")
        if " 403" in " " + status + " " or status.rstrip().endswith(" 403"):
            raise MetrikaError(f"нет доступа к счётчику {counter_id} (403): {status}")
        if " 200 " not in " " + status + " " and not status.rstrip().endswith(
            " 200"
        ):
            raise MetrikaError(f"{what}: {status}")
        try:
            payload = json.loads(body)
        except ValueError as e:
            raise MetrikaError(f"{what}: bad json ({e})") from None
        if not isinstance(payload, dict):
            raise MetrikaError(f"{what}: неожиданный ответ API")
        return payload
    raise MetrikaError(f"{what}: 429 (повтор не помог)")


async def stat_table(
    token: str,
    counter_id: int,
    date_from: str,
    date_to: str,
    dimensions: list[str],
    metrics: list[str],
    attribution: str,
    filters: str | None,
    limit: int = 1000,
    max_rows: int = STAT_MAX_ROWS,
) -> dict:
    """stat/v1/data со всеми страницами (v1.14.1: offset до total_rows).

    `limit` — размер страницы. Сверх `max_rows` не тянем: тогда
    `truncated=True` в ответе — вызывающий обязан показать неполноту.
    """
    async def _page(ms: list[str], offset: int) -> dict:
        params: dict[str, object] = {
            "ids": counter_id,
            "date1": date_from,
            "date2": date_to,
            "metrics": ",".join(ms),
            "accuracy": "full",
            "limit": limit,
            "offset": offset,
            "lang": "ru",
            "sort": f"-{ms[0]}" if ms else "-ym:s:visits",
        }
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        if attribution:
            params["attribution"] = attribution
        if filters:
            params["filters"] = filters
        path = "/stat/v1/data?" + urlencode({k: str(v) for k, v in params.items()})
        return await asyncio.to_thread(
            _stat_fetch, token, path, counter_id, f"metrika stat {counter_id}"
        )

    async def _one(ms: list[str]) -> dict:
        first = await _page(ms, 1)
        data = list(first.get("data") or [])
        total = first.get("total_rows")
        total = int(total) if isinstance(total, (int, float)) else len(data)
        while dimensions and len(data) < min(total, max_rows):
            page = await _page(ms, len(data) + 1)
            chunk = page.get("data") or []
            if not chunk:
                break
            data.extend(chunk)
        out = dict(first)
        out["data"] = data[:max_rows]
        out["truncated"] = total > len(out["data"])
        return out

    if len(metrics) <= 20:
        return await _one(metrics)
    # Лимит API — 20 метрик на запрос: базу тянем один раз, цели — чанками.
    base = [m for m in metrics if not m.startswith("ym:s:goal")]
    goal_ms = [m for m in metrics if m.startswith("ym:s:goal")]
    first = await _one(base + goal_ms[: max(1, 20 - len(base))])
    merged_data: dict[tuple, dict] = {}
    order: list[tuple] = []

    def _key(item: dict) -> tuple:
        return tuple(
            d.get("name") if isinstance(d, dict) else str(d)
            for d in (item.get("dimensions") or [])
        )

    def _fold(payload: dict, ms: list[str]) -> None:
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            key = _key(item)
            slot = merged_data.get(key)
            if slot is None:
                slot = {"dims": item.get("dimensions") or [], "vals": {}}
                merged_data[key] = slot
                order.append(key)
            vals = item.get("metrics") or []
            for i, m in enumerate(ms):
                if i < len(vals):
                    slot["vals"][m] = vals[i]

    _fold(first, base + goal_ms[: max(1, 20 - len(base))])
    size = max(1, 20 - len(base)) if base else 20
    for i in range(size, len(goal_ms), size):
        chunk = goal_ms[i : i + size]
        payload = await _one((base[:1] if base else []) + chunk)
        _fold(payload, (base[:1] if base else []) + chunk)
    data = []
    for key in order:
        slot = merged_data[key]
        data.append({
            "dimensions": slot["dims"],
            "metrics": [slot["vals"].get(m) for m in metrics],
        })
    out = dict(first)
    out["data"] = data
    out["total_rows"] = len(data)
    out["truncated"] = bool(first.get("truncated"))
    return out


async def stat_bytime(
    token: str,
    counter_id: int,
    date_from: str,
    date_to: str,
    group: str,
    metrics: list[str],
    attribution: str,
    filters: str | None,
) -> dict:
    params: dict[str, object] = {
        "ids": counter_id,
        "date1": date_from,
        "date2": date_to,
        "group": group,
        "metrics": ",".join(metrics),
        "accuracy": "full",
        "lang": "ru",
    }
    if attribution:
        params["attribution"] = attribution
    if filters:
        params["filters"] = filters
    path = "/stat/v1/data/bytime?" + urlencode(
        {k: str(v) for k, v in params.items()}
    )
    return await asyncio.to_thread(
        _stat_fetch, token, path, counter_id, f"metrika bytime {counter_id}"
    )


def _dim_text(dim: object) -> str:
    if isinstance(dim, dict):
        name = dim.get("name")
        if name is not None and str(name) != "":
            return str(name)
        if dim.get("id") is not None:
            return str(dim["id"])
        return "—"
    return str(dim) if dim is not None else "—"


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round2(value: object) -> float | None:
    num = _num(value)
    return round(num, 2) if num is not None else None


async def resolve_counter(
    ctx: Ctx,
    counter_id: int | None,
    campaign_id: int | None,
    account: str,
) -> tuple[int | None, list[str]]:
    """counter_id > campaign_id+логин > [metrika] counter_id. Ошибки — списком."""
    if counter_id:
        return counter_id, []
    if campaign_id:
        try:
            entries = ctx.accounts(account)
        except ConfigError as e:
            return None, [f"Ошибка: {e}"]
        counters, problems = await campaign_counters(ctx, entries, [campaign_id])
        found: list[int] = []
        for lst in counters.values():
            found.extend(lst)
        found = sorted(set(found))
        if not found:
            msg = f"Ошибка: у кампании {campaign_id} счётчики не найдены."
            return None, [*problems, msg]
        notes = list(problems)
        if len(found) > 1:
            notes.append(
                f"Примечание: у кампании {campaign_id} счётчиков несколько "
                f"({', '.join(map(str, found))}) — взят {found[0]}."
            )
        return found[0], notes
    if ctx.settings.counter_id:
        return ctx.settings.counter_id, []
    return None, [("Ошибка: укажите counter_id, campaign_id "
                    "или [metrika] counter_id в accounts.toml.")]


async def resolve_goals(
    ctx: Ctx,
    counter_id: int,
    goal_ids: list[str],
    account: str,
    campaign_id: int | None,
) -> tuple[list[str], dict[str, str], str]:
    """(goal_ids, names, пометка). Default — основная цель, иначе все цели."""
    if goal_ids:
        try:
            names = await counter_goal_names(ctx.token, counter_id)
        except MetrikaError:
            names = {}
        return list(goal_ids), names, ""
    cids = [campaign_id] if campaign_id else []
    primary, _source = resolve_primary_goal(ctx.settings, account, cids, None)
    try:
        names = await counter_goal_names(ctx.token, counter_id)
    except MetrikaError:
        names = {}
    if primary:
        return [primary], names, ""
    try:
        gtypes = await counter_goal_types(ctx.token, counter_id)
    except MetrikaError:
        gtypes = {}
    all_ids = sorted(
        gtypes, key=lambda x: (0, int(x)) if x.isdigit() else (1, x)
    )
    return all_ids, names, "основная цель не задана"


class _BaseReportParams(GetActionParams):
    counter_id: int | None = Field(default=None)
    campaign_id: int | None = Field(default=None)
    date_from: str = ""
    date_to: str = ""
    goal_ids: list[str] = Field(default_factory=list)
    attribution: str = Field(default="lastsign")
    filter_utm_source: str = ""
    filter_utm_campaign: str = ""

    @field_validator("date_from", "date_to")
    @classmethod
    def _date_fmt(cls, value: str) -> str:
        if value and not DATE_RE.fullmatch(value):
            raise ValueError("dates must be YYYY-MM-DD")
        return value


class MetrikaTrafficParams(_BaseReportParams):
    group_by: str = Field(default="source")

    @field_validator("group_by")
    @classmethod
    def _group(cls, value: str) -> str:
        if value not in GROUP_VALUES:
            raise ValueError(f"group_by must be one of {', '.join(GROUP_VALUES)}")
        return value


class MetrikaGoalsReportParams(_BaseReportParams):
    group_by: str = Field(default="")

    @field_validator("group_by")
    @classmethod
    def _group(cls, value: str) -> str:
        if value and value not in GROUP_VALUES:
            raise ValueError(f"group_by must be one of {', '.join(GROUP_VALUES)}")
        return value


class MetrikaBytimeParams(_BaseReportParams):
    group: str = Field(default="day")

    @field_validator("group")
    @classmethod
    def _group(cls, value: str) -> str:
        if value not in ("day", "week"):
            raise ValueError("group must be day|week")
        return value


def _traffic_rows(
    payload: dict,
    group_cols: tuple[str, ...],
    goal_ids: list[str],
    goal_names: dict[str, str],
) -> tuple[list[dict], list[dict], bool]:
    metrics = list(BASE_METRICS) + [f"ym:s:goal{g}reaches" for g in goal_ids]
    idx = {m: i for i, m in enumerate(metrics)}
    rows: list[dict] = []
    raw: list[dict] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        dims = item.get("dimensions") or []
        vals = item.get("metrics") or []
        row: dict = {}
        for pos, col in enumerate(group_cols):
            row[col] = _dim_text(dims[pos]) if pos < len(dims) else "—"
        visits = _num(vals[idx["ym:s:visits"]]) if idx["ym:s:visits"] < len(vals) else None
        users = _num(vals[idx["ym:s:users"]]) if idx["ym:s:users"] < len(vals) else None
        bounce = (
            _round2(vals[idx["ym:s:bounceRate"]])
            if idx["ym:s:bounceRate"] < len(vals)
            else None
        )
        depth = (
            _round2(vals[idx["ym:s:pageDepth"]])
            if idx["ym:s:pageDepth"] < len(vals)
            else None
        )
        avg_time = (
            _num(vals[idx["ym:s:avgVisitDurationSeconds"]])
            if idx["ym:s:avgVisitDurationSeconds"] < len(vals)
            else None
        )
        row["Visits"] = int(visits) if visits is not None else "—"
        row["Users"] = int(users) if users is not None else "—"
        row["BounceRate"] = bounce if bounce is not None else "—"
        row["PageDepth"] = depth if depth is not None else "—"
        row["AvgTime"] = int(avg_time) if avg_time is not None else "—"
        for gid in goal_ids:
            key = f"ym:s:goal{gid}reaches"
            reaches = (
                _num(vals[idx[key]]) if idx[key] < len(vals) else None
            )
            reaches_i = int(reaches) if reaches is not None else 0
            cr = (
                round(reaches_i / visits * 100, 2)
                if visits
                else 0.0
            )
            if len(goal_ids) == 1:
                label = goal_names.get(gid, "")
                row["Goal"] = f"{label} ({gid})" if label else gid
                row["GoalReaches"] = reaches_i
                row["GoalCR"] = cr
            else:
                row[f"Goal_{gid}_reaches"] = reaches_i
                row[f"Goal_{gid}_CR"] = cr
        rows.append(row)
        raw.append({
            "dimensions": [_dim_text(d) for d in dims],
            "metrics": {m: vals[i] if i < len(vals) else None for m, i in idx.items()},
        })
    sampled = bool(payload.get("sampled"))
    return rows, raw, sampled


def _columns_for(
    group_cols: tuple[str, ...], goal_ids: list[str]
) -> list[str]:
    cols = list(group_cols) + ["Visits", "Users", "BounceRate", "PageDepth", "AvgTime"]
    if len(goal_ids) == 1:
        cols += ["Goal", "GoalReaches", "GoalCR"]
    else:
        for gid in goal_ids:
            cols += [f"Goal_{gid}_reaches", f"Goal_{gid}_CR"]
    return cols


def _header(
    tool: str,
    counter_id: int,
    date_from: str,
    date_to: str,
    extra: str,
    attribution: str,
    goal_note: str,
    sampled: bool,
) -> str:
    head = (
        f"{tool}: счётчик {counter_id}, {date_from}–{date_to}, "
        f"{extra}, атрибуция {attribution}."
    )
    if goal_note:
        head += f" {goal_note}."
    if sampled:
        head += " Выборка: данные семплированы (accuracy=full не хватило)."
    return head


@action(
    "metrika_traffic",
    "read",
    "Метрика: визиты по источникам (source/utm/direct/landing/device/region)",
    (
        "метрика",
        "metrika",
        "трафик",
        "визиты",
        "источники",
        "utm",
        "traffic",
        "отказы",
    ),
    MetrikaTrafficParams,
)
async def _traffic(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, MetrikaTrafficParams)
    dates = resolve_dates(params.date_from, params.date_to)
    if isinstance(dates, str):
        return dates
    date_from, date_to = dates
    counter_id, notes = await resolve_counter(
        ctx, params.counter_id, params.campaign_id, params.account
    )
    if counter_id is None:
        return "\n".join(notes)
    errors = list(notes)
    try:
        dims = group_dimensions(params.group_by, params.attribution)
    except ValueError as e:
        return f"Ошибка: {e}"
    goal_ids, goal_names, goal_note = await resolve_goals(
        ctx, counter_id, params.goal_ids, params.account, params.campaign_id
    )
    if not goal_ids:
        # Целей нет вовсе — показываем базовые метрики без CR.
        pass
    filters = build_filters(params.filter_utm_source, params.filter_utm_campaign)
    metrics = metrics_for(goal_ids)
    try:
        payload = await stat_table(
            ctx.token, counter_id, date_from, date_to, dims, metrics,
            params.attribution, filters,
        )
    except MetrikaError as e:
        return human_metrika_error(counter_id, e)
    if note := truncation_note(payload):
        errors.append(note)
    group_cols = GROUP_COLUMNS[params.group_by]
    rows, raw, sampled = _traffic_rows(payload, group_cols, goal_ids, goal_names)
    if not rows and not errors:
        errors.append("Строк нет за период (пустой ответ API).")
    context = _header(
        "metrika_traffic", counter_id, date_from, date_to,
        f"group_by={params.group_by}", params.attribution, goal_note, sampled,
    )
    if params.filter_utm_source or params.filter_utm_campaign:
        context += (
            f" Фильтры: utm_source={params.filter_utm_source or '—'}, "
            f"utm_campaign={params.filter_utm_campaign or '—'} (точное совпадение)."
        )
    columns = _columns_for(group_cols, goal_ids) if rows else []
    if rows and not goal_ids:
        columns = list(group_cols) + ["Visits", "Users", "BounceRate", "PageDepth", "AvgTime"]
    return finalize(
        ctx, context, "metrika_traffic", columns, rows,
        params.limit or 20, params.save_as, errors,
        money_cols=(), with_totals=False,
        output=params.output, format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="metrika_traffic",
        dump_params=params.model_dump(),
        dump_raw={"metrika_traffic": raw},
        dump_fields={"Metrika": ["stat/v1/data", "counter/{id}/goals"]},
        dump_tally={}, dump_logins=[],
        dump_scope="counter",
        dump_complete=not errors, dump_truncated=bool(errors),
    )


@action(
    "metrika_goals_report",
    "read",
    "Метрика: достижения целей и CR (в разрезе group_by — опционально)",
    (
        "метрика",
        "metrika",
        "цели",
        "конверсии",
        "goals",
        "cr",
        "достижения",
    ),
    MetrikaGoalsReportParams,
)
async def _goals_report(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, MetrikaGoalsReportParams)
    dates = resolve_dates(params.date_from, params.date_to)
    if isinstance(dates, str):
        return dates
    date_from, date_to = dates
    counter_id, notes = await resolve_counter(
        ctx, params.counter_id, params.campaign_id, params.account
    )
    if counter_id is None:
        return "\n".join(notes)
    errors = list(notes)
    goal_ids, goal_names, goal_note = await resolve_goals(
        ctx, counter_id, params.goal_ids, params.account, params.campaign_id
    )
    if not goal_ids:
        return (
            f"metrika_goals_report: счётчик {counter_id}, "
            f"{date_from}–{date_to}, атрибуция {params.attribution}. "
            "У счётчика нет целей."
        )
    filters = build_filters(params.filter_utm_source, params.filter_utm_campaign)
    metrics = ["ym:s:visits"] + [f"ym:s:goal{g}reaches" for g in goal_ids]
    dims = (
        group_dimensions(params.group_by, params.attribution) if params.group_by else []
    )
    try:
        payload = await stat_table(
            ctx.token, counter_id, date_from, date_to, dims, metrics,
            params.attribution, filters,
        )
    except MetrikaError as e:
        return human_metrika_error(counter_id, e)
    if note := truncation_note(payload):
        errors.append(note)
    sampled = bool(payload.get("sampled"))
    data = payload.get("data") or []
    group_cols = GROUP_COLUMNS[params.group_by] if params.group_by else ()
    rows: list[dict] = []
    raw: list[dict] = []
    if not dims:
        vals = data[0].get("metrics") or [] if data else []
        visits = _num(vals[0]) if len(vals) > 0 else None
        for pos, gid in enumerate(goal_ids):
            reaches = _num(vals[pos + 1]) if len(vals) > pos + 1 else None
            reaches_i = int(reaches) if reaches is not None else 0
            cr = round(reaches_i / visits * 100, 2) if visits else 0.0
            label = goal_names.get(gid, "")
            rows.append({
                "GoalId": gid,
                "GoalName": label or "—",
                "Visits": int(visits) if visits is not None else "—",
                "GoalReaches": reaches_i,
                "GoalCR": cr,
            })
            raw.append({"goal_id": gid, "visits": visits,
                        "reaches": reaches_i, "cr": cr})
    else:
        for item in data:
            if not isinstance(item, dict):
                continue
            item_dims = item.get("dimensions") or []
            vals = item.get("metrics") or []
            visits = _num(vals[0]) if len(vals) > 0 else None
            base: dict = {}
            for pos, col in enumerate(group_cols):
                base[col] = _dim_text(item_dims[pos]) if pos < len(item_dims) else "—"
            for pos, gid in enumerate(goal_ids):
                reaches = _num(vals[pos + 1]) if len(vals) > pos + 1 else None
                reaches_i = int(reaches) if reaches is not None else 0
                cr = round(reaches_i / visits * 100, 2) if visits else 0.0
                label = goal_names.get(gid, "")
                row = dict(base)
                row["GoalId"] = gid
                row["GoalName"] = label or "—"
                row["Visits"] = int(visits) if visits is not None else "—"
                row["GoalReaches"] = reaches_i
                row["GoalCR"] = cr
                rows.append(row)
                raw.append(dict(row))
    if not rows and not errors:
        errors.append("Строк нет за период (пустой ответ API).")
    extra = f"group_by={params.group_by}" if params.group_by else "по целям"
    context = _header(
        "metrika_goals_report", counter_id, date_from, date_to,
        extra, params.attribution, goal_note, sampled,
    )
    columns = (
        (list(group_cols) + ["GoalId", "GoalName", "Visits", "GoalReaches", "GoalCR"])
        if rows else []
    )
    return finalize(
        ctx, context, "metrika_goals_report", columns, rows,
        params.limit or 20, params.save_as, errors,
        money_cols=(), with_totals=False,
        output=params.output, format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="metrika_goals_report",
        dump_params=params.model_dump(),
        dump_raw={"metrika_goals_report": raw},
        dump_fields={"Metrika": ["stat/v1/data", "counter/{id}/goals"]},
        dump_tally={}, dump_logins=[],
        dump_scope="counter",
        dump_complete=not errors, dump_truncated=bool(errors),
    )


@action(
    "metrika_bytime",
    "read",
    "Метрика: динамика по дням/неделям (визиты, отказы, цель)",
    (
        "метрика",
        "metrika",
        "динамика",
        "bytime",
        "по дням",
        "по неделям",
        "тренд",
    ),
    MetrikaBytimeParams,
)
async def _bytime(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, MetrikaBytimeParams)
    dates = resolve_dates(params.date_from, params.date_to)
    if isinstance(dates, str):
        return dates
    date_from, date_to = dates
    counter_id, notes = await resolve_counter(
        ctx, params.counter_id, params.campaign_id, params.account
    )
    if counter_id is None:
        return "\n".join(notes)
    errors = list(notes)
    goal_ids, goal_names, goal_note = await resolve_goals(
        ctx, counter_id, params.goal_ids, params.account, params.campaign_id
    )
    goal_id = goal_ids[0] if goal_ids else None
    metrics = ["ym:s:visits", "ym:s:bounceRate"]
    if goal_id:
        metrics.append(f"ym:s:goal{goal_id}reaches")
    filters = build_filters(params.filter_utm_source, params.filter_utm_campaign)
    try:
        payload = await stat_bytime(
            ctx.token, counter_id, date_from, date_to, params.group,
            metrics, params.attribution, filters,
        )
    except MetrikaError as e:
        return human_metrika_error(counter_id, e)
    if note := truncation_note(payload):
        errors.append(note)
    sampled = bool(payload.get("sampled"))
    intervals: list = payload.get("time_intervals") or payload.get("intervals") or []
    data = payload.get("data") or []
    series: list = []
    if data and isinstance(data[0], dict):
        series = data[0].get("metrics") or []
    rows: list[dict] = []
    raw: list[dict] = []
    for pos, stamp in enumerate(intervals):
        if isinstance(stamp, (list, tuple)):
            stamp = stamp[0] if stamp else ""
        date_text = str(stamp)[:10]
        vals = [s[pos] if pos < len(s) else None for s in series] if series else []
        visits = _num(vals[0]) if len(vals) > 0 else None
        bounce = _round2(vals[1]) if len(vals) > 1 else None
        reaches = _num(vals[2]) if len(vals) > 2 else None
        reaches_i = int(reaches) if reaches is not None else 0
        cr = round(reaches_i / visits * 100, 2) if visits else 0.0
        label = goal_names.get(goal_id, "") if goal_id else ""
        row = {
            "Date": date_text,
            "Visits": int(visits) if visits is not None else "—",
            "BounceRate": bounce if bounce is not None else "—",
        }
        if goal_id:
            row["Goal"] = f"{label} ({goal_id})" if label else goal_id
            row["GoalReaches"] = reaches_i
            row["GoalCR"] = cr
        rows.append(row)
        raw.append(dict(row))
    if not rows and not errors:
        errors.append("Строк нет за период (пустой ответ API).")
    extra = f"group={params.group}"
    if goal_id:
        extra += f", цель {goal_id}"
    context = _header(
        "metrika_bytime", counter_id, date_from, date_to,
        extra, params.attribution, goal_note, sampled,
    )
    columns = (
        ["Date", "Visits", "BounceRate"] + (["Goal", "GoalReaches", "GoalCR"] if goal_id else [])
    ) if rows else []
    return finalize(
        ctx, context, "metrika_bytime", columns, rows,
        params.limit or 20, params.save_as, errors,
        money_cols=(), with_totals=False,
        output=params.output, format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="metrika_bytime",
        dump_params=params.model_dump(),
        dump_raw={"metrika_bytime": raw},
        dump_fields={"Metrika": ["stat/v1/data/bytime", "counter/{id}/goals"]},
        dump_tally={}, dump_logins=[],
        dump_scope="counter",
        dump_complete=not errors, dump_truncated=bool(errors),
    )
