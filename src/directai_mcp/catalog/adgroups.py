"""Read action adgroups_list (AdGroups.get)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    finalize,
    map_accounts,
    split_request,
    summarize,
)
from directai_mcp.catalog.registry import Ctx, action, write_action
from directai_mcp.config import AccountEntry

FIELDS = ["Id", "CampaignId", "Name", "Status", "ServingStatus", "RegionIds",
          "RestrictedRegionIds", "Type",
          # v1.3.3: минусы и tracking групп для dump (WSDL adgroups/get).
          "NegativeKeywords", "NegativeKeywordSharedSetIds",
          "TrackingParams"]


class AdGroupsListParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)


def _regions(value: object) -> str:
    """Плюсы — таргет; минусы (например -213) — явно «исключены» (v1.1.12)."""
    if not isinstance(value, list):
        return "—"
    pos: list[str] = []
    neg: list[str] = []
    for raw in value:
        try:
            num = int(raw)
        except (TypeError, ValueError):
            continue
        (neg if num < 0 else pos).append(str(abs(num)))
    text = ",".join(pos) if pos else "—"
    if len(text) > 60:
        text = text[:60] + "…"
    if neg:
        text += f"; исключены: {','.join(neg)}"
    return text


def _restricted(value: object) -> str:
    """RestrictedRegionIds: список или {Items}, null/пусто — «—» (v1.1.12)."""
    items = value.get("Items") if isinstance(value, dict) else value
    if not items or not isinstance(items, list):
        return "—"
    return ", ".join(str(i) for i in items)


def _negatives_count(value: object) -> str:
    """v1.3.3: NegativeKeywords {Items} -> «N фраз» или «—»."""
    items = value.get("Items") if isinstance(value, dict) else value
    if not items or not isinstance(items, list):
        return "—"
    return f"{len(items)} фраз"


def _shared_ids(value: object) -> str:
    """v1.3.3: NegativeKeywordSharedSetIds {Items} -> «id, ...» или «—»."""
    return _restricted(value)


@action(
    "adgroups_list",
    "read",
    "Группы кампании: статусы, регионы",
    ("группы", "adgroups", "группы объявлений", "ad group"),
    AdGroupsListParams,
)
async def _list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AdGroupsListParams)
    if not params.campaign_ids and not params.adgroup_ids:
        return "Ошибка: укажите campaign_ids или adgroup_ids."
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""

    async def fetch(entry: AccountEntry, client):
        items: list[dict] = []
        for ids in chunk(params.campaign_ids, 10):
            items.extend(
                await client.get_all(
                    "adgroups",
                    {"SelectionCriteria": {"CampaignIds": ids}, "FieldNames": FIELDS},
                    entry.login,
                    "AdGroups",
                    # v1.1.12: Type групп только через v501 (UNIFIED_AD_GROUP);
                    # v5 отдаёт устаревший TEXT_AD_GROUP (проба живьём).
                    # Write-подготовки/проверки ниже остаются на v5.
                    "v501",
                )
            )
        if params.adgroup_ids:
            items.extend(
                await client.get_all(
                    "adgroups",
                    {
                        "SelectionCriteria": {"Ids": params.adgroup_ids},
                        "FieldNames": FIELDS,
                    },
                    entry.login,
                    "AdGroups",
                    "v501",
                )
            )
        return items

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    # v1.1.20: настройки автотаргетинга групп (один Keywords.get на кабинет).
    auto: dict[tuple[str, int], str | None] = {}
    auto_errors: list[str] = []
    group_ids: dict[str, list[int]] = {}
    for entry, payload in results:
        if isinstance(payload, DirectError):
            continue
        assert isinstance(payload, list)
        for item in payload:
            try:
                gid = int(item["Id"])
            except (KeyError, TypeError, ValueError):
                continue
            group_ids.setdefault(entry.login, []).append(gid)

    async def _one_auto(entry: AccountEntry):
        from directai_mcp.catalog.keywords import autotargeting_by_group

        client = ctx.direct()
        try:
            try:
                return entry.login, await autotargeting_by_group(
                    client, entry.login, group_ids[entry.login]
                )
            except DirectError as e:
                return entry.login, e
        finally:
            await client.aclose()

    import asyncio as _asyncio

    for login, payload in await _asyncio.gather(
        *(_one_auto(e) for e in entries if e.login in group_ids)
    ):
        if isinstance(payload, DirectError):
            auto_errors.append(
                f"⚠ {login}: автотаргетинг: {payload.human_message()}"
            )
        else:
            for gid, text in payload.items():
                auto[(login, gid)] = text
    columns = ["Id", "CampaignId", "Name", "Type", "Status", "ServingStatus",
               "Regions", "Restricted", "Negatives", "SharedSets", "Tracking",
               "Autotargeting"]
    rows: list[dict] = []
    errors: list[str] = []
    errors.extend(auto_errors)
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, list)
        for item in payload:
            try:
                gid = int(item["Id"])
            except (TypeError, ValueError):
                gid = None
            rows.append(
                {
                    "_account": entry.login,
                    "Id": item.get("Id"),
                    "CampaignId": item.get("CampaignId"),
                    "Name": item.get("Name"),
                    "Type": item.get("Type"),
                    "Status": item.get("Status"),
                    "ServingStatus": item.get("ServingStatus"),
                    "Regions": _regions(item.get("RegionIds")),
                    "Restricted": _restricted(item.get("RestrictedRegionIds")),
                    "Negatives": _negatives_count(
                        item.get("NegativeKeywords")),
                    "SharedSets": _shared_ids(
                        item.get("NegativeKeywordSharedSetIds")),
                    "Tracking": item.get("TrackingParams") or "—",
                    "Autotargeting": auto.get((entry.login, gid)),
                }
            )
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}adgroups_list: {', '.join(e.login for e in entries)}."
    return finalize(
        ctx,
        context,
        "adgroups_list",
        display,
        rows,
        params.limit,
        params.save_as,
        errors,
        output=params.output,
        format=params.format,
        account=params.account,
    )


class AdGroupCreateItem(BaseModel):
    campaign_id: int
    name: str
    region_ids: list[int] = Field(min_length=1)
    negatives: list[str] = Field(default_factory=list)
    tracking_params: str | None = None


class AdGroupsCreateParams(GetActionParams):
    groups: list[AdGroupCreateItem] = Field(min_length=1)


class AdGroupUpdateItem(BaseModel):
    id: int
    name: str | None = None
    region_ids: list[int] | None = None
    negatives: list[str] | None = None
    tracking_params: str | None = None
    # v1.1.36: регионы именами/Id («-Имя» — минус-регион):
    # add/remove — в т.ч. в боевой, replace — только TEST.
    regions: list[str] | None = None
    regions_mode: Literal["add", "remove", "replace"] = "replace"


class AdGroupsUpdateParams(GetActionParams):
    groups: list[AdGroupUpdateItem] = Field(min_length=1)


def _group_add_body(item: AdGroupCreateItem) -> dict:
    body: dict = {
        "Name": item.name,
        "CampaignId": item.campaign_id,
        "RegionIds": item.region_ids,
    }
    if item.negatives:
        body["NegativeKeywords"] = {"Items": item.negatives}
    if item.tracking_params:
        body["TrackingParams"] = item.tracking_params
    return body


async def _prepare_adgroups_create(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, AdGroupsCreateParams)
    bodies = [_group_add_body(g) for g in params.groups]
    lines = [
        f"{g.name} (кампания {g.campaign_id}, регионы {g.region_ids})"
        for g in params.groups
    ]
    return {
        "before": None,
        "requests": [("adgroups", "add", {"AdGroups": bodies})],
        "preview": f"Будет создано групп: {len(bodies)}:\n"
        + "\n".join(f"- {line}" for line in lines),
        "warnings": [],
    }


async def _apply_batch(
    ctx: Ctx,
    entry: AccountEntry,
    plan,
    result_key: str,
    labels: list[str],
    id_field: str = "Id",
) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    service, method, body, version = split_request(plan.requests[0])
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
        items = result.get(result_key, [])
        lines, ok = summarize(labels, items, id_field)
        total = len(labels)
        status = "applied" if ok == total else "failed" if ok == 0 else "partial"
        return {"status": status, "lines": lines, "response": result}
    finally:
        await client.aclose()


async def _verify_adgroups_created(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    last = getattr(plan, "last_response", None) or {}
    created = [
        r.get("Id")
        for r in (last.get("response") or {}).get("AddResults", [])
        if r.get("Id") is not None
    ]
    if not created:
        return {
            "after": None,
            "ok": False,
            "note": "read-back: ни одна группа не создана.",
        }
    client = ctx.direct()
    try:
        found = await client.get_all(
            "adgroups",
            {
                "SelectionCriteria": {"Ids": created},
                "FieldNames": ["Id", "Name", "CampaignId"],
            },
            entry.login,
            "AdGroups",
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
        "note": f"подтверждено read-back: создано {len(created)}.",
    }


write_action(
    "adgroups_create",
    "Пакетное создание групп",
    ("создать группу", "adgroups", "add", "новая группа"),
    AdGroupsCreateParams,
    prepare=_prepare_adgroups_create,
    apply=lambda ctx, entry, plan: _apply_batch(
        ctx,
        entry,
        plan,
        "AddResults",
        [g["Name"] for g in plan.requests[0][2]["AdGroups"]],
    ),
    verify=_verify_adgroups_created,
)


def _signed(seq: object) -> list[int]:
    """RegionIds/RestrictedRegionIds → список int."""
    items = seq.get("Items") if isinstance(seq, dict) else seq
    out = []
    for value in items or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


async def _resolve_regions(ctx: Ctx, tokens: list[str]) -> list[int]:
    """Имена/Id → знаковые Id (минус — префикс -, adgroups/update)."""
    from directai_mcp.catalog.common import region_names

    names = await region_names(ctx)
    by_name = {str(name).casefold(): rid for rid, name in names.items()}
    resolved = []
    for raw in tokens:
        token = str(raw or "").strip()
        if not token:
            raise ValueError("регионы: пустое значение.")
        minus = token.startswith("-")
        core = token[1:].strip() if minus else token
        if not core:
            raise ValueError(f"регионы: «{token}» без региона.")
        if core.isdigit():
            rid = int(core)
            if rid != 0 and rid not in names:
                raise ValueError(f"регион {rid} не найден в справочнике.")
        elif core.casefold() in by_name:
            rid = by_name[core.casefold()]
        else:
            close = [n for rid_, n in names.items()
                     if core.casefold() in str(n).casefold()][:5]
            hint = f" Похожие: {', '.join(close)}." if close else ""
            raise ValueError(f"регион «{core}» не найден.{hint}")
        resolved.append(-rid if minus and rid != 0 else rid)
    return resolved


def _region_labels(ids: list[int], names: dict[int, str]) -> list[str]:
    out = []
    for rid in ids:
        name = names.get(abs(rid), str(abs(rid)))
        out.append(f"−{name}" if rid < 0 else name)
    return out


async def _prepare_adgroups_update(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    from directai_mcp.catalog.common import region_names

    assert isinstance(params, AdGroupsUpdateParams)
    ids = [g.id for g in params.groups]
    client = ctx.direct()
    try:
        found = await client.get_all(
            "adgroups",
            {
                "SelectionCriteria": {"Ids": ids},
                "FieldNames": ["Id", "Name", "CampaignId", "RegionIds",
                               "RestrictedRegionIds"],
            },
            entry.login,
            "AdGroups",
        )
    finally:
        await client.aclose()
    current = {int(i["Id"]): i for i in found if i.get("Id") is not None}
    missing = [i for i in ids if i not in current]
    if missing:
        raise ValueError(f"группы не найдены: {missing}.")
    names = await region_names(ctx)
    bodies = []
    lines = []
    warnings: list[str] = []
    for item in params.groups:
        body: dict = {"Id": item.id}
        desc = []
        if item.name is not None:
            body["Name"] = item.name
            desc.append(f"имя: {current[item.id].get('Name')} → {item.name}")
        if item.region_ids is not None and item.regions is not None:
            raise ValueError(
                f"группа {item.id}: укажите region_ids или regions, не оба.")
        if item.region_ids is not None:
            body["RegionIds"] = item.region_ids
            desc.append(f"регионы → {item.region_ids}")
        if item.regions is not None:
            # v1.1.36: add/remove/replace по знаковому множеству.
            cur = _signed(current[item.id].get("RegionIds"))
            cur += [-r for r in _signed(
                current[item.id].get("RestrictedRegionIds"))]
            want = await _resolve_regions(ctx, item.regions)
            if item.regions_mode == "add":
                final = sorted(set(cur) | set(want))
            elif item.regions_mode == "remove":
                final = sorted(set(cur) - set(want))
            else:
                final = sorted(set(want))
            if not final:
                raise ValueError(
                    f"группа {item.id}: пустой таргетинг недопустим.")
            if all(r < 0 for r in final):
                raise ValueError(
                    f"группа {item.id}: только минус-регионы недопустимы "
                    "(adgroups/update).")
            if 0 in final and any(r < 0 for r in final):
                raise ValueError(
                    f"группа {item.id}: минус-регионы нельзя при регионе 0.")
            body["RegionIds"] = final
            removed = sorted(set(cur) - set(final))
            if item.regions_mode == "add":
                # v1.1.36.1: счётчики слияния в превью (тест 29).
                new_n = len([r for r in want if r not in set(cur)])
                dups = len(want) - new_n
                desc.append(
                    f"регионы (add): было {len(cur)}, добавляется {len(want)}, "
                    f"дублей {dups}, станет {len(final)}: "
                    f"{', '.join(_region_labels(want, names))}")
            else:
                desc.append(
                    f"регионы ({item.regions_mode}): "
                    f"{', '.join(_region_labels(want, names))} → "
                    f"станет: {', '.join(_region_labels(final, names))}")
            if removed and item.regions_mode in ("remove", "replace"):
                hit = await _adjusted_regions(
                    ctx, entry, current[item.id].get("CampaignId"), removed)
                if hit:
                    warnings.append(
                        f"группа {item.id}: корректировки {hit} по регионам "
                        f"({', '.join(_region_labels(removed, names))}), "
                        "которых больше нет в таргетинге.")
        if item.negatives is not None:
            body["NegativeKeywords"] = {"Items": item.negatives}
            desc.append(f"минус-фразы → {len(item.negatives)} шт")
        if item.tracking_params is not None:
            body["TrackingParams"] = item.tracking_params
            desc.append("tracking_params обновлены")
        if len(body) == 1:
            raise ValueError(f"группа {item.id}: нечего менять.")
        bodies.append(body)
        lines.append(f"{current[item.id].get('Name')} ({item.id}): " + "; ".join(desc))
    return {
        "before": {gid: current[gid].get("Name") for gid in ids},
        "requests": [("adgroups", "update", {"AdGroups": bodies})],
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in lines),
        "warnings": warnings,
    }


async def _adjusted_regions(ctx: Ctx, entry: AccountEntry, campaign_id,
                            removed: list[int]) -> str:
    """Корректировки кампании по удалённым регионам (v1.1.36, предупреждение)."""
    from directai_mcp.catalog.bids import _modifiers_by_scope

    if campaign_id is None:
        return ""
    gone = {abs(r) for r in removed}
    client = ctx.direct()
    try:
        items = await _modifiers_by_scope(client, entry.login, [int(campaign_id)], [])
    finally:
        await client.aclose()
    hit = []
    for item in items:
        adj = item.get("RegionalAdjustment") or {}
        rid = adj.get("RegionId")
        if rid is not None and int(rid) in gone:
            hit.append(f"{item.get('Id')}:{rid}")
    return ", ".join(hit)


async def _verify_adgroups_updated(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    ids = [g["id"] for g in params["groups"]]
    client = ctx.direct()
    try:
        found = await client.get_all(
            "adgroups",
            {
                "SelectionCriteria": {"Ids": ids},
                "FieldNames": ["Id", "Name", "RegionIds",
                               "RestrictedRegionIds"],
            },
            entry.login,
            "AdGroups",
        )
    finally:
        await client.aclose()
    current = {int(i["Id"]): i for i in found if i.get("Id") is not None}
    sent = {}
    for request in plan.requests:
        from directai_mcp.catalog.common import split_request
        service, method, body, _version = split_request(request)
        if service == "adgroups" and method == "update":
            for group in body.get("AdGroups", []):
                if group.get("Id") is not None and "RegionIds" in group:
                    sent[int(group["Id"])] = list(group["RegionIds"] or [])
    bad = []
    for item in params["groups"]:
        cur = current.get(item["id"], {})
        if item.get("name") is not None and cur.get("Name") != item["name"]:
            bad.append(f"{item['id']}.name: {cur.get('Name')!r}")
        if (
            item.get("region_ids") is not None
            and cur.get("RegionIds") != item["region_ids"]
        ):
            bad.append(f"{item['id']}.regions: {cur.get('RegionIds')!r}")
        if item["id"] in sent and item.get("regions") is not None:
            # v1.1.36: сверяем знаковые множества (API отдаёт RegionIds
            # как есть, со знаком; Restricted дублируем на случай сплита).
            want = sorted(sent[item["id"]])
            got = sorted(set(_signed(cur.get("RegionIds"))) | {
                -r for r in _signed(cur.get("RestrictedRegionIds"))})
            if got != want:
                bad.append(f"{item['id']}.regions: {got!r}")
    if bad:
        return {
            "after": current,
            "ok": False,
            "note": "read-back НЕ подтвердил: " + "; ".join(bad),
        }
    return {
        "after": {k: v.get("Name") for k, v in current.items()},
        "ok": True,
        "note": "подтверждено read-back.",
    }


write_action(
    "adgroups_update",
    "Изменение групп: имя, регионы, минусы",
    ("изменить группу", "adgroups", "update", "регионы группы"),
    AdGroupsUpdateParams,
    prepare=_prepare_adgroups_update,
    apply=lambda ctx, entry, plan: _apply_batch(
        ctx,
        entry,
        plan,
        "UpdateResults",
        [str(g["Id"]) for g in plan.requests[0][2]["AdGroups"]],
    ),
    verify=_verify_adgroups_updated,
)
