"""Read actions stats_* over Reports API (SPEC 8)."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from directai_mcp.api.errors import DirectError
from directai_mcp.api.reports import PERIODS, ReportsClient
from directai_mcp.catalog.accounts import ensure_cache
from directai_mcp.catalog.common import (
    clean_phrase,
    finalize,
    goal_label,
    version_footer,
)
from directai_mcp.catalog.registry import ACCOUNT_HELP, Ctx, action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import GOAL_VALUE_NOTE, num, to_float, totals, totals_line

GROUP_DATE_FIELD = {"day": "Date", "week": "Week", "month": "Month",
                      # v1.1.24: дня недели в API нет — запрашиваем Date,
                      # агрегируем клиентски в group_weekday_rows.
                      "weekday": "Date"}

# v1.1.5: измерения CUSTOM_REPORT (AgeGroup API отвергает — поле Age).
# v1.1.27: +Slot (место показа: PREMIUMBLOCK/SUGGEST/PRODUCT_GALLERY/…).
CUSTOM_TIMES = ("day", "week", "month")
CUSTOM_DIMS = (
    "Device",
    "Age",
    "Gender",
    "LocationOfPresenceName",
    "AdNetworkType",
    "Placement",
    "Slot",
)

# v1.1.27: подписи мест показа для inline/MD (CSV/JSON — сырые значения).
SLOT_LABELS = {
    "PREMIUMBLOCK": "Спецразмещение",
    "SUGGEST": "Подсказки",
    "PRODUCT_GALLERY": "Товарная галерея",
    "COMMERCIAL_SEARCH": "Коммерческий поиск",
    "OTHER": "Прочие",
}


def slot_value_map(columns: list[str]) -> dict[str, dict[str, str]] | None:
    """v1.1.27: маппинг значений Slot (неизвестные — как есть)."""
    if "Slot" not in columns:
        return None
    return {"Slot": SLOT_LABELS}

# Campaigns.get filter for auto goals: same as campaigns_list without archive.
ACTIVE_GOAL_STATES = ["ON", "OFF", "SUSPENDED", "ENDED"]
# 0 = no optimization, 12 = engaged sessions (default, not key),
# 13 = placeholder "priority goals" inside strategy GoalId.
EXCLUDED_AUTO_GOAL_IDS = {0, 12, 13}

METRIC_FIELDS = ["Impressions", "Clicks", "Cost"]
CONV_FIELDS = ["Conversions", "CostPerConversion", "Revenue"]

# v1.1.22: доли/средние из API (сырые значения; в итогах/сводках — только
# из агрегатного запроса, суммирование и усреднение по строкам запрещены).
EXTRA_FIELDS = ["BounceRate", "AvgImpressionPosition", "AvgClickPosition"]
# SEARCH_QUERY_PERFORMANCE_REPORT — ограниченный набор полей, доп. поля
# туда не запрашиваем (риск ошибки 8000).
EXTRA_REPORTS = {
    "ACCOUNT_PERFORMANCE_REPORT",
    "CAMPAIGN_PERFORMANCE_REPORT",
    "ADGROUP_PERFORMANCE_REPORT",
    "AD_PERFORMANCE_REPORT",
    "CRITERIA_PERFORMANCE_REPORT",
    "CUSTOM_REPORT",
}

ACCOUNT_COL = "_account"


class StatsParams(BaseModel):
    account: str = Field(default="all", description=ACCOUNT_HELP)
    period: str = "LAST_7_DAYS"
    date_from: str | None = None
    date_to: str | None = None
    campaign_ids: list[int] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    goals_mode: Literal["key", "all"] = "key"
    attribution: list[str] = Field(default_factory=list)
    with_conversions: bool = True
    group_by: str = "none"
    # v1.1.24: даты без строк — нулевые строки с пометкой (только day).
    fill_calendar: bool = True
    limit: int | None = None
    show_negatives: bool = False
    # v1.3.4: отрезание « -…» только здесь (display); show_negatives больше
    # не режет (legacy-поле для совместимости вызовов).
    short_phrases: bool = False
    include_empty: bool = False
    save_as: Literal["csv", "md"] | None = None
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"
    # v1.1.19: строки считаются по одной выбранной цели (без дублей визитов).
    primary_goal: str | None = None

    @field_validator("period")
    @classmethod
    def _period(cls, value: str) -> str:
        if value not in PERIODS:
            raise ValueError(f"unknown period '{value}'")
        return value

    @field_validator("group_by")
    @classmethod
    def _group(cls, value):
        # Списки измерений проверяет CustomParams; строки — здесь.
        if isinstance(value, list):
            return value
        if value not in ("none", "day", "week", "month", "weekday", "slot"):
            raise ValueError(
                "group_by must be none, day, week, month, weekday or slot")
        return value

    @field_validator("limit")
    @classmethod
    def _limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit must be positive")
        return value

    @field_validator("goals_mode")
    @classmethod
    def _goals_mode(cls, value: str) -> str:
        if value not in ("key", "all"):
            raise ValueError("goals_mode must be key or all")
        return value


class CustomParams(StatsParams):
    report_type: str = "CUSTOM_REPORT"
    field_names: list[str] = Field(default_factory=list)
    filters: list[dict] = Field(default_factory=list)
    # v1.1.5: измерения группировки (AgeGroup в API нет — поле Age).
    group_by: list[str] = Field(default_factory=list)

    @field_validator("group_by")
    @classmethod
    def _group_dims(cls, value: list[str]) -> list[str]:
        allowed = set(CUSTOM_TIMES) | set(CUSTOM_DIMS)
        bad = [v for v in value if v not in allowed]
        if bad:
            raise ValueError(f"unknown dimensions {bad}; allowed {sorted(allowed)}")
        return value

    @field_validator("report_type")
    @classmethod
    def _type(cls, value: str) -> str:
        allowed = {
            "ACCOUNT_PERFORMANCE_REPORT",
            "CAMPAIGN_PERFORMANCE_REPORT",
            "ADGROUP_PERFORMANCE_REPORT",
            "AD_PERFORMANCE_REPORT",
            "CRITERIA_PERFORMANCE_REPORT",
            "SEARCH_QUERY_PERFORMANCE_REPORT",
            "CUSTOM_REPORT",
        }
        if value not in allowed:
            raise ValueError(f"unknown report_type '{value}'")
        return value


def _dates(params: StatsParams) -> tuple[str, dict]:
    if params.date_from or params.date_to:
        if not (params.date_from and params.date_to):
            raise ValueError("date_from and date_to must be set together")
        for value in (params.date_from, params.date_to):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("dates must be YYYY-MM-DD")
        if params.date_from > params.date_to:
            raise ValueError("date_from is after date_to")
        return "CUSTOM_DATE", {"DateFrom": params.date_from, "DateTo": params.date_to}
    return params.period, {}


class SearchQueriesParams(StatsParams):
    """stats_search_queries: группировка сырых строк (v1.1.4)."""

    query_grouping: Literal["query", "raw"] = "query"


class PlacementsParams(StatsParams):
    """stats_placements: пороги флага аномального CTR (v1.1.9)."""

    anomaly_min_clicks: int = Field(default=20, ge=0)
    anomaly_min_ctr: float = Field(default=5.0, ge=0)


class KeywordsParams(StatsParams):
    """stats_keywords: разбивка по типу соответствия (v1.1.20)."""

    match_mode: Literal["split", "sum"] = Field(
        default="split",
        description="split: строки по MatchType без склейки; "
        "sum: сумма по фразе (MatchType не запрашивается, сводит API).",
    )


class CompareParams(BaseModel):
    """stats_compare: сравнение двух периодов (v1.1.22).

    Отдельная модель (не StatsParams): два явных периода CUSTOM_DATE,
    group_by — разрез сравнения (none/device/region).
    """

    account: str = Field(default="all", description=ACCOUNT_HELP)
    campaign_ids: list[int] = Field(default_factory=list)
    period_a_from: str
    period_a_to: str
    period_b_from: str
    period_b_to: str
    goals: list[str] = Field(default_factory=list)
    goals_mode: Literal["key", "all"] = "key"
    attribution: list[str] = Field(default_factory=list)
    with_conversions: bool = True
    primary_goal: str | None = None
    group_by: Literal["none", "device", "region"] = "none"
    save_as: Literal["csv", "md"] | None = None
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"

    @field_validator(
        "period_a_from", "period_a_to", "period_b_from", "period_b_to"
    )
    @classmethod
    def _date(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("dates must be YYYY-MM-DD")
        return value

    @field_validator("goals_mode")
    @classmethod
    def _goals_mode(cls, value: str) -> str:
        if value not in ("key", "all"):
            raise ValueError("goals_mode must be key or all")
        return value


# v1.1.22: разрезы сравнения (измерения CUSTOM_REPORT).
COMPARE_GROUP_DIMS = {
    "device": ["Device"],
    "region": ["LocationOfPresenceId", "LocationOfPresenceName"],
}


def group_query_rows(
    columns: list[str], rows: list[dict]
) -> tuple[list[dict], int]:
    """Сгруппировать сырые строки по тексту Query (v1.1.4).

    Суммы Decimal; CPC/CPA и стоимость конверсии пересчитываются из сумм.
    Ключи строк — исходные имена колонок. Возвращает (строки, число сырых).
    """
    from decimal import ROUND_HALF_UP as _HALF_UP

    from directai_mcp.fmt import to_decimal

    by_metric: dict[str, list[str]] = {}
    for col in columns:
        match = _GOAL_COL_RE.match(col)
        if match:
            by_metric.setdefault(match.group(1) + ":" + match.group(2), []).append(col)

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        query = row.get("Query") or "—"
        if query not in groups:
            groups[query] = []
            order.append(query)
        groups[query].append(row)

    def _sum(items: list[dict], key: str) -> Decimal:
        total = Decimal(0)
        for item in items:
            parsed = to_decimal(item.get(key))
            if parsed is not None:
                total += parsed
        return total

    def _text(items: list[dict], key: str) -> str:
        vals = {str(r.get(key) or "") for r in items}
        vals.discard("")
        if len(vals) == 1:
            return next(iter(vals))
        if not vals:
            return "—"
        count = len(vals)
        word = "значение" if count == 1 else ("значения" if count < 5 else "значений")
        return f"{count} {word}"

    def _num(value: Decimal, places: int) -> str:
        if places == 0:
            return f"{int(value)}"
        quantized = value.quantize(Decimal(10) ** -places, rounding=_HALF_UP)
        return format(quantized.normalize(), "f")

    out: list[dict] = []
    for query in order:
        sub = groups[query]
        row: dict = {
            "CampaignId": _text(sub, "CampaignId"),
            "AdGroupId": _text(sub, "AdGroupId"),
            "Query": query,
            "MatchedKeyword": _text(sub, "MatchedKeyword"),
            "Criterion": _text(sub, "Criterion"),
            "Impressions": _num(_sum(sub, "Impressions"), 0),
            "Clicks": _num(_sum(sub, "Clicks"), 0),
            "Cost": _num(_sum(sub, "Cost"), 2),
        }
        clicks = _sum(sub, "Clicks")
        seen_gids: set[str] = set()
        for key, cols in by_metric.items():
            metric, gid = key.split(":", 1)
            if metric in ("CostPerConversion", "ConversionRate"):
                continue  # пересчитываются из сумм ниже
            if metric not in ("Conversions", "Revenue"):
                for col in cols:
                    row[col] = _text(sub, col)
                continue
            if gid in seen_gids:
                continue
            seen_gids.add(gid)
            conv = sum((_sum(sub, c) for c in by_metric.get("Conversions:" + gid, [])),
                       Decimal(0))
            revenue = sum((_sum(sub, c) for c in by_metric.get("Revenue:" + gid, [])),
                          Decimal(0))
            for col in by_metric.get("Conversions:" + gid, []):
                row[col] = _num(conv, 0)
            for col in by_metric.get("Revenue:" + gid, []):
                row[col] = _num(revenue, 2)
            for col in by_metric.get("CostPerConversion:" + gid, []):
                row[col] = _num(revenue / conv, 2) if conv else None
            for col in by_metric.get("ConversionRate:" + gid, []):
                row[col] = _num(conv / clicks * 100, 2) if clicks else None
        out.append(row)
    return out, len(rows)


# v1.1.24: порядок дней недели в group_weekday_rows (сортировка Пн..Вс,
# не по расходу — режим новый, наследия нет).
WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def group_weekday_rows(
    columns: list[str], rows: list[dict], expected: list[str] | None = None
) -> tuple[list[str], list[dict], str]:
    """v1.1.24: свод по дню недели из Date (Hour/DayOfWeek в API нет).

    Суммы Decimal; доли/средние (BounceRate, позиции) не суммируются
    и не усредняются (v1.1.22) — в строках их нет, только в итоге.
    При перечисляемом периоде присутствуют все 7 дней (пустые — нулями,
    DaysInPeriod — вхождений дня в период). Возвращает (columns, rows, note).
    """
    from directai_mcp.fmt import to_decimal

    by_metric: dict[str, list[str]] = {}
    for col in columns:
        match = _GOAL_COL_RE.match(col)
        if match:
            by_metric.setdefault(match.group(1) + ":" + match.group(2), []).append(col)

    def _sum(items: list[dict], key: str) -> Decimal:
        total = Decimal(0)
        for item in items:
            parsed = to_decimal(item.get(key))
            if parsed is not None:
                total += parsed
        return total

    def _num(value: Decimal, places: int) -> str:
        from decimal import ROUND_HALF_UP as _HALF_UP

        if places == 0:
            return f"{int(value)}"
        quantized = value.quantize(Decimal(10) ** -places, rounding=_HALF_UP)
        return format(quantized.normalize(), "f")

    try:
        date_pos = columns.index("Date")
    except ValueError:
        return columns, rows, ""
    dim_cols = [c for c in columns[:date_pos] if not _is_value_col(c)]
    dim_cols += [c for c in columns[date_pos + 1:] if not _is_value_col(c)]
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    day_sets: dict[tuple, set] = {}
    skipped = 0
    for row in rows:
        try:
            weekday = date.fromisoformat(str(row.get("Date"))).weekday()
        except (ValueError, TypeError):
            skipped += 1
            continue
        key = (weekday, *(row.get(c) for c in dim_cols))
        if key not in groups:
            groups[key] = []
            day_sets[key] = set()
            order.append(key)
        groups[key].append(row)
        day_sets[key].add(str(row.get("Date")))

    new_columns = (
        columns[:date_pos] + ["Weekday", "DaysInPeriod"]
        + [c for c in columns[date_pos + 1:] if c != "Date"]
    )
    # v1.1.24: все 7 дней при перечисляемом периоде (пустые — нулями).
    # Достройка — после drop_empty (complete_weekday_rows): здесь только
    # подсчёт вхождений для DaysInPeriod.
    cal_sets: dict[int, set] = {}
    if expected:
        for iso in expected:
            try:
                wd = date.fromisoformat(iso).weekday()
            except (ValueError, TypeError):
                continue
            cal_sets.setdefault(wd, set()).add(iso)
    out: list[dict] = []
    # Сортировка Пн..Вс; внутри дня — по строковому представлению измерений
    # (смешанные типы в ключе сравнивать напрямую нельзя).
    for key in sorted(order, key=lambda k: (k[0], tuple(str(v) for v in k[1:]))):
        sub = groups[key]
        weekday = key[0]
        assert isinstance(weekday, int)
        dims_source = sub[0] if sub else {}
        row: dict = {
            c: dims_source.get(c) for c in dim_cols if c in columns
        }
        row["Weekday"] = WEEKDAY_NAMES[weekday]
        if expected is not None and weekday in cal_sets:
            row["DaysInPeriod"] = len(cal_sets[weekday])
        else:
            row["DaysInPeriod"] = len(day_sets[key])
        row["Impressions"] = _num(_sum(sub, "Impressions"), 0)
        row["Clicks"] = _num(_sum(sub, "Clicks"), 0)
        row["Cost"] = _num(_sum(sub, "Cost"), 2)
        if "Conversions" in columns:
            row["Conversions"] = _num(_sum(sub, "Conversions"), 0)
        if "Revenue" in columns:
            row["Revenue"] = _num(_sum(sub, "Revenue"), 2)
        clicks = _sum(sub, "Clicks")
        seen_gids: set[str] = set()
        for gkey in by_metric:
            metric, gid = gkey.split(":", 1)
            if metric in ("CostPerConversion", "ConversionRate"):
                continue  # пересчитываются из сумм ниже
            if metric not in ("Conversions", "Revenue"):
                continue
            if gid in seen_gids:
                continue
            seen_gids.add(gid)
            conv = sum((_sum(sub, c) for c in by_metric.get("Conversions:" + gid, [])),
                       Decimal(0))
            revenue = sum((_sum(sub, c) for c in by_metric.get("Revenue:" + gid, [])),
                          Decimal(0))
            for col in by_metric.get("Conversions:" + gid, []):
                row[col] = _num(conv, 0)
            for col in by_metric.get("Revenue:" + gid, []):
                row[col] = _num(revenue, 2)
            for col in by_metric.get("CostPerConversion:" + gid, []):
                row[col] = _num(revenue / conv, 2) if conv else None
            for col in by_metric.get("ConversionRate:" + gid, []):
                row[col] = _num(conv / clicks * 100, 2) if clicks else None
        out.append(row)
    note = ""
    if any(c in columns for c in EXTRA_FIELDS):
        note = ("Доли/средние (отказы, позиции) по дням недели API не даёт — "
                "только в детальных строках и итоге.")
    if skipped:
        skipped_note = f"Строк без даты пропущено: {skipped}."
        note += ((" " if note else "") + skipped_note)
    return new_columns, out, note


def complete_weekday_rows(
    columns: list[str], rows: list[dict], expected: list[str] | None
) -> list[dict]:
    """v1.1.24: достройка всех 7 дней нулями — после drop_empty.

    DaysInPeriod — вхождений дня в период. Без перечисляемого периода
    и без колонки Weekday — без изменений.
    """
    if not expected or "Weekday" not in columns:
        return rows
    cal_count: dict[int, int] = {}
    for iso in expected:
        try:
            wd = date.fromisoformat(iso).weekday()
        except (ValueError, TypeError):
            continue
        cal_count[wd] = cal_count.get(wd, 0) + 1
    dim_cols = [c for c in columns
                if c not in ("Weekday", "DaysInPeriod") and not _is_value_col(c)]
    combos: list[tuple] = []
    base: dict[tuple, dict] = {}
    have: set[tuple] = set()
    for row in rows:
        key = tuple(row.get(c) for c in dim_cols)
        if key not in base:
            base[key] = {c: row.get(c) for c in dim_cols}
            combos.append(key)
        wd = WEEKDAY_NAMES.index(row["Weekday"]) if row.get("Weekday") in WEEKDAY_NAMES else None
        if wd is not None:
            have.add((key, wd))
    if not combos:
        combos = [tuple(None for _ in dim_cols)]
        base[combos[0]] = {c: None for c in dim_cols}
    filled = list(rows)
    for key in combos:
        for wd in range(7):
            if (key, wd) in have or wd not in cal_count:
                continue
            filled.append({
                **base[key],
                "Weekday": WEEKDAY_NAMES[wd],
                "DaysInPeriod": cal_count[wd],
                "Impressions": "0",
                "Clicks": "0",
                "Cost": "0.00",
            })
    filled.sort(key=lambda r: (
        WEEKDAY_NAMES.index(r["Weekday"]) if r.get("Weekday") in WEEKDAY_NAMES else 99,
        tuple(str(r.get(c)) for c in dim_cols),
    ))
    return filled


def fill_calendar_rows(
    columns: list[str], rows: list[dict], params: StatsParams
) -> tuple[list[dict], str]:
    """v1.1.24: даты периода без строк — нулевые строки (только day).

    Возвращает (rows, note); note пуст, если пропусков нет, или сообщает
    о неперечисляемом периоде.
    """
    expected = _period_dates(params)
    if expected is None:
        return rows, "Календарь не заполнен: период не перечисляется."
    dim_cols = [c for c in columns if c != "Date" and not _is_value_col(c)]
    have: set[tuple] = set()
    keys: list[tuple] = []
    base: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(c) for c in dim_cols)
        if key not in base:
            base[key] = {c: row.get(c) for c in dim_cols}
            keys.append(key)
        if row.get("Date") is not None:
            have.add((key, str(row.get("Date"))))
    if not keys:
        keys = [tuple(None for _ in dim_cols)]
        base[keys[0]] = {c: None for c in dim_cols}
    missing: set[str] = set()
    filled = list(rows)
    for key in keys:
        for day in expected:
            if (key, day) in have:
                continue
            missing.add(day)
            filled.append({
                **base[key],
                "Date": day,
                "Impressions": "0",
                "Clicks": "0",
                "Cost": "0.00",
            })
    if not missing:
        return filled, ""
    ordered = sorted(missing)

    def _ru(iso: str) -> str:
        try:
            return _fmt_date(date.fromisoformat(iso))
        except ValueError:
            return iso

    return filled, "Нет показов: " + ", ".join(_ru(d) for d in ordered) + "."


def _definition(
    ctx: Ctx,
    params: StatsParams,
    report_type: str,
    dims: list[str],
    goals_override: list[str] | None = None,
) -> dict:
    date_range, extra = _dates(params)
    fields: list[str] = []
    if params.group_by != "none":
        # v1.1.27: slot — измерение Slot, не дата.
        fields.append(GROUP_DATE_FIELD.get(params.group_by, "Slot"))
    fields += dims + METRIC_FIELDS
    if report_type in EXTRA_REPORTS:
        fields += EXTRA_FIELDS
    if goals_override is None:
        goals = list(params.goals) or list(ctx.settings.goals)
    else:
        goals = list(goals_override)
    if params.with_conversions or goals:
        fields += CONV_FIELDS
    selection: dict = dict(extra)
    if params.campaign_ids:
        selection["Filter"] = [
            {
                "Field": "CampaignId",
                "Operator": "IN",
                "Values": [str(i) for i in params.campaign_ids],
            }
        ]
    definition: dict = {
        "SelectionCriteria": selection,
        "FieldNames": fields,
        "ReportType": report_type,
        "DateRangeType": date_range,
        "Format": "TSV",
        "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
    }
    if goals:
        definition["Goals"] = goals
        definition["AttributionModels"] = effective_attribution(ctx, params)
    return definition


def effective_attribution(ctx: Ctx, params: StatsParams) -> list[str]:
    """Attribution: model params, then config defaults, then AUTO."""
    return list(params.attribution) or list(ctx.settings.attribution) or ["AUTO"]


def extract_campaign_goals(item: dict, include_engaged: bool = False) -> set[int]:
    """Key goal ids from Campaigns.get item (PriorityGoals + strategy).

    Sources (docs campaigns/get*): TextCampaign/UnifiedCampaign PriorityGoals,
    BiddingStrategy.*.GoalId, TextCampaign RelevantKeywords.OptimizeGoalId.
    Always excludes 0 (no optimization) and 13 (priority-group placeholder).
    Goal 12 (engaged sessions, default when no PriorityGoals) is excluded
    unless include_engaged (goals_mode=all).
    """
    excluded = {0, 13} if include_engaged else EXCLUDED_AUTO_GOAL_IDS
    found: set[int] = set()

    def _add(gid: object) -> None:
        if isinstance(gid, int) and gid > 0 and gid not in excluded:
            found.add(gid)

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "GoalId":
                    _add(value)
                else:
                    _walk(value)
        elif isinstance(node, list):
            for sub in node:
                _walk(sub)

    for block_key in ("TextCampaign", "UnifiedCampaign"):
        body = item.get(block_key)
        if not isinstance(body, dict):
            continue
        priority = body.get("PriorityGoals") or {}
        items = priority.get("Items") if isinstance(priority, dict) else None
        if isinstance(items, list):
            for goal in items:
                if isinstance(goal, dict):
                    _add(goal.get("GoalId"))
        _walk(body.get("BiddingStrategy"))
        relevant = body.get("RelevantKeywords")
        if isinstance(relevant, dict):
            _add(relevant.get("OptimizeGoalId"))
    return found


def chunk_goals(goals: list[str], size: int = 10) -> list[list[str]]:
    """Split goal ids into Reports API chunks (Goals max 10, live 7000)."""
    return [goals[i : i + size] for i in range(0, len(goals), size)] if goals else []


async def resolve_auto_goals(
    ctx: Ctx,
    entries: list[AccountEntry],
    campaign_ids: list[int],
    mode: str = "key",
) -> dict[str, list[str]]:
    """Per-login goal ids from live campaign settings (read-only get).

    key: active campaigns only, without engaged sessions (12).
    all: union(настройки кампаний, все id из goals.toml) — v1.1.3: Reports
    принимает любые цели привязанного счётчика, не только цели кампании.
    """
    from directai_mcp.config import all_goal_ids

    home = ctx.data_dir or (
        ctx.settings.accounts_path.parent if ctx.settings.accounts_path else None
    )
    toml_ids = all_goal_ids(home) if mode == "all" else []

    async def fetch(entry: AccountEntry, client):
        criteria: dict = {}
        if campaign_ids:
            criteria["Ids"] = list(campaign_ids)
        elif mode == "key":
            criteria["States"] = list(ACTIVE_GOAL_STATES)
        # all without ids: empty criteria = all campaigns incl. archived
        body = {
            "SelectionCriteria": criteria,
            "FieldNames": ["Id"],
            "TextCampaignFieldNames": [
                "PriorityGoals",
                "BiddingStrategy",
                "RelevantKeywords",
            ],
            "UnifiedCampaignFieldNames": ["PriorityGoals", "BiddingStrategy"],
        }
        items = await client.get_all("campaigns", body, entry.login, "Campaigns", "v501")
        merged: set[int] = set()
        for item in items:
            if isinstance(item, dict):
                merged |= extract_campaign_goals(item, include_engaged=(mode == "all"))
        for gid in toml_ids:
            if gid.isdigit():
                merged.add(int(gid))
        return sorted(merged)

    import asyncio as _asyncio

    async def _one(entry: AccountEntry):
        client = ctx.direct()
        try:
            try:
                return entry.login, await fetch(entry, client)
            except DirectError as e:
                return entry.login, e
        finally:
            await client.aclose()

    sem = _asyncio.Semaphore(3)

    async def _guarded(entry: AccountEntry):
        async with sem:
            return await _one(entry)

    out: dict[str, list[str]] = {}
    for login, payload in await _asyncio.gather(*(_guarded(e) for e in entries)):
        if isinstance(payload, DirectError):
            out[login] = []
        else:
            out[login] = [str(g) for g in payload]
    return out


def _fmt_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _period_label(params: StatsParams) -> str:
    """Concrete dates DD.MM.YYYY for the header instead of a preset (SPEC 6.6)."""
    if params.date_from and params.date_to:
        try:
            start = date.fromisoformat(params.date_from)
            end = date.fromisoformat(params.date_to)
        except ValueError:
            return f"{params.date_from}..{params.date_to}"
        return f"{_fmt_date(start)}–{_fmt_date(end)}"
    today = datetime.now().astimezone().date()
    one = timedelta(days=1)
    period = params.period
    if period == "TODAY":
        return _fmt_date(today)
    if period == "YESTERDAY":
        return _fmt_date(today - one)
    match = re.fullmatch(r"LAST_(\d+)_DAYS", period)
    if match:
        days = int(match.group(1))
        return f"{_fmt_date(today - timedelta(days=days))}–{_fmt_date(today - one)}"
    if period == "THIS_WEEK_MON_TODAY":
        return (
            f"{_fmt_date(today - timedelta(days=today.weekday()))}–{_fmt_date(today)}"
        )
    if period == "THIS_WEEK_SUN_TODAY":
        back = (today.weekday() + 1) % 7
        return f"{_fmt_date(today - timedelta(days=back))}–{_fmt_date(today)}"
    if period == "LAST_WEEK":
        monday = today - timedelta(days=today.weekday() + 7)
        return f"{_fmt_date(monday)}–{_fmt_date(monday + timedelta(days=6))}"
    if period == "LAST_BUSINESS_WEEK":
        monday = today - timedelta(days=today.weekday() + 7)
        return f"{_fmt_date(monday)}–{_fmt_date(monday + timedelta(days=4))}"
    if period == "LAST_WEEK_SUN_SAT":
        sunday = today - timedelta(days=(today.weekday() + 1) % 7 + 7)
        return f"{_fmt_date(sunday)}–{_fmt_date(sunday + timedelta(days=6))}"
    if period == "THIS_MONTH":
        return f"{_fmt_date(today.replace(day=1))}–{_fmt_date(today)}"
    if period == "LAST_MONTH":
        first = today.replace(day=1)
        end = first - one
        return f"{_fmt_date(end.replace(day=1))}–{_fmt_date(end)}"
    if period == "ALL_TIME":
        return "вся доступная статистика"
    return period


def _period_dates(params: StatsParams) -> list[str] | None:
    """v1.1.24: перечислимые даты периода (ISO) для fill_calendar.

    None — период не перечисляется (ALL_TIME, AUTO и неизвестные).
    Логика зеркал _period_label (тот же today).
    """
    if params.date_from and params.date_to:
        try:
            start = date.fromisoformat(params.date_from)
            end = date.fromisoformat(params.date_to)
        except ValueError:
            return None
        if start > end:
            return None
        out = []
        day = start
        while day <= end:
            out.append(day.isoformat())
            day += timedelta(days=1)
        return out
    today = datetime.now().astimezone().date()
    one = timedelta(days=1)
    period = params.period
    if period == "TODAY":
        return [today.isoformat()]
    if period == "YESTERDAY":
        return [(today - one).isoformat()]
    match = re.fullmatch(r"LAST_(\d+)_DAYS", period)
    if match:
        days = int(match.group(1))
        return [(today - timedelta(days=d)).isoformat()
                for d in range(days, 0, -1)]
    if period == "LAST_WEEK":
        monday = today - timedelta(days=today.weekday() + 7)
        return [(monday + timedelta(days=d)).isoformat() for d in range(7)]
    if period == "LAST_BUSINESS_WEEK":
        monday = today - timedelta(days=today.weekday() + 7)
        return [(monday + timedelta(days=d)).isoformat() for d in range(5)]
    if period == "LAST_WEEK_SUN_SAT":
        sunday = today - timedelta(days=(today.weekday() + 1) % 7 + 7)
        return [(sunday + timedelta(days=d)).isoformat() for d in range(7)]
    if period == "THIS_WEEK_MON_TODAY":
        monday = today - timedelta(days=today.weekday())
        day, out = monday, []
        while day <= today:
            out.append(day.isoformat())
            day += one
        return out
    if period == "THIS_WEEK_SUN_TODAY":
        sunday = today - timedelta(days=(today.weekday() + 1) % 7)
        day, out = sunday, []
        while day <= today:
            out.append(day.isoformat())
            day += one
        return out
    if period == "THIS_MONTH":
        day, out = today.replace(day=1), []
        while day <= today:
            out.append(day.isoformat())
            day += one
        return out
    if period == "LAST_MONTH":
        first = today.replace(day=1)
        end = first - one
        day, out = end.replace(day=1), []
        while day <= end:
            out.append(day.isoformat())
            day += one
        return out
    return None


def _clean_criterion(value: object, short_phrases: bool) -> object:
    """Alias kept for tests; logic lives in common.clean_phrase."""
    return clean_phrase(value, short_phrases)


_GOAL_COL_RE = re.compile(
    r"^(Conversions|Revenue|CostPerConversion|ConversionRate|GoalsRoi)_(\d+)_(.+)$"
)


def drop_roi(
    columns: list[str], rows: list[dict]
) -> tuple[list[str], list[dict]]:
    """Убрать GoalsRoi-колонки: ДРР по условным ценностям не выводим (v1.1.1)."""
    doomed = {c for c in columns if c.startswith("GoalsRoi")}
    doomed |= {k for row in rows for k in row if str(k).startswith("GoalsRoi")}
    if not doomed:
        return columns, rows
    return (
        [c for c in columns if c not in doomed],
        [{k: v for k, v in row.items() if k not in doomed} for row in rows],
    )


def drop_zero_revenue(
    columns: list[str], rows: list[dict]
) -> tuple[list[str], list[dict], bool]:
    """v1.1.26: Revenue-колонки с нулём по всем строкам — не выводим.

    Убирает «Выручка 0.00» при условной ценности без назначенной суммы.
    Возвращает (columns, rows, dropped).
    """
    from directai_mcp.fmt import to_decimal

    doomed = {c for c in columns if c == "Revenue" or c.startswith("Revenue_")}
    doomed |= {k for row in rows for k in row
               if str(k) == "Revenue" or str(k).startswith("Revenue_")}
    if not doomed:
        return columns, rows, False
    for row in rows:
        for col in doomed:
            if (to_decimal(row.get(col)) or Decimal(0)) != 0:
                return columns, rows, False
    return (
        [c for c in columns if c not in doomed],
        [{k: v for k, v in row.items() if k not in doomed} for row in rows],
        True,
    )


def revenue_header_map(
    columns: list[str], value_types: dict[str, str] | None = None
) -> dict[str, str]:
    """v1.1.26: «Ценность целей (условная)» вместо Revenue в MD/inline.

    v1.1.29: CRM-цели — «Выручка CRM». CSV/JSON хранят имена полей API
    (маппинг только для заголовков таблиц).
    """
    types = value_types or {}
    out = {}
    for col in columns:
        label = None
        if col == "Revenue":
            label = "Ценность целей (условная)"
        elif col.startswith("Revenue_"):
            match = _GOAL_COL_RE.match(col)
            gid = match.group(2) if match else None
            base = "Выручка CRM" if types.get(gid or "") == "crm" \
                else "Ценность целей (условная)"
            label = base + "_" + col[len("Revenue_"):]
        if label is not None:
            out[col] = label
    return out


async def _adgroups_for_stats(
    ctx: Ctx, entries: list[AccountEntry], campaign_ids: list[int]
) -> tuple[dict, list[int], list[str]]:
    """v1.1.26: группы кампаний одним AdGroups.get (v501): статус/тип/счёт.

    Возвращает ({(логин, gid): info}, [gid...], ошибки). Info: Status,
    ServingStatus, Type, Name, CampaignId. State у групп API не отдаёт
    (8000, доказано v1.1.25).
    """
    from directai_mcp.catalog.common import chunk as _chunk

    info: dict = {}
    order: list[int] = []
    problems: list[str] = []
    for entry in entries:
        client = ctx.direct()
        try:
            for ids in _chunk(list(campaign_ids), 10):
                try:
                    items = await client.get_all(
                        "adgroups",
                        {
                            "SelectionCriteria": {"CampaignIds": ids},
                            "FieldNames": ["Id", "CampaignId", "Name", "Status",
                                           "ServingStatus", "Type"],
                        },
                        entry.login,
                        "AdGroups",
                        "v501",
                    )
                except DirectError as e:
                    problems.append(
                        f"⚠ {entry.login}: группы: {e.human_message()}"
                    )
                    continue
                for item in items:
                    gid = item.get("Id")
                    if not isinstance(gid, int):
                        continue
                    info[(entry.login, gid)] = item
                    if gid not in order:
                        order.append(gid)
        finally:
            await client.aclose()
    return info, order, problems


def _reconcile_note(
    render_totals: dict, reconc_rows: list[dict] | None
) -> str | None:
    """v1.1.26: сверка итога с агрегатом кампании по числам (показы/клики/расход).

    None — сверки нет (ошибка/пусто/несравнимо): молча ничего, без оговорок.
    """
    from directai_mcp.fmt import money as _money
    from directai_mcp.fmt import num as _num
    from directai_mcp.fmt import totals as _totals

    if not reconc_rows:
        return None
    base = _totals(reconc_rows)
    if not any((base.get(k) or Decimal(0)) != 0
               for k in ("Impressions", "Clicks", "Cost")):
        return None
    diffs = []
    labels = []
    ok = True
    for key, plain in (("Impressions", True), ("Clicks", True),
                       ("Cost", False)):
        a = render_totals.get(key) or Decimal(0)
        b = base.get(key) or Decimal(0)
        text = _num(a, 0) if plain else _money(a)
        labels.append(f"{text}{' ₽' if not plain else ''}")
        if abs(a - b) > Decimal("0.005"):
            ok = False
            other = _num(b, 0) if plain else _money(b)
            diff = _num(a - b, 0) if plain else _money(a - b)
            diffs.append(f"{key}: отчёт {text} / кампания {other} (Δ {diff})")
    if ok:
        return "Сверка с итогом кампании: сходится (" + " / ".join(labels) + ")."
    return ("Сверка с итогом кампании: расходится: "
            + "; ".join(diffs) + ".")


def _row_gid(row: dict) -> tuple:
    """v1.1.26: ключ группы в строке (логин, id)."""
    try:
        gid = int(row.get("AdGroupId"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        gid = None
    return (row.get(ACCOUNT_COL), gid)


def is_empty_row(row: dict) -> bool:
    """v1.1.7: строка пустая, только если нули показы, клики, расход и конверсии.

    Отсутствие значения (None/«--») считается нулём. Строки с показами,
    но без кликов/расхода — не пустые (сигнал для анализа CTR).
    """
    from directai_mcp.fmt import to_decimal

    for key in ("Impressions", "Clicks", "Cost"):
        parsed = to_decimal(row.get(key))
        if parsed is not None and parsed != 0:
            return False
    for key, val in row.items():
        if key == "Conversions" or str(key).startswith("Conversions_"):
            parsed = to_decimal(val)
            if parsed is not None and parsed != 0:
                return False
    return True


def drop_empty_rows(rows: list[dict]) -> tuple[list[dict], int]:
    """Исключить пустые строки (v1.1.7). Возвращает (строки, число исключённых)."""
    kept = [r for r in rows if not is_empty_row(r)]
    return kept, len(rows) - len(kept)


def count_empty_rows(rows: list[dict]) -> int:
    """Число пустых строк без исключения (для пометки при include_empty)."""
    return sum(1 for r in rows if is_empty_row(r))


def row_conversions(row: dict) -> Decimal | None:
    """v1.1.9: сумма конверсий строки по всем goal-колонкам.

    None — в строке вообще нет конверсионных входов (показывать «—»,
    а не 0). Вызывать до add_derived (иначе посчитает свою же колонку).
    """
    from directai_mcp.fmt import to_decimal

    found = False
    total = Decimal(0)
    for key, val in row.items():
        if key == "Conversions" or str(key).startswith("Conversions_"):
            found = True
            parsed = to_decimal(val)
            if parsed is not None:
                total += parsed
    return total if found else None


def _fmt2(value: Decimal) -> str:
    """v1.1.9: 2 знака, Decimal HALF_UP, без группировки тысяч (чистый CSV)."""
    from decimal import ROUND_HALF_UP

    return format(
        value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"
    )


# v1.1.19: режимы конверсий строк (дедуп визитов между целями API не даёт —
# report-format.md: сумма по целям может превышать число целевых визитов).
# v1.2.2: итог = Σ строк (уникальные больше не выделяются итогом из агрегата).
SUM_SUFFIX = " (сумма по целям)"
DUP_NOTE = (
    "в строках возможны дубли визитов,"
    " итог — сумма строк"
)


def conv_mode(n_goals: int, primary: str | None) -> str:
    """single: одна цель (как раньше); sum: сумма с подписью; primary: одна цель."""
    if primary and n_goals > 1:
        return "primary"
    if n_goals > 1:
        return "sum"
    return "single"


def derived_names(mode: str) -> tuple[str, str, str]:
    """Имена колонок (Conversions, CPA, CR) для режима."""
    if mode == "sum":
        return (
            f"Conversions{SUM_SUFFIX}", f"CPA{SUM_SUFFIX}", f"CR{SUM_SUFFIX}"
        )
    return ("Conversions", "CPA", "CR")


def check_primary(goals: list[str], primary: str | None) -> None:
    """v1.1.19: primary_goal обязана быть среди целей отчёта."""
    if primary and primary not in goals:
        raise ValueError(
            f"primary_goal {primary} нет среди целей отчёта: "
            + (", ".join(goals) if goals else "целей нет")
            + "."
        )


def row_goal_conversions(row: dict, gid: str) -> Decimal | None:
    """v1.1.19: конверсии строки только по одной цели (без дублей).

    Численно равно dedicated single-goal запросу для этой цели:
    те же колонки, та же модель. Нет колонок цели — plain как есть.
    Ищет и сырые (`Conversions_<gid>_<model>`), и локализованные
    (`Conversions_<имя> (<gid>)_<model>`) имена — derived считается
    после localize_goal_columns.
    """
    import re as _re

    from directai_mcp.fmt import to_decimal

    prefix = f"Conversions_{gid}_"
    localized = _re.compile(rf"\({_re.escape(gid)}\)_")
    found = False
    total = Decimal(0)
    for key, val in row.items():
        name = str(key)
        # Только Conversions_: колонка CostPerConversion той же цели
        # сюда не попадает.
        if not name.startswith("Conversions_"):
            continue
        if name.startswith(prefix) or localized.search(name):
            found = True
            parsed = to_decimal(val)
            if parsed is not None:
                total += parsed
    if found:
        return total
    return to_decimal(row.get("Conversions"))


def add_derived(
    rows: list[dict],
    total_cost: Decimal,
    include_share: bool = True,
    conv_col: str = "Conversions",
    cpa_col: str = "CPA",
    cr_col: str = "CR",
    primary: str | None = None,
) -> None:
    """v1.1.9: дефолтные колонки CTR/CPC/CPA/Conversions (+CostShare).

    v1.1.19: +CR (конверсии/клики, % только в MD/inline); при primary —
    конверсии строки только по этой цели.

    Пересчёт из сумм строки, Decimal HALF_UP. Значения — plain-числа
    без % (знак % добавляет рендер MD/inline, CSV остаётся числом).
    """
    for row in rows:
        from directai_mcp.fmt import to_decimal

        impr = to_decimal(row.get("Impressions")) or Decimal(0)
        clicks = to_decimal(row.get("Clicks")) or Decimal(0)
        cost = to_decimal(row.get("Cost")) or Decimal(0)
        conv = (
            row_goal_conversions(row, primary)
            if primary
            else row_conversions(row)
        )
        row["CTR"] = _fmt2(clicks / impr * 100) if impr else None
        row["CPC"] = _fmt2(cost / clicks) if clicks else None
        if conv is None:
            row[conv_col] = None
        else:
            row[conv_col] = (
                str(int(conv)) if conv == int(conv)
                else format(conv.normalize(), "f")
            )
        row[cpa_col] = _fmt2(cost / conv) if conv else None
        row[cr_col] = _fmt2(conv / clicks * 100) if (conv and clicks) else None
        if include_share:
            row["CostShare"] = (
                _fmt2(cost / total_cost * 100) if total_cost else None
            )


def rebuild_columns(
    columns: list[str],
    include_share: bool = True,
    conv_col: str = "Conversions",
    cpa_col: str = "CPA",
    cr_col: str = "CR",
) -> list[str]:
    """v1.1.9: порядок дефолтных колонок (сырые goal-колонки — следом)."""
    cost_block = ["Cost"] + (["CostShare"] if include_share else []) + ["CPC"]
    derived_block = [conv_col, cpa_col, cr_col]
    out: list[str] = []
    conv_done = False
    for col in columns:
        if col == "Clicks":
            out += ["Clicks", "CTR"]
        elif col == "Cost":
            out += cost_block
        elif col == "Conversions":
            out += derived_block  # сырое значение уже перезаписано
        elif col.startswith("Conversions_") and not conv_done:
            out += [*derived_block, col]
            conv_done = True
        else:
            out.append(col)
    if not conv_done and "Conversions" not in columns:
        anchor = "CPC" if "CPC" in out else (out[-1] if out else None)
        if anchor is not None:
            pos = out.index(anchor) + 1
            out[pos:pos] = derived_block
        else:
            out += derived_block
    # Производные без входов (нет Clicks/Cost) — не показывать столбец из «—».
    return out


def prune_derived(
    columns: list[str], rows: list[dict], conv_col: str = "Conversions"
) -> list[str]:
    """v1.1.9: убрать производные, где везде None (нет входов), кроме Conversions."""
    drop_base = ("CTR", "CostShare", "CPC", "CPA", "CR")
    out = []
    for col in columns:
        if col == conv_col:
            out.append(col)
            continue
        if col.split(" (", 1)[0] in drop_base and not any(
            r.get(col) is not None for r in rows
        ):
            continue
        out.append(col)
    return out


def add_rank(rows: list[dict]) -> None:
    """v1.1.7: колонка «#» — ранг строки (1..N) после сортировки по расходу."""
    for pos, row in enumerate(rows, start=1):
        row["#"] = pos


