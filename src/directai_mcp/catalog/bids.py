"""Read actions bids_get (KeywordBids) and bid_modifiers_get (BidModifiers);
write actions bids_set and bid_modifiers_set (step 6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    finalize,
    map_accounts,
    micros_to_rubles,
    region_names,
    split_request,
    summarize,
)
from directai_mcp.catalog.registry import Ctx, action, write_action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import money
from directai_mcp.safety.rules import check_ratio, load_rules


class BidsGetParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)
    keyword_ids: list[int] = Field(default_factory=list)


class BidModifiersGetParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=lambda: ["CAMPAIGN", "AD_GROUP"])


# Все 13 типов корректировок (проверено живьём 26.09.2026: SMART_TV_ADJUSTMENT
# есть в API — SmartTvAdjustment{BidModifier}, в публичных доках его нет).
_ALL_MOD_TYPES = (
    "MOBILE_ADJUSTMENT",
    "TABLET_ADJUSTMENT",
    "DESKTOP_ADJUSTMENT",
    "DESKTOP_ONLY_ADJUSTMENT",
    "SMART_TV_ADJUSTMENT",
    "DEMOGRAPHICS_ADJUSTMENT",
    "RETARGETING_ADJUSTMENT",
    "REGIONAL_ADJUSTMENT",
    "VIDEO_ADJUSTMENT",
    "SMART_AD_ADJUSTMENT",
    "SERP_LAYOUT_ADJUSTMENT",
    "INCOME_GRADE_ADJUSTMENT",
    "AD_GROUP_ADJUSTMENT",
)

_DETAIL_KEYS = (
    "MobileAdjustment",
    "TabletAdjustment",
    "DesktopAdjustment",
    "DesktopOnlyAdjustment",
    "SmartTvAdjustment",
    "DemographicsAdjustment",
    "RetargetingAdjustment",
    "RegionalAdjustment",
    "VideoAdjustment",
    "SmartAdAdjustment",
    "SerpLayoutAdjustment",
    "IncomeGradeAdjustment",
    "AdGroupAdjustment",
)


def _pct(raw: object) -> str:
    """BidModifier as delta vs 100: 130 -> +30%; 0 disables serving."""
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    delta = value - 100
    text = f"{delta:+d}%"
    if value == 0:
        text += " (показы отключены)"
    return text


def _raw_modifier(item: dict) -> int | None:
    """v1.1.18: сырой BidModifier из любого блока (только CSV/JSON)."""
    for key in _DETAIL_KEYS:
        sub = item.get(key)
        if isinstance(sub, dict) and isinstance(sub.get("BidModifier"), int):
            return int(sub["BidModifier"])
    return None


def _detail(
    item: dict,
    regions: dict[int, str] | None = None,
    retargeting: dict[int, str] | None = None,
) -> str:
    for key in _DETAIL_KEYS:
        sub = item.get(key)
        if isinstance(sub, dict):
            parts = []
            for name, val in sub.items():
                if val is None:
                    continue
                if name == "BidModifier":
                    parts.append(f"{name}={_pct(val)}")
                elif name == "RegionId":
                    try:
                        rid = int(val)
                    except (TypeError, ValueError):
                        rid = None
                    label = regions.get(rid, "?") if regions and rid else "?"
                    parts.append(f"{name}={label} ({val})")
                elif name == "RetargetingConditionId":
                    # v1.1.14: название условия, id в скобках.
                    try:
                        cid = int(val)
                    except (TypeError, ValueError):
                        cid = None
                    cname = (retargeting or {}).get(cid) if cid is not None else None
                    parts.append(f"{name}={cname} ({val})" if cname else f"{name}={val}")
                else:
                    parts.append(f"{name}={val}")
            return f"{key}: " + ", ".join(parts)
    return "—"


async def retargeting_names(
    ctx: Ctx, ids_by_login: dict[str, list[int]]
) -> dict[int, str]:
    """v1.1.14: id условия -> название (RetargetingLists.get, по кабинетам)."""
    out: dict[int, str] = {}
    if not any(ids_by_login.values()):
        return out
    client = ctx.direct()
    try:
        for login, ids in ids_by_login.items():
            if not ids:
                continue
            try:
                lists = await client.get_all(
                    "retargetinglists",
                    {"SelectionCriteria": {"Ids": sorted(set(ids))},
                     "FieldNames": ["Id", "Name"]},
                    login,
                    "RetargetingLists",
                )
            except DirectError:
                continue
            for lst in lists:
                if isinstance(lst, dict) and lst.get("Id") is not None:
                    out[int(lst["Id"])] = str(lst.get("Name") or lst["Id"])
    finally:
        await client.aclose()
    return out


@action(
    "bids_get",
    "read",
    "Ставки фраз: поиск и сети",
    ("ставки", "ставка", "bids", "бид", "bid", "цена клика"),
    BidsGetParams,
)
async def _bids(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, BidsGetParams)
    if not params.campaign_ids and not params.adgroup_ids and not params.keyword_ids:
        return "Ошибка: укажите campaign_ids, adgroup_ids или keyword_ids."
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    fields = {
        "FieldNames": ["KeywordId", "AdGroupId", "CampaignId", "ServingStatus"],
        "SearchFieldNames": ["Bid", "AutotargetingSearchBidIsAuto"],
        "NetworkFieldNames": ["Bid"],
    }
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        items: list[dict] = []
        for ids in chunk(params.campaign_ids, 10):
            items.extend(
                await client.get_all(
                    "keywordbids",
                    dict({"SelectionCriteria": {"CampaignIds": ids}}, **fields),
                    entry.login,
                    "KeywordBids",
                    tally=tally,
                )
            )
        for key, ids in (
            ("AdGroupIds", params.adgroup_ids),
            ("KeywordIds", params.keyword_ids),
        ):
            if ids:
                items.extend(
                    await client.get_all(
                        "keywordbids",
                        dict({"SelectionCriteria": {key: ids}}, **fields),
                        entry.login,
                        "KeywordBids",
                        tally=tally,
                    )
                )
        return items

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = [
        "KeywordId",
        "AdGroupId",
        "SearchBid",
        "SearchAuto",
        "NetworkBid",
        "ServingStatus",
    ]
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
            search = item.get("Search") or {}
            network = item.get("Network") or {}
            rows.append(
                {
                    "_account": entry.login,
                    "KeywordId": item.get("KeywordId"),
                    "AdGroupId": item.get("AdGroupId"),
                    "SearchBid": money(micros_to_rubles(search.get("Bid"))),
                    "SearchAuto": search.get("AutotargetingSearchBidIsAuto") or "—",
                    "NetworkBid": money(micros_to_rubles(network.get("Bid"))),
                    "ServingStatus": item.get("ServingStatus"),
                }
            )
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}bids_get: {', '.join(e.login for e in entries)}."
    return finalize(
        ctx,
        context,
        "bids_get",
        display,
        rows,
        params.limit,
        params.save_as,
        errors,
        money_cols=(),
        output=params.output,
        format=params.format,
        account=params.account,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="bids_get",
        dump_params=params.model_dump(),
        dump_raw={"bids_get": [
            dict(i, linked_to_campaign=True) for i in raw]},
        dump_fields=fields,
        dump_tally=tally,
        dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )


@action(
    "bid_modifiers_get",
    "read",
    "Корректировки ставок кампаний и групп",
    (
        "корректировки",
        "bidmodifiers",
        "bid modifiers",
        "коэффициенты",
        "модификаторы ставок",
        "ставка",
        "bid",
        "модификатор",
        "modifier",
        "регион",
        "гео",
        "geo",
        "устройства",
        "device",
        "время",
        "аудитория",
        "audience",
    ),
    BidModifiersGetParams,
)
async def _modifiers(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, BidModifiersGetParams)
    if not params.campaign_ids and not params.adgroup_ids:
        return "Ошибка: укажите campaign_ids или adgroup_ids."
    for level in params.levels:
        if level not in ("CAMPAIGN", "AD_GROUP"):
            return "Ошибка: levels только CAMPAIGN и AD_GROUP."
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}
    extra = {        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Level", "Type"],
        "MobileAdjustmentFieldNames": ["BidModifier", "OperatingSystemType"],
        "TabletAdjustmentFieldNames": ["BidModifier", "OperatingSystemType"],
        "DesktopAdjustmentFieldNames": ["BidModifier"],
        "DesktopOnlyAdjustmentFieldNames": ["BidModifier"],
        "SmartTvAdjustmentFieldNames": ["BidModifier"],
        "DemographicsAdjustmentFieldNames": ["Gender", "Age", "BidModifier", "Enabled"],
        "RetargetingAdjustmentFieldNames": [
            "RetargetingConditionId",
            "BidModifier",
            "Accessible",
            "Enabled",
        ],
        "RegionalAdjustmentFieldNames": ["RegionId", "BidModifier", "Enabled"],
        "VideoAdjustmentFieldNames": ["BidModifier"],
        "SmartAdAdjustmentFieldNames": ["BidModifier"],
        "SerpLayoutAdjustmentFieldNames": ["SerpLayout", "BidModifier", "Enabled"],
        "IncomeGradeAdjustmentFieldNames": ["Grade", "BidModifier", "Enabled"],
        "AdGroupAdjustmentFieldNames": ["BidModifier"],
    }

    async def fetch(entry: AccountEntry, client):
        items: list[dict] = []
        for ids in chunk(params.campaign_ids, 10):
            items.extend(
                await client.get_all(
                    "bidmodifiers",
                    dict(
                        {
                            "SelectionCriteria": {
                                "CampaignIds": ids,
                                "Levels": params.levels,
                            }
                        },
                        **extra,
                    ),
                    entry.login,
                    "BidModifiers",
                    tally=tally,
                )
            )
        if params.adgroup_ids:
            items.extend(
                await client.get_all(
                    "bidmodifiers",
                    dict(
                        {
                            "SelectionCriteria": {
                                "AdGroupIds": params.adgroup_ids,
                                "Levels": params.levels,
                            }
                        },
                        **extra,
                    ),
                    entry.login,
                    "BidModifiers",
                    tally=tally,
                )
            )
        return items

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    regions = await region_names(ctx)
    # v1.1.14: названия ретаргетинг-условий одним запросом на кабинет.
    cond_ids: dict[str, list[int]] = {}
    for entry, payload in results:
        if isinstance(payload, DirectError):
            continue
        assert isinstance(payload, list)
        for item in payload:
            sub = item.get("RetargetingAdjustment") if isinstance(item, dict) else None
            cid = sub.get("RetargetingConditionId") if isinstance(sub, dict) else None
            try:
                cid_int = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid_int = None
            if cid_int is not None:
                cond_ids.setdefault(entry.login, []).append(cid_int)
    names = await retargeting_names(ctx, cond_ids)
    # v1.1.18: сырой коэффициент — только CSV/JSON отдельной колонкой.
    file_only = bool(params.output == "file" or params.save_as)
    columns = ["Id", "Level", "Type", "CampaignId", "AdGroupId", "Detail"]
    if file_only:
        columns.append("BidModifier")
    rows: list[dict] = []
    raw: list[dict] = []
    errors: list[str] = []
    seen_types: set[str] = set()
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, list)
        raw.extend(payload)
        for item in payload:
            if isinstance(item.get("Type"), str):
                seen_types.add(item["Type"])
            row = {
                "_account": entry.login,
                "Id": item.get("Id"),
                "Level": item.get("Level"),
                "Type": item.get("Type"),
                "CampaignId": item.get("CampaignId"),
                "AdGroupId": item.get("AdGroupId") or "—",
                "Detail": _detail(item, regions, names),
            }
            if file_only:
                row["BidModifier"] = _raw_modifier(item)
            rows.append(row)
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}bid_modifiers_get: {', '.join(e.login for e in entries)}."
    out = finalize(
        ctx,
        context,
        "bid_modifiers_get",
        display,
        rows,
        params.limit,
        params.save_as,
        errors,
        money_cols=(),
        output=params.output,
        format=params.format,
        account=params.account,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="bid_modifiers_get",
        dump_params=params.model_dump(),
        dump_raw={"bid_modifiers_get": [
            dict(i, linked_to_campaign=True) for i in raw]},
        dump_fields=extra,
        dump_tally=tally,
        dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )
    if not errors:
        missing = [t for t in _ALL_MOD_TYPES if t not in seen_types]
        if missing:
            out += "\n\nНе заданы: " + ", ".join(missing) + "."
    return out


def rubles_to_micros(value: float) -> int:
    return round(float(value) * 1_000_000)


class BidsSetParams(GetActionParams):
    keyword_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)
    campaign_ids: list[int] = Field(default_factory=list)
    search_bid: float | None = None
    network_bid: float | None = None


def _bid_scope(params: BidsSetParams) -> list[tuple[str, list[int]]]:
    scopes = []
    if params.campaign_ids:
        scopes.append(("CampaignId", list(params.campaign_ids)))
    if params.adgroup_ids:
        scopes.append(("AdGroupId", list(params.adgroup_ids)))
    if params.keyword_ids:
        scopes.append(("KeywordId", list(params.keyword_ids)))
    return scopes


async def _current_bids(client, login: str, params: BidsSetParams) -> list[dict]:
    fields = {
        "FieldNames": ["KeywordId", "AdGroupId", "CampaignId", "ServingStatus"],
        "SearchFieldNames": ["Bid"],
        "NetworkFieldNames": ["Bid"],
    }
    items: list[dict] = []
    for ids in chunk(params.campaign_ids, 10):
        items.extend(
            await client.get_all(
                "keywordbids",
                dict({"SelectionCriteria": {"CampaignIds": ids}}, **fields),
                login,
                "KeywordBids",
            )
        )
    for key, ids in (
        ("AdGroupIds", params.adgroup_ids),
        ("KeywordIds", params.keyword_ids),
    ):
        if ids:
            items.extend(
                await client.get_all(
                    "keywordbids",
                    dict({"SelectionCriteria": {key: ids}}, **fields),
                    login,
                    "KeywordBids",
                )
            )
    return items


def _bid_of(item: dict, side: str) -> int | None:
    block = item.get("Search" if side == "search" else "Network") or {}
    value = block.get("Bid")
    return int(value) if isinstance(value, int) else None


async def _prepare_bids_set(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    from directai_mcp.safety.guard import GuardBlocked

    assert isinstance(params, BidsSetParams)
    scopes = _bid_scope(params)
    if not scopes:
        raise ValueError("укажите keyword_ids, adgroup_ids или campaign_ids.")
    if params.search_bid is None and params.network_bid is None:
        raise ValueError("укажите search_bid или network_bid (рубли).")
    for label, value in (("search_bid", params.search_bid), ("network_bid", params.network_bid)):
        if value is not None and value <= 0:
            raise ValueError(f"{label} должен быть больше 0.")
    rules = load_rules(ctx.data_dir / "rules.toml" if ctx.data_dir else None)
    client = ctx.direct()
    try:
        items = await _current_bids(client, entry.login, params)
    finally:
        await client.aclose()
    if params.keyword_ids and not items:
        raise GuardBlocked(f"фразы не найдены: {params.keyword_ids}.")
    new_search = rubles_to_micros(params.search_bid) if params.search_bid is not None else None
    new_network = rubles_to_micros(params.network_bid) if params.network_bid is not None else None
    preview_lines: list[str] = []
    warnings: list[str] = []
    bodies: list[dict] = []
    if params.keyword_ids:
        found = {int(i["KeywordId"]): i for i in items if i.get("KeywordId") is not None}
        missing = [k for k in params.keyword_ids if k not in found]
        if missing:
            raise ValueError(f"фразы не найдены: {missing}.")
        for kid in params.keyword_ids:
            item = found[kid]
            body: dict = {"KeywordId": kid}
            parts = []
            if new_search is not None:
                body["SearchBid"] = new_search
                old = _bid_of(item, "search")
                parts.append(
                    f"поиск {money(micros_to_rubles(old))} → {money(params.search_bid)} ₽"
                )
                warn = check_ratio(rules.max_bid_ratio, old, new_search, f"фраза {kid} поиск")
                if warn:
                    warnings.append(warn)
            if new_network is not None:
                body["NetworkBid"] = new_network
                old = _bid_of(item, "network")
                parts.append(
                    f"сети {money(micros_to_rubles(old))} → {money(params.network_bid)} ₽"
                )
                warn = check_ratio(rules.max_bid_ratio, old, new_network, f"фраза {kid} сети")
                if warn:
                    warnings.append(warn)
            bodies.append(body)
            preview_lines.append(f"Фраза {kid}: " + "; ".join(parts))
        requests = [("keywordbids", "set", {"KeywordBids": bodies}, "v5")]
        before = {
            str(k): {"search": _bid_of(found[k], "search"), "network": _bid_of(found[k], "network")}
            for k in params.keyword_ids
        }
    else:
        before = {}
        requests = []
        for field, ids in scopes:
            bodies = []
            for scope_id in ids:
                body = {field: scope_id}
                desc = []
                if new_search is not None:
                    body["SearchBid"] = new_search
                    desc.append(f"поиск → {money(params.search_bid)} ₽")
                if new_network is not None:
                    body["NetworkBid"] = new_network
                    desc.append(f"сети → {money(params.network_bid)} ₽")
                bodies.append(body)
                preview_lines.append(f"{field} {scope_id}: " + ", ".join(desc))
            requests.append(("keywordbids", "set", {"KeywordBids": bodies}, "v5"))
    return {
        "before": before or None,
        "requests": requests,
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in preview_lines),
        "warnings": warnings,
    }


async def _apply_bid_results(ctx: Ctx, entry: AccountEntry, plan, key: str) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    lines: list[str] = []
    ok = 0
    total = 0
    created: list[int] = []
    last: dict = {}
    client = ctx.direct()
    try:
        for request in plan.requests:
            service, method, body, version = split_request(request)
            items = body.get("KeywordBids") or body.get("BidModifiers") or []
            labels = [
                str(b.get("KeywordId") or b.get("AdGroupId") or b.get("CampaignId") or b.get("Id"))
                for b in items
            ]
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
            last = result
            batch, n_ok = summarize(labels, result.get(key, []))
            lines.extend(batch)
            ok += n_ok
            total += len(labels)
            if key == "AddResults":
                for res in result.get(key, []):
                    created.extend(res.get("Ids") or [])
    finally:
        await client.aclose()
    response: dict = dict(last)
    if key == "AddResults":
        response = {"created_ids": created, "response": last}
    status = "applied" if ok == total else "failed" if ok == 0 else "partial"
    return {"status": status, "lines": lines, "response": response}


async def _verify_bids_set(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    client = ctx.direct()
    try:
        items = await _current_bids(
            client,
            entry.login,
            BidsSetParams(
                account=entry.login,
                keyword_ids=params.get("keyword_ids", []),
                adgroup_ids=params.get("adgroup_ids", []),
                campaign_ids=params.get("campaign_ids", []),
            ),
        )
    finally:
        await client.aclose()
    want_search = (
        rubles_to_micros(params["search_bid"]) if params.get("search_bid") is not None else None
    )
    want_network = (
        rubles_to_micros(params["network_bid"]) if params.get("network_bid") is not None else None
    )
    bad: list[str] = []
    after: dict = {}
    if params.get("keyword_ids"):
        found = {int(i["KeywordId"]): i for i in items if i.get("KeywordId") is not None}
        for kid in params["keyword_ids"]:
            item = found.get(int(kid), {})
            got_search, got_network = _bid_of(item, "search"), _bid_of(item, "network")
            after[str(kid)] = {"search": got_search, "network": got_network}
            if want_search is not None and got_search != want_search:
                bad.append(f"{kid}.search: {got_search} != {want_search}")
            if want_network is not None and got_network != want_network:
                bad.append(f"{kid}.network: {got_network} != {want_network}")
    else:
        after = {"checked": len(items)}
    if bad:
        return {"after": after, "ok": False, "note": "read-back НЕ подтвердил: " + "; ".join(bad)}
    return {"after": after, "ok": True, "note": "подтверждено read-back."}


write_action(
    "bids_set",
    "Установка ставок фраз: поиск и сети",
    ("установить ставку", "bids", "bid", "изменить ставку", "ставка фразы"),
    BidsSetParams,
    prepare=_prepare_bids_set,
    apply=lambda ctx, entry, plan: _apply_bid_results(ctx, entry, plan, "SetResults"),
    verify=_verify_bids_set,
)


class ModifierSetOne(BaseModel):
    id: int
    bid_modifier: int


class ModifierAddOne(BaseModel):
    campaign_id: int | None = None
    adgroup_id: int | None = None
    kind: Literal["REGIONAL", "MOBILE", "DESKTOP", "DESKTOP_ONLY"] = "REGIONAL"
    region: str | None = None
    bid_modifier: int = 100


class BidModifiersSetParams(GetActionParams):
    set_items: list[ModifierSetOne] = Field(default_factory=list)
    add_items: list[ModifierAddOne] = Field(default_factory=list)
    delete_ids: list[int] = Field(default_factory=list)


_MOD_LEVELS = ["CAMPAIGN", "AD_GROUP"]
_MOD_FIELDS = {"FieldNames": ["Id", "CampaignId", "AdGroupId", "Level", "Type"]}
_MOD_SUBFIELDS = {
    "MobileAdjustmentFieldNames": ["BidModifier"],
    "TabletAdjustmentFieldNames": ["BidModifier"],
    "DesktopAdjustmentFieldNames": ["BidModifier"],
    "DesktopOnlyAdjustmentFieldNames": ["BidModifier"],
    "SmartTvAdjustmentFieldNames": ["BidModifier"],
    "RegionalAdjustmentFieldNames": ["RegionId", "BidModifier"],
    "VideoAdjustmentFieldNames": ["BidModifier"],
    "SmartAdAdjustmentFieldNames": ["BidModifier"],
}
_MOD_RANGES = {"REGIONAL": (10, 1300)}


async def _modifiers_by_ids(client, login: str, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    items = await client.get_all(
        "bidmodifiers",
        {
            "SelectionCriteria": {"Ids": ids, "Levels": _MOD_LEVELS},
            **_MOD_FIELDS,
            **_MOD_SUBFIELDS,
        },
        login,
        "BidModifiers",
    )
    out: dict[int, dict] = {}
    for item in items:
        if isinstance(item, dict) and item.get("Id") is not None:
            out[int(item["Id"])] = item
            for key in (
                "MobileAdjustment",
                "TabletAdjustment",
                "DesktopAdjustment",
                "DesktopOnlyAdjustment",
                "SmartTvAdjustment",
                "RegionalAdjustment",
                "VideoAdjustment",
                "SmartAdAdjustment",
            ):
                if isinstance(item.get(key), dict):
                    out[int(item["Id"])][f"_{key}"] = item[key]
    return out


async def _modifiers_by_scope(
    client, login: str, campaign_ids: list[int], adgroup_ids: list[int]
) -> list[dict]:
    items: list[dict] = []
    for ids in chunk(campaign_ids, 10):
        items.extend(
            await client.get_all(
                "bidmodifiers",
                {
                    "SelectionCriteria": {"CampaignIds": ids, "Levels": _MOD_LEVELS},
                    **_MOD_FIELDS,
                    **_MOD_SUBFIELDS,
                },
                login,
                "BidModifiers",
            )
        )
    if adgroup_ids:
        items.extend(
            await client.get_all(
                "bidmodifiers",
                {
                    "SelectionCriteria": {"AdGroupIds": adgroup_ids, "Levels": _MOD_LEVELS},
                    **_MOD_FIELDS,
                    **_MOD_SUBFIELDS,
                },
                login,
                "BidModifiers",
            )
        )
    return items


async def _own_modifier(
    ctx: Ctx, client, login: str, item: dict, ref: str,
    enforce_test: bool = True,
) -> str:
    """Test-scope ownership for a modifier item; returns scope label."""
    from directai_mcp.safety.guard import GuardBlocked, require_test_campaign

    group_id = item.get("AdGroupId")
    if group_id is not None:
        groups = await client.get_all(
            "adgroups",
            {"SelectionCriteria": {"Ids": [int(group_id)]}, "FieldNames": ["Id", "CampaignId"]},
            login,
            "AdGroups",
        )
        if not groups:
            raise GuardBlocked(f"корректировка {ref}: группа {group_id} не найдена.")
        if enforce_test:
            await require_test_campaign(ctx, client, login, int(groups[0]["CampaignId"]))
        return f"группу {group_id}"
    campaign_id = item.get("CampaignId")
    if campaign_id is None:
        raise GuardBlocked(f"корректировка {ref}: нет привязки к кампании/группе.")
    if enforce_test:
        await require_test_campaign(ctx, client, login, int(campaign_id))
    return f"кампанию {campaign_id}"


async def _resolve_region(ctx: Ctx, value: str) -> tuple[int, str]:
    names = await region_names(ctx)
    text = (value or "").strip()
    if text.isdigit() and int(text) in names:
        rid = int(text)
        return rid, names[rid]
    lowered = text.lower()
    hits = [(rid, name) for rid, name in names.items() if name.lower() == lowered]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise ValueError(f"регион «{value}» неоднозначен: {[n for _, n in hits[:5]]}.")
    partial = [(rid, name) for rid, name in names.items() if lowered in name.lower()]
    if len(partial) == 1:
        return partial[0]
    raise ValueError(
        f"регион «{value}» не найден."
        + (f" Варианты: {[n for _, n in partial[:5]]}." if partial else "")
    )


def _mod_value(item: dict) -> int | None:
    for key in (
        "MobileAdjustment",
        "TabletAdjustment",
        "DesktopAdjustment",
        "DesktopOnlyAdjustment",
        "SmartTvAdjustment",
        "RegionalAdjustment",
        "VideoAdjustment",
        "SmartAdAdjustment",
    ):
        sub = item.get(key)
        if isinstance(sub, dict) and isinstance(sub.get("BidModifier"), int):
            return int(sub["BidModifier"])
    return None


_ADD_KEY = {
    "REGIONAL": "RegionalAdjustments",
    "MOBILE": "MobileAdjustment",
    "DESKTOP": "DesktopAdjustment",
    "DESKTOP_ONLY": "DesktopOnlyAdjustment",
}
_ADD_SINGLE = {"MOBILE": "MobileAdjustment", "DESKTOP": "DesktopAdjustment"}


async def _prepare_bid_modifiers_set(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    from directai_mcp.safety.guard import GuardBlocked, require_test_campaign

    assert isinstance(params, BidModifiersSetParams)
    if not params.set_items and not params.add_items and not params.delete_ids:
        raise ValueError("укажите set_items, add_items или delete_ids.")
    # v1.1.34: add/set корректировок — можно в боевой; delete — только TEST.
    from directai_mcp.safety.guard import combat_allowed
    enforce_test = not combat_allowed(
        "bid_modifiers_set", params.model_dump())
    rules = load_rules(ctx.data_dir / "rules.toml" if ctx.data_dir else None)
    client = ctx.direct()
    try:
        current = await _modifiers_by_ids(
            client,
            entry.login,
            [m.id for m in params.set_items] + list(params.delete_ids),
        )
        preview_lines: list[str] = []
        warnings: list[str] = []
        before: dict = {}
        set_bodies: list[dict] = []
        for item in params.set_items:
            found = current.get(int(item.id))
            if found is None:
                raise ValueError(f"корректировка {item.id} не найдена.")
            scope = await _own_modifier(
                ctx, client, entry.login, found, str(item.id),
                enforce_test=enforce_test)
            old = _mod_value(found)
            lo, hi = _MOD_RANGES.get("SET", (0, 1300))
            if not (lo <= item.bid_modifier <= hi):
                raise ValueError(
                    f"корректировка {item.id}: BidModifier {item.bid_modifier} вне {lo}..{hi}."
                )
            before[str(item.id)] = {"value": old, "type": found.get("Type")}
            set_bodies.append({"Id": int(item.id), "BidModifier": int(item.bid_modifier)})
            preview_lines.append(
                f"Корректировка {item.id} ({found.get('Type')}) на {scope}: "
                f"{_pct(old)} → {_pct(item.bid_modifier)}"
            )
            warn = check_ratio(
                rules.max_bid_ratio, old, item.bid_modifier, f"корректировка {item.id}"
            )
            if warn:
                warnings.append(warn)
        add_bodies: list[dict] = []
        for add in params.add_items:
            if (add.campaign_id is None) == (add.adgroup_id is None):
                raise ValueError("add: укажите ровно один из campaign_id/adgroup_id.")
            lo, hi = _MOD_RANGES.get(add.kind, (0, 1300))
            if not (lo <= add.bid_modifier <= hi):
                raise ValueError(
                    f"add {add.kind}: BidModifier {add.bid_modifier} вне {lo}..{hi}."
                )
            scope_body: dict = {}
            if add.campaign_id is not None:
                if enforce_test:
                    await require_test_campaign(ctx, client, entry.login, int(add.campaign_id))
                scope_body["CampaignId"] = int(add.campaign_id)
                scope = f"кампанию {add.campaign_id}"
            else:
                groups = await client.get_all(
                    "adgroups",
                    {
                        "SelectionCriteria": {"Ids": [int(add.adgroup_id)]},
                        "FieldNames": ["Id", "CampaignId"],
                    },
                    entry.login,
                    "AdGroups",
                )
                if not groups:
                    raise GuardBlocked(f"группа {add.adgroup_id} не найдена.")
                if enforce_test:
                    await require_test_campaign(
                        ctx, client, entry.login, int(groups[0]["CampaignId"])
                    )
                scope_body["AdGroupId"] = int(add.adgroup_id)
                scope = f"группу {add.adgroup_id}"
            if add.kind == "REGIONAL":
                if not add.region:
                    raise ValueError("add REGIONAL: укажите region.")
                rid, rname = await _resolve_region(ctx, add.region)
                scope_body["RegionalAdjustments"] = [
                    {"RegionId": rid, "BidModifier": int(add.bid_modifier)}
                ]
                preview_lines.append(
                    f"REGIONAL {rname} ({rid}) на {scope}: {_pct(add.bid_modifier)}"
                )
            else:
                scope_body[_ADD_SINGLE[add.kind]] = {"BidModifier": int(add.bid_modifier)}
                preview_lines.append(
                    f"{add.kind} на {scope}: {_pct(add.bid_modifier)}"
                )
            add_bodies.append(scope_body)
        for mid in params.delete_ids:
            found = current.get(int(mid))
            if found is None:
                raise ValueError(f"корректировка {mid} не найдена.")
            scope = await _own_modifier(ctx, client, entry.login, found, str(mid))
            before[f"delete:{mid}"] = {"value": _mod_value(found), "type": found.get("Type")}
            preview_lines.append(
                f"Удалить корректировку {mid} ({found.get('Type')}) на {scope}: "
                f"{_pct(_mod_value(found))} → —"
            )
    finally:
        await client.aclose()
    requests = []
    if add_bodies:
        requests.append(("bidmodifiers", "add", {"BidModifiers": add_bodies}, "v5"))
    if set_bodies:
        requests.append(("bidmodifiers", "set", {"BidModifiers": set_bodies}, "v5"))
    if params.delete_ids:
        requests.append(
            ("bidmodifiers", "delete", {"SelectionCriteria": {"Ids": list(params.delete_ids)}}, "v5")
        )
    return {
        "before": before or None,
        "requests": requests,
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in preview_lines),
        "warnings": warnings,
    }


async def _verify_bid_modifiers_set(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    set_items = params.get("set_items", []) or []
    add_items = params.get("add_items", []) or []
    delete_ids = [int(i) for i in (params.get("delete_ids", []) or [])]
    client = ctx.direct()
    try:
        bad: list[str] = []
        after: dict = {}
        if set_items:
            current = await _modifiers_by_ids(
                client, entry.login, [int(m["id"]) for m in set_items]
            )
            for m in set_items:
                found = current.get(int(m["id"]), {})
                got = _mod_value(found)
                after[str(m["id"])] = got
                if got != int(m["bid_modifier"]):
                    bad.append(f"{m['id']}: {got} != {m['bid_modifier']}")
        if add_items or delete_ids:
            scopes_c = sorted(
                {
                    int(a["campaign_id"])
                    for a in add_items
                    if a.get("campaign_id") is not None
                }
            )
            scopes_g = sorted(
                {int(a["adgroup_id"]) for a in add_items if a.get("adgroup_id") is not None}
            )
            if delete_ids:
                gone = await _modifiers_by_ids(client, entry.login, delete_ids)
                for mid in delete_ids:
                    if mid in gone:
                        bad.append(f"{mid}: не удалена")
                    after[f"delete:{mid}"] = gone.get(mid, "удалена")
            if add_items:
                live = await _modifiers_by_scope(client, entry.login, scopes_c, scopes_g)
                for a in add_items:
                    want = int(a["bid_modifier"])
                    if a.get("kind") == "REGIONAL" and a.get("region"):
                        try:
                            rid, _ = await _resolve_region(ctx, a["region"])
                        except ValueError:
                            rid = None
                        hit = any(
                            isinstance(m.get("RegionalAdjustment"), dict)
                            and m["RegionalAdjustment"].get("RegionId") == rid
                            and m["RegionalAdjustment"].get("BidModifier") == want
                            for m in live
                        )
                        after[f"add:{a['kind']}:{a['region']}"] = "OK" if hit else "НЕ НАЙДЕНО"
                        if not hit:
                            bad.append(f"add {a['kind']} {a['region']}: не подтверждено")
                    else:
                        key = _ADD_SINGLE.get(a.get("kind", ""), "")
                        hit = any(
                            isinstance(m.get(key), dict) and m[key].get("BidModifier") == want
                            for m in live
                        )
                        after[f"add:{a['kind']}"] = "OK" if hit else "НЕ НАЙДЕНО"
                        if not hit:
                            bad.append(f"add {a.get('kind')}: не подтверждено")
    finally:
        await client.aclose()
    if bad:
        return {"after": after, "ok": False, "note": "read-back НЕ подтвердил: " + "; ".join(bad)}
    return {"after": after, "ok": True, "note": "подтверждено read-back."}


write_action(
    "bid_modifiers_set",
    "Корректировки ставок: добавить, изменить, удалить. "
    "Правила: разные категории перемножаются; внутри категории побеждает "
    "наибольшая; −100% низший приоритет; групповая перекрывает ту же "
    "категорию кампании; в конверсионных стратегиях меняет целевую CPA/ДРР.",
    (
        "корректировка ставок",
        "bidmodifiers",
        "bid modifiers",
        "изменить корректировку",
        "удалить корректировку",
        "региональная корректировка",
    ),
    BidModifiersSetParams,
    prepare=_prepare_bid_modifiers_set,
    apply=lambda ctx, entry, plan: _apply_modifiers(ctx, entry, plan),
    verify=_verify_bid_modifiers_set,
)


_MOD_RESULT_KEY = {"add": "AddResults", "set": "SetResults", "delete": "DeleteResults"}


async def _apply_modifiers(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    lines: list[str] = []
    ok = 0
    total = 0
    created: list[int] = []
    last: dict = {}
    client = ctx.direct()
    try:
        for request in plan.requests:
            service, method, body, version = split_request(request)
            key = _MOD_RESULT_KEY.get(method, "SetResults")
            if method == "add":
                labels = [f"add{i}" for i, _ in enumerate(body.get("BidModifiers", []))]
            elif method == "delete":
                labels = [str(i) for i in (body.get("SelectionCriteria") or {}).get("Ids", [])]
            else:
                labels = [str(b.get("Id")) for b in body.get("BidModifiers", [])]
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
            last = result
            batch, n_ok = summarize(labels, result.get(key, []))
            lines.extend(batch)
            ok += n_ok
            total += len(labels)
            if method == "add":
                for res in result.get(key, []):
                    created.extend(res.get("Ids") or [])
    finally:
        await client.aclose()
    status = "applied" if ok == total else "failed" if ok == 0 else "partial"
    return {"status": status, "lines": lines, "response": {"created_ids": created, "last": last}}
