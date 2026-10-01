"""Read actions for campaign config dump (v1.3.0, step 0).

Seven read-only get-actions covering Direct API objects missing from the
catalog: package strategies, feeds, dynamic/feed/smart targets, business
profiles and turbo pages. VCards are intentionally absent: Yandex removed
them (API 3500 "Визитки больше не поддерживаются").
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    finalize,
    goal_label,
    map_accounts,
    micros_to_rubles,
)
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import money

_MONEY_FIELDS = frozenset(
    {
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "AverageCpc",
        "AverageCpa",
        "BidCeiling",
        "Cpa",
        "MinimumExplorationBudget",
        "SpendLimit",
        "Value",
        "Bid",
        "ContextBid",
        "FilterAverageCpa",
        "FilterAverageCpc",
    }
)

_COND_TYPE_LABELS = {
    "PAGES_ALL": "все страницы",
    "PAGES_SUBSET": "подмножество страниц",
    "ITEMS_ALL": "все товары",
    "ITEMS_SUBSET": "подмножество товаров",
}


def _short(value: object, limit: int = 120) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _val(key: str, value: object) -> str:
    """Money fields (micros) -> rubles; dicts one level deep; rest raw."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        parts = [f"{k}={_val(k, v)}" for k, v in value.items()]
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, list):
        return ", ".join(_short(v) for v in value) or "—"
    if key in _MONEY_FIELDS and isinstance(value, (int, float)):
        shown = money(micros_to_rubles(value))
        return shown if shown else str(value)
    return str(value)


def _conds(conditions: object) -> str:
    """Target conditions -> 'Operand Operator args; ...' (both shapes)."""
    if isinstance(conditions, dict):
        conditions = conditions.get("Items", [])
    if not isinstance(conditions, list) or not conditions:
        return "—"
    parts = []
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        args = cond.get("Arguments")
        args_s = (
            ", ".join(str(a) for a in args)
            if isinstance(args, list)
            else _short(args or "—", 60)
        )
        parts.append(
            f"{cond.get('Operand', '?')} {cond.get('Operator', '?')} {args_s}"
        )
    return "; ".join(parts) or "—"


def _cond_type(value: object) -> str:
    if not value:
        return "—"
    label = _COND_TYPE_LABELS.get(str(value))
    return f"{label} ({value})" if label else str(value)


def _goals(items: object, names: dict[str, str]) -> str:
    if isinstance(items, dict):
        items = items.get("Items", [])
    if not isinstance(items, list) or not items:
        return "—"
    parts = []
    for goal in items:
        if not isinstance(goal, dict):
            continue
        label = goal_label(goal.get("GoalId"), names)
        val = goal.get("Value")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            parts.append(f"{label}: {money(micros_to_rubles(val))}")
        else:
            parts.append(label)
    return "; ".join(parts) or "—"


_STRATEGY_BLOCKS = (
    "WbMaximumClicks",
    "WbMaximumConversionRate",
    "AverageCpc",
    "AverageCpa",
    "AverageCpaMultipleGoals",
    "MaxProfit",
    "PayForConversion",
    "PayForConversionMultipleGoals",
    "AverageCrr",
    "PayForConversionCrr",
    "HighestPosition",
    "ManualCpm",
    "AverageCpaPerCampaign",
    "PayForConversionPerCampaign",
    "PayForConversionPerFilter",
    "AverageCpaPerFilter",
    "AverageCpcPerCampaign",
    "AverageCpcPerFilter",
)


def _strategy_params(item: dict) -> str:
    """Compact 'Block.k=v' summary of the present subtype block."""
    for block in _STRATEGY_BLOCKS:
        sub = item.get(block)
        if not isinstance(sub, dict):
            continue
        parts = [
            f"{k}={_val(k, v)}" for k, v in sub.items() if v is not None
        ]
        return f"{block}: " + ("; ".join(parts) if parts else "—")
    return "—"


