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
    "Аудиторные условия групп и списки ретаргетинга (с правилами)",
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

    async def fetch(entry: AccountEntry, client):
        targets: list[dict] = []
        for ids in chunk(params.campaign_ids, 100):
            targets.extend(
                await client.get_all(
                    "audiencetargets",
                    {
                        "SelectionCriteria": {"CampaignIds": ids},
                        "FieldNames": [
                            "Id",
                            "CampaignId",
                            "AdGroupId",
                            "RetargetingListId",
                            "InterestId",
                            "State",
                            "ContextBid",
                            "StrategyPriority",
                        ],
                    },
                    entry.login,
                    "AudienceTargets",
                )
            )
        if params.adgroup_ids:
            targets.extend(
                await client.get_all(
                    "audiencetargets",
                    {
                        "SelectionCriteria": {"AdGroupIds": params.adgroup_ids},
                        "FieldNames": [
                            "Id",
                            "CampaignId",
                            "AdGroupId",
                            "RetargetingListId",
                            "InterestId",
                            "State",
                            "ContextBid",
                            "StrategyPriority",
                        ],
                    },
                    entry.login,
                    "AudienceTargets",
                )
            )
        lists = await client.get_all(
            "retargetinglists",
            {"FieldNames": ["Id", "Type", "Name", "IsAvailable", "Scope",
                            "Rules", "AvailableForTargetsInAdGroupTypes"]},
            entry.login,
            "RetargetingLists",
        )
        return targets, lists

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    errors: list[str] = []
    target_rows: list[dict] = []
    list_rows: list[dict] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        targets, lists = payload
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
    return "\n\n".join(parts)