# v1.1.21: корзины сводки по типам критериев stats_keywords.
# v1.1.28: +RETARGETING отдельной корзиной (раньше тонул в «прочие»).
CRITERION_BUCKETS = ("KEYWORD", "AUTOTARGETING", "RETARGETING")
CRITERION_LABELS = {
    "KEYWORD": "КЛЮЧИ",
    "AUTOTARGETING": "АВТОТАРГЕТИНГ",
    "RETARGETING": "РЕТАРГЕТИНГ",
    "прочие": "ПРОЧЕЕ",
}


def criterion_summary(
    rows: list[dict], conv_col: str, total_cost: Decimal
) -> list[str]:
    """v1.1.21: сводка по типам критериев по ВСЕМ строкам (до обрезки топ-N).

    Корзины KEYWORD / AUTOTARGETING / прочие (всё остальное). Метрики те же,
    что в таблице: показы, клики, CTR, расход, доля от итога, конверсии
    (колонка conv_col — с учётом режима single/sum/primary), CPA.
    Пустые корзины пропускаются.
    """
    from directai_mcp.fmt import money, num, to_decimal

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        ctype = row.get("CriterionType")
        bucket = ctype if ctype in CRITERION_BUCKETS else "прочие"
        if bucket not in groups:
            groups[bucket] = []
            order.append(bucket)
        groups[bucket].append(row)
    lines = [f"Сводка по типам критериев (всего строк: {len(rows)}):"]
    for bucket in order:
        sub = groups[bucket]
        impr = sum((to_decimal(r.get("Impressions")) or Decimal(0) for r in sub),
                   Decimal(0))
        clicks = sum((to_decimal(r.get("Clicks")) or Decimal(0) for r in sub),
                     Decimal(0))
        cost = sum((to_decimal(r.get("Cost")) or Decimal(0) for r in sub),
                   Decimal(0))
        conv = sum((to_decimal(r.get(conv_col)) or Decimal(0) for r in sub),
                   Decimal(0))
        ctr = (clicks / impr * 100) if impr else None
        share = (cost / total_cost * 100) if total_cost else None
        cpa = (cost / conv) if conv else None
        cpa_text = f"CPA {money(cpa)} ₽." if cpa is not None else "CPA —."
        lines.append(
            f"- {CRITERION_LABELS[bucket]}: показы {num(impr, 0)}; "
            f"клики {num(clicks, 0)}; CTR {num(ctr)}%; "
            f"расход {money(cost)} ₽; доля {num(share)}%; "
            f"конверсии {num(conv, 0)}; {cpa_text}"
        )
    return lines