def _ids(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("Items", [])
    if not isinstance(value, list) or not value:
        return "—"
    return ", ".join(str(v) for v in value)


_STRATEGY_FIELDS = [
    "Id",
    "AttributionModel",
    "CounterIds",
    "PriorityGoals",
    "Type",
    "Name",
    "StatusArchived",
]
_STRATEGY_SUBFIELDS = {
    "StrategyMaximumClicksFieldNames": [
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "CustomPeriodBudget",
        "BudgetType",
        "BidCeiling",
    ],
    "StrategyMaximumConversionRateFieldNames": [
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "CustomPeriodBudget",
        "BudgetType",
        "BidCeiling",
        "GoalId",
    ],
    "StrategyAverageCpcFieldNames": [
        "AverageCpc",
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "CustomPeriodBudget",
        "BudgetType",
    ],
    "StrategyAverageCpaFieldNames": [
        "AverageCpa",
        "GoalId",
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "CustomPeriodBudget",
        "BudgetType",
        "BidCeiling",
        "ExplorationBudget",
    ],
    "StrategyAverageCpaMultipleGoalsFieldNames": [
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "CustomPeriodBudget",
        "BudgetType",
        "ExplorationBudget",
        "BidCeiling",
    ],
    "StrategyPayForConversionFieldNames": [
        "Cpa",
        "GoalId",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
    ],
    "StrategyPayForConversionMultipleGoalsFieldNames": [
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
    ],
    "StrategyAverageCrrFieldNames": [
        "Crr",
        "GoalId",
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "CustomPeriodBudget",
        "BudgetType",
        "ExplorationBudget",
    ],
    "StrategyPayForConversionCrrFieldNames": [
        "Crr",
        "GoalId",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
    ],
    "StrategyMaxProfitFieldNames": [
        "WeeklySpendLimit",
        "WeeklyBudgetRollover",
        "CustomPeriodBudget",
        "BudgetType",
        "ExplorationBudget",
    ],
    "StrategyAverageCpaPerCampaignFieldNames": [
        "AverageCpa",
        "GoalId",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
        "BidCeiling",
        "ExplorationBudget",
    ],
    "StrategyPayForConversionPerCampaignFieldNames": [
        "Cpa",
        "GoalId",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
    ],
    "StrategyPayForConversionPerFilterFieldNames": [
        "Cpa",
        "GoalId",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
    ],
    "StrategyAverageCpaPerFilterFieldNames": [
        "FilterAverageCpa",
        "GoalId",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
        "BidCeiling",
        "ExplorationBudget",
    ],
    "StrategyAverageCpcPerCampaignFieldNames": [
        "AverageCpc",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
        "BidCeiling",
    ],
    "StrategyAverageCpcPerFilterFieldNames": [
        "FilterAverageCpc",
        "WeeklySpendLimit",
        "CustomPeriodBudget",
        "BudgetType",
        "BidCeiling",
    ],
}


class StrategiesGetParams(GetActionParams):
    strategy_ids: list[int] = Field(default_factory=list)


@action(
    "strategies_get",
    "read",
    "Пакетные стратегии: настройки, бюджеты и цели. "
    "GoalId 13 = «все приоритетные цели» (служебное), 12 = вовлечённые сессии. "
    "API vs интерфейс: WB_MAXIMUM_CLICKS требует WeeklySpendLimit "
    "(+ опц. BidCeiling); AVERAGE_CPC — отдельная стратегия с AverageCpc, "
    "параметры не смешивать. Запись стратегий запрещена guard.",
    (
        "стратегии",
        "стратегия",
        "strategies",
        "strategy",
        "пакетная стратегия",
        "пакетные стратегии",
        "пакетная",
        "bidding",
        "назначение ставок",
    ),
    StrategiesGetParams,
)
async def _strategies(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, StrategiesGetParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        criteria = {"Ids": params.strategy_ids} if params.strategy_ids else {}
        return await client.get_all(
            "strategies",
            dict(
                {"SelectionCriteria": criteria, "FieldNames": _STRATEGY_FIELDS},
                **_STRATEGY_SUBFIELDS,
            ),
            entry.login,
            "Strategies",
            tally=tally,
        )

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = ["Id", "Name", "Type", "Archived", "Attribution", "Counters",
               "Goals", "Params"]
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
            row = {
                "Id": item.get("Id"),
                "Name": item.get("Name"),
                "Type": item.get("Type"),
                "Archived": item.get("StatusArchived"),
                "Attribution": item.get("AttributionModel"),
                "Counters": _ids(item.get("CounterIds")),
                "Goals": _goals(
                    item.get("PriorityGoals"), ctx.settings.goal_names),
                "Params": _strategy_params(item),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            rows.append(row)
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}strategies_get: {', '.join(e.login for e in entries)}."
    linked = bool(params.strategy_ids)
    return finalize(
        ctx, context, "strategies_get", display, rows, params.limit,
        params.save_as, errors, money_cols=(), output=params.output,
        format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="strategies_get",
        dump_params=params.model_dump(),
        dump_raw={"strategies_get": [
            dict(i, linked_to_campaign=linked) for i in raw]},
        dump_fields=dict({"FieldNames": _STRATEGY_FIELDS},
                         **_STRATEGY_SUBFIELDS),
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign" if linked else "cabinet",
    )


class FeedsGetParams(GetActionParams):
    feed_ids: list[int] = Field(default_factory=list)


_FEED_FIELDS = [
    "Id", "Name", "BusinessType", "SourceType", "FilterSchema", "UpdatedAt",
    "CampaignIds", "NumberOfItems", "Status", "TitleAndTextSources",
]


@action(
    "feeds_get",
    "read",
    "Фиды: источник, статус обработки, кампании и схема фильтров",
    (
        "фиды",
        "фид",
        "feeds",
        "feed",
        "источник данных",
        "каталог товаров",
        "товарный фид",
    ),
    FeedsGetParams,
)
async def _feeds(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, FeedsGetParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        # Без Ids критерий опускается целиком: пустой SelectionCriteria API
        # отклоняет ("Отсутствует обязательный параметр Ids").
        body: dict = {
            "FieldNames": _FEED_FIELDS,
            "UrlFeedFieldNames": ["Login", "Url", "RemoveUtmTags"],
            "FileFeedFieldNames": ["Filename"],
        }
        if params.feed_ids:
            body["SelectionCriteria"] = {"Ids": sorted(set(params.feed_ids))}
        return await client.get_all(
            "feeds", body, entry.login, "Feeds", tally=tally)

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = ["Id", "Name", "BusinessType", "SourceType", "Status", "Items",
               "UpdatedAt", "Source", "Campaigns", "FilterSchema",
               "TitleSources"]
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
            url_feed = item.get("UrlFeed") or {}
            file_feed = item.get("FileFeed") or {}
            row = {
                "Id": item.get("Id"),
                "Name": item.get("Name"),
                "BusinessType": item.get("BusinessType"),
                "SourceType": item.get("SourceType"),
                "Status": item.get("Status"),
                "Items": item.get("NumberOfItems"),
                "UpdatedAt": item.get("UpdatedAt"),
                "Source": url_feed.get("Url") or file_feed.get("Filename"),
                "Campaigns": _ids(item.get("CampaignIds")),
                "FilterSchema": item.get("FilterSchema"),
                "TitleSources": _ids(item.get("TitleAndTextSources")),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            rows.append(row)
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}feeds_get: {', '.join(e.login for e in entries)}."
    linked = bool(params.feed_ids)
    return finalize(
        ctx, context, "feeds_get", display, rows, params.limit,
        params.save_as, errors, money_cols=(), output=params.output,
        format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="feeds_get", dump_params=params.model_dump(),
        dump_raw={"feeds_get": [
            dict(i, linked_to_campaign=linked) for i in raw]},
        dump_fields={"FieldNames": _FEED_FIELDS,
                     "UrlFeedFieldNames": ["Login", "Url", "RemoveUtmTags"],
                     "FileFeedFieldNames": ["Filename"]},
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign" if linked else "cabinet",
    )


class _TargetsParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)
    target_ids: list[int] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)


def _targets_criteria(params: _TargetsParams) -> dict:
    criteria: dict = {}
    if params.target_ids:
        criteria["Ids"] = sorted(set(params.target_ids))
    if params.adgroup_ids:
        criteria["AdGroupIds"] = sorted(set(params.adgroup_ids))
    if params.campaign_ids:
        criteria["CampaignIds"] = sorted(set(params.campaign_ids))
    if params.states:
        criteria["States"] = list(params.states)
    return criteria


async def _fetch_targets(
    ctx: Ctx,
    params: _TargetsParams,
    service: str,
    items_key: str,
    field_names: list[str],
    tally: dict | None = None,
) -> tuple[list[AccountEntry], list[dict], list[str], list[tuple]]:
    """Shared get-loop for the three target services. Returns entries/rows/errors/raw."""
    if not params.campaign_ids and not params.adgroup_ids \
            and not params.target_ids:
        return [], [], ["Ошибка: укажите campaign_ids, adgroup_ids или target_ids."], []

    async def fetch(entry: AccountEntry, client):
        items: list[dict] = []
        base = _targets_criteria(params)
        if params.campaign_ids and not params.adgroup_ids \
                and not params.target_ids:
            for ids in chunk(params.campaign_ids, 10):
                criteria = dict(base, CampaignIds=list(ids))
                items.extend(
                    await client.get_all(
                        service,
                        {"SelectionCriteria": criteria,
                         "FieldNames": field_names},
                        entry.login,
                        items_key,
                        tally=tally,
                    )
                )
        else:
            items.extend(
                await client.get_all(
                    service,
                    {"SelectionCriteria": base, "FieldNames": field_names},
                    entry.login,
                    items_key,
                    tally=tally,
                )
            )
        return items

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    rows: list[dict] = []
    raws: list[tuple] = []
    errors: list[str] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, list)
        for item in payload:
            raws.append((entry, item))
    return entries, rows, errors, raws


