"""Read action audiences_list (AudienceTargets + RetargetingLists)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    goal_label,
    map_accounts,
    micros_to_rubles,
)
from directai_mcp.catalog.extensions import _section
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import money


class AudiencesListParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)


def _rules_text(rules: object, names: dict[str, str]) -> str:
    """v1.1.28: Rules списков ретаргетинга (Operator + ExternalId + дни).

    Имена целей — из goals-кэша (goal_label), если есть; иначе голый id.
    """
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
                ext = arg.get("ExternalId")
                span = arg.get("MembershipLifeSpan")
                label = goal_label(ext, names)
                days = f"{span} дн." if span is not None else "—"
                parts.append(f"{label} ({days})")
        blocks.append(f"{operator}: " + ("; ".join(parts) if parts else "—"))
    return " | ".join(blocks) if blocks else "—"


def _available_in(value: object) -> str:
    """v1.1.28: AvailableForTargetsInAdGroupTypes ({Items: [...]}) -> строка."""
    if isinstance(value, dict):
        items = value.get("Items")
        if isinstance(items, list) and items:
            return ", ".join(str(i) for i in items)
    return "—"


@action(
    "audiences_list",
    "read",
    "Аудиторные условия групп и списки ретаргетинга (с правилами). "
    "ЕПК и текстово-графические группы — RETARGETING с goal_id; "
    "AUDIENCE — только медийные группы; на поиске аудитории сужают (AND), "
    "в сетях расширяют (OR); исключить текущих клиентов — только корректировкой.",
    (
        "аудитории",
        "аудитория",
        "audiences",
        "audience",
        "ретаргетинг",
        "retargeting",
        "условия нацеливания",
        "правила",
    ),
    AudiencesListParams,
)
async def _list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AudiencesListParams)
    if not params.campaign_ids and not params.adgroup_ids:
        return "Ошибка: укажите campaign_ids или adgroup_ids."
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}
    _TARGET_FIELDS = [
        "Id", "CampaignId", "AdGroupId", "RetargetingListId", "InterestId",
        "State", "ContextBid", "StrategyPriority",
    ]
    _LISTS_FIELDS = ["Id", "Type", "Name", "IsAvailable", "Scope", "Rules",
                     "AvailableForTargetsInAdGroupTypes"]

    async def fetch(entry: AccountEntry, client):
        targets: list[dict] = []
        for ids in chunk(params.campaign_ids, 100):
            targets.extend(
                await client.get_all(
                    "audiencetargets",
                    {
                        "SelectionCriteria": {"CampaignIds": ids},
                        "FieldNames": _TARGET_FIELDS,
                    },
                    entry.login,
                    "AudienceTargets",
                    tally=tally,
                )
            )
        if params.adgroup_ids:
            targets.extend(
                await client.get_all(
                    "audiencetargets",
                    {
                        "SelectionCriteria": {"AdGroupIds": params.adgroup_ids},
                        "FieldNames": _TARGET_FIELDS,
                    },
                    entry.login,
                    "AudienceTargets",
                    tally=tally,
                )
            )
        lists = await client.get_all(
            "retargetinglists",
            {"FieldNames": _LISTS_FIELDS},
            entry.login,
            "RetargetingLists",
            tally=tally,
        )
        return targets, lists, {"targets": _TARGET_FIELDS,
                                "lists": _LISTS_FIELDS}

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    errors: list[str] = []
    target_rows: list[dict] = []
    list_rows: list[dict] = []
    raw_targets: list[dict] = []
    raw_lists: list[dict] = []
    req_fields: dict = {}
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        targets, lists, req_fields = payload
        raw_targets.extend(targets)
        raw_lists.extend(lists)
        for t in targets:
            row = {
                "Id": t.get("Id"),
                "CampaignId": t.get("CampaignId"),
                "AdGroupId": t.get("AdGroupId"),
                "RetargetingListId": t.get("RetargetingListId") or "—",
                "InterestId": t.get("InterestId") or "—",
                "State": t.get("State"),
                "ContextBid": money(micros_to_rubles(t.get("ContextBid"))),
                "Priority": t.get("StrategyPriority"),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            target_rows.append(row)
        for rl in lists:
            row = {
                "Id": rl.get("Id"),
                "Type": rl.get("Type"),
                "Name": rl.get("Name"),
                "Available": rl.get("IsAvailable"),
                "Scope": rl.get("Scope"),
                "Rules": _rules_text(rl.get("Rules"), ctx.settings.goal_names),
                "AvailableIn": _available_in(
                    rl.get("AvailableForTargetsInAdGroupTypes")),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            list_rows.append(row)
    acc = ["_account"] if len(entries) > 1 else []
    accounts = ", ".join(e.login for e in entries)
    parts = [f"{mark}audiences_list: {accounts}."]
    section, _ = await _section(
        ctx,
        "audiences_targets",
        "Условия нацеливания.",
        acc
        + [
            "Id",
            "CampaignId",
            "AdGroupId",
            "RetargetingListId",
            "InterestId",
            "State",
            "ContextBid",
            "Priority",
        ],
        target_rows,
        params,
        [],
    )
    parts.append(f"## Условия нацеливания\n\n{section}")
    section, _ = await _section(
        ctx,
        "audiences_lists",
        "Списки ретаргетинга.",
        acc + ["Id", "Type", "Name", "Available", "Scope", "Rules",
               "AvailableIn"],
        list_rows,
        params,
        [],
    )
    parts.append(f"## Списки ретаргетинга\n\n{section}")
    if errors:
        parts.append("\n".join(errors))
    if params.dump_dir:
        from directai_mcp.catalog.common import write_dump_sections

        linked_lists = set()
        for t in raw_targets:
            if isinstance(t, dict) and t.get("RetargetingListId") is not None:
                try:
                    linked_lists.add(int(t["RetargetingListId"]))
                except (TypeError, ValueError):
                    continue
        target_cols = [
            "Id", "CampaignId", "AdGroupId", "RetargetingListId",
            "InterestId", "State", "ContextBid", "Priority",
        ]
        list_cols = ["Id", "Type", "Name", "Available", "Scope", "Rules",
                     "AvailableIn"]
        parts.append(write_dump_sections(
            ctx,
            params.dump_dir,
            "audiences_list",
            params.account,
            params.model_dump(),
            {
                "audiences_targets": {
                    "columns": target_cols,
                    "display_rows": target_rows,
                    "raw_items": [
                        dict(t, linked_to_campaign=True)
                        for t in raw_targets if isinstance(t, dict)],
                },
                "audiences_lists": {
                    "columns": list_cols,
                    "display_rows": list_rows,
                    "raw_items": [
                        dict(rl, linked_to_campaign=(
                            isinstance(rl, dict) and rl.get("Id")
                            in linked_lists))
                        for rl in raw_lists if isinstance(rl, dict)],
                },
            },
            {"AudienceTargets": {"FieldNames": req_fields.get("targets", [])},
             "RetargetingLists": {"FieldNames": req_fields.get("lists", [])}},
            tally,
            [e.login for e in entries],
            "campaign",
            list(errors),
            len(target_rows) > 20 or len(list_rows) > 20,
            dump_tag=params.dump_tag,
        ))
    return "\n\n".join(parts)