def _bucket_sums(
    rows: list[dict], conv_col: str
) -> tuple[dict[str, list[dict]], list[str]]:
    """v1.1.28: группировка строк по корзинам CriterionType (порядок встречи)."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        ctype = row.get("CriterionType")
        bucket = ctype if ctype in CRITERION_BUCKETS else "прочие"
        if bucket not in groups:
            groups[bucket] = []
            order.append(bucket)
        groups[bucket].append(row)
    return groups, order


def audience_aggregate(
    rows: list[dict], conv_col: str, total: dict
) -> list[str]:
    """v1.1.28: аудиторный агрегат кампании (stats_audiences).

    Разбивка по CriterionType по всем строкам; доли каждой корзины —
    от итога отчёта (total: тот же источник, что итоговая строка таблицы).
    Метрики: показы, клики, CTR, расход, конверсии (conv_col), CPA.
    """
    from directai_mcp.fmt import money, num, to_decimal

    groups, order = _bucket_sums(rows, conv_col)
    base_impr = total.get("Impressions") or Decimal(0)
    base_clicks = total.get("Clicks") or Decimal(0)
    base_cost = total.get("Cost") or Decimal(0)
    base_conv = total.get("Conversions") or Decimal(0)
    lines = [f"Агрегат по типам критериев (всего строк: {len(rows)}):"]
    for bucket in order:
        sub = groups[bucket]
        impr = sum((to_decimal(r.get("Impressions")) or Decimal(0) for r in sub),
                   Decimal(0))
        clicks = sum((to_decimal(r.get("Clicks")) or Decimal(0) for r in sub),
                     Decimal(0))
        cost = sum((to_decimal(r.get("Cost")) or Decimal(0) for r in sub),
                   Decimal(0))
        conv = sum((to_decimal(r.get(conv_col)) or Decimal(0) for r in sub),
                   Decimal(0))
        ctr = (clicks / impr * 100) if impr else None
        cpa = (cost / conv) if conv else None
        cpa_text = f"CPA {money(cpa)} ₽." if cpa is not None else "CPA —."
        lines.append(
            f"- {CRITERION_LABELS[bucket]}: показы {num(impr, 0)} "
            f"({num(impr / base_impr * 100) if base_impr else '—'}%); "
            f"клики {num(clicks, 0)} "
            f"({num(clicks / base_clicks * 100) if base_clicks else '—'}%); "
            f"CTR {num(ctr)}%; расход {money(cost)} ₽ "
            f"({num(cost / base_cost * 100) if base_cost else '—'}%); "
            f"конверсии {num(conv, 0)} "
            f"({num(conv / base_conv * 100) if base_conv else '—'}%); {cpa_text}"
        )
    if "RETARGETING" in groups:
        sub = groups["RETARGETING"]
        impr = sum((to_decimal(r.get("Impressions")) or Decimal(0) for r in sub),
                   Decimal(0))
        clicks = sum((to_decimal(r.get("Clicks")) or Decimal(0) for r in sub),
                     Decimal(0))
        cost = sum((to_decimal(r.get("Cost")) or Decimal(0) for r in sub),
                   Decimal(0))
        conv = sum((to_decimal(r.get(conv_col)) or Decimal(0) for r in sub),
                   Decimal(0))
        lines.append(
            f"Доля РЕТАРГЕТИНГА от итога: показы "
            f"{num(impr / base_impr * 100) if base_impr else '—'}%; клики "
            f"{num(clicks / base_clicks * 100) if base_clicks else '—'}%; "
            f"расход {num(cost / base_cost * 100) if base_cost else '—'}%; "
            f"конверсии {num(conv / base_conv * 100) if base_conv else '—'}%."
        )
    else:
        lines.append("Строк RETARGETING нет.")
    return lines


async def audience_target_map(
    ctx: Ctx, entries: list, campaign_ids: list[int]
) -> dict[tuple[str, int], tuple[int | None, str | None]]:
    """v1.1.28: (логин, Id условия) -> (RetargetingListId, название списка).

    Маппинг строк RETARGETING отчёта через AudienceTargets.get +
    названия через RetargetingLists.get. Ошибка API — пропуск кабинета.
    """
    from directai_mcp.api.errors import DirectError
    from directai_mcp.catalog.bids import retargeting_names
    from directai_mcp.catalog.common import chunk as _chunk

    out: dict[tuple[str, int], tuple[int | None, str | None]] = {}
    if not campaign_ids:
        return out
    client = ctx.direct()
    try:
        for entry in entries:
            try:
                targets: list[dict] = []
                for ids in _chunk(list(campaign_ids), 100):
                    targets.extend(
                        await client.get_all(
                            "audiencetargets",
                            {"SelectionCriteria": {"CampaignIds": ids},
                             "FieldNames": ["Id", "RetargetingListId"]},
                            entry.login,
                            "AudienceTargets",
                        )
                    )
            except DirectError:
                continue
            list_ids = sorted({
                int(t["RetargetingListId"]) for t in targets
                if isinstance(t, dict) and t.get("RetargetingListId") is not None
            })
            names = await retargeting_names(ctx, {entry.login: list_ids})
            for t in targets:
                if not isinstance(t, dict) or t.get("Id") is None:
                    continue
                lid = t.get("RetargetingListId")
                out[(entry.login, int(t["Id"]))] = (
                    int(lid) if lid is not None else None,
                    names.get(int(lid)) if lid is not None else None,
                )
    finally:
        await client.aclose()
    return out


def retargeting_detail_lines(
    rows: list[dict],
    conv_col: str,
    target_map: dict[tuple[str, int], tuple[int | None, str | None]],
) -> list[str]:
    """v1.1.28: строки по условиям RETARGETING (Id + название списка)."""
    from directai_mcp.fmt import money, num, to_decimal

    lines = ["Условия ретаргетинга:"]
    found = False
    for row in rows:
        if row.get("CriterionType") != "RETARGETING":
            continue
        found = True
        try:
            cid = int(row.get("CriterionId"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            cid = None
        login = row.get(ACCOUNT_COL)
        lid, lname = target_map.get((login, cid), (None, None)) \
            if cid is not None else (None, None)
        criterion = row.get("Criterion") or "—"
        if lname and lid is not None:
            lst = f"{lname} ({lid})"
        elif lid is not None:
            lst = str(lid)
        else:
            lst = "—"
        impr = to_decimal(row.get("Impressions")) or Decimal(0)
        clicks = to_decimal(row.get("Clicks")) or Decimal(0)
        cost = to_decimal(row.get("Cost")) or Decimal(0)
        conv = to_decimal(row.get(conv_col)) or Decimal(0)
        lines.append(
            f"- условие {cid if cid is not None else '—'} («{criterion}»): "
            f"список {lst}; показы {num(impr, 0)}; клики {num(clicks, 0)}; "
            f"расход {money(cost)} ₽; конверсии {num(conv, 0)}."
        )
    if not found:
        lines.append("Строк RETARGETING нет.")
    return lines


def _empty_note(excluded: int) -> str:
    return (
        f"Пустых строк исключено: {excluded} "
        "(без показов, кликов, расхода и конверсий; все строки: include_empty=true)."
    )


def goal_value_lines(
    columns: list[str],
    rows: list[dict],
    names: dict[str, str],
    counters: dict[str, int] | None = None,
    value_types: dict[str, str] | None = None,
    total_cost: Decimal | None = None,
) -> list[str]:
    """Единый формат ценности цели (v1.1.1, п.2): пара Conversions/Revenue.

    v1.1.29: CRM-цели подписываются «Выручка CRM» + ДРР (расход/выручка)
    и ROI ((выручка−расход)/расход) от итога расхода отчёта.
    """
    from directai_mcp.fmt import money, num, to_decimal

    conv: dict[str, Decimal] = {}
    total: dict[str, Decimal] = {}
    for col in columns:
        match = _GOAL_COL_RE.match(col)
        if not match:
            continue
        metric, gid = match.group(1), match.group(2)
        if metric not in ("Conversions", "Revenue"):
            continue
        bucket = conv if metric == "Conversions" else total
        for row in rows:
            parsed = to_decimal(row.get(col))
            if parsed is not None:
                bucket[gid] = bucket.get(gid, Decimal(0)) + parsed
    lines = []
    # v1.1.27: без данных Revenue строка «Суммарная: 0.00 ₽» выдумана —
    # показываем только цели с ценностью.
    for gid in sorted(set(total), key=int):
        count = conv.get(gid, Decimal(0))
        amount = total.get(gid, Decimal(0))
        if not count and not amount:
            continue
        label = goal_label(gid, names, counters)
        unit = f"{money(amount / count)} ₽" if count else "—"
        if (value_types or {}).get(gid) == "crm":
            tail = ""
            if total_cost and amount:
                drr = total_cost / amount * 100
                roi = (amount - total_cost) / total_cost * 100
                tail = f" · ДРР {num(drr)}% · ROI {num(roi)}%"
            lines.append(
                f"Выручка CRM цели {label}: {unit} · "
                f"Суммарная: {money(amount)} ₽ ({_count(count)} конв.){tail}"
            )
        else:
            lines.append(
                f"Ценность цели {label}: {unit} · "
                f"Суммарная: {money(amount)} ₽ ({_count(count)} конв.)"
            )
    return lines


def goal_ids_with_revenue(columns: list[str]) -> list[str]:
    """v1.1.29: id целей из сырых Revenue-колонок (до локализации)."""
    out = []
    for col in columns:
        match = _GOAL_COL_RE.match(str(col))
        if match and match.group(1) == "Revenue" and match.group(2) not in out:
            out.append(match.group(2))
    return out


def revenue_total_label(value_types: dict[str, str]) -> str | None:
    """v1.1.29: подпись агрегата Revenue (п.3: смешанные типы — не выводим)."""
    kinds = set(value_types.values())
    if kinds == {"crm"}:
        return "Выручка CRM"
    if kinds == {"conditional"} or not kinds:
        return "Ценность целей (условная)"
    return None


async def _value_info(
    ctx: Ctx,
    entries: list,
    campaign_ids: list[int],
    union_goals: list[str],
    rev_gids: list[str],
) -> tuple[dict[str, str], str]:
    """v1.1.29: типы ценности целей + источник (Метрика, fallback goals.toml).

    Пусто без Revenue-данных (лишних Campaigns.get/Метрики нет).
    """
    if not rev_gids:
        return {}, ""
    scope = sorted(
        set(rev_gids) | set(union_goals or []),
        key=lambda x: (0, int(x)) if x.isdigit() else (1, x),
    )
    from directai_mcp.catalog.metrika_goals import resolve_value_types

    return await resolve_value_types(ctx, entries, list(campaign_ids), scope)


def _value_note(value_types: dict[str, str], rev_gids: list[str]) -> str:
    """v1.1.29: trailing-нота под строками ценности (per-goal типы)."""
    if rev_gids and all(value_types.get(g) == "crm" for g in rev_gids):
        return "Выручка CRM — фактические суммы заказов (Метрика)."
    return f"{GOAL_VALUE_NOTE}."


def goals_with_data(columns: list[str], rows: list[dict]) -> int:
    """v1.1.27: число целей с ненулевыми конверсиями (для пометки дублей).

    Раньше проксировалась через value_lines; после v1.1.27 те показывают
    только цели с ценностью — считаем напрямую по Conversions-колонкам.
    Вызывать до localize_goal_columns (сырые имена `Conversions_<id>_*`).
    """
    from directai_mcp.fmt import to_decimal

    buckets: dict[str, Decimal] = {}
    for col in columns:
        match = _GOAL_COL_RE.match(col)
        if not match or match.group(1) != "Conversions":
            continue
        gid = match.group(2)
        for row in rows:
            parsed = to_decimal(row.get(col))
            if parsed is not None:
                buckets[gid] = buckets.get(gid, Decimal(0)) + parsed
    return sum(1 for total in buckets.values() if total != 0)


def _count(value: Decimal) -> str:
    return f"{int(value)}" if value == Decimal(int(value)) else f"{value.normalize()}"


def _auto_key(row: dict) -> tuple:
    """Ключ настроек автотаргетинга: (логин, id группы)."""
    try:
        gid = int(row.get("AdGroupId"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        gid = None
    return (row.get(ACCOUNT_COL), gid)


async def _autotargeting_for_stats(
    ctx: Ctx, entries: list[AccountEntry], rows: list[dict]
) -> tuple[dict, list[str]]:
    """v1.1.20: настройки автотаргетинга для AUTOTARGETING-строк.

    Один Keywords.get на кабинет по группам из строк. Возвращает
    ({(логин, группа): сводка}, ошибки).
    """
    from directai_mcp.catalog.keywords import autotargeting_by_group

    wanted: dict[str, set[int]] = {}
    for row in rows:
        if row.get("CriterionType") != "AUTOTARGETING":
            continue
        login, gid = _auto_key(row)
        if login and gid is not None:
            wanted.setdefault(login, set()).add(gid)
    found: dict = {}
    problems: list[str] = []
    for entry in entries:
        ids = sorted(wanted.get(entry.login, ()))
        if not ids:
            continue
        client = ctx.direct()
        try:
            try:
                by_group = await autotargeting_by_group(
                    client, entry.login, ids
                )
            except DirectError as e:
                problems.append(
                    f"⚠ {entry.login}: автотаргетинг: {e.human_message()}"
                )
                continue
            for gid, text in by_group.items():
                if text is not None:
                    found[(entry.login, gid)] = text
        finally:
            await client.aclose()
    return found, problems


def _localize_col(
    col: str, names: dict[str, str], counters: dict[str, int] | None = None
) -> str:
    """v1.1.29: одна goal-колонка -> 'Метрика Имя (id) модель' (или как есть)."""
    match = _GOAL_COL_RE.match(col)
    if match and (names.get(match.group(2)) or (counters or {}).get(match.group(2))):
        return (
            f"{match.group(1)}_{goal_label(match.group(2), names, counters)}"
            f"_{match.group(3)}"
        )
    return col


def localize_goal_columns(
    columns: list[str],
    rows: list[dict],
    names: dict[str, str],
    counters: dict[str, int] | None = None,
) -> tuple[list[str], list[dict]]:
    """Bare goal ids -> 'Name (id)' in dynamic report columns (goals.toml)."""
    mapping = {c: _localize_col(c, names, counters) for c in columns}
    if all(k == v for k, v in mapping.items()):
        return columns, rows
    return (
        [mapping[c] for c in columns],
        [{mapping.get(k, k): v for k, v in row.items()} for row in rows],
    )


def _revenue_note(
    ctx: Ctx,
    params: StatsParams,
    goals: list[str],
    value_info: tuple[dict[str, str], str] | None = None,
) -> str:
    """Тип ценности целей (v1.1.29: per goal, источник — Метрика/goals.toml).

    Без value_info — legacy-нота «условная» (v1.1.1–1.1.2, сужена
    DECISIONS v1.1.29 до conditional-целей и неизвестного).
    """
    if goals and value_info is not None:
        types, source = value_info
        parts = []
        for gid in goals:
            kind = types.get(gid, "conditional")
            tag = "выручка CRM" if kind == "crm" else "условная"
            parts.append(f"{goal_label(gid, ctx.settings.goal_names)} — {tag}")
        return f"ценность: {'; '.join(parts)} (типы: {source})"
    if goals:
        return "ценность целей: условная (из настроек цели, не выручка)"
    if isinstance(params, CustomParams):
        has_revenue = any("evenue" in f for f in params.field_names)
    else:
        has_revenue = bool(params.with_conversions)
    if has_revenue:
        return "ценность целей: условная (из настроек цели, не выручка)"
    return ""


def _cabinet(n: int) -> str:
    """v1.2.1: '1 кабинет / 2 кабинета / 5 кабинетов'."""
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} кабинет"
    if 2 <= n % 10 <= 4 and n % 100 not in (12, 13, 14):
        return f"{n} кабинета"
    return f"{n} кабинетов"


def _split_key_rows(
    rows: list[dict], goalless_logins: set[str]
) -> tuple[list[dict], list[dict]]:
    """v1.2.1: строки key-популяции и беcцельных кабинетов.

    Строка без ACCOUNT_COL или с неизвестным логином — в key-корзину
    (не теряем показанные данные из итога).
    """
    key, goal = [], []
    for r in rows:
        (goal if r.get(ACCOUNT_COL) in goalless_logins else key).append(r)
    return key, goal


def _col_matches_goal(col: str, prefix: str, gid: str) -> bool:
    """v1.2.1: колонка Conversions_/Revenue_ относится к цели gid.

    Сырые (Conversions_9_AUTO) и локализованные (Conversions_Имя (9)_AUTO)
    имена; голая Conversions/Revenue — False (популяция неопределена).
    """
    if col == prefix or not col.startswith(prefix + "_"):
        return False
    rest = col[len(prefix) + 1:]
    return rest == gid or rest.startswith(gid + "_") or f"({gid})" in rest


def _key_sums(
    key_rows: list[dict], primary_gid: str | None
) -> tuple[Decimal, Decimal]:
    """v1.2.1: Σ Conversions/Revenue key-строк; при primary — только его цель."""
    from directai_mcp.fmt import to_decimal as _td

    conv = rev = Decimal(0)
    for r in key_rows:
        for k, v in r.items():
            if k == "Conversions" or k.startswith("Conversions_"):
                if primary_gid and not _col_matches_goal(k, "Conversions",
                                                         primary_gid):
                    continue
                parsed = _td(v)
                if parsed is not None:
                    conv += parsed
            elif k == "Revenue" or k.startswith("Revenue_"):
                if primary_gid and not _col_matches_goal(k, "Revenue",
                                                         primary_gid):
                    continue
                parsed = _td(v)
                if parsed is not None:
                    rev += parsed
    return conv, rev


def _population_totals(
    ctx: Ctx,
    params: StatsParams,
    entries: list[AccountEntry],
    rows: list[dict],
    goals_by_login: dict[str, list[str]],
    goals_info: tuple[list[str], str] | None,
    totals_override: dict[str, Decimal] | None,
) -> tuple[dict[str, Decimal], str, list[str], bool, bool]:
    """v1.2.2: общий расчёт итогов key-популяции (_run_report/_run_custom).

    Возвращает (render_totals, totals_label, extra_lines, mixing,
    model_col). Правило v1.2.1: итог = Σ строк той же популяции; деньги —
    из беcцелевого агрегата (v1.1.27), конверсии/ценность — Σ key-строк.
    При mixing проставляет строкам поле 'Модель' (model_col=True).
    """
    union_goals = goals_info[0] if goals_info and goals_info[0] else []
    goalless_logins = {e.login for e in entries if not goals_by_login.get(e.login)}
    key_rows, bare_rows = _split_key_rows(rows, goalless_logins)
    mixing = bool(union_goals) and bool(key_rows) and bool(bare_rows)
    model = ",".join(effective_attribution(ctx, params))
    totals_label = "Итого"
    extra_totals_lines: list[str] = []
    model_col = False
    if mixing:
        for r in key_rows:
            r["Модель"] = model
        for r in bare_rows:
            r["Модель"] = "все цели (LC)"
        model_col = True
    if union_goals and key_rows:
        key_t = totals(key_rows)
        pg = params.primary_goal or None
        if pg:
            key_t["Conversions"], key_t["Revenue"] = _key_sums(key_rows, pg)
        if mixing:
            render_totals = key_t
            seen = {r.get(ACCOUNT_COL) for r in key_rows if r.get(ACCOUNT_COL)}
            keyed_entries = [e.login for e in entries
                             if goals_by_login.get(e.login)]
            n_key = len(seen & set(keyed_entries)) or len(keyed_entries)
            kind = ("key-целям" if (goals_info and goals_info[1].startswith("auto"))
                    else "целям")
            totals_label = (f"Итого по {kind} ({model}), {_cabinet(n_key)}")
            all_money = (totals_override if totals_override is not None
                         else totals(rows))
            extra_totals_lines.append(
                totals_line(all_money, label="Итого (все кабинеты)",
                            skip_conversions=True))
        elif totals_override is not None:
            render_totals = dict(totals_override)
            render_totals["Conversions"] = key_t["Conversions"]
            render_totals["Revenue"] = key_t["Revenue"]
        else:
            render_totals = key_t
    else:
        render_totals = (totals_override if totals_override is not None
                         else totals(rows))
    if union_goals and totals_override is not None:
        lc_conv = totals_override.get("Conversions") or Decimal(0)
        if lc_conv:
            extra_totals_lines.append(
                f"Конверсии (все цели, LC): {num(lc_conv, 0)} "
                "(другая популяция, не итог).")
    return render_totals, totals_label, extra_totals_lines, mixing, model_col


def _context(
    ctx: Ctx,
    name: str,
    entries: list[AccountEntry],
    params: StatsParams,
    goals_info: tuple[list[str], str] | None = None,
    value_info: tuple[dict[str, str], str] | None = None,
    goals_by_login: dict[str, list[str]] | None = None,
) -> str:
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    # v1.2.2: при >5 кабинетах — только счётчик, без перечисления логинов.
    accounts = (", ".join(e.login for e in entries) if len(entries) <= 5
                else _cabinet(len(entries)))
    if goals_info is None:
        goals = list(params.goals) or list(ctx.settings.goals)
        source = "explicit" if goals else "none"
    else:
        goals, source = goals_info
    revenue = _revenue_note(ctx, params, goals, value_info)
    suffix = f", {revenue}" if revenue else ""
    vat = "с НДС" if ctx.settings.include_vat else "без НДС"
    if goals:
        attribution = ",".join(effective_attribution(ctx, params))
        if source == "explicit":
            src_label = "указаны явно"
        elif source == "auto-all":
            src_label = "авто all: PriorityGoals+стратегия+архив+12"
        else:
            src_label = "авто key: PriorityGoals+стратегия"
        # v1.2.1: смешение популяций — фактическая модель покампанийно.
        mixed = ""
        if goals_by_login:
            keyed = [e.login for e in entries if goals_by_login.get(e.login)]
            bare = [e.login for e in entries if not goals_by_login.get(e.login)]
            if keyed and bare:
                if len(entries) <= 5:
                    def _gl(n: int) -> str:
                        if n % 10 == 1 and n % 100 != 11:
                            return f"{n} цель"
                        if 2 <= n % 10 <= 4 and n % 100 not in (12, 13, 14):
                            return f"{n} цели"
                        return f"{n} целей"
                    counts = "; ".join(
                        f"кабинет {login}: {_gl(len(goals_by_login[login]))}"
                        for login in keyed
                    )
                    counts += "; " + "; ".join(
                        f"кабинет {login}: без ключевых целей" for login in bare
                    )
                    mixed = (f" атрибуция: {attribution} (кабинеты: "
                             f"{', '.join(keyed)}); LC без целей (кабинеты: "
                             f"{', '.join(bare)}). {counts}.")
                else:
                    mixed = (f" атрибуция: {attribution}: {_cabinet(len(keyed))}; "
                             f"LC без целей: {_cabinet(len(bare))}.")
        text = (
            f"{mark}{name}: {accounts}, {_period_label(params)}, расход {vat}, "
            f"атрибуция: {attribution}, целей: {len(goals)} ({src_label}), "
            f"режим: {params.goals_mode}{suffix}."
        )
        if mixed:
            text += mixed
        # v1.1.19: дубли визитов между целями + primary.
        mode = conv_mode(len(goals), params.primary_goal)
        if mode == "sum":
            text += f" {DUP_NOTE.capitalize()}."
        elif mode == "primary" and params.primary_goal:
            label = goal_label(
                params.primary_goal,
                ctx.settings.goal_names,
                ctx.settings.goal_counters,
            )
            text += f" Строки — по цели {label}."
        return text
    # v1.1.8: без целей Reports игнорирует AttributionModels — фактически LC.
    return (
        f"{mark}{name}: {accounts}, {_period_label(params)}, расход {vat}, "
        f"атрибуция: LC (AUTO неприменима без целей; "
        f"у кампании нет ключевых целей){suffix}."
    )


async def _fetch_one(
    client: ReportsClient,
    entry: AccountEntry,
    definition: dict,
    sem: asyncio.Semaphore,
) -> tuple[AccountEntry, list[str], list[dict] | DirectError]:
    async with sem:
        try:
            columns, rows = await client.fetch(entry.login, definition)
            for row in rows:
                row[ACCOUNT_COL] = entry.login
            return entry, columns, rows
        except DirectError as e:
            return entry, [], e


async def _fetch_merged(
    client: ReportsClient,
    entry: AccountEntry,
    definitions: list[dict],
    sem: asyncio.Semaphore,
) -> tuple[AccountEntry, list[str], list[dict] | DirectError]:
    """Fetch goal chunks (Goals max 10) and merge rows on dimension key."""
    columns: list[str] = []
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for definition in definitions:
        _, cols, payload = await _fetch_one(client, entry, definition, sem)
        if isinstance(payload, DirectError):
            return entry, [], payload
        for col in cols:
            if col not in columns:
                columns.append(col)
        key_cols = [c for c in cols if not _is_value_col(c)]
        for row in payload:
            assert isinstance(row, dict)
            key = tuple(row.get(c) for c in key_cols)
            if key not in merged:
                merged[key] = dict(row)
                order.append(key)
            else:
                for col in cols:
                    if _is_value_col(col) and row.get(col) is not None:
                        merged[key][col] = row[col]
    return entry, columns, [merged[k] for k in order]


def _is_value_col(col: str) -> bool:
    if col in METRIC_FIELDS or col == ACCOUNT_COL:
        return True
    # v1.1.22: доли/средние — значения (от Goals не зависят, в ключ
    # склейки чанков не входят, иначе разобьют строки).
    if col in EXTRA_FIELDS:
        return True
    return col.startswith(
        ("Conversions", "CostPerConversion", "Revenue", "ConversionRate", "GoalsRoi")
    )


async def _effective_goals(
    ctx: Ctx, entries: list[AccountEntry], params: StatsParams
) -> tuple[dict[str, list[str]], str]:
    """Explicit goals win; else auto goals when conversions are wanted."""
    explicit = list(params.goals) or list(ctx.settings.goals)
    if explicit:
        return {e.login: list(explicit) for e in entries}, "explicit"
    if not params.with_conversions:
        return {e.login: [] for e in entries}, "none"
    resolved = await resolve_auto_goals(
        ctx, entries, list(params.campaign_ids), params.goals_mode
    )
    if not any(resolved.values()):
        return {e.login: [] for e in entries}, "none"
    return resolved, ("auto-all" if params.goals_mode == "all" else "auto-key")


async def _run_report(
    ctx: Ctx,
    params: StatsParams,
    name: str,
    report_type: str,
    dims: list[str],
    show_account: bool,
    definition_override: dict | None = None,
    keep_empty: bool = False,
) -> str:
    entries = ctx.accounts(params.account)
    if params.account in ("all", "active"):
        # Шаг 1.1-2 (Q5): ленивый discover/refresh кеша кабинетов.
        for note in await ensure_cache(ctx, params.account):
            ctx.notes.append(note)
    # v1.1.20: split — разбивка строк по MatchType (без склейки);
    # sum — MatchType не запрашиваем, сводит сам API («сумма по фразе»).
    if (name == "stats_keywords" and isinstance(params, KeywordsParams)
            and params.match_mode == "split" and "MatchType" not in dims):
        dims = [*dims, "MatchType"]
    goals_by_login: dict[str, list[str]] = {}
    if definition_override is not None:
        chunks_by_login = {e.login: [definition_override] for e in entries}
        goals_info: tuple[list[str], str] | None = None
    else:
        goals_by_login, source = await _effective_goals(ctx, entries, params)
        chunks_by_login = {
            e.login: [
                _definition(ctx, params, report_type, dims, chunk)
                for chunk in chunk_goals(goals_by_login[e.login])
            ]
            or [_definition(ctx, params, report_type, dims, [])]
            for e in entries
        }
        union = sorted({g for goals in goals_by_login.values() for g in goals})
        goals_info = (union, source)
        # v1.1.19: primary проверяем до запросов (fail fast, без API).
        check_primary(union, params.primary_goal)
    requested = params.limit or ctx.settings.max_rows

    client = ctx.reports()
    sem = asyncio.Semaphore(3)
    reconc_rows: list[dict] = []
    try:
        results = await asyncio.gather(
            *(
                _fetch_merged(client, e, chunks_by_login[e.login], sem)
                for e in entries
            )
        )
        # v1.1.3 п.4: итоги только из агрегата без целей (дубли визитов).
        # v1.1.4: для поисковых запросов агрегат всегда уровня кампании.
        # v1.1.27: агрегат всегда без измерений (иначе итог — сумма
        # пост-округлённых групп: Slot давал .95 вместо .94).
        agg_rows: list[dict] = []
        want_agg = bool(goals_info and goals_info[0]) or name == "stats_search_queries"
        no_group = params.model_copy(update={"group_by": "none"})
        if name == "stats_search_queries":
            # Поисковый отчёт всегда сыплет построчно: агрегат берём
            # из CAMPAIGN_PERFORMANCE_REPORT (1605/15176.23, а не 1549/15176.27).
            agg_dims, agg_report = [], "CAMPAIGN_PERFORMANCE_REPORT"
        else:
            agg_dims, agg_report = [], report_type
        if want_agg:
            # _fetch_one без склейки: _fetch_merged схлопнул бы всё в 1 строку.
            agg_results = await asyncio.gather(
                *(
                    _fetch_one(
                        client, e,
                        _definition(ctx, no_group, agg_report, agg_dims, []), sem,
                    )
                    for e in entries
                )
            )
            for _, _, payload in agg_results:
                if not isinstance(payload, DirectError):
                    agg_rows.extend(payload)
        # v1.1.26: сверка с агрегатом кампании (показы/клики/расход) —
        # только при фильтре по кампании; запрос бесплатен (Reports).
        if (name not in ("stats_summary", "stats_campaigns")
                and params.campaign_ids
                and name != "stats_search_queries"):
            date_range, date_extra = _dates(params)
            reconc_sel: dict = dict(date_extra)
            reconc_sel["Filter"] = [{
                "Field": "CampaignId",
                "Operator": "IN",
                "Values": [str(i) for i in params.campaign_ids],
            }]
            reconc_def = {
                "SelectionCriteria": reconc_sel,
                "FieldNames": ["CampaignId", "Impressions", "Clicks", "Cost"],
                "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
                "DateRangeType": date_range,
                "Format": "TSV",
                "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
            }
            reconc_results = await asyncio.gather(
                *(_fetch_one(client, e, reconc_def, sem) for e in entries)
            )
            for _, _, payload in reconc_results:
                if not isinstance(payload, DirectError):
                    reconc_rows.extend(payload)
    finally:
        await client.aclose()

    totals_override = totals(agg_rows) if agg_rows else None

    columns: list[str] = []
    rows: list[dict] = []
    errors: list[str] = []
    for entry, cols, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        if not columns:
            columns = list(cols)
        rows.extend(payload)
    # v1.1.26: группы кампании одним AdGroups.get (статус/тип/счёт).
    group_info: dict = {}
    group_order: list[int] = []
    if name == "stats_adgroups" and params.campaign_ids:
        group_info, group_order, group_errors = await _adgroups_for_stats(
            ctx, entries, params.campaign_ids)
        errors.extend(group_errors)
    # v1.1.26: нулевые Revenue-колонки не выводим («Выручка 0.00»);
    # вместе с ними уходят и строки ценности (дублируют Conversions).
    columns, rows, rev_dropped = drop_zero_revenue(columns, rows)
    # v1.1.29: типы ценности — только при живых Revenue-данных.
    rev_gids = [] if rev_dropped else goal_ids_with_revenue(columns)
    union_goals = goals_info[0] if goals_info and goals_info[0] else []
    value_types, vtype_source = await _value_info(
        ctx, entries, params.campaign_ids, union_goals, rev_gids)
    value_info = (value_types, vtype_source) if value_types else None
    cost_for_value = (
        (totals_override.get("Cost") if totals_override else None)
        or totals(rows).get("Cost")
    )
    value_lines = [] if rev_dropped else goal_value_lines(
        columns, rows, ctx.settings.goal_names, ctx.settings.goal_counters,
        value_types, cost_for_value,
    )
    # v1.1.29: хедер Revenue до локализации (сырые gid), ключи — в локализованные.
    raw_hmap = revenue_header_map(columns, value_types)
    # v1.1.27: пометка дублей — по целям с данными (сырые имена колонок).
    goals_data = goals_with_data(columns, rows)
    coverage = ""
    if (
        name == "stats_search_queries"
        and isinstance(params, SearchQueriesParams)
        and params.query_grouping == "query"
    ):
        rows, n_raw = group_query_rows(columns, rows)
        n_show = len(rows)
        if n_show != n_raw:
            coverage = (
                "Строки сгруппированы по тексту запроса: "
                f"{n_raw} сырых строк → {n_show} запросов."
            )
    if name == "stats_search_queries" and agg_rows:
        from directai_mcp.fmt import to_decimal as _to_dec

        agg_impr = sum((_to_dec(r.get("Impressions")) or Decimal(0) for r in agg_rows),
                       Decimal(0))
        agg_clk = sum((_to_dec(r.get("Clicks")) or Decimal(0) for r in agg_rows),
                      Decimal(0))
        got_impr = sum((_to_dec(r.get("Impressions")) or Decimal(0) for r in rows),
                       Decimal(0))
        got_clk = sum((_to_dec(r.get("Clicks")) or Decimal(0) for r in rows),
                      Decimal(0))
        coverage += (
            (" " if coverage else "")
            + f"Отчёт по запросам покрывает {int(got_impr)} из {int(agg_impr)} "
            f"показов; клики {int(got_clk)} из {int(agg_clk)}."
        )
    columns, rows = localize_goal_columns(
        columns, rows, ctx.settings.goal_names, ctx.settings.goal_counters
    )
    # v1.1.29: перевод ключей хедера Revenue в локализованные имена.
    header_map = {
        _localize_col(c, ctx.settings.goal_names, ctx.settings.goal_counters): v
        for c, v in raw_hmap.items()
    }
    # v1.1.24: свод по дню недели — из Date, до локализации (сырые имена
    # goal-колонок нужны агрегатору); итог остаётся из агрегата.
    if params.group_by == "weekday":
        columns, rows, wd_note = group_weekday_rows(
            columns, rows, _period_dates(params))
        if wd_note:
            coverage += ((" " if coverage else "") + wd_note)
    columns, rows = drop_roi(columns, rows)

    auto_map: dict = {}
    if name == "stats_keywords":
        auto_map, auto_errors = await _autotargeting_for_stats(
            ctx, entries, rows
        )
        errors.extend(auto_errors)

    for row in rows:
        if "Criterion" in row:
            if name == "stats_keywords":
                # v1.1.10: основная колонка — полная фраза с минусами как в API
                # (иначе кросс-минусованные выглядят дублями); чистая — отдельно.
                row["CleanCriterion"] = _clean_criterion(row["Criterion"], False)
                # v1.1.20: настройки автотаргетинга на строках AUTOTARGETING.
                if row.get("CriterionType") == "AUTOTARGETING":
                    text = auto_map.get(_auto_key(row))
                    row["Autotargeting"] = text
            else:
                row["Criterion"] = _clean_criterion(
                    row["Criterion"], params.short_phrases
                )
    if name == "stats_keywords" and "Criterion" in columns:
        # v1.1.20: CleanCriterion — только CSV/JSON (в inline/MD агенты
        # берут короткую фразу и теряют минусы).
        file_only = bool(params.output == "file" or params.save_as)
        if file_only:
            pos = columns.index("Criterion") + 1
            columns[pos:pos] = ["CleanCriterion"]
        if any(r.get("Autotargeting") is not None for r in rows):
            anchor = "MatchType" if "MatchType" in columns else "CriterionType"
            if anchor in columns:
                pos = columns.index(anchor) + 1
                columns[pos:pos] = ["Autotargeting"]
    if not keep_empty and definition_override is None and params.goals_mode != "all":
        columns = [c for c in columns if any(r.get(c) is not None for r in rows)]

    # v1.1.24: weekday сортируется Пн..Вс в агрегаторе (наследия нет).
    if params.group_by != "weekday":
        rows.sort(key=lambda r: to_float(r.get("Cost")) or -1.0, reverse=True)
    if params.include_empty:
        n_empty = count_empty_rows(rows)
        empty_note = (
            f"Пустые строки оставлены: {n_empty} "
            "(без показов, кликов, расхода и конверсий)." if n_empty else ""
        )
    else:
        rows, n_empty = drop_empty_rows(rows)
        empty_note = _empty_note(n_empty) if n_empty else ""
    # v1.1.26: статусы/типы групп и счёт (AdGroups.get, один запрос).
    group_count_note = ""
    if name == "stats_adgroups" and params.campaign_ids and group_order:
        if "AdGroupName" in columns:
            pos = columns.index("AdGroupName") + 1
            columns[pos:pos] = ["Status", "ServingStatus", "Type"]
        present: set[tuple] = set()
        for row in rows:
            try:
                gid = int(row.get("AdGroupId"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            key = (row.get(ACCOUNT_COL), gid)
            info = group_info.get(key)
            row["Status"] = (info or {}).get("Status")
            row["ServingStatus"] = (info or {}).get("ServingStatus")
            row["Type"] = (info or {}).get("Type")
            if not is_empty_row(row):
                present.add(key)
        if params.include_empty:
            have = {_row_gid(r) for r in rows}
            for (login, gid), info in group_info.items():
                if (login, gid) in have:
                    continue
                rows.append({
                    ACCOUNT_COL: login,
                    "CampaignId": info.get("CampaignId"),
                    "CampaignName": None,
                    "AdGroupId": gid,
                    "AdGroupName": info.get("Name"),
                    "Status": info.get("Status"),
                    "ServingStatus": info.get("ServingStatus"),
                    "Type": info.get("Type"),
                    "Impressions": "0",
                    "Clicks": "0",
                    "Cost": "0.00",
                })
        group_count_note = (
            f"Групп в кампании: {len(group_order)}, "
            f"со статистикой: {len(present)}."
        )
    # v1.1.24: заливка календаря — после drop (нулевые строки не выкидывать),
    # сортировка day не меняется (наследие); достройка weekday — тоже здесь.
    if params.group_by == "weekday" and "Weekday" in columns:
        rows = complete_weekday_rows(columns, rows, _period_dates(params))
    if (params.group_by == "day" and params.fill_calendar
            and "Date" in columns):
        rows, fill_note = fill_calendar_rows(columns, rows, params)
        if fill_note:
            empty_note += ((" " if empty_note else "") + fill_note)
    # v1.1.9: итоги для долей/топа — из сырых строк ДО производных колонок
    # (иначе totals() посчитал бы Conversions дважды: сырые + вычисленная).
    # v1.2.2: общий расчёт key-популяции (без изменения поведения v1.2.1).
    (render_totals, totals_label, extra_totals_lines, _mixing,
     model_col) = _population_totals(
        ctx, params, entries, rows, goals_by_login, goals_info,
        totals_override)
    if model_col and "Модель" not in columns:
        columns.insert(0, "Модель")
    total_cost = render_totals.get("Cost") or Decimal(0)
    top_line: str | None = None
    if (
        name != "stats_summary"
        and params.limit
        and len(rows) > params.limit
    ):
        from directai_mcp.fmt import top_totals_line as _top_line

        top_line = _top_line(
            params.limit, totals(rows[: params.limit]), total_cost
        )
    include_share = name != "stats_summary"
    # v1.1.19: режим конверсий строк (single/sum/primary) + имена колонок.
    union_goals = goals_info[0] if goals_info and goals_info[0] else []
    mode = conv_mode(len(union_goals), params.primary_goal)
    conv_col, cpa_col, cr_col = derived_names(mode)
    primary_gid = params.primary_goal if mode == "primary" else None
    add_derived(rows, total_cost, include_share,
                conv_col, cpa_col, cr_col, primary_gid)
    if name == "stats_placements" and isinstance(params, PlacementsParams):
        from directai_mcp.fmt import to_decimal as _to_dec

        n_flag = 0
        for row in rows:
            impr = _to_dec(row.get("Impressions")) or Decimal(0)
            clicks = _to_dec(row.get("Clicks")) or Decimal(0)
            ctr = (clicks / impr * 100) if impr else Decimal(0)
            if clicks >= params.anomaly_min_clicks and ctr >= Decimal(
                str(params.anomaly_min_ctr)
            ):
                row["Flag"] = "⚠ аномальный CTR"
                n_flag += 1
            else:
                row.pop("Flag", None)
        if n_flag:
            columns = [*columns, "Flag"]
    columns = rebuild_columns(columns, include_share,
                                conv_col, cpa_col, cr_col)
    columns = prune_derived(columns, rows, conv_col)
    add_rank(rows)
    dims_first = [c for c in columns if c != ACCOUNT_COL]
    columns = ["#"] + columns
    display_cols = (
        ["#"] + ([ACCOUNT_COL] if len(entries) > 1 else [])
        + [c for c in dims_first if c != "#"]
    )

    context = _context(ctx, name, entries, params, goals_info, value_info,
                       goals_by_login=goals_by_login)
    # v1.1.21: сводка по типам критериев — по всем строкам (до обрезки
    # топ-N), перед таблицей фраз: автотаргетинг виден при любом limit.
    if name == "stats_keywords" and rows:
        context += "\n\n" + "\n".join(
            criterion_summary(rows, conv_col, total_cost)
        )
    # v1.1.28: аудиторный агрегат кампании + строки по условиям
    # RETARGETING (маппинг CriterionId через AudienceTargets).
    if name == "stats_audiences" and rows:
        target_map = await audience_target_map(
            ctx, entries, params.campaign_ids)
        context += "\n\n" + "\n".join(
            audience_aggregate(rows, conv_col, render_totals)
        )
        context += "\n\n" + "\n".join(
            retargeting_detail_lines(rows, conv_col, target_map)
        )
    if group_count_note:
        context += f"\n{group_count_note}"
    # v1.1.26: сверка с итогом кампании по числам вместо оговорки
    # про копейки; несравнимо/ошибка — молча ничего (§6).
    if name == "stats_search_queries":
        reconc_src: list[dict] | None = agg_rows or None
    elif (name not in ("stats_summary", "stats_campaigns")
            and params.campaign_ids):
        reconc_src = reconc_rows or None
    else:
        reconc_src = None
    out = finalize(
        ctx,
        context,
        name,
        display_cols,
        rows,
        requested,
        params.save_as,
        errors,
        with_totals=True,
        output=params.output,
        format=params.format,
        account=params.account,
        single_goal=bool(goals_info and goals_info[0]) and len(goals_info[0]) == 1,
        totals_override=render_totals,
        totals_suffix=_reconcile_note(render_totals, reconc_src),
        top_line=top_line,
        with_version=True,
        header_map=header_map,
        value_map=slot_value_map(columns),
        totals_label=totals_label,
        extra_totals_lines=extra_totals_lines,
        # v1.1.29: смешанные типы ценности — агрегат Revenue не выводим.
        revenue_label=revenue_total_label(
            {g: value_types.get(g, "conditional") for g in rev_gids}),
    )
    if value_lines:
        out += "\n\n" + "\n".join(value_lines)
        out += f"\n{_value_note(value_types, rev_gids)}"
    # v1.1.9 п.3: дубли возможны, только если данные есть ≥2 целей.
    if goals_data >= 2:
        out += "\nСуммы по целям: возможны дубли визитов."
    if coverage:
        out += f"\n{coverage}"
    if empty_note:
        out += f"\n{empty_note}"
    # v1.1.11: версия запущенного процесса — самая последняя строка.
    return out + f"\n\n{version_footer()}"


def _register(
    name: str,
    summary: str,
    keywords: tuple[str, ...],
    report_type: str,
    dims: list[str],
    model: type[StatsParams] = StatsParams,
):
    @action(name, "read", summary, keywords, model)
    async def _run(ctx: Ctx, params: BaseModel) -> str:
        assert isinstance(params, StatsParams)
        if isinstance(params, CustomParams):
            return await _run_custom(ctx, params)
        return await _run_report(
            ctx, params, name, report_type, dims, show_account=True
        )

    return _run


async def _run_custom(ctx: Ctx, params: CustomParams) -> str:
    if not params.field_names:
        raise ValueError("field_names must not be empty for stats_custom")
    entries = ctx.accounts(params.account)
    if params.account in ("all", "active"):
        # Шаг 1.1-2 (Q5): ленивый discover/refresh кеша кабинетов.
        for note in await ensure_cache(ctx, params.account):
            ctx.notes.append(note)
    # v1.1.7: единый режим целей со всеми stats_* — только _effective_goals
    # (явные > авто key > нет). Сниффинг field_names на Conversions/Revenue
    # удалён: with_conversions=false больше не тянет авто-цели.
    goals_by_login, source = await _effective_goals(ctx, entries, params)
    union = sorted({g for goals in goals_by_login.values() for g in goals})
    goals_info: tuple[list[str], str] = (union, source if union else "none")
    # v1.1.19: primary проверяем до запросов; режим имён колонок.
    check_primary(union, params.primary_goal)
    _mode = conv_mode(len(union), params.primary_goal)
    conv_col, cpa_col, cr_col = derived_names(_mode)
    primary_gid = params.primary_goal if _mode == "primary" else None

    async def _custom_definitions(
        entry_login: str, with_goals: bool = True, with_dims: bool = True
    ) -> list[dict]:
        date_range, extra = _dates(params)
        selection: dict = dict(extra)
        filters = list(params.filters)
        if params.campaign_ids:
            filters.append(
                {
                    "Field": "CampaignId",
                    "Operator": "IN",
                    "Values": [str(i) for i in params.campaign_ids],
                }
            )
        if filters:
            selection["Filter"] = filters
        dim_fields: list[str] = []
        if with_dims:
            for dim in params.group_by:
                if dim in GROUP_DATE_FIELD:
                    dim_fields.append(GROUP_DATE_FIELD[dim])
                elif dim not in dim_fields and dim not in params.field_names:
                    dim_fields.append(dim)
        base: dict = {
            "SelectionCriteria": selection,
            "FieldNames": dim_fields + list(params.field_names),
            "ReportType": params.report_type,
            "DateRangeType": date_range,
            "Format": "TSV",
            "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
        }
        if not with_goals:
            return [base]
        chunks = chunk_goals(goals_by_login[entry_login]) or [[]]
        definitions = []
        for chunk in chunks:
            definition = dict(base)
            if chunk:
                definition["Goals"] = chunk
                definition["AttributionModels"] = effective_attribution(ctx, params)
            definitions.append(definition)
        return definitions

    requested = params.limit or ctx.settings.max_rows
    client = ctx.reports()
    sem = asyncio.Semaphore(3)

    async def _fetch_custom(entry: AccountEntry, with_goals: bool = True):
        definitions = await _custom_definitions(entry.login, with_goals)
        return await _fetch_merged(client, entry, definitions, sem)

    async def _fetch_custom_agg(entry: AccountEntry):
        # v1.1.6: итог без измерений (иначе сумма строк ≠ агрегат на копейки).
        definitions = await _custom_definitions(entry.login, False, False)
        _, _, payload = await _fetch_one(client, entry, definitions[0], sem)
        return entry, [], payload

    try:
        results = await asyncio.gather(*(_fetch_custom(e) for e in entries))
        # v1.1.3 п.4: итоги только из агрегата без целей.
        # v1.1.6: при group_by агрегат нужен и без целей (сумма ≠ агрегат).
        # _fetch_one без склейки: merged схлопнул бы строки в одну.
        agg_rows: list[dict] = []
        if (goals_info and goals_info[0]) or params.group_by:
            agg_results = await asyncio.gather(
                *(_fetch_custom_agg(e) for e in entries)
            )
            for _, _, payload in agg_results:
                if not isinstance(payload, DirectError):
                    agg_rows.extend(payload)
    finally:
        await client.aclose()

    totals_override = totals(agg_rows) if agg_rows else None

    columns: list[str] = []
    rows: list[dict] = []
    errors: list[str] = []
    for entry, cols, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        if not columns:
            columns = list(cols)
        rows.extend(payload)
    # v1.1.26: нулевые Revenue-колонки не выводим (как в _run_report).
    columns, rows, rev_dropped = drop_zero_revenue(columns, rows)
    # v1.1.29: типы ценности — только при живых Revenue-данных.
    rev_gids = [] if rev_dropped else goal_ids_with_revenue(columns)
    union_goals = goals_info[0] if goals_info and goals_info[0] else []
    value_types, vtype_source = await _value_info(
        ctx, entries, params.campaign_ids, union_goals, rev_gids)
    value_info = (value_types, vtype_source) if value_types else None
    cost_for_value = (
        (totals_override.get("Cost") if totals_override else None)
        or totals(rows).get("Cost")
    )
    value_lines = [] if rev_dropped else goal_value_lines(
        columns, rows, ctx.settings.goal_names, ctx.settings.goal_counters,
        value_types, cost_for_value,
    )
    # v1.1.29: хедер Revenue до локализации (сырые gid).
    raw_hmap = revenue_header_map(columns, value_types)
    # v1.1.27: пометка дублей — по целям с данными (сырые имена колонок).
    goals_data = goals_with_data(columns, rows)
    columns, rows = localize_goal_columns(
        columns, rows, ctx.settings.goal_names, ctx.settings.goal_counters
    )
    # v1.1.29: перевод ключей хедера Revenue в локализованные имена.
    header_map = {
        _localize_col(c, ctx.settings.goal_names, ctx.settings.goal_counters): v
        for c, v in raw_hmap.items()
    }
    columns, rows = drop_roi(columns, rows)
    for row in rows:
        if "Criterion" in row:
            row["Criterion"] = _clean_criterion(row["Criterion"], params.short_phrases)
    rows.sort(key=lambda r: to_float(r.get("Cost")) or -1.0, reverse=True)
    if params.include_empty:
        n_empty = count_empty_rows(rows)
        empty_note = (
            f"Пустые строки оставлены: {n_empty} "
            "(без показов, кликов, расхода и конверсий)." if n_empty else ""
        )
    else:
        rows, n_empty = drop_empty_rows(rows)
        empty_note = _empty_note(n_empty) if n_empty else ""
    # v1.1.9: итоги из сырых строк ДО производных (без двойного счёта).
    # v1.2.2: общий расчёт key-популяции (как в _run_report).
    (render_totals, totals_label, extra_totals_lines, _mixing,
     model_col) = _population_totals(
        ctx, params, entries, rows, goals_by_login, goals_info,
        totals_override)
    if model_col and "Модель" not in columns:
        columns.insert(0, "Модель")
    total_cost = render_totals.get("Cost") or Decimal(0)
    top_line: str | None = None
    if params.limit and len(rows) > params.limit:
        from directai_mcp.fmt import top_totals_line as _top_line

        top_line = _top_line(
            params.limit, totals(rows[: params.limit]), total_cost
        )
    include_share = bool(params.group_by)
    add_derived(rows, total_cost, include_share,
                conv_col, cpa_col, cr_col, primary_gid)
    columns = rebuild_columns(columns, include_share,
                                conv_col, cpa_col, cr_col)
    columns = prune_derived(columns, rows, conv_col)
    add_rank(rows)
    dims_first = [c for c in columns if c != ACCOUNT_COL]
    columns = ["#"] + columns
    display_cols = (
        ["#"] + ([ACCOUNT_COL] if len(entries) > 1 else [])
        + [c for c in dims_first if c not in ("#", ACCOUNT_COL)]
    )
    context = _context(ctx, "stats_custom", entries, params, goals_info,
                       value_info, goals_by_login=goals_by_login)
    out = finalize(
        ctx,
        context,
        "stats_custom",
        display_cols,
        rows,
        requested,
        params.save_as,
        errors,
        with_totals=True,
        output=params.output,
        format=params.format,
        account=params.account,
        single_goal=bool(goals_info and goals_info[0]) and len(goals_info[0]) == 1,
        totals_override=render_totals,
        totals_suffix=None,
        top_line=top_line,
        with_version=True,
        header_map=header_map,
        value_map=slot_value_map(columns),
        # v1.1.29: смешанные типы ценности — агрегат Revenue не выводим.
        revenue_label=revenue_total_label(
            {g: value_types.get(g, "conditional") for g in rev_gids}),
        totals_label=totals_label,
        extra_totals_lines=extra_totals_lines,
    )
    if value_lines:
        out += "\n\n" + "\n".join(value_lines)
        out += f"\n{_value_note(value_types, rev_gids)}"
    # v1.1.9 п.3: дубли возможны, только если данные есть ≥2 целей.
    if goals_data >= 2:
        out += "\nСуммы по целям: возможны дубли визитов."
    if empty_note:
        out += f"\n{empty_note}"
    # v1.1.11: версия запущенного процесса — самая последняя строка.
    return out + f"\n\n{version_footer()}"


# v1.1.22: метрики сравнения (порядок строк вывода).
# kind: count — целые; money — ₽ 2 знака; rate — % 2 знака, Δ в п.п. + Δ%;
# pos — средняя позиция, только Δ абс. (позиция — порядковая шкала).
COMPARE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("Impressions", "Показы", "count"),
    ("Clicks", "Клики", "count"),
    ("CTR", "CTR, %", "rate"),
    ("Cost", "Расход, ₽", "money"),
    ("CPC", "CPC, ₽", "money"),
    ("Conversions", "Конверсии (достижения)", "count"),
    ("CR", "CR, %", "rate"),
    ("CPA", "CPA, ₽", "money"),
    ("BounceRate", "Отказы, %", "rate"),
    ("AvgImpressionPosition", "Ср. позиция показов", "pos"),
    ("AvgClickPosition", "Ср. позиция кликов", "pos"),
)


def _compare_dataset(
    agg_rows: list[dict], conv_col_present: bool
) -> dict | None:
    """v1.1.22: метрики периода из агрегатных строк (сырые Decimal, без округлений).

    None — данных нет (пустой ответ или ошибка уже зафиксирована вызывающим).
    Доли/средние (BounceRate, позиции) — только значение агрегата, никакого
    суммирования/усреднения по строкам.
    """
    from directai_mcp.fmt import to_decimal as _dec

    if not agg_rows:
        return None
    base = totals(agg_rows)
    impr = base.get("Impressions") or Decimal(0)
    clicks = base.get("Clicks") or Decimal(0)
    cost = base.get("Cost") or Decimal(0)
    conv = base.get("Conversions") or Decimal(0)
    first = agg_rows[0]
    out: dict = {
        "Impressions": impr,
        "Clicks": clicks,
        "Cost": cost,
        "CTR": (clicks / impr * 100) if impr else None,
        "CPC": (cost / clicks) if clicks else None,
        "BounceRate": _dec(first.get("BounceRate")),
        "AvgImpressionPosition": _dec(first.get("AvgImpressionPosition")),
        "AvgClickPosition": _dec(first.get("AvgClickPosition")),
    }
    if conv_col_present:
        out["Conversions"] = conv
        out["CR"] = (conv / clicks * 100) if clicks else None
        out["CPA"] = (cost / conv) if conv else None
    else:
        out["Conversions"] = None
        out["CR"] = None
        out["CPA"] = None
    return out


def _has_conv_cols(rows: list[dict]) -> bool:
    """Есть ли хоть одна непустая конверсионная колонка (иначе «—», а не 0)."""
    for row in rows:
        for key, val in row.items():
            if val is None:
                continue
            if key == "Conversions" or str(key).startswith(
                ("Conversions_", "Revenue", "CostPerConversion")
            ):
                return True
    return False


def _fmt_ab(kind: str, value: Decimal | None) -> str:
    """v1.1.22: значение A/B, единая точность 2 знака (целые — 0 знаков)."""
    from directai_mcp.fmt import money as _money
    from directai_mcp.fmt import num as _num

    if value is None:
        return "—"
    if kind == "count":
        return _num(value, 0)
    if kind == "money":
        return _money(value)
    return _num(value)


def _fmt_delta(kind: str, delta: Decimal | None) -> str:
    """v1.1.22: Δ абс. от неокруглённых значений; у долей — в п.п."""
    from directai_mcp.fmt import money as _money
    from directai_mcp.fmt import num as _num

    if delta is None:
        return "—"
    if kind == "count":
        base = _num(delta, 0)
    elif kind == "money":
        base = _money(delta)
    else:
        base = _num(delta)
    text = f"+{base}" if delta > 0 else base
    return f"{text} п.п." if kind == "rate" else text


def _fmt_pct(delta_pct: Decimal | None) -> str:
    """v1.1.22: Δ% от неокруглённых значений (A=0 → «—», деление запрещено)."""
    from directai_mcp.fmt import num as _num

    if delta_pct is None:
        return "—"
    base = _num(delta_pct)
    signed = f"+{base}" if delta_pct > 0 else base
    return f"{signed}%"


def _compare_context(
    ctx: Ctx,
    entry: AccountEntry,
    params: CompareParams,
    goals: list[str],
    source: str,
) -> str:
    """v1.1.22: шапка с единым режимом целей и атрибуции для обоих периодов."""
    from datetime import date as _date

    def _label(value: str) -> str:
        try:
            return _fmt_date(_date.fromisoformat(value))
        except ValueError:
            return value

    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    vat = "с НДС" if ctx.settings.include_vat else "без НДС"
    # _revenue_note ждёт StatsParams — у CompareParams те же поля (duck typing).
    revenue = _revenue_note(ctx, params, goals)  # type: ignore[arg-type]
    suffix = f", {revenue}" if revenue else ""
    group = {"none": "итог", "device": "разрез: устройства",
             "region": "разрез: регионы"}[params.group_by]
    head = (
        f"{mark}stats_compare: {entry.login}, "
        f"A: {_label(params.period_a_from)}–{_label(params.period_a_to)}, "
        f"B: {_label(params.period_b_from)}–{_label(params.period_b_to)}, "
        f"расход {vat}, {group}{suffix}."
    )
    if goals:
        attribution = ",".join(effective_attribution(ctx, params))  # type: ignore[arg-type]
        src_label = {"explicit": "указаны явно",
                     "auto-all": "авто all: PriorityGoals+стратегия+архив+12"}.get(
            source, "авто key: PriorityGoals+стратегия")
        head += (f" атрибуция: {attribution}, целей: {len(goals)} "
                 f"({src_label}), режим: {params.goals_mode}.")
        mode = conv_mode(len(goals), params.primary_goal)
        if mode == "sum":
            head += f" {DUP_NOTE.capitalize()}."
        elif mode == "primary" and params.primary_goal:
            label = goal_label(params.primary_goal, ctx.settings.goal_names,
                               ctx.settings.goal_counters)
            head += f" Строки — по цели {label}."
    else:
        # v1.1.8: без целей Reports игнорирует AttributionModels — фактически LC.
        head += (" атрибуция: LC (AUTO неприменима без целей; "
                 "у кампании нет ключевых целей).")
    return head


@action(
    "stats_compare",
    "read",
    "Сравнение двух периодов: A, B, Δ абс., Δ% (неделя к неделе)",
    ("сравнение", "сравни", "динамика", "периоды", "неделя к неделе",
     "изменения", "прирост", "падение", "compare", "динамика"),
    CompareParams,
)
async def _run_compare(ctx: Ctx, params: BaseModel) -> str:
    """v1.1.22: один вызов вместо двух stats_* с ручной арифметикой."""
    assert isinstance(params, CompareParams)
    entries = ctx.accounts(params.account)
    if params.account in ("all", "active"):
        for note in await ensure_cache(ctx, params.account):
            ctx.notes.append(note)
    if len(entries) != 1:
        raise ValueError(
            "stats_compare: нужен ровно один кабинет "
            "(дельты между кабинетами не считаем)"
        )
    entry = entries[0]
    for start, end in ((params.period_a_from, params.period_a_to),
                       (params.period_b_from, params.period_b_to)):
        if start > end:
            raise ValueError("period start is after period end")
    # _effective_goals/_context-хелперы duck-typed (нужны goals,
    # with_conversions, campaign_ids, goals_mode, attribution).
    goals_by_login, source = await _effective_goals(ctx, entries, params)  # type: ignore[arg-type]
    union = sorted({g for goals in goals_by_login.values() for g in goals})
    check_primary(union, params.primary_goal)

    dims = list(COMPARE_GROUP_DIMS.get(params.group_by, []))
    report_type = "CUSTOM_REPORT" if dims else "CAMPAIGN_PERFORMANCE_REPORT"

    def _definition_for(
        date_from: str, date_to: str, goals_chunk: list[str], with_dims: bool
    ) -> dict:
        selection: dict = {"DateFrom": date_from, "DateTo": date_to}
        if params.campaign_ids:
            selection["Filter"] = [{
                "Field": "CampaignId",
                "Operator": "IN",
                "Values": [str(i) for i in params.campaign_ids],
            }]
        fields = (list(dims) if with_dims else []) + METRIC_FIELDS + EXTRA_FIELDS
        if params.with_conversions or goals_chunk:
            fields += CONV_FIELDS
        definition: dict = {
            "SelectionCriteria": selection,
            "FieldNames": fields,
            "ReportType": report_type,
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
        }
        if goals_chunk:
            definition["Goals"] = list(goals_chunk)
            definition["AttributionModels"] = effective_attribution(ctx, params)  # type: ignore[arg-type]
        return definition

    chunks = chunk_goals(goals_by_login[entry.login]) or [[]]
    periods = ((params.period_a_from, params.period_a_to),
               (params.period_b_from, params.period_b_to))
    client = ctx.reports()
    sem = asyncio.Semaphore(3)
    errors: list[str] = []
    datasets: list[dict | None] = []
    group_rows: list[list[dict]] = []
    try:
        for date_from, date_to in periods:
            detail = await _fetch_merged(
                client, entry,
                [_definition_for(date_from, date_to, ch, True) for ch in chunks],
                sem,
            )
            _, _, agg_payload = await _fetch_one(
                client, entry, _definition_for(date_from, date_to, [], False), sem
            )
            if isinstance(detail[2], DirectError):
                errors.append(f"⚠ {entry.login}: {detail[2].human_message()}")
                detail_rows: list[dict] = []
            else:
                detail_rows = [r for r in detail[2] if isinstance(r, dict)]
            if isinstance(agg_payload, DirectError):
                errors.append(f"⚠ {entry.login}: {agg_payload.human_message()}")
                agg_rows: list[dict] = []
            else:
                agg_rows = [r for r in agg_payload if isinstance(r, dict)]
            # v1.1.22: итоги долей/средних — только из агрегата (без целей
            # и измерений); при его отсутствии — из детальных строк нельзя
            # (суммирование/усреднение запрещено) → метрики отсутствуют.
            if agg_rows:
                datasets.append(
                    _compare_dataset(agg_rows, _has_conv_cols(agg_rows)))
            elif not dims and detail_rows:
                datasets.append(
                    _compare_dataset(detail_rows,
                                     _has_conv_cols(detail_rows)))
            else:
                datasets.append(None)
            group_rows.append(detail_rows)
    finally:
        await client.aclose()

    data_a, data_b = datasets
    columns = ["Метрика", "A", "B", "Δ", "Δ%"]
    if dims:
        columns = ["Группа", *columns]
    rows: list[dict] = []
    if dims:
        keys: dict[tuple, dict] = {}
        order: list[tuple] = []
        for period_rows in group_rows:
            for row in period_rows:
                key = tuple(row.get(d) for d in dims)
                if key not in keys:
                    keys[key] = {}
                    order.append(key)
        per_a = _group_datasets(group_rows[0], dims)
        per_b = _group_datasets(group_rows[1], dims)
        # v1.1.22: первая строка — Итого строго из агрегата (доли/средние
        # нельзя складывать/усреднять по группам), далее группы.
        rows.extend(_metric_rows("Итого", data_a, data_b))
        # Сортировка групп по расходу B (убывание).
        order.sort(
            key=lambda k: ((per_b.get(k) or {}).get("Cost") or Decimal(0)),
            reverse=True,
        )
        for key in order:
            label = _group_label(params.group_by, per_a, per_b, key, dims)
            rows.extend(_metric_rows(label, per_a.get(key), per_b.get(key)))
    else:
        rows.extend(_metric_rows(None, data_a, data_b))

    context = _compare_context(ctx, entry, params, union, source)
    out = finalize(
        ctx, context, "stats_compare", columns, rows,
        ctx.settings.max_rows, params.save_as, errors,
        with_totals=False, output=params.output, format=params.format,
        account=params.account, with_version=True,
    )
    # v1.1.11: версия запущенного процесса — самая последняя строка.
    return out + f"\n\n{version_footer()}"


def _group_datasets(
    period_rows: list[dict], dims: list[str]
) -> dict[tuple, dict]:
    """v1.1.22: метрики периода по группам; доли/средние — значение строки API."""
    from directai_mcp.fmt import to_decimal as _dec

    grouped: dict[tuple, list[dict]] = {}
    for row in period_rows:
        grouped.setdefault(tuple(row.get(d) for d in dims), []).append(row)
    out: dict[tuple, dict] = {}
    for key, sub in grouped.items():
        base = totals(sub)
        impr = base.get("Impressions") or Decimal(0)
        clicks = base.get("Clicks") or Decimal(0)
        cost = base.get("Cost") or Decimal(0)
        conv = base.get("Conversions") or Decimal(0)
        first = sub[0]
        metrics: dict = {
            "Impressions": impr,
            "Clicks": clicks,
            "Cost": cost,
            "CTR": (clicks / impr * 100) if impr else None,
            "CPC": (cost / clicks) if clicks else None,
            "BounceRate": _dec(first.get("BounceRate")),
            "AvgImpressionPosition": _dec(first.get("AvgImpressionPosition")),
            "AvgClickPosition": _dec(first.get("AvgClickPosition")),
            "_label_row": first,
        }
        if _has_conv_cols(sub):
            metrics["Conversions"] = conv
            metrics["CR"] = (conv / clicks * 100) if clicks else None
            metrics["CPA"] = (cost / conv) if conv else None
        else:
            metrics["Conversions"] = None
            metrics["CR"] = None
            metrics["CPA"] = None
        out[key] = metrics
    return out


def _group_label(
    group_by: str, per_a: dict, per_b: dict, key: tuple, dims: list[str]
) -> str:
    """v1.1.22: подпись группы (регионы — имя; устройства — значение)."""
    if group_by == "region":
        row = (per_a.get(key) or per_b.get(key) or {}).get("_label_row") or {}
        name = row.get("LocationOfPresenceName") or "—"
        gid = row.get("LocationOfPresenceId")
        return f"{name} ({gid})" if gid else str(name)
    return str(key[0] if key else "—")


def _metric_rows(
    group: str | None, data_a: dict | None, data_b: dict | None
) -> list[dict]:
    """v1.1.22: строки A/B/Δ/Δ% по всем метрикам (Δ% только при A≠0)."""
    rows: list[dict] = []
    for key, label, kind in COMPARE_METRICS:
        a = data_a.get(key) if data_a else None
        b = data_b.get(key) if data_b else None
        if a is None or b is None:
            delta = None
            pct = None
        elif kind == "pos":
            delta = b - a
            pct = None
        else:
            delta = b - a
            pct = (delta / a * 100) if a != 0 else None
        row = {
            "Метрика": label,
            "A": _fmt_ab(kind, a),
            "B": _fmt_ab(kind, b),
            "Δ": _fmt_delta(kind, delta),
            "Δ%": "—" if kind == "pos" else _fmt_pct(pct),
        }
        if group is not None:
            row = {"Группа": group, **row}
        rows.append(row)
    return rows


_register(
    "stats_summary",
    "Итоги по аккаунтам: показы, клики, расход, конверсии",
    ("расход", "итоги", "суммарно", "spend", "summary", "статистика", "account",
     "report", "отчёт", "конверсии", "conversions"),
    "ACCOUNT_PERFORMANCE_REPORT",
    [],
)
_register(
    "stats_campaigns",
    "Статистика по кампаниям: расход, клики, конверсии",
    ("кампании", "campaigns", "расходы по кампаниям", "статистика",
     "report", "отчёт", "конверсии", "conversions", "cpa", "ctr",
     "cost", "расход", "доход", "revenue"),
    "CAMPAIGN_PERFORMANCE_REPORT",
    ["CampaignId", "CampaignName"],
)
_register(
    "stats_adgroups",
    "Статистика по группам объявлений",
    ("группы", "adgroups", "группы объявлений", "статистика"),
    "ADGROUP_PERFORMANCE_REPORT",
    ["CampaignId", "CampaignName", "AdGroupId", "AdGroupName"],
)
_register(
    "stats_ads",
    "Статистика по объявлениям",
    ("объявления", "ads", "креативы", "статистика"),
    "AD_PERFORMANCE_REPORT",
    ["CampaignId", "CampaignName", "AdGroupId", "AdGroupName", "AdId"],
)
_register(
    "stats_keywords",
    "Статистика по фразам и автотаргетингу",
    ("фразы", "ключи", "keywords", "автотаргетинг", "criteria", "условия показа"),
    "CRITERIA_PERFORMANCE_REPORT",
    ["CampaignId", "AdGroupId", "AdGroupName", "Criterion", "CriterionId",
     "CriterionType"],
    KeywordsParams,
)
_register(
    "stats_audiences",
    "Аудиторный агрегат кампании: разбивка по типам критериев "
    "(ключи/автотаргетинг/ретаргетинг) и условия ретаргетинга",
    ("аудитории", "аудитория", "audiences", "audience", "ретаргетинг",
     "retargeting", "условия нацеливания", "доля ретаргетинга",
     "criterion type", "типы критериев"),
    "CRITERIA_PERFORMANCE_REPORT",
    ["CampaignId", "AdGroupId", "AdGroupName", "Criterion", "CriterionId",
     "CriterionType"],
)
_register(
    "stats_search_queries",
    "Поисковые запросы пользователей (только офлайн-отчёт)",
    ("запросы", "поиск", "queries", "search", "что искали", "семантика"),
    "SEARCH_QUERY_PERFORMANCE_REPORT",
    ["CampaignId", "AdGroupId", "Query", "MatchedKeyword", "Criterion"],
    SearchQueriesParams,
)
_register(
    "stats_regions",
    "Статистика по регионам местонахождения",
    ("регионы", "гео", "regions", "география", "location"),
    "CUSTOM_REPORT",
    ["LocationOfPresenceId", "LocationOfPresenceName"],
)
_register(
    "stats_placements",
    "Статистика по площадкам РСЯ",
    ("площадки", "placements", "рся", "сайты", "placement"),
    "CUSTOM_REPORT",
    ["Placement"],
    PlacementsParams,
)
_register(
    "stats_devices",
    "Статистика по устройствам",
    ("устройства", "devices", "десктоп", "мобайл", "device"),
    "CUSTOM_REPORT",
    ["Device"],
)
_register(
    "stats_custom",
    "Произвольный отчёт: свои поля, фильтры и тип",
    ("произвольный", "custom", "свои поля", "фильтры", "custom report",
     "report", "отчёт", "поля"),
    "CUSTOM_REPORT",
    [],
    CustomParams,
)