def _base_target_row(entry: AccountEntry, item: dict, multi: bool) -> dict:
    row = {
        "Id": item.get("Id"),
        "CampaignId": item.get("CampaignId"),
        "AdGroupId": item.get("AdGroupId"),
        "Name": item.get("Name"),
        "State": item.get("State"),
    }
    if multi:
        row["_account"] = entry.login
    return row


class DynamicTargetsGetParams(_TargetsParams):
    pass


_DYNAMIC_FIELDS = [
    "Id", "CampaignId", "AdGroupId", "Name", "Bid", "ContextBid",
    "StrategyPriority", "State", "StatusClarification", "Conditions",
    "ConditionType",
]


@action(
    "dynamic_targets_get",
    "read",
    "Условия нацеливания динамических объявлений (сайт): ставки и фильтры страниц",
    (
        "динамические объявления",
        "динамический таргетинг",
        "условия нацеливания динамических",
        "dynamic",
        "веб-страницы",
        "webpages",
        "таргеты",
        "условия показа",
    ),
    DynamicTargetsGetParams,
)
async def _dynamic(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, DynamicTargetsGetParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}
    entries, rows, errors, raws = await _fetch_targets(
        ctx, params, "dynamictextadtargets", "Webpages", _DYNAMIC_FIELDS,
        tally=tally)
    if not entries:
        return "\n\n".join(errors) if errors else "Нет данных."
    multi = len(entries) > 1
    for entry, item in raws:
        row = _base_target_row(entry, item, multi)
        row.update(
            {
                "Bid": money(micros_to_rubles(item.get("Bid"))),
                "ContextBid": money(micros_to_rubles(item.get("ContextBid"))),
                "Priority": item.get("StrategyPriority"),
                "CondType": _cond_type(item.get("ConditionType")),
                "Conditions": _conds(item.get("Conditions")),
                "Clarification": item.get("StatusClarification"),
            }
        )
        rows.append(row)
    columns = ["Id", "CampaignId", "AdGroupId", "Name", "State", "Bid",
               "ContextBid", "Priority", "CondType", "Conditions",
               "Clarification"]
    display = (["_account"] if multi else []) + columns
    context = (f"{mark}dynamic_targets_get: "
               f"{', '.join(e.login for e in entries)}.")
    return finalize(
        ctx, context, "dynamic_targets_get", display, rows, params.limit,
        params.save_as, errors, money_cols=(), output=params.output,
        format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="dynamic_targets_get",
        dump_params=params.model_dump(),
        dump_raw={"dynamic_targets_get": [
            dict(i, linked_to_campaign=True) for _, i in raws]},
        dump_fields={"FieldNames": _DYNAMIC_FIELDS},
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )


class DynamicFeedTargetsGetParams(_TargetsParams):
    pass


_DYNAMIC_FEED_FIELDS = [
    "Id", "AdGroupId", "CampaignId", "Name", "Bid", "ContextBid",
    "Conditions", "ConditionType", "State", "AvailableItemsOnly",
]


@action(
    "dynamic_feed_targets_get",
    "read",
    "Условия нацеливания динамических объявлений по фиду: ставки и фильтры",
    (
        "фид-таргеты",
        "динамические по фиду",
        "dynamic feed",
        "условия по фиду",
        "фильтры фида",
        "таргеты фида",
    ),
    DynamicFeedTargetsGetParams,
)
async def _dynamic_feed(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, DynamicFeedTargetsGetParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}
    entries, rows, errors, raws = await _fetch_targets(
        ctx, params, "dynamicfeedadtargets", "DynamicFeedAdTargets",
        _DYNAMIC_FEED_FIELDS, tally=tally)
    if not entries:
        return "\n\n".join(errors) if errors else "Нет данных."
    multi = len(entries) > 1
    for entry, item in raws:
        row = _base_target_row(entry, item, multi)
        row.update(
            {
                "Bid": money(micros_to_rubles(item.get("Bid"))),
                "ContextBid": money(micros_to_rubles(item.get("ContextBid"))),
                "CondType": _cond_type(item.get("ConditionType")),
                "Conditions": _conds(item.get("Conditions")),
                "AvailableOnly": item.get("AvailableItemsOnly"),
            }
        )
        rows.append(row)
    columns = ["Id", "CampaignId", "AdGroupId", "Name", "State", "Bid",
               "ContextBid", "CondType", "Conditions", "AvailableOnly"]
    display = (["_account"] if multi else []) + columns
    context = (f"{mark}dynamic_feed_targets_get: "
               f"{', '.join(e.login for e in entries)}.")
    return finalize(
        ctx, context, "dynamic_feed_targets_get", display, rows,
        params.limit, params.save_as, errors, money_cols=(),
        output=params.output, format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="dynamic_feed_targets_get",
        dump_params=params.model_dump(),
        dump_raw={"dynamic_feed_targets_get": [
            dict(i, linked_to_campaign=True) for _, i in raws]},
        dump_fields={"FieldNames": _DYNAMIC_FEED_FIELDS},
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )


