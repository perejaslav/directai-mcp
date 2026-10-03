"""Read/write actions for retargeting (B4, v1.10.0).

Reads: retargeting_lists_list (cabinet conditions + where used),
audience_targets_list (bindings by campaign/group/target/list).
Writes (all behind [retargeting] write_enabled, default false):
retargeting_list_create/update/delete, audience_target_add/state.
Bid adjustments for audiences live in bid_modifiers_set (RETARGETING kind).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    finalize,
    goal_label,
    micros_to_rubles,
    split_request,
    summarize,
)
from directai_mcp.catalog.registry import Ctx, action, write_action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import money
from directai_mcp.safety.guard import TEST_PREFIX

LIST_FIELDS = [
    "Id", "Type", "Name", "Description", "Rules",
    "IsAvailable", "Scope", "AvailableForTargetsInAdGroupTypes",
]
TARGET_FIELDS = [
    "Id", "AdGroupId", "CampaignId", "RetargetingListId", "InterestId",
    "ContextBid", "StrategyPriority", "State",
]

# Limits from Direct API docs (retargetinglists/add, objects/retargeting-list).
MAX_LISTS_PER_CALL = 1000
MAX_RULES = 50
MAX_ARGS_PER_RULE = 250
MAX_NAME_LEN = 250
MAX_DESC_LEN = 4096
MIN_LIFESPAN = 1
MAX_LIFESPAN = 540
# Floor reused from keywords (MIN_KW_BID 0.30). Ceiling is a client-side
# sanity cap, NOT an API limit (API caps live in Dictionaries.Currencies).
MIN_CONTEXT_BID = 0.30
MAX_CONTEXT_BID_RUB = 5000.0
MANUAL_SEARCH = "HIGHEST_POSITION"
MANUAL_NETWORK = ("MAXIMUM_COVERAGE", "MANUAL_CPM")

# v1.10.1: API v5 трактует ЕПК-группы как TEXT_AD_GROUP (живая проверка
# 01.10.2026: привязка 48420621 на UNIFIED_AD_GROUP 5203919471 существует,
# хотя AvailableForTargetsInAdGroupTypes никогда не содержит UNIFIED_AD_GROUP).
V501_TO_V5_ADGROUP_TYPE = {"UNIFIED_AD_GROUP": "TEXT_AD_GROUP"}

# AND/OR reminder required in every plan_write preview (A8).
AND_OR_NOTE = (
    "На поиске условие сужает аудиторию (AND с фразами), "
    "в сетях — расширяет (OR). Исключить аудиторию (например, текущих "
    "клиентов) можно только корректировкой ставки −100% "
    "(bid_modifiers_set, RETARGETING), а не условием."
)


def _single_entry(ctx: Ctx, account_value: str) -> tuple[AccountEntry | None, str | None]:
    try:
        entries = ctx.accounts(account_value)
    except Exception as e:  # noqa: BLE001 — ConfigError -> текст ошибки
        return None, f"Ошибка: {e}"
    if len(entries) != 1:
        return None, (
            "Ошибка: укажите ровно один кабинет (alias/логин), не 'all'/'active' "
            f"(получено кабинетов: {len(entries)})."
        )
    return entries[0], None


def _interest_label(external_id: object) -> str | None:
    """ExternalId 10/20/30-префикс -> 'интерес ...' (живой формат 10.2026)."""
    text = str(external_id or "")
    if len(text) >= 11 and text[:2] in ("10", "20", "30") and text[2:].isdigit():
        kind = {"10": "краткосрочный интерес", "20": "долгосрочный интерес",
                "30": "интерес за любой период"}[text[:2]]
        return f"{kind} {text[2:]}"
    return None


def _arg_label(external_id: object, span: object, goal_names: dict) -> str:
    interest = _interest_label(external_id)
    if interest is not None:
        return interest
    label = goal_label(external_id, goal_names or {})
    if span is not None:
        label += f" ({span} дн.)"
    return label


def human_rules(rules: object, goal_names: dict | None = None) -> str:
    """Rules -> человекочитаемое условие («... И НЕ ...»)."""
    names = goal_names or {}
    if not isinstance(rules, list) or not rules:
        return "—"
    blocks = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        operator = rule.get("Operator") or "?"
        args = rule.get("Arguments")
        parts = []
        if isinstance(args, list):
            for arg in args:
                if not isinstance(arg, dict):
                    continue
                parts.append(_arg_label(
                    arg.get("ExternalId"), arg.get("MembershipLifeSpan"), names))
        body = "; ".join(parts) if parts else "—"
        if operator == "ALL":
            blocks.append(f"выполнили всё: {body}")
        elif operator == "ANY":
            blocks.append(f"выполнили хотя бы одно: {body}")
        elif operator == "NONE":
            blocks.append(f"НЕ выполнили ничего: {body}")
        else:
            blocks.append(f"{operator}: {body}")
    return " И ".join(blocks) if blocks else "—"


def _scope_label(scope: object) -> str:
    return {
        "FOR_TARGETS_AND_ADJUSTMENTS": "нацеливание и корректировки",
        "FOR_ADJUSTMENTS_ONLY": "только корректировки (привязать к группе нельзя)",
        "FOR_TARGETS_ONLY": "только нацеливание",
    }.get(str(scope), str(scope) if scope is not None else "—")


def _available_label(value: object) -> str:
    if isinstance(value, dict):
        items = value.get("Items")
        if isinstance(items, list) and items:
            return ", ".join(str(i) for i in items)
    return "только корректировки"


def _has_positive(rules: list[dict]) -> bool:
    return any(r.get("Operator") in ("ALL", "ANY") for r in rules if isinstance(r, dict))


class RetargetingRuleArg(BaseModel):
    external_id: int
    membership_life_span: int | None = None


class RetargetingRule(BaseModel):
    operator: str
    arguments: list[RetargetingRuleArg] = Field(min_length=1)


def validate_rules(list_type: str, rules: list[RetargetingRule]) -> tuple[list[dict], list[str]]:
    """Rules -> API body. Raises ValueError (отказ до API)."""
    if list_type not in ("RETARGETING", "AUDIENCE"):
        raise ValueError("type только RETARGETING или AUDIENCE.")
    if not rules:
        raise ValueError("укажите rules (1..50 правил).")
    if len(rules) > MAX_RULES:
        raise ValueError(f"правил {len(rules)} (лимит {MAX_RULES}).")
    api_rules: list[dict] = []
    warnings: list[str] = []
    for rule in rules:
        if rule.operator not in ("ALL", "ANY", "NONE"):
            raise ValueError(f"operator только ALL/ANY/NONE (получено {rule.operator}).")
        if len(rule.arguments) > MAX_ARGS_PER_RULE:
            raise ValueError(f"аргументов {len(rule.arguments)} (лимит {MAX_ARGS_PER_RULE}).")
        api_args = []
        for arg in rule.arguments:
            if arg.external_id == 0:
                raise ValueError("external_id не может быть 0.")
            if arg.membership_life_span is not None and not (
                MIN_LIFESPAN <= arg.membership_life_span <= MAX_LIFESPAN
            ):
                raise ValueError(
                    f"срок членства {arg.membership_life_span} вне "
                    f"{MIN_LIFESPAN}..{MAX_LIFESPAN} дней."
                )
            if arg.external_id in (12, 13):
                raise ValueError(
                    f"ExternalId {arg.external_id} — служебная цель, "
                    "Директ не принимает её в условиях ретаргетинга (ошибка 8800)."
                )
            api_args.append({
                "ExternalId": int(arg.external_id),
                **({"MembershipLifeSpan": int(arg.membership_life_span)}
                   if arg.membership_life_span is not None else {}),
            })
        api_rules.append({"Operator": rule.operator, "Arguments": api_args})
    if list_type == "AUDIENCE" and not _has_positive(
        [{"Operator": r.operator} for r in rules]
    ):
        raise ValueError(
            "условие AUDIENCE должно содержать хотя бы одно правило ALL/ANY "
            "(только-NONE API отклоняет)."
        )
    if not _has_positive([{"Operator": r.operator} for r in rules]):
        warnings.append(
            "Условие только из NONE-правил: Scope будет FOR_ADJUSTMENTS_ONLY — "
            "только для корректировок ставок, привязать к группе нельзя."
        )
    if list_type == "AUDIENCE":
        warnings.append(
            "Тип AUDIENCE — для медийных групп; для ЕПК/текстово-графических "
            "привязка проверяется по AvailableForTargetsInAdGroupTypes "
            "(краткосрочные интересы допустимы и в ТГО)."
        )
    return api_rules, warnings


async def _fetch_lists(client, login: str, ids: list[int] | None,
                       types: list[str] | None) -> list[dict]:
    criteria: dict = {}
    if ids:
        criteria["Ids"] = sorted(set(ids))
    if types:
        criteria["Types"] = list(types)
    params = {"FieldNames": LIST_FIELDS}
    if criteria:
        params["SelectionCriteria"] = criteria
    return await client.get_all(
        "retargetinglists", params, login, "RetargetingLists")


async def _usage_map(client, login: str, list_ids: list[int]) -> dict[int, list[dict]]:
    """RetargetingListId -> [{target_id, adgroup_id, campaign_id}]."""
    out: dict[int, list[dict]] = {}
    for part in chunk(sorted(set(list_ids)), 1000):
        targets = await client.get_all(
            "audiencetargets",
            {"SelectionCriteria": {"RetargetingListIds": part},
             "FieldNames": ["Id", "AdGroupId", "CampaignId", "RetargetingListId"]},
            login, "AudienceTargets",
        )
        for t in targets:
            if not isinstance(t, dict) or t.get("RetargetingListId") is None:
                continue
            try:
                lid = int(t["RetargetingListId"])
            except (TypeError, ValueError):
                continue
            out.setdefault(lid, []).append(t)
    return out


class RetargetingListsListParams(GetActionParams):
    account: str = Field(default="active", description="Ровно один кабинет: алиас или логин.")
    list_ids: list[int] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)


@action(
    "retargeting_lists_list",
    "read",
    "Условия ретаргетинга кабинета: правила человеческим языком, где используются",
    ("ретаргетинг", "retargeting", "условия", "списки ретаргетинга", "аудитории"),
    RetargetingListsListParams,
)
async def _lists_list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, RetargetingListsListParams)
    entry, err = _single_entry(ctx, params.account)
    if err is not None or entry is None:
        return err or "Ошибка: кабинет не найден."
    for t in params.types:
        if t not in ("RETARGETING", "AUDIENCE"):
            return "Ошибка: types только RETARGETING и AUDIENCE."
    client = ctx.direct()
    try:
        try:
            lists = await _fetch_lists(
                client, entry.login,
                [int(i) for i in params.list_ids] or None,
                list(params.types) or None,
            )
        except DirectError as e:
            return f"Ошибка: {e.human_message()}"
        try:
            usage = await _usage_map(
                client, entry.login,
                [int(r["Id"]) for r in lists if r.get("Id") is not None])
        except DirectError:
            usage = {}
    finally:
        await client.aclose()
    rows = []
    for rl in lists:
        lid = rl.get("Id")
        used = usage.get(int(lid), []) if lid is not None else []
        rows.append({
            "Id": lid,
            "Type": rl.get("Type"),
            "Name": rl.get("Name"),
            "Available": rl.get("IsAvailable"),
            "Scope": _scope_label(rl.get("Scope")),
            "Rules": human_rules(rl.get("Rules"), ctx.settings.goal_names),
            "UsedIn": ("; ".join(
                f"таргетинг {t.get('Id')} (группа {t.get('AdGroupId')}, "
                f"кампания {t.get('CampaignId')})" for t in used
            ) if used else "не используется"),
        })
    context = (
        f"retargeting_lists_list @ {entry.login}: условий {len(rows)}. "
        "Правила объединяются по AND."
    )
    return finalize(
        ctx, context, "retargeting_lists_list",
        ["Id", "Type", "Name", "Available", "Scope", "Rules", "UsedIn"],
        rows, params.limit, params.save_as, [],
        money_cols=(), output=params.output, format=params.format,
        account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="retargeting_lists_list", dump_params=params.model_dump(),
        dump_raw={"retargeting_lists_list": lists},
        dump_fields={"FieldNames": LIST_FIELDS},
        dump_tally={}, dump_logins=[entry.login], dump_scope="account",
    )


class AudienceTargetsListParams(GetActionParams):
    account: str = Field(default="active", description="Ровно один кабинет: алиас или логин.")
    campaign_id: int | None = None
    adgroup_ids: list[int] = Field(default_factory=list)
    target_ids: list[int] = Field(default_factory=list)
    retargeting_list_ids: list[int] = Field(default_factory=list)


@action(
    "audience_targets_list",
    "read",
    "Привязки аудиторий к группам: какое условие, состояние, ставка/приоритет",
    ("привязки", "таргетинг", "audience", "нацеливание", "ретаргетинг"),
    AudienceTargetsListParams,
)
async def _targets_list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AudienceTargetsListParams)
    scopes = sum([
        params.campaign_id is not None, bool(params.adgroup_ids),
        bool(params.target_ids), bool(params.retargeting_list_ids),
    ])
    if scopes != 1:
        return ("Ошибка: укажите ровно одно из: campaign_id | adgroup_ids | "
                "target_ids | retargeting_list_ids.")
    entry, err = _single_entry(ctx, params.account)
    if err is not None or entry is None:
        return err or "Ошибка: кабинет не найден."
    if params.campaign_id is not None:
        criteria = {"CampaignIds": [int(params.campaign_id)]}
    elif params.adgroup_ids:
        criteria = {"AdGroupIds": [int(i) for i in params.adgroup_ids]}
    elif params.target_ids:
        criteria = {"Ids": [int(i) for i in params.target_ids]}
    else:
        criteria = {"RetargetingListIds": [int(i) for i in params.retargeting_list_ids]}
    client = ctx.direct()
    try:
        try:
            targets = await client.get_all(
                "audiencetargets",
                {"SelectionCriteria": criteria, "FieldNames": TARGET_FIELDS},
                entry.login, "AudienceTargets",
            )
        except DirectError as e:
            return f"Ошибка: {e.human_message()}"
        names: dict[int, str] = {}
        lids = sorted({int(t["RetargetingListId"]) for t in targets
                       if t.get("RetargetingListId") is not None})
        if lids:
            try:
                for rl in await _fetch_lists(client, entry.login, lids, None):
                    if rl.get("Id") is not None:
                        names[int(rl["Id"])] = str(rl.get("Name") or rl["Id"])
            except DirectError:
                pass
    finally:
        await client.aclose()
    rows = []
    for t in targets:
        lid = t.get("RetargetingListId")
        label = f"{names.get(int(lid))} ({lid})" if lid is not None and int(lid) in names \
            else (str(lid) if lid is not None else f"интерес {t.get('InterestId')}")
        rows.append({
            "Id": t.get("Id"),
            "CampaignId": t.get("CampaignId"),
            "AdGroupId": t.get("AdGroupId"),
            "List": label,
            "State": t.get("State"),
            "ContextBid": money(micros_to_rubles(t.get("ContextBid"))),
            "Priority": t.get("StrategyPriority"),
        })
    context = (
        f"audience_targets_list @ {entry.login}: привязок {len(rows)}. "
        "На поиске условие сужает аудиторию (AND с фразами), "
        "в сетях — расширяет (OR)."
    )
    return finalize(
        ctx, context, "audience_targets_list",
        ["Id", "CampaignId", "AdGroupId", "List", "State", "ContextBid", "Priority"],
        rows, params.limit, params.save_as, [],
        money_cols=(), output=params.output, format=params.format,
        account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="audience_targets_list", dump_params=params.model_dump(),
        dump_raw={"audience_targets_list": targets},
        dump_fields={"FieldNames": TARGET_FIELDS},
        dump_tally={}, dump_logins=[entry.login], dump_scope="campaign",
    )


class RetargetingListCreateParams(GetActionParams):
    name: str
    list_type: str = "RETARGETING"
    rules: list[RetargetingRule] = Field(min_length=1)
    description: str | None = None


async def _prepare_list_create(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    assert isinstance(params, RetargetingListCreateParams)
    name = (params.name or "").strip()
    if not name:
        raise ValueError("укажите name.")
    if len(name) > MAX_NAME_LEN:
        raise ValueError(f"name длиннее {MAX_NAME_LEN} символов.")
    if params.description is not None and len(params.description) > MAX_DESC_LEN:
        raise ValueError(f"description длиннее {MAX_DESC_LEN} символов.")
    api_rules, warnings = validate_rules(params.list_type, list(params.rules))
    body: dict = {"Name": name, "Rules": api_rules}
    if params.list_type != "RETARGETING":
        body["Type"] = params.list_type
    if params.description:
        body["Description"] = params.description
    preview = (
        "Будет создано условие ретаргетинга:\n"
        f"- Название: {name}\n"
        f"- Тип: {params.list_type}\n"
        f"- Правило: {human_rules(api_rules, ctx.settings.goal_names)}\n"
        f"- {AND_OR_NOTE}"
    )
    return {
        "before": None,
        "requests": [("retargetinglists", "add", {"RetargetingLists": [body]}, "v5")],
        "preview": preview,
        "warnings": warnings,
    }


async def _apply_single(ctx: Ctx, entry: AccountEntry, plan, key: str) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    service, method, body, version = split_request(plan.requests[0])
    client = ctx.direct()
    try:
        try:
            result = await client.call(service, method, body, entry.login, version)
        except DirectUnverifiedError:
            raise
        except DirectError as e:
            return {"status": "failed", "lines": [f"Ошибка API: {e.human_message()}"],
                    "response": {"error": e.human_message()}}
    finally:
        await client.aclose()
    items = result.get(key, [])
    labels = [str(i + 1) for i in range(len(items))]
    lines, ok = summarize(labels, items)
    dup = any(i.get("Warnings") for i in items if isinstance(i, dict))
    if dup:
        lines.append("Условие с таким набором правил уже существовало — возвращён его Id.")
    total = len(items)
    return {"status": "applied" if ok == total else "failed" if ok == 0 else "partial",
            "lines": lines, "response": result}


async def _verify_list_present(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    last = getattr(plan, "last_response", None) or {}
    created = [r.get("Id") for r in (last.get("response") or {}).get("AddResults", [])
               if r.get("Id") is not None]
    if not created:
        return {"after": None, "ok": False, "note": "read-back: условие не создано."}
    client = ctx.direct()
    try:
        found = await client.get_all(
            "retargetinglists",
            {"SelectionCriteria": {"Ids": created}, "FieldNames": ["Id", "Name"]},
            entry.login, "RetargetingLists")
    finally:
        await client.aclose()
    have = {int(i["Id"]) for i in found if i.get("Id") is not None}
    missing = [c for c in created if c not in have]
    if missing:
        return {"after": have, "ok": False,
                "note": "read-back НЕ подтвердил id: " + ", ".join(map(str, missing))}
    return {"after": {int(i["Id"]): i.get("Name") for i in found if i.get("Id") is not None},
            "ok": True, "note": "подтверждено read-back."}


write_action(
    "retargeting_list_create",
    "Создание условия ретаргетинга (цели Метрики / сегменты). Только запись через план; "
    "исключение аудитории — только корректировкой −100%, не условием.",
    ("создать условие", "ретаргетинг", "retargeting", "условие ретаргетинга", "аудитория"),
    RetargetingListCreateParams,
    prepare=_prepare_list_create,
    apply=lambda ctx, entry, plan: _apply_single(ctx, entry, plan, "AddResults"),
    verify=_verify_list_present,
)


class RetargetingListUpdateParams(GetActionParams):
    list_id: int
    name: str | None = None
    description: str | None = None
    rules: list[RetargetingRule] | None = None


async def _prepare_list_update(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    assert isinstance(params, RetargetingListUpdateParams)
    if params.name is None and params.description is None and params.rules is None:
        raise ValueError("укажите name, description или rules.")
    if params.name is not None and len(params.name) > MAX_NAME_LEN:
        raise ValueError(f"name длиннее {MAX_NAME_LEN} символов.")
    if params.description is not None and len(params.description) > MAX_DESC_LEN:
        raise ValueError(f"description длиннее {MAX_DESC_LEN} символов.")
    client = ctx.direct()
    try:
        found = await client.get_all(
            "retargetinglists",
            {"SelectionCriteria": {"Ids": [int(params.list_id)]},
             "FieldNames": ["Id", "Type", "Name", "Rules"]},
            entry.login, "RetargetingLists")
    finally:
        await client.aclose()
    if not found:
        raise ValueError(f"условие {params.list_id} не найдено.")
    current = found[0]
    list_type = str(current.get("Type") or "RETARGETING")
    body: dict = {"Id": int(params.list_id)}
    lines = []
    warnings: list[str] = []
    before = {"Name": current.get("Name"), "Type": list_type}
    if params.name is not None:
        body["Name"] = params.name
        lines.append(f"Название: {current.get('Name')} → {params.name}")
    if params.description is not None:
        body["Description"] = params.description
        lines.append("Описание: обновлено")
    if params.rules is not None:
        api_rules, warnings = validate_rules(list_type, list(params.rules))
        old_pos = _has_positive(current.get("Rules") or [])
        new_pos = _has_positive(api_rules)
        if old_pos != new_pos:
            raise ValueError(
                "API запрещает менять класс условия (ALL/ANY ↔ только-NONE): "
                "создайте новое условие."
            )
        body["Rules"] = api_rules
        lines.append(
            f"Правила: {human_rules(current.get('Rules'), ctx.settings.goal_names)} → "
            f"{human_rules(api_rules, ctx.settings.goal_names)}"
        )
        lines.append(AND_OR_NOTE)
    return {
        "before": before,
        "requests": [("retargetinglists", "update", {"RetargetingLists": [body]}, "v5")],
        "preview": f"Условие {params.list_id} ({list_type}):\n"
                   + "\n".join(f"- {line}" for line in lines),
        "warnings": warnings,
    }


async def _verify_list_updated(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    want = plan.requests[0][2]["RetargetingLists"][0]
    client = ctx.direct()
    try:
        found = await client.get_all(
            "retargetinglists",
            {"SelectionCriteria": {"Ids": [int(params["list_id"])]},
             "FieldNames": ["Id", "Name", "Description"]},
            entry.login, "RetargetingLists")
    finally:
        await client.aclose()
    if not found:
        return {"after": None, "ok": False, "note": "read-back: условие исчезло."}
    bad = []
    if "Name" in want and found[0].get("Name") != want["Name"]:
        bad.append("name не совпало")
    if bad:
        return {"after": found[0], "ok": False,
                "note": "read-back НЕ подтвердил: " + "; ".join(bad)}
    return {"after": found[0], "ok": True, "note": "подтверждено read-back."}


write_action(
    "retargeting_list_update",
    "Изменение условия ретаргетинга: имя/описание/правила (класс ALL-ANY ↔ только-NONE менять нельзя)",
    ("изменить условие", "ретаргетинг", "retargeting", "обновить условие"),
    RetargetingListUpdateParams,
    prepare=_prepare_list_update,
    apply=lambda ctx, entry, plan: _apply_single(ctx, entry, plan, "UpdateResults"),
    verify=_verify_list_updated,
)


class RetargetingListDeleteParams(GetActionParams):
    list_id: int


async def _prepare_list_delete(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    assert isinstance(params, RetargetingListDeleteParams)
    client = ctx.direct()
    try:
        found = await client.get_all(
            "retargetinglists",
            {"SelectionCriteria": {"Ids": [int(params.list_id)]},
             "FieldNames": ["Id", "Name", "Type"]},
            entry.login, "RetargetingLists")
        if not found:
            raise ValueError(f"условие {params.list_id} не найдено.")
        usage = await _usage_map(client, entry.login, [int(params.list_id)])
    finally:
        await client.aclose()
    used = usage.get(int(params.list_id), [])
    if used:
        groups = "; ".join(
            f"таргетинг {t.get('Id')} (группа {t.get('AdGroupId')}, "
            f"кампания {t.get('CampaignId')})" for t in used)
        raise ValueError(
            f"условие {params.list_id} используется: {groups}. "
            "Сначала отвяжите его (audience_target_state delete)."
        )
    name = str(found[0].get("Name") or params.list_id)
    warnings = []
    if not name.startswith(TEST_PREFIX):
        warnings.append(
            f"Удаление условия без префикса {TEST_PREFIX}: требуется явное "
            "согласие (acknowledge_warnings=true)."
        )
    return {
        "before": {"Id": params.list_id, "Name": name},
        "requests": [("retargetinglists", "delete",
                      {"SelectionCriteria": {"Ids": [int(params.list_id)]}}, "v5")],
        "preview": f"Будет удалено условие {params.list_id} «{name}» (нигде не используется).",
        "warnings": warnings,
    }


async def _verify_list_deleted(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    client = ctx.direct()
    try:
        found = await client.get_all(
            "retargetinglists",
            {"SelectionCriteria": {"Ids": [int(params["list_id"])]},
             "FieldNames": ["Id"]},
            entry.login, "RetargetingLists")
    finally:
        await client.aclose()
    if found:
        return {"after": found, "ok": False, "note": "read-back: условие не удалено."}
    return {"after": None, "ok": True, "note": "подтверждено read-back: условие удалено."}


async def _apply_delete(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    service, method, body, version = split_request(plan.requests[0])
    client = ctx.direct()
    try:
        try:
            result = await client.call(service, method, body, entry.login, version)
        except DirectUnverifiedError:
            raise
        except DirectError as e:
            return {"status": "failed", "lines": [f"Ошибка API: {e.human_message()}"],
                    "response": {"error": e.human_message()}}
    finally:
        await client.aclose()
    return {"status": "applied", "lines": ["OK"], "response": result}


write_action(
    "retargeting_list_delete",
    "Удаление условия ретаргетинга — только если нигде не используется; "
    "без префикса [TEST DirectAI] нужно явное согласие",
    ("удалить условие", "ретаргетинг", "retargeting", "удаление условия"),
    RetargetingListDeleteParams,
    prepare=_prepare_list_delete,
    apply=_apply_delete,
    verify=_verify_list_deleted,
)


def _campaign_strategy(campaign: dict) -> dict:
    strategy: dict = {}
    for block in ("TextCampaign", "UnifiedCampaign"):
        body = campaign.get(block)
        if isinstance(body, dict):
            strategy.update(body.get("BiddingStrategy") or {})
    return strategy


def _is_auto_strategy(strategy: dict) -> tuple[bool, str]:
    search = (strategy.get("Search") or {}).get("BiddingStrategyType")
    network = (strategy.get("Network") or {}).get("BiddingStrategyType")
    manual_search = search in (None, MANUAL_SEARCH, "SERVING_OFF")
    manual_network = network in (None, *MANUAL_NETWORK, "SERVING_OFF", "NETWORK_DEFAULT")
    if manual_search and manual_network:
        return False, str(search or network or "ручная")
    return True, str(search if search not in (None, MANUAL_SEARCH) else network)


class AudienceTargetAddParams(GetActionParams):
    adgroup_id: int
    retargeting_list_id: int
    context_bid: float | None = None
    strategy_priority: str | None = None


async def _prepare_target_add(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    assert isinstance(params, AudienceTargetAddParams)
    if params.strategy_priority is not None and params.strategy_priority not in (
        "LOW", "NORMAL", "HIGH"):
        raise ValueError("strategy_priority только LOW/NORMAL/HIGH.")
    client = ctx.direct()
    try:
        groups = await client.get_all(
            "adgroups",
            {"SelectionCriteria": {"Ids": [int(params.adgroup_id)]},
             "FieldNames": ["Id", "CampaignId", "Type"]},
            entry.login, "AdGroups", "v501")
        if not groups or groups[0].get("CampaignId") is None:
            raise ValueError(f"группа {params.adgroup_id} не найдена.")
        group = groups[0]
        group_type = str(group.get("Type") or "?")
        campaign_id = int(group["CampaignId"])
        lists = await _fetch_lists(client, entry.login, [int(params.retargeting_list_id)], None)
        if not lists:
            raise ValueError(f"условие {params.retargeting_list_id} не найдено.")
        rl = lists[0]
        existing = await client.get_all(
            "audiencetargets",
            {"SelectionCriteria": {"AdGroupIds": [int(params.adgroup_id)]},
             "FieldNames": ["Id", "RetargetingListId"]},
            entry.login, "AudienceTargets")
        camps = await client.get_all(
            "campaigns",
            {"SelectionCriteria": {"Ids": [campaign_id]},
             "FieldNames": ["Id", "Name", "Type"],
             "TextCampaignFieldNames": ["BiddingStrategy"],
             "UnifiedCampaignFieldNames": ["BiddingStrategy"]},
            entry.login, "Campaigns", "v501")
    finally:
        await client.aclose()
    scope = str(rl.get("Scope") or "")
    if scope == "FOR_ADJUSTMENTS_ONLY":
        raise ValueError(
            f"условие {params.retargeting_list_id} — только для корректировок "
            "(Scope FOR_ADJUSTMENTS_ONLY): привязать к группе нельзя, "
            "используйте bid_modifiers_set (RETARGETING)."
        )
    items = ((rl.get("AvailableForTargetsInAdGroupTypes") or {}).get("Items")
             if isinstance(rl.get("AvailableForTargetsInAdGroupTypes"), dict) else None)
    effective_type = V501_TO_V5_ADGROUP_TYPE.get(group_type, group_type)
    if not items or (group_type not in items and effective_type not in items):
        raise ValueError(
            f"условие {params.retargeting_list_id} нельзя привязать к группе "
            f"{params.adgroup_id} (тип {group_type}): допустимы {items or '—'}."
        )
    for t in existing:
        if t.get("RetargetingListId") is not None and int(t["RetargetingListId"]) == int(
            params.retargeting_list_id):
            raise ValueError(
                f"условие {params.retargeting_list_id} уже привязано к группе "
                f"{params.adgroup_id} (таргетинг {t.get('Id')})."
            )
    strategy = _campaign_strategy(camps[0]) if camps else {}
    is_auto, strat_name = _is_auto_strategy(strategy)
    warnings: list[str] = []
    body: dict = {"AdGroupId": int(params.adgroup_id),
                  "RetargetingListId": int(params.retargeting_list_id)}
    bid_line = ""
    if is_auto:
        if params.context_bid is not None:
            warnings.append(
                f"context_bid {params.context_bid} проигнорирована: при автостратегии "
                f"({strat_name}) действует только strategy_priority."
            )
        body["StrategyPriority"] = params.strategy_priority or "NORMAL"
        bid_line = f"приоритет {body['StrategyPriority']} (автостратегия {strat_name})"
    else:
        if params.context_bid is not None:
            if params.context_bid < MIN_CONTEXT_BID:
                raise ValueError(
                    f"context_bid {params.context_bid} ниже минимума {MIN_CONTEXT_BID:.2f} ₽.")
            if params.context_bid > MAX_CONTEXT_BID_RUB:
                raise ValueError(
                    f"context_bid {params.context_bid} выше санитарного лимита "
                    f"{MAX_CONTEXT_BID_RUB:.0f} ₽ (не лимит API).")
            from directai_mcp.catalog.bids import rubles_to_micros

            body["ContextBid"] = rubles_to_micros(params.context_bid)
            bid_line = f"ставка {params.context_bid} ₽"
        else:
            bid_line = "ставка по умолчанию (минимальная)"
        if params.strategy_priority is not None:
            body["StrategyPriority"] = params.strategy_priority
            bid_line += f", приоритет {params.strategy_priority} (применится при смене на авто)"
    if rl.get("IsAvailable") == "NO":
        warnings.append(
            f"Условие {params.retargeting_list_id} недоступно (IsAvailable=NO: цель/сегмент "
            "удалены или доступ отменён) — показы и корректировки работать не будут."
        )
    preview = (
        "Будет привязано условие:\n"
        f"- Условие «{rl.get('Name')}» ({params.retargeting_list_id}, "
        f"{rl.get('Type')}): {human_rules(rl.get('Rules'), ctx.settings.goal_names)}\n"
        f"- Группа {params.adgroup_id} (кампания {campaign_id}): {bid_line}\n"
        f"- {AND_OR_NOTE}"
    )
    return {
        # v1.13.0: кампания группы — для привязки журнала.
        "before": {"campaign_id": campaign_id, "adgroup_id": int(params.adgroup_id)},
        "requests": [("audiencetargets", "add", {"AudienceTargets": [body]}, "v5")],
        "preview": preview,
        "warnings": warnings,
    }


async def _verify_target_present(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    client = ctx.direct()
    try:
        targets = await client.get_all(
            "audiencetargets",
            {"SelectionCriteria": {"AdGroupIds": [int(params["adgroup_id"])]},
             "FieldNames": ["Id", "RetargetingListId", "State"]},
            entry.login, "AudienceTargets")
    finally:
        await client.aclose()
    hit = [t for t in targets
           if t.get("RetargetingListId") is not None
           and int(t["RetargetingListId"]) == int(params["retargeting_list_id"])]
    if not hit:
        return {"after": None, "ok": False, "note": "read-back: привязка не найдена."}
    return {"after": hit[0], "ok": True, "note": "подтверждено read-back."}


write_action(
    "audience_target_add",
    "Привязка условия ретаргетинга к группе (авто → приоритет, ручная → ставка; "
    "исключение аудитории — только корректировкой −100%, не условием)",
    ("привязать аудиторию", "таргетинг", "audience", "условие к группе", "ретаргетинг"),
    AudienceTargetAddParams,
    prepare=_prepare_target_add,
    apply=lambda ctx, entry, plan: _apply_single(ctx, entry, plan, "AddResults"),
    verify=_verify_target_present,
)


class AudienceTargetStateParams(GetActionParams):
    target_ids: list[int] = Field(min_length=1)
    operation: str


_TARGET_STATE_EXPECTED = {"suspend": "SUSPENDED", "resume": "ON"}


async def _prepare_target_state(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    assert isinstance(params, AudienceTargetStateParams)
    if params.operation not in ("suspend", "resume", "delete"):
        raise ValueError("operation только suspend/resume/delete.")
    client = ctx.direct()
    try:
        found = await client.get_all(
            "audiencetargets",
            {"SelectionCriteria": {"Ids": [int(i) for i in params.target_ids]},
             "FieldNames": ["Id", "AdGroupId", "CampaignId",
                            "RetargetingListId", "State"]},
            entry.login, "AudienceTargets")
    finally:
        await client.aclose()
    have = {int(t["Id"]): t for t in found if t.get("Id") is not None}
    missing = [i for i in params.target_ids if int(i) not in have]
    if missing:
        raise ValueError(f"привязки не найдены: {missing}.")
    if params.operation == "delete":
        requests = [("audiencetargets", "delete",
                     {"SelectionCriteria": {"Ids": [int(i) for i in params.target_ids]}}, "v5")]
        lines = [f"Удалить привязку {i} (группа {have[int(i)].get('AdGroupId')})"
                 for i in params.target_ids]
    else:
        expected = _TARGET_STATE_EXPECTED[params.operation]
        requests = [("audiencetargets", params.operation,
                     {"SelectionCriteria": {"Ids": [int(i) for i in params.target_ids]}}, "v5")]
        lines = [f"Привязка {i}: {have[int(i)].get('State')} → {expected}"
                 for i in params.target_ids]
    return {
        "before": {
            **{str(i): have[int(i)].get("State") for i in params.target_ids},
            # v1.15.1: привязка к кампании в журнале.
            "campaign_ids": sorted({
                int(have[int(i)]["CampaignId"]) for i in params.target_ids
                if have[int(i)].get("CampaignId") is not None}),
        },
        "requests": requests,
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in lines),
        "warnings": [],
    }


async def _verify_target_state(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    ids = [int(i) for i in params["target_ids"]]
    client = ctx.direct()
    try:
        found = await client.get_all(
            "audiencetargets",
            {"SelectionCriteria": {"Ids": ids},
             "FieldNames": ["Id", "State"]},
            entry.login, "AudienceTargets")
    finally:
        await client.aclose()
    have = {int(t["Id"]): t.get("State") for t in found if t.get("Id") is not None}
    if params["operation"] == "delete":
        missing = [i for i in ids if i not in have]
        if len(missing) != len(ids):
            return {"after": have, "ok": False,
                    "note": "read-back: не удалены: "
                            + ", ".join(map(str, set(ids) - set(missing)))}
        return {"after": None, "ok": True, "note": "подтверждено read-back: удалены."}
    expected = _TARGET_STATE_EXPECTED[params["operation"]]
    bad = [f"{i}: {have.get(i)} != {expected}" for i in ids if have.get(i) != expected]
    if bad:
        return {"after": have, "ok": False,
                "note": "read-back НЕ подтвердил: " + "; ".join(bad)}
    return {"after": have, "ok": True, "note": "подтверждено read-back."}


async def _apply_target_state(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    service, method, body, version = split_request(plan.requests[0])
    params = plan.params
    assert isinstance(params, dict)
    client = ctx.direct()
    try:
        try:
            result = await client.call(service, method, body, entry.login, version)
        except DirectUnverifiedError:
            raise
        except DirectError as e:
            return {"status": "failed", "lines": [f"Ошибка API: {e.human_message()}"],
                    "response": {"error": e.human_message()}}
    finally:
        await client.aclose()
    key = {"suspend": "SuspendResults", "resume": "ResumeResults"}.get(method, "DeleteResults")
    items = result.get(key, [])
    labels = [str(i) for i in params["target_ids"]]
    lines, ok = summarize(labels, items)
    return {"status": "applied" if ok == len(labels) else "failed" if ok == 0 else "partial",
            "lines": lines, "response": result}


write_action(
    "audience_target_state",
    "Пауза/возобновление/удаление привязок аудиторий",
    ("остановить таргетинг", "audience", "suspend", "resume", "удалить привязку"),
    AudienceTargetStateParams,
    prepare=_prepare_target_state,
    apply=_apply_target_state,
    verify=_verify_target_state,
)
