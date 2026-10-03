"""Read actions campaigns_list, campaigns_get (Campaigns.get)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    finalize,
    goal_label,
    map_accounts,
    micros_to_rubles,
    split_request,
    summarize,
    version_footer,
)
from directai_mcp.catalog.registry import Ctx, action, write_action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import money

ACTIVE_STATES = ["ON", "OFF", "SUSPENDED", "ENDED"]

LIST_FIELDS = ["Id", "Name", "Type", "Status", "State"]
GET_FIELDS = [
    "Id",
    "Name",
    "StartDate",
    "EndDate",
    "Type",
    "Status",
    "State",
    "StatusPayment",
    "StatusClarification",
    "TimeTargeting",
    "DailyBudget",
    "TimeZone",
    "ClientInfo",
    "NegativeKeywords",
    "BlockedIps",
    "ExcludedSites",
]
TEXT_FIELDS = [
    # Ровно TextCampaignFieldEnum из docs/api/v501/campaigns.wsdl (шаг 1.1, Q1).
    "CounterIds",
    "RelevantKeywords",
    "Settings",
    "BiddingStrategy",
    "PriorityGoals",
    "TrackingParams",
    "AttributionModel",
    "PackageBiddingStrategy",
    "CanBeUsedAsPackageBiddingStrategySource",
    "NegativeKeywordSharedSetIds",
    "WeeklyBudgetRollover",
]
UNIFIED_FIELDS = [
    # Ровно UnifiedCampaignFieldEnum из docs/api/v501/campaigns.wsdl (шаг 1.1, Q1).
    "CounterIds",
    "Settings",
    "BiddingStrategy",
    "PriorityGoals",
    "TrackingParams",
    "AttributionModel",
    "PackageBiddingStrategy",
    "CanBeUsedAsPackageBiddingStrategySource",
    "NegativeKeywordSharedSetIds",
    "WeeklyBudgetRollover",
]

# Типы с детальным выводом в шаге 1.1; остальные — Id/Name/Type/State + пометка.
SUPPORTED_DETAIL_TYPES = ("TEXT_CAMPAIGN", "UNIFIED_CAMPAIGN")

# long-поля стратегий с денежным смыслом (XSD Strategy*): micros -> рубли.
_MONEY_KEYS = frozenset(
    {
        "WeeklySpendLimit",
        "AverageCpa",
        "AverageCpc",
        "Cpa",
        "BidCeiling",
        "AverageCpi",
        "FilterAverageCpa",
        "FilterAverageCpc",
        "AverageCpm",
        "SpendLimit",
        "AverageCpv",
    }
)

AREA_OPTION = "ENABLE_AREA_OF_INTEREST_TARGETING"

# Поисковые площадки по WSDL (шаг 1.1: Text — 3, Unified — 5; «Карты» = Maps).
_PLACEMENT_ORDER = {
    "TEXT_CAMPAIGN": ("SearchResults", "ProductGallery", "DynamicPlaces"),
    "UNIFIED_CAMPAIGN": (
        "SearchResults",
        "ProductGallery",
        "DynamicPlaces",
        "Maps",
        "SearchOrganizationList",
    ),
}

_DAY_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _setting(item: dict, option: str) -> str | None:
    for block in ("TextCampaign", "UnifiedCampaign"):
        body = item.get(block)
        if isinstance(body, dict):
            for entry in body.get("Settings") or []:
                if isinstance(entry, dict) and entry.get("Option") == option:
                    return str(entry.get("Value"))
    return None


def _block(item: dict) -> dict | None:
    """Детальный блок по Type; при обоих блоках — по типу (шаг 1.1, Q6)."""
    kind = item.get("Type")
    if kind == "UNIFIED_CAMPAIGN":
        body = item.get("UnifiedCampaign")
    elif kind == "TEXT_CAMPAIGN":
        body = item.get("TextCampaign")
    else:
        body = item.get("UnifiedCampaign") or item.get("TextCampaign")
    return body if isinstance(body, dict) else None


def _fmt_limit(key: str, value: object) -> str:
    """Одно поле стратегии: деньги micros->₽, %/коэф. как есть, неясное с «?»."""
    if isinstance(value, dict):
        inner = ", ".join(f"{k}={_fmt_limit(k, v)}" for k, v in value.items())
        return "{" + inner + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_fmt_limit(key, v) for v in value) + "]"
    if key in _MONEY_KEYS:
        rub = micros_to_rubles(value)
        return f"{money(rub)} ₽" if rub is not None else "—"
    if key in ("Crr", "ReserveReturn"):
        return f"{value}%"
    if key in ("GoalId", "StrategyId", "ClicksPerWeek", "RoiCoef"):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value} (?)"  # единица по XSD неочевидна (шаг 1.1, Q4)
    return str(value)


def _fmt_side(side: object) -> str:
    if not isinstance(side, dict):
        return "—"
    parts = [str(side.get("BiddingStrategyType") or "?")]
    for key, value in side.items():
        if key == "BiddingStrategyType":
            continue
        parts.append(f"{key}: {_fmt_limit(key, value)}")
    return "; ".join(parts)


def _fmt_goals(
    body: dict, names: dict[str, str], counters: dict[str, int] | None = None
) -> str:
    raw = body.get("PriorityGoals") or {}
    items = raw.get("Items") if isinstance(raw, dict) else None
    if not items:
        return "—"
    parts = []
    for goal in items:
        if not isinstance(goal, dict):
            continue
        gid = goal.get("GoalId")
        rub = micros_to_rubles(goal.get("Value"))
        label = goal_label(gid, names, counters)
        # v1.1.12: типа цели API не отдаёт (только GoalId/Value) —
        # показываем источник ценности вместо типа.
        src = goal.get("IsMetrikaSourceOfValue")
        mark = "Метрика" if src == "YES" else ("фикс" if src == "NO" else "?")
        if rub is not None:
            parts.append(f"{label}: {money(rub)} ₽ ({mark})")
        else:
            parts.append(f"{label}: — ({mark})")
    return "; ".join(parts) or "—"


def _fmt_ids(value: object) -> str:
    items = value.get("Items") if isinstance(value, dict) else value
    if not items:
        return "—"
    return ", ".join(str(i) for i in items)


def _fmt_settings(body: dict) -> str:
    parts = []
    for entry in body.get("Settings") or []:
        if isinstance(entry, dict):
            parts.append(f"{entry.get('Option')}: {entry.get('Value')}")
    return "; ".join(parts) or "—"


def _fmt_package(body: dict) -> str:
    pack = body.get("PackageBiddingStrategy")
    if isinstance(pack, dict):
        sid = pack.get("StrategyId")
        return f"id {sid}" if sid is not None else "есть"
    return "—"


def _fmt_daily(value: object) -> str:
    """v1.1.12: DailyBudget Amount (micros) + Mode (STANDARD/DISTRIBUTED)."""
    if not isinstance(value, dict):
        return "—"
    rub = micros_to_rubles(value.get("Amount"))
    mode = value.get("Mode") or "?"
    return f"{money(rub)} ₽ ({mode})" if rub is not None else "—"


def _day_profile(hours: list[int]) -> str:
    """Часы с одинаковым % — в диапазоны «10:00–19:00 (100%)» (конец — граница).

    Сверено с интерфейсом Директа (v1.1.12, тест 9): показы 9:00–18:00 —
    часы 9..17, конец интервала не включён.
    """
    runs: list[tuple[int, int, int]] = []
    start = 0
    for hour in range(1, 25):
        pct = hours[hour] if hour < 24 else None
        if pct != hours[start]:
            if hours[start] > 0:
                runs.append((start, hour, hours[start]))
            start = hour
    if not runs:
        return "0%"
    return ", ".join(f"{a:02d}:00–{b:02d}:00 ({p}%)" for a, b, p in runs)


def _fmt_schedule(time_targeting: object) -> str:
    """v1.1.12: Schedule «D,h0..h23» в таблицу Пн–Вс (эталон: Пн–Пт 10–19)."""
    tt = time_targeting if isinstance(time_targeting, dict) else {}
    sched = tt.get("Schedule")
    if isinstance(sched, dict):
        items = sched.get("Items") or []
    elif isinstance(sched, list):
        items = sched
    else:
        items = []
    profiles: dict[int, str] = {}
    for raw in items:
        try:
            nums = [int(p) for p in str(raw).split(",")]
        except ValueError:
            continue
        if len(nums) != 25 or not 1 <= nums[0] <= 7 or any(h < 0 for h in nums[1:]):
            continue
        profiles[nums[0]] = _day_profile(nums[1:])
    if not profiles:
        return "—"
    runs: list[list] = []
    for day in range(1, 8):
        if day not in profiles:
            continue
        if runs and runs[-1][2] == profiles[day] and day == runs[-1][1] + 1:
            runs[-1][1] = day
        else:
            runs.append([day, day, profiles[day]])
    parts = []
    for first, last, prof in runs:
        span = _DAY_SHORT[first - 1] if first == last else (
            f"{_DAY_SHORT[first - 1]}–{_DAY_SHORT[last - 1]}")
        parts.append(f"{span} {prof}")
    return "; ".join(parts)


def _fmt_holidays(time_targeting: object) -> str:
    """v1.1.12: SuspendOnHolidays + ConsiderWorkingWeekends."""
    tt = time_targeting if isinstance(time_targeting, dict) else {}
    raw_hol = tt.get("HolidaysSchedule")
    hol = raw_hol if isinstance(raw_hol, dict) else {}
    parts = [f"остановка: {hol.get('SuspendOnHolidays') or '—'}"]
    for key in ("BidPercent", "StartHour", "EndHour"):
        if hol.get(key) is not None:
            parts.append(f"{key}={hol[key]}")
    parts.append(f"рабочие выходные: {tt.get('ConsiderWorkingWeekends') or '—'}")
    return "; ".join(parts)


def _fmt_placements(search: object, kind: object) -> str:
    """v1.1.12: типы площадок поиска с YES/NO («Карты» = Maps)."""
    found = search.get("PlacementTypes") if isinstance(search, dict) else None
    if not isinstance(found, dict):
        return "—"
    order = _PLACEMENT_ORDER.get(str(kind), tuple(found))
    parts = [f"{key} {found.get(key, '—')}" for key in order]
    return ", ".join(parts) if parts else "—"


def _short(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _block_strategy(body: dict) -> str:
    strategy = body.get("BiddingStrategy")
    if not isinstance(strategy, dict):
        return "—"
    return (
        f"поиск: {_fmt_side(strategy.get('Search'))} / "
        f"сети: {_fmt_side(strategy.get('Network'))}"
    )


def _detail(
    login: str,
    item: dict,
    body: dict | None,
    names: dict[str, str],
    counters: dict[str, int] | None = None,
) -> str:
    kind = item.get("Type")
    lines = [
        f"### {item.get('Name')} ({item.get('Id')}) [{login}]",
        f"- Тип/State/Status: {kind} / {item.get('State')} / {item.get('Status')}",
        f"- Даты: {item.get('StartDate')} — {item.get('EndDate')}",
        f"- Часовой пояс: {item.get('TimeZone')}",
        f"- Расписание: {_fmt_schedule(item.get('TimeTargeting'))}",
        f"- Праздники/выходные: {_fmt_holidays(item.get('TimeTargeting'))}",
        f"- Дневной бюджет: {_fmt_daily(item.get('DailyBudget'))}",
        f"- StatusPayment: {item.get('StatusPayment') or '—'}",
        f"- StatusClarification: {item.get('StatusClarification') or '—'}",
        f"- Интерес к региону ({AREA_OPTION}): {_setting(item, AREA_OPTION) or '—'}",
    ]
    if body is None or kind not in SUPPORTED_DETAIL_TYPES:
        lines.append("- Детали: тип не разбирается сервером (шаг 1.1: только Text/Unified).")
    else:
        strategy = body.get("BiddingStrategy")
        search = strategy.get("Search") if isinstance(strategy, dict) else None
        network = strategy.get("Network") if isinstance(strategy, dict) else None
        rollover = body.get("WeeklyBudgetRollover")
        lines += [
            f"- Стратегия поиск: {_fmt_side(search)}",
            f"- Стратегия сети: {_fmt_side(network)}",
            f"- Площадки поиска: {_fmt_placements(search, kind)}",
            f"- Цели: {_fmt_goals(body, names, counters)}",
            f"- Атрибуция: {body.get('AttributionModel') or '—'}",
            f"- TrackingParams: {body.get('TrackingParams') or '—'}",
            f"- Пакетная стратегия: {_fmt_package(body)}",
            "- Перенос недельного бюджета: "
            + (f"{rollover} (?)" if rollover is not None else "—"),
            f"- Источник пакетной стратегии: {body.get('CanBeUsedAsPackageBiddingStrategySource') or '—'}",
            f"- Общие наборы минус-фраз: {_fmt_ids(body.get('NegativeKeywordSharedSetIds'))}",
            f"- Счётчики: {_fmt_ids(body.get('CounterIds'))}",
            f"- Settings: {_fmt_settings(body)}",
        ]
    negatives = _items(item.get("NegativeKeywords"))
    lines += [
        f"- Минус-фразы ({len(negatives)}): {_join(negatives, 2000)}",
        f"- BlockedIps: {_join(_items(item.get('BlockedIps')))}",
        f"- ExcludedSites: {_join(_items(item.get('ExcludedSites')))}",
    ]
    return "\n".join(lines)


class CampaignsListParams(GetActionParams):
    include_archived: bool = False
    campaign_ids: list[int] = Field(default_factory=list)
    # B1: поиск по имени/подстроке (ambiguous при >1); lookup_days — период
    # проверки Reports для statistics_only (по умолчанию 90 дней).
    search: str | None = None
    lookup_days: int = 90


class CampaignsGetParams(GetActionParams):
    campaign_ids: list[int] = Field(min_length=1)
    full: bool = False
    lookup_days: int = 90


class CampaignsStateParams(GetActionParams):
    campaign_ids: list[int] = Field(min_length=1)
    operation: Literal["suspend", "resume", "archive", "unarchive"]


def _items(value: object) -> list:
    if isinstance(value, dict) and isinstance(value.get("Items"), list):
        return value["Items"]
    return []


def _join(items: list, limit: int = 120) -> str:
    if not items:
        return "—"
    text = ", ".join(str(i) for i in items)
    return text if len(text) <= limit else text[:limit] + "…"


def _context(ctx: Ctx, name: str, entries: list[AccountEntry], extra: str = "") -> str:
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    accounts = ", ".join(e.login for e in entries)
    return f"{mark}{name}: {accounts}{extra}."


async def _reports_has_data(
    ctx: Ctx,
    entries: list[AccountEntry],
    campaign_ids: list[int],
    days: int = 90,
) -> bool:
    """B1: лёгкий отчёт CAMPAIGN_PERFORMANCE_REPORT для statistics_only.

    Только когда Campaigns API вернул пусто по явному ID. Поля CampaignId,
    Impressions/Clicks; период по умолчанию 90 дней (переопределяется).
    """
    from datetime import datetime, timedelta

    from directai_mcp.api.errors import DirectError

    if not campaign_ids or not entries:
        return False
    try:
        days_int = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days_int = 90
    today = datetime.now().astimezone().date()
    date_to = (today - timedelta(days=1)).isoformat()
    date_from = (today - timedelta(days=days_int)).isoformat()
    client = ctx.reports()
    try:
        import asyncio as _asyncio

        sem = _asyncio.Semaphore(3)

        async def _one(entry: AccountEntry) -> bool:
            definition = {
                "SelectionCriteria": {
                    "DateFrom": date_from,
                    "DateTo": date_to,
                    "Filter": [{
                        "Field": "CampaignId",
                        "Operator": "IN",
                        "Values": [str(i) for i in campaign_ids],
                    }],
                },
                "FieldNames": ["CampaignId", "Impressions", "Clicks"],
                "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
            }
            try:
                async with sem:
                    _cols, rows = await client.fetch(entry.login, definition)
            except DirectError:
                return False
            return bool(rows)

        results = await _asyncio.gather(*(_one(e) for e in entries))
        return bool(any(results))
    finally:
        await client.aclose()


@action(
    "campaigns_list",
    "read",
    "Список кампаний; по умолчанию без архивных",
    ("кампании", "campaigns", "список кампаний", "campaign list"),
    CampaignsListParams,
)
async def _list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, CampaignsListParams)
    criteria: dict = {}
    if params.campaign_ids:
        criteria["Ids"] = params.campaign_ids
    if not params.include_archived:
        criteria["States"] = ACTIVE_STATES
    body = {"SelectionCriteria": criteria, "FieldNames": LIST_FIELDS}
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        return await client.get_all(
            "campaigns", dict(body), entry.login, "Campaigns", "v501",
            tally=tally,
        )

    results = await map_accounts(ctx, params.account, fetch)
    columns = ["Id", "Name", "Type", "State", "Status"]
    rows: list[dict] = []
    raw: list[dict] = []
    errors: list[str] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, list)
        raw.extend(payload)
        for item in payload:
            rows.append(
                {
                    "_account": entry.login,
                    "Id": item.get("Id"),
                    "Name": item.get("Name"),
                    "Type": item.get("Type"),
                    "State": item.get("State"),
                    "Status": item.get("Status"),
                }
            )
    display = (["_account"] if len(results) > 1 else []) + columns
    note = (
        ", архивные исключены" if not params.include_archived else ", включая архивные"
    )
    # B1: типизированный статус на уровне ответа.
    from directai_mcp.catalog import lookup as _lookup

    api_errors = [p for _, p in results if isinstance(p, DirectError)]
    if api_errors and not rows:
        look = _lookup.failed(
            "ошибка API при campaigns_list; код ошибки Директа — в строке выше. "
            "Не трактовать как «пусто»."
        )
    elif params.search:
        needle = params.search.strip().casefold()
        hits = [r for r in rows if needle in str(r.get("Name") or "").casefold()]
        if len(hits) > 1:
            look = _lookup.ambiguous([
                {"id": str(r.get("Id")), "name": r.get("Name"),
                 "state": r.get("State")} for r in hits
            ])
        elif len(hits) == 1:
            look = _lookup.resolved_configured(
                "поиск по имени дал ровно одно совпадение.")
        else:
            look = _lookup.not_observed("поиск по имени, без периода Reports")
    elif not rows:
        if not params.campaign_ids:
            look = _lookup.build_lookup(
                "resolved", None, False,
                _lookup.empty_list_message(
                    [e.login for e, _ in results], "States/Ids"),
            )
        else:
            look = _lookup.build_lookup(
                "not_observed", None, False,
                "нет ни в Campaigns API, ни проверка Reports не запускалась "
                "(только campaigns_get проверяет Reports). "
                "Не утверждается, что кампании не существует.",
            )
    else:
        if tally.get("complete") is False:
            look = _lookup.incomplete(
                "ответ частичный: пагинация Campaigns.get не завершена; "
                "проверено не всё.")
        else:
            look = _lookup.resolved_configured(
                f"найдено кампаний: {len(rows)}.")
    out = finalize(
        ctx,
        _context(ctx, "campaigns_list", [e for e, _ in results], note),
        "campaigns_list",
        display,
        rows,
        params.limit,
        params.save_as,
        errors,
        output=params.output,
        format=params.format,
        account=params.account,
        with_version=True,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="campaigns_list",
        dump_params=params.model_dump(),
        dump_raw={"campaigns_list": [
            dict(i, linked_to_campaign=True) for i in raw]},
        dump_fields={"FieldNames": LIST_FIELDS},
        dump_tally=tally,
        dump_logins=[e.login for e, _ in results],
        dump_scope="campaign" if params.campaign_ids else "cabinet",
        dump_lookup=look,
    )
    return out + f"\n{_lookup.lookup_line(look)}\n\n{version_footer()}"


@action(
    "campaigns_get",
    "read",
    "Полные настройки кампаний по id. Поле ENABLE_AREA_OF_INTEREST_TARGETING "
    "только читается (отменено Яндексом 31.08.2026): значение YES/NO — "
    "неуправляемое поле, см. campaign_setting_notices. "
    "Турбо-блоки, clients.site и CPM-видео через API недоступны.",
    (
        "настройки кампании",
        "campaigns",
        "параметры кампании",
        "campaign settings",
        "стратегия",
        "strategy",
        "стратегия ставок",
        "бюджет",
        "budget",
        "цели",
        "цель",
        "goals",
        "goal",
        "приоритетные цели",
        "атрибуция",
        "attribution",
        "utm",
        "метки",
        "tracking params",
        "разметка",
        "счётчик",
        "counter",
        "settings",
        "расписание",
        "schedule",
        "время показов",
        "дневной бюджет",
        "daily budget",
        "площадки",
        "placements",
    ),
    CampaignsGetParams,
)
async def _get(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, CampaignsGetParams)
    body = {
        "SelectionCriteria": {"Ids": params.campaign_ids},
        "FieldNames": GET_FIELDS,
        "TextCampaignFieldNames": TEXT_FIELDS,
        "UnifiedCampaignFieldNames": UNIFIED_FIELDS,
        # v1.1.12: площадки поиска (WSDL: Text — 3, Unified — 5 с Maps).
        "TextCampaignSearchStrategyPlacementTypesFieldNames": [
            "SearchResults",
            "ProductGallery",
            "DynamicPlaces",
        ],
        "UnifiedCampaignSearchStrategyPlacementTypesFieldNames": [
            "SearchResults",
            "ProductGallery",
            "DynamicPlaces",
            "Maps",
            "SearchOrganizationList",
        ],
    }
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        return await client.get_all(
            "campaigns", dict(body), entry.login, "Campaigns", "v501",
            tally=tally,
        )

    results = await map_accounts(ctx, params.account, fetch)
    names = ctx.settings.goal_names
    columns = [
        "Id",
        "Name",
        "Type",
        "State",
        "Status",
        "StartDate",
        "EndDate",
        "TimeZone",
        "DailyBudget",
        "StatusPayment",
        "StatusClarification",
        "GeoInterest",
        "Strategy",
        "Goals",
        "Attrib",
        "Shared",
        "Counters",
        "Negatives",
    ]
    rows: list[dict] = []
    errors: list[str] = []
    details: list[str] = []
    raw: list[dict] = []
    notices_all: list[dict] = []
    from directai_mcp.catalog.notices import notices_for_settings as _notices
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, list)
        raw.extend(payload)
        for item in payload:
            negatives = _items(item.get("NegativeKeywords"))
            kind = item.get("Type")
            body_detail = _block(item)
            if body_detail is None or kind not in SUPPORTED_DETAIL_TYPES:
                strategy = "тип не разбирается сервером" if kind not in SUPPORTED_DETAIL_TYPES else "—"
                goals = attrib = "—"
                shared = counters = "—"
            else:
                strategy = _block_strategy(body_detail)
                goals = _fmt_goals(body_detail, names, ctx.settings.goal_counters)
                attrib = str(body_detail.get("AttributionModel") or "—")
                shared = _fmt_ids(body_detail.get("NegativeKeywordSharedSetIds"))
                counters = _fmt_ids(body_detail.get("CounterIds"))
            rows.append(
                {
                    "_account": entry.login,
                    "Id": item.get("Id"),
                    "Name": item.get("Name"),
                    "Type": kind,
                    "State": item.get("State"),
                    "Status": item.get("Status"),
                    "StartDate": item.get("StartDate"),
                    "EndDate": item.get("EndDate"),
                    "TimeZone": item.get("TimeZone"),
                    "DailyBudget": _fmt_daily(item.get("DailyBudget")),
                    "StatusPayment": item.get("StatusPayment") or "—",
                    "StatusClarification": item.get("StatusClarification") or "—",
                    "GeoInterest": _setting(item, AREA_OPTION) or "—",
                    "Strategy": _short(strategy),
                    "Goals": _short(goals),
                    "Attrib": attrib,
                    "Shared": shared,
                    "Counters": counters,
                    "Negatives": f"{len(negatives)}: {_join(negatives)}",
                }
            )
            if params.full:
                details.append(
                    _detail(entry.login, item, body_detail, names,
                            ctx.settings.goal_counters)
                )
            _body = _block(item) or {}
            for _n in _notices(_body.get("Settings")):
                notices_all.append({"campaign_id": item.get("Id"),
                                    **_n})
    display = (["_account"] if len(results) > 1 else []) + columns
    _dump_fields = {
        "FieldNames": GET_FIELDS,
        "TextCampaignFieldNames": TEXT_FIELDS,
        "UnifiedCampaignFieldNames": UNIFIED_FIELDS,
        "TextCampaignSearchStrategyPlacementTypesFieldNames": [
            "SearchResults", "ProductGallery", "DynamicPlaces"],
        "UnifiedCampaignSearchStrategyPlacementTypesFieldNames": [
            "SearchResults", "ProductGallery", "DynamicPlaces",
            "Maps", "SearchOrganizationList"],
    }
    # B1: типизированный статус. Reports-проверка — только когда Campaigns API
    # вернул пусто по явному ID (один лёгкий отчёт).
    from directai_mcp.catalog import lookup as _lookup

    api_errors = [p for _, p in results if isinstance(p, DirectError)]
    found_ids = {str(r.get("Id")) for r in rows if r.get("Id") is not None}
    want_ids = [str(c) for c in params.campaign_ids]
    missing = [c for c in want_ids if c not in found_ids]
    if api_errors and not rows:
        look = _lookup.failed(
            "ошибка API при campaigns_get; код ошибки Директа — в строке выше. "
            "Не трактовать как «пусто».")
    elif rows and not missing:
        if tally.get("complete") is False:
            look = _lookup.incomplete(
                "ответ частичный: пагинация Campaigns.get не завершена; "
                "проверено не всё.")
        else:
            look = _lookup.resolved_configured("объект найден в Campaigns API.")
    elif rows and missing:
        look = _lookup.incomplete(
            f"ответ частичный: не найдены в Campaigns API: {', '.join(missing)}; "
            f"найдено: {', '.join(sorted(found_ids)) or '—'}.")
    else:
        has_data = await _reports_has_data(
            ctx, [e for e, _ in results], params.campaign_ids,
            params.lookup_days)
        period_text = f"последние {params.lookup_days} дней"
        if has_data:
            look = _lookup.resolved_statistics_only()
        else:
            look = _lookup.not_observed(period_text)
    out = finalize(
        ctx,
        _context(ctx, "campaigns_get", [e for e, _ in results]),
        "campaigns_get",
        display,
        rows,
        params.limit,
        params.save_as,
        errors,
        output=params.output,
        format=params.format,
        account=params.account,
        with_version=True,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="campaigns_get",
        dump_params=params.model_dump(),
        dump_raw={"campaigns_get": [
            dict(i, linked_to_campaign=True) for i in raw]},
        dump_extra=({"campaign_setting_notices": notices_all}
                    if notices_all else None),
        dump_fields=_dump_fields,
        dump_tally=tally,
        dump_logins=[e.login for e, _ in results],
        dump_scope="campaign",
        dump_lookup=look,
    )
    if details:
        out += "\n\n" + "\n\n".join(details)
    if notices_all:
        out += "\n\nПометки настроек (campaign_setting_notices, неуправляемые поля):\n"
        for _n in notices_all:
            out += (f"- кампания {_n.get('campaign_id')}: "
                    f"{_n.get('field')} ({_n.get('status')}, с {_n.get('since')}): "
                    f"{_n.get('message')} Текущее: {_n.get('value')}.\n")
    out += f"\n{_lookup.lookup_line(look)}"
    return out + f"\n\n{version_footer()}"


EXPECTED_STATE = {
    "suspend": "SUSPENDED",
    "resume": "ON",
    "archive": "ARCHIVED",
    "unarchive": "OFF",
}

REQUIRED_STATE = {
    "suspend": ("ON",),
    "resume": ("SUSPENDED",),
    "archive": ("SUSPENDED",),
    "unarchive": ("ARCHIVED",),
}

_RESULT_KEY = {
    "suspend": "SuspendResults",
    "resume": "ResumeResults",
    "archive": "ArchiveResults",
    "unarchive": "UnarchiveResults",
}


async def _prepare_state(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    assert isinstance(params, CampaignsStateParams)
    client = ctx.direct()
    try:
        items = await client.get_all(
            "campaigns",
            {
                "SelectionCriteria": {"Ids": params.campaign_ids},
                "FieldNames": ["Id", "Name", "State"],
            },
            entry.login,
            "Campaigns",
        )
    finally:
        await client.aclose()
    found = {int(i["Id"]): i for i in items if i.get("Id") is not None}
    missing = [c for c in params.campaign_ids if c not in found]
    if missing:
        raise ValueError(f"кампании не найдены: {missing}.")
    before = {cid: found[cid].get("State") for cid in params.campaign_ids}
    required = REQUIRED_STATE[params.operation]
    bad_state = [
        f"{cid}: {before[cid]}"
        for cid in params.campaign_ids
        if before[cid] not in required
    ]
    if bad_state:
        raise ValueError(
            f"операция {params.operation} требует {required}: " + "; ".join(bad_state)
        )
    expected = EXPECTED_STATE[params.operation]
    lines = [
        f"{found[cid].get('Name')} ({cid}): {before[cid]} → {expected}"
        for cid in params.campaign_ids
    ]
    warnings: list[str] = []
    if params.operation in ("suspend", "archive") and len(params.campaign_ids) > 3:
        warnings.append(
            f"Операция {params.operation} сразу для {len(params.campaign_ids)} кампаний."
        )
    return {
        "before": before,
        "requests": [
            (
                "campaigns",
                params.operation,
                {"SelectionCriteria": {"Ids": params.campaign_ids}},
            )
        ],
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in lines),
        "warnings": warnings,
    }


async def _apply_state(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    service, method, body, version = split_request(plan.requests[0])
    params = plan.params
    assert isinstance(params, dict)
    operation = params["operation"]
    client = ctx.direct()
    try:
        try:
            result = await client.call(service, method, body, entry.login, version)
        except DirectUnverifiedError:
            raise
        except DirectError as e:
            return {
                "status": "failed",
                "lines": [f"Ошибка API: {e.human_message()}"],
                "response": {"error": e.human_message()},
            }
        items = result.get(_RESULT_KEY[operation], [])
        lines: list[str] = []
        ok = 0
        for cid, res in zip(params["campaign_ids"], items):
            errors = res.get("Errors") or []
            warns = res.get("Warnings") or []
            if errors:
                lines.append(
                    f"{cid}: ОШИБКА "
                    + "; ".join(f"{e.get('Code')}: {e.get('Message')}" for e in errors)
                )
            else:
                ok += 1
                suffix = ""
                if warns:
                    suffix = (
                        " ("
                        + "; ".join(
                            f"{w.get('Code')}: {w.get('Message')}" for w in warns
                        )
                        + ")"
                    )
                lines.append(f"{cid}: OK{suffix}")
        status = (
            "applied"
            if ok == len(params["campaign_ids"])
            else "failed"
            if ok == 0
            else "partial"
        )
        return {"status": status, "lines": lines, "response": result}
    finally:
        await client.aclose()


async def _verify_state(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    expected = EXPECTED_STATE[params["operation"]]
    client = ctx.direct()
    try:
        items = await client.get_all(
            "campaigns",
            {
                "SelectionCriteria": {"Ids": params["campaign_ids"]},
                "FieldNames": ["Id", "State"],
            },
            entry.login,
            "Campaigns",
        )
    finally:
        await client.aclose()
    after = {int(i["Id"]): i.get("State") for i in items if i.get("Id") is not None}
    bad = [
        f"{cid}: {after.get(cid)} != {expected}"
        for cid in params["campaign_ids"]
        if after.get(cid) != expected
    ]
    if bad:
        return {
            "after": after,
            "ok": False,
            "note": "read-back НЕ подтвердил: " + "; ".join(bad),
        }
    return {"after": after, "ok": True, "note": "подтверждено read-back."}


write_action(
    "campaigns_state",
    "Остановка/возобновление/архив кампаний",
    (
        "остановить кампанию",
        "campaigns",
        "suspend",
        "resume",
        "archive",
        "состояние кампании",
        "запустить кампанию",
    ),
    CampaignsStateParams,
    prepare=_prepare_state,
    apply=_apply_state,
    verify=_verify_state,
)


class CampaignsCreateParams(GetActionParams):
    name: str
    campaign_type: Literal["TEXT_CAMPAIGN", "UNIFIED_CAMPAIGN"] = "UNIFIED_CAMPAIGN"
    start_date: str | None = None
    end_date: str | None = None
    search_strategy: str = "HIGHEST_POSITION"
    network_strategy: str = "SERVING_OFF"
    negatives: list[str] = Field(default_factory=list)
    counter_ids: list[int] = Field(default_factory=list)


class CampaignsUpdateParams(GetActionParams):
    campaign_ids: list[int] = Field(min_length=1)
    end_date: str | None = None
    negatives: list[str] | None = None
    strategy: dict | None = None
    # Исключённые площадки (добавление к существующим, с дедупом).
    excluded_sites: list[str] | None = None
    # A1: любые Settings с неуправляемым полем отклоняются до API.
    settings: list[dict] | None = None
    # A9: TrackingParams с валидацией макросов (предупреждение, не блок).
    tracking_params: str | None = None
    # v1.15.0: возвращены переименование и дневной бюджет (₽). Guard:
    # mode=block — запрет до API; mode=confirm — «опасная операция»,
    # применение только с owner_confirmed=true. Недельный бюджет — в strategy
    # (WeeklySpendLimit внутри блока стратегии).
    name: str | None = None
    daily_budget: float | None = Field(default=None, gt=0)
    daily_budget_mode: Literal["STANDARD", "DISTRIBUTED"] = "STANDARD"


def _today() -> str:
    from datetime import datetime

    return datetime.now().astimezone().date().isoformat()


async def _prepare_campaigns_create(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, CampaignsCreateParams)
    start = params.start_date or _today()
    body: dict = {"Name": params.name, "StartDate": start}
    if params.end_date:
        body["EndDate"] = params.end_date
    warnings: list[str] = []
    # v1.8.0: ЕПК по умолчанию (запрос на json/v501); legacy TEXT —
    # только явно, с предупреждением (устаревший тип для новых кампаний).
    version = "v501" if params.campaign_type == "UNIFIED_CAMPAIGN" else "v5"
    if params.campaign_type == "TEXT_CAMPAIGN":
        warnings.append(
            "TEXT_CAMPAIGN — устаревший тип для новых кампаний; "
            "по умолчанию создаётся UNIFIED_CAMPAIGN (ЕПК)."
        )
    if params.campaign_type == "TEXT_CAMPAIGN":
        body["TextCampaign"] = {
            "BiddingStrategy": {
                "Search": {"BiddingStrategyType": params.search_strategy},
                "Network": {"BiddingStrategyType": params.network_strategy},
            }
        }
        if params.counter_ids:
            body["TextCampaign"]["CounterIds"] = {"Items": params.counter_ids}
    else:
        body["UnifiedCampaign"] = {
            "BiddingStrategy": {
                "Search": {"BiddingStrategyType": params.search_strategy},
                "Network": {"BiddingStrategyType": params.network_strategy},
            }
        }
        if params.counter_ids:
            body["UnifiedCampaign"]["CounterIds"] = {"Items": params.counter_ids}
    if params.negatives:
        body["NegativeKeywords"] = {"Items": params.negatives}
    preview = (
        f"Создать {params.campaign_type} «{params.name}», старт {start}"
        + (f", конец {params.end_date}" if params.end_date else "")
        + f", стратегия {params.search_strategy}/{params.network_strategy}"
        + (f", минус-фраз: {len(params.negatives)}" if params.negatives else "")
    )
    return {
        "before": None,
        "requests": [("campaigns", "add", {"Campaigns": [body]}, version)],
        "preview": "Будет выполнено:\n- " + preview,
        "warnings": warnings,
    }


async def _verify_campaigns_created(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    last = getattr(plan, "last_response", None) or {}
    created = [
        r.get("Id")
        for r in (last.get("response") or {}).get("AddResults", [])
        if r.get("Id") is not None
    ]
    if not created:
        return {"after": None, "ok": False, "note": "read-back: кампания не создана."}
    client = ctx.direct()
    try:
        found = await client.get_all(
            "campaigns",
            {
                "SelectionCriteria": {"Ids": created},
                "FieldNames": ["Id", "Name", "State"],
            },
            entry.login,
            "Campaigns",
        )
    finally:
        await client.aclose()
    have = {int(i["Id"]): i.get("Name") for i in found if i.get("Id") is not None}
    missing = [c for c in created if c not in have]
    if missing:
        return {
            "after": have,
            "ok": False,
            "note": "read-back НЕ подтвердил id: " + ", ".join(map(str, missing)),
        }
    return {
        "after": have,
        "ok": True,
        "note": f"подтверждено read-back: создана {created[0]}.",
    }


write_action(
    "campaigns_create",
    "Создание кампании (по умолчанию UNIFIED_CAMPAIGN/ЕПК)",
    ("создать кампанию", "campaigns", "add", "новая кампания"),
    CampaignsCreateParams,
    prepare=_prepare_campaigns_create,
    apply=lambda ctx, entry, plan: _apply_batch_campaigns(ctx, entry, plan),
    verify=_verify_campaigns_created,
)


async def _apply_batch_campaigns(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    service, method, body, version = split_request(plan.requests[0])
    key = "AddResults" if method == "add" else "UpdateResults"
    labels = [c.get("Name", str(c.get("Id"))) for c in body["Campaigns"]]
    client = ctx.direct()
    try:
        try:
            result = await client.call(service, method, body, entry.login, version)
        except DirectUnverifiedError:
            raise
        except DirectError as e:
            return {
                "status": "failed",
                "lines": [f"Ошибка API: {e.human_message()}"],
                "response": {"error": e.human_message()},
            }
        lines, ok = summarize(labels, result.get(key, []))
        total = len(labels)
        status = "applied" if ok == total else "failed" if ok == 0 else "partial"
        return {"status": status, "lines": lines, "response": result}
    finally:
        await client.aclose()


def _network_type(item: dict) -> str | None:
    for block in ("TextCampaign", "UnifiedCampaign"):
        strategy = (item.get(block) or {}).get("BiddingStrategy") or {}
        network = strategy.get("Network") or {}
        if isinstance(network, dict) and network.get("BiddingStrategyType"):
            return str(network["BiddingStrategyType"])
    return None


async def _prepare_campaigns_update(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, CampaignsUpdateParams)
    from directai_mcp.catalog.notices import is_read_only
    if params.settings:
        for s in params.settings:
            if isinstance(s, dict) and is_read_only(str(s.get("Option"))):
                raise ValueError(
                    f"поле {s.get('Option')} только читается "
                    f"(campaign_setting_notices, read_only): запись запрещена."
                )
    track_warnings: list[str] = []
    if params.tracking_params is not None:
        from directai_mcp.catalog.tracking import validate_tracking_macros
        _rec, _warns = validate_tracking_macros(params.tracking_params)
        track_warnings.extend(_warns)
    given: dict = {}
    if params.end_date is not None:
        given["EndDate"] = params.end_date
    if params.negatives is not None:
        given["NegativeKeywords"] = {"Items": params.negatives}
    if params.strategy is not None:
        # v1.8.0: блок стратегии — по типу кампании, в тела ниже.
        given["_strategy"] = params.strategy
    if params.excluded_sites is not None:
        given["_excluded_sites_add"] = list(params.excluded_sites)
    if params.tracking_params is not None:
        given["_tracking_params"] = params.tracking_params
    if params.name is not None:
        if not params.name.strip():
            raise ValueError("name: пустое имя кампании.")
        if len(params.campaign_ids) != 1:
            raise ValueError("переименование — ровно одна кампания за план.")
        given["Name"] = params.name.strip()
    if params.daily_budget is not None:
        given["DailyBudget"] = {
            "Amount": round(params.daily_budget * 1_000_000),
            "Mode": params.daily_budget_mode,
        }
    if not given:
        raise ValueError(
            "укажите end_date, negatives, strategy, excluded_sites, "
            "tracking_params, name или daily_budget.")
    client = ctx.direct()
    try:
        found = await client.get_all(
            "campaigns",
            {
                "SelectionCriteria": {"Ids": params.campaign_ids},
                # v1.8.0: Type через v501 (v5 отдаёт устаревший TEXT_CAMPAIGN
                # для ЕПК) — для выбора блока стратегии и версии запроса.
                "FieldNames": ["Id", "Name", "Type", "ExcludedSites", "DailyBudget"],
                "TextCampaignFieldNames": ["BiddingStrategy"],
                "UnifiedCampaignFieldNames": ["BiddingStrategy"],
            },
            entry.login,
            "Campaigns",
            "v501",
        )
    finally:
        await client.aclose()
    names = {int(i["Id"]): i.get("Name") for i in found if i.get("Id") is not None}
    missing = [c for c in params.campaign_ids if c not in names]
    if missing:
        raise ValueError(f"кампании не найдены: {missing}.")
    types = {int(i["Id"]): i.get("Type") for i in found if i.get("Id") is not None}
    unknown = [c for c in params.campaign_ids if types.get(c) not in
               ("TEXT_CAMPAIGN", "UNIFIED_CAMPAIGN")]
    if unknown:
        raise ValueError(
            f"неизвестный тип кампаний {unknown} — обновление отклонено до API.")
    use_v501 = any(types[c] == "UNIFIED_CAMPAIGN" for c in params.campaign_ids)
    if params.excluded_sites is not None:
        # Добавление к существующим (валидация из campaigns/update:
        # ≤1000 элементов, элемент ≤255 символов).
        for site in params.excluded_sites:
            if not str(site or "").strip():
                raise ValueError("excluded_sites: пустой элемент.")
            if len(str(site)) > 255:
                raise ValueError(f"excluded_sites: элемент длиннее 255: {site}.")
        current: dict[int, list[str]] = {}
        for item in found:
            raw = item.get("ExcludedSites")
            seq = raw.get("Items") if isinstance(raw, dict) else raw
            current[int(item["Id"])] = [str(s) for s in seq] if isinstance(seq, list) else []
        merged: dict[int, list[str]] = {}
        for cid in params.campaign_ids:
            seen: dict[str, str] = {}
            for site in current.get(cid, []) + params.excluded_sites:
                key = str(site).strip().casefold()
                if key and key not in seen:
                    seen[key] = str(site).strip()
            merged[cid] = list(seen.values())
            if len(merged[cid]) > 1000:
                raise ValueError(
                    f"кампания {cid}: исключений {len(merged[cid])} (лимит 1000).")
        for cid in params.campaign_ids:
            before = len(current.get(cid, []))
            new = len([s for s in params.excluded_sites
                       if str(s).strip().casefold()
                       not in {str(x).strip().casefold() for x in current.get(cid, [])}])
            dups = len(params.excluded_sites) - new
            given.setdefault("_excluded_preview", {})[cid] = (
                f"было {before}, добавляется {len(params.excluded_sites)}, "
                f"дублей {dups}, станет {len(merged[cid])}")
        given["_excluded_merged"] = merged
    bodies = [dict({"Id": cid}, **{k: v for k, v in given.items()
                                   if not k.startswith("_")})
              for cid in params.campaign_ids]
    if given.get("_strategy") is not None:
        # v1.8.0: блок стратегии — по типу кампании (v501 поддерживает оба).
        for body in bodies:
            block = ("UnifiedCampaign" if types[body["Id"]] == "UNIFIED_CAMPAIGN"
                     else "TextCampaign")
            body[block] = {"BiddingStrategy": given["_strategy"]}
    if given.get("_tracking_params") is not None:
        for body in bodies:
            block = ("UnifiedCampaign" if types[body["Id"]] == "UNIFIED_CAMPAIGN"
                     else "TextCampaign")
            body[block] = {**(body.get(block) or {}),
                           "TrackingParams": given["_tracking_params"]}
    if params.excluded_sites is not None:
        for body in bodies:
            body["ExcludedSites"] = {"Items": given["_excluded_merged"][body["Id"]]}
    desc = []
    warnings: list[str] = list(track_warnings)
    if params.name is not None:
        desc.append(f"имя → «{params.name.strip()}»")
    budget_desc: dict[int, str] = {}
    if params.daily_budget is not None:
        from directai_mcp.safety.rules import check_ratio, load_rules

        rules = load_rules(ctx.data_dir / "rules.toml" if ctx.data_dir else None)
        for item in found:
            cid = int(item["Id"])
            raw = item.get("DailyBudget") or {}
            old = (raw.get("Amount") / 1_000_000
                   if isinstance(raw, dict) and raw.get("Amount") else None)
            budget_desc[cid] = (
                f"дневной бюджет: {f'{old:g} ₽' if old else 'нет'} → "
                f"{params.daily_budget:g} ₽ ({params.daily_budget_mode})")
            warn = check_ratio(rules.max_budget_ratio, old, params.daily_budget,
                               f"кампания {cid} дневной бюджет")
            if warn:
                warnings.append(warn)
    if params.end_date is not None:
        desc.append(f"EndDate → {params.end_date}")
    if params.negatives is not None:
        desc.append(f"минус-фразы → {len(params.negatives)} шт")
    if params.tracking_params is not None:
        desc.append("tracking_params обновлены (макросы проверены)")
    excl_desc = {}
    if params.excluded_sites is not None:
        for cid in params.campaign_ids:
            excl_desc[cid] = (
                f"исключения площадок: {given['_excluded_preview'][cid]}")
    if params.strategy is not None:
        desc.append("стратегия обновлена")
        warnings.append("Смена стратегии/целей сбрасывает обучение кампании.")
        new_network = (
            params.strategy.get("Network", {}).get("BiddingStrategyType")
            if isinstance(params.strategy.get("Network"), dict)
            else None
        )
        if new_network not in (None, "SERVING_OFF"):
            turned_on = [
                str(cid)
                for cid in params.campaign_ids
                if _network_type(next(i for i in found if int(i["Id"]) == cid))
                == "SERVING_OFF"
            ]
            if turned_on:
                warnings.append(
                    "Показы в сетях (РСЯ) включаются у кампаний, где были выключены: "
                    + ", ".join(turned_on)
                    + "."
                )
    lines = [f"{names[cid]} ({cid}): " + "; ".join(
        desc + ([excl_desc[cid]] if cid in excl_desc else [])
        + ([budget_desc[cid]] if cid in budget_desc else []))
        for cid in params.campaign_ids]
    return {
        "before": names,
        "requests": [("campaigns", "update", {"Campaigns": bodies},
                      "v501" if use_v501 else "v5")],
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in lines),
        "warnings": warnings,
    }


async def _verify_campaigns_updated(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    import asyncio as _asyncio

    params = plan.params
    assert isinstance(params, dict)

    def _check(current: dict) -> list[str]:
        bad: list[str] = []
        for cid in params["campaign_ids"]:
            cur = current.get(cid, {})
            if (
                params.get("end_date") is not None
                and cur.get("EndDate") != params["end_date"]
            ):
                bad.append(f"{cid}.EndDate: {cur.get('EndDate')!r}")
            if params.get("negatives") is not None:
                items = (
                    (cur.get("NegativeKeywords") or {}).get("Items")
                    if isinstance(cur.get("NegativeKeywords"), dict)
                    else cur.get("NegativeKeywords")
                ) or []
                if list(items) != params["negatives"]:
                    bad.append(f"{cid}.negatives: {len(items)} шт")
            if params.get("excluded_sites") is not None:
                raw = cur.get("ExcludedSites")
                seq = raw.get("Items") if isinstance(raw, dict) else raw
                got = {str(s).strip().casefold()
                       for s in seq or [] if str(s).strip()}
                want = {str(s).strip().casefold()
                        for s in params["excluded_sites"] if str(s).strip()}
                if not want <= got:
                    bad.append(f"{cid}.excluded: {len(got)} шт")
            if params.get("name") is not None and cur.get("Name") != params["name"].strip():
                bad.append(f"{cid}.Name: {cur.get('Name')!r}")
            if params.get("daily_budget") is not None:
                amount = (cur.get("DailyBudget") or {}).get("Amount")
                want_amount = round(params["daily_budget"] * 1_000_000)
                if amount != want_amount:
                    bad.append(f"{cid}.DailyBudget: {amount!r}")
        return bad

    async def _read() -> dict:
        client = ctx.direct()
        try:
            found = await client.get_all(
                "campaigns",
                {
                    "SelectionCriteria": {"Ids": params["campaign_ids"]},
                    "FieldNames": ["Id", "Name", "EndDate", "NegativeKeywords",
                                   "ExcludedSites", "DailyBudget"],
                },
                entry.login,
                "Campaigns",
            )
        finally:
            await client.aclose()
        return {int(i["Id"]): i for i in found if i.get("Id") is not None}

    current = await _read()
    bad = _check(current)
    if bad:
        # Репликация API отставет: один повтор read-back после паузы.
        await _asyncio.sleep(10)
        current = await _read()
        bad = _check(current)
    if bad:
        return {
            "after": {k: v.get("EndDate") for k, v in current.items()},
            "ok": False,
            "note": "read-back НЕ подтвердил: " + "; ".join(bad),
        }
    return {
        "after": {k: v.get("EndDate") for k, v in current.items()},
        "ok": True,
        "note": "подтверждено read-back.",
    }


write_action(
    "campaigns_update",
    "Изменение кампаний: даты, минусы, исключения площадок, tracking_params, "
    "имя, дневной бюджет, стратегия (бюджет/стратегия/имя — по политике guard: "
    "block — запрет, confirm — опасная операция с owner_confirmed; "
    "ENABLE_AREA_OF_INTEREST_TARGETING только читается — запись отклоняется)",
    ("изменить кампанию", "campaigns", "update", "настройки кампании",
     "исключить площадки", "excluded"),
    CampaignsUpdateParams,
    prepare=_prepare_campaigns_update,
    apply=lambda ctx, entry, plan: _apply_batch_campaigns(ctx, entry, plan),
    verify=_verify_campaigns_updated,
)