class SmartTargetsGetParams(_TargetsParams):
    pass


_SMART_FIELDS = [
    "Id", "AdGroupId", "CampaignId", "Name", "AverageCpc", "AverageCpa",
    "StrategyPriority", "Conditions", "ConditionType", "State", "Audience",
    "AvailableItemsOnly",
]


@action(
    "smart_targets_get",
    "read",
    "Фильтры смарт-баннеров: аудитория, ставки и условия",
    (
        "смарт-баннеры",
        "смарт-таргеты",
        "smart",
        "фильтры смарт",
        "аудитория смарт",
        "смартбаннеры",
    ),
    SmartTargetsGetParams,
)
async def _smart(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, SmartTargetsGetParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}
    entries, rows, errors, raws = await _fetch_targets(
        ctx, params, "smartadtargets", "SmartAdTargets", _SMART_FIELDS,
        tally=tally)
    if not entries:
        return "\n\n".join(errors) if errors else "Нет данных."
    multi = len(entries) > 1
    for entry, item in raws:
        row = _base_target_row(entry, item, multi)
        row.update(
            {
                "AvgCpc": money(micros_to_rubles(item.get("AverageCpc"))),
                "AvgCpa": money(micros_to_rubles(item.get("AverageCpa"))),
                "Priority": item.get("StrategyPriority"),
                "Audience": item.get("Audience"),
                "CondType": _cond_type(item.get("ConditionType")),
                "Conditions": _conds(item.get("Conditions")),
                "AvailableOnly": item.get("AvailableItemsOnly"),
            }
        )
        rows.append(row)
    columns = ["Id", "CampaignId", "AdGroupId", "Name", "State", "AvgCpc",
               "AvgCpa", "Priority", "Audience", "CondType", "Conditions",
               "AvailableOnly"]
    display = (["_account"] if multi else []) + columns
    context = (f"{mark}smart_targets_get: "
               f"{', '.join(e.login for e in entries)}.")
    return finalize(
        ctx, context, "smart_targets_get", display, rows, params.limit,
        params.save_as, errors, money_cols=(), output=params.output,
        format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="smart_targets_get",
        dump_params=params.model_dump(),
        dump_raw={"smart_targets_get": [
            dict(i, linked_to_campaign=True) for _, i in raws]},
        dump_fields={"FieldNames": _SMART_FIELDS},
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )


class BusinessesGetParams(GetActionParams):
    business_ids: list[int] = Field(default_factory=list)


_BUSINESS_FIELDS = [
    "Id", "Name", "Address", "Phone", "ProfileUrl", "InternalUrl",
    "IsPublished", "MergedIds", "Rubric", "Urls", "HasOffice",
]


@action(
    "businesses_get",
    "read",
    "Профили организаций: адрес, телефон, публикация и ссылки",
    (
        "организации",
        "организация",
        "бизнес",
        "businesses",
        "business",
        "профиль организации",
        "businessid",
        "бизнес-профиль",
    ),
    BusinessesGetParams,
)
async def _businesses(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, BusinessesGetParams)
    if not params.business_ids:
        return ("Ошибка: укажите business_ids (профили из объявлений; "
                "весь кабинет не выгружается).")
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        return await client.get_all(
            "businesses",
            {
                "SelectionCriteria": {"Ids": sorted(set(params.business_ids))},
                "FieldNames": _BUSINESS_FIELDS,
            },
            entry.login,
            "Businesses",
            page_limit=1000,  # больше businesses.get отклоняет (4002)
            tally=tally,
        )

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = ["Id", "Name", "Address", "Phone", "Published", "Rubric",
               "Urls", "ProfileUrl"]
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
            row = {
                "Id": item.get("Id"),
                "Name": item.get("Name"),
                "Address": item.get("Address"),
                "Phone": item.get("Phone"),
                "Published": item.get("IsPublished"),
                "Rubric": item.get("Rubric"),
                "Urls": _ids(item.get("Urls")),
                "ProfileUrl": item.get("ProfileUrl"),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            rows.append(row)
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}businesses_get: {', '.join(e.login for e in entries)}."
    return finalize(
        ctx, context, "businesses_get", display, rows, params.limit,
        params.save_as, errors, money_cols=(), output=params.output,
        format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="businesses_get", dump_params=params.model_dump(),
        dump_raw={"businesses_get": [
            dict(i, linked_to_campaign=True) for i in raw]},
        dump_fields={"FieldNames": _BUSINESS_FIELDS},
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )


class TurboPagesGetParams(GetActionParams):
    turbopage_ids: list[int] = Field(default_factory=list)


_TURBO_FIELDS = ["Id", "Name", "Href", "PreviewHref", "TurboSiteHref",
                 "BoundWithHref"]


@action(
    "turbopages_get",
    "read",
    "Турбо-страницы: название, ссылки и предпросмотр (без содержимого блоков). "
    "Содержимое Турбо-блоков и clients.site недоступно через API; "
    "CPM-видео-креативы создаются только в интерфейсе.",
    (
        "турбо-страницы",
        "турбо-страница",
        "турбо",
        "turbopages",
        "turbo",
        "лендинг",
        "turbopageid",
    ),
    TurboPagesGetParams,
)
async def _turbo(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, TurboPagesGetParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        criteria = ({"Ids": sorted(set(params.turbopage_ids))}
                    if params.turbopage_ids else {})
        return await client.get_all(
            "turbopages",
            {"SelectionCriteria": criteria, "FieldNames": _TURBO_FIELDS},
            entry.login,
            "TurboPages",
            tally=tally,
        )

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = ["Id", "Name", "Href", "Preview", "Site"]
    rows: list[dict] = []
    errors: list[str] = []
    raw: list[dict] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, list)
        raw.extend(payload)
        for item in payload:
            row = {
                "Id": item.get("Id"),
                "Name": item.get("Name"),
                "Href": item.get("Href"),
                "Preview": item.get("PreviewHref"),
                "Site": item.get("TurboSiteHref"),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            rows.append(row)
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}turbopages_get: {', '.join(e.login for e in entries)}."
    linked = bool(params.turbopage_ids)
    return finalize(
        ctx, context, "turbopages_get", display, rows, params.limit,
        params.save_as, errors, money_cols=(), output=params.output,
        format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="turbopages_get", dump_params=params.model_dump(),
        dump_raw={"turbopages_get": [
            dict(i, linked_to_campaign=linked) for i in raw]},
        dump_fields={"FieldNames": _TURBO_FIELDS},
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign" if linked else "cabinet",
    )
