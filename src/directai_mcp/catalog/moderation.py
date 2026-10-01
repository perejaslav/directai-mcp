"""moderation_check: сводка модерации кабинета/кампаний (v1.1.25, read-only)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog import ads as _ads
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    finalize,
    map_accounts,
    version_footer,
)
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import AccountEntry

# Архив в выборку не входит (как campaigns_list без архива, SPEC §7.7).
LIVE_STATES = ["ON", "OFF", "SUSPENDED", "ENDED"]
# Приостановлено вручную (не архив).
SUSPENDED_STATES = ("OFF", "SUSPENDED")


class ModerationCheckParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    include_archived: bool = False


def _suspended(state: object) -> bool:
    return state in SUSPENDED_STATES


async def _fetch_login(entry: AccountEntry, client, params: ModerationCheckParams,
                     tally: dict | None = None):
    """Кампании, группы, объявления, фразы, модерация уточнений (read-only)."""
    states = list(LIVE_STATES) + (["ARCHIVED"] if params.include_archived else [])
    camps = await client.get_all(
        "campaigns",
        {
            "SelectionCriteria": {"States": states},
            "FieldNames": ["Id", "Name", "State", "Status", "Type"],
        },
        entry.login,
        "Campaigns",
        tally=tally,
    )
    if params.campaign_ids:
        wanted = set(params.campaign_ids)
        camps = [c for c in camps if c.get("Id") in wanted]
    cids = [c["Id"] for c in camps
            if isinstance(c.get("Id"), int) and c.get("State") != "ARCHIVED"]

    from directai_mcp.catalog import ads as _ads

    groups: list[dict] = []
    for ids in chunk(cids, 10):
        groups.extend(await client.get_all(
            "adgroups",
            {
                "SelectionCriteria": {"CampaignIds": ids},
                "FieldNames": ["Id", "CampaignId", "Name", "Status",
                               "ServingStatus"],
            },
            entry.login,
            "AdGroups",
            "v501",
            tally=tally,
        ))
    ads: list[dict] = []
    for ids in chunk(cids, 10):
        ads.extend(await client.get_all(
            "ads",
            {
                "SelectionCriteria": {"CampaignIds": ids},
                "FieldNames": _ads.FIELDS,
                "TextAdFieldNames": _ads.TEXT_FIELDS,
                "ResponsiveAdFieldNames": _ads.RESPONSIVE_FIELDS,
            },
            entry.login,
            "Ads",
            tally=tally,
        ))
    infos = {a.get("Id"): _ads._extract(a) for a in ads}

    keywords: list[dict] = []
    for ids in chunk(cids, 10):
        keywords.extend(await client.get_all(
            "keywords",
            {
                "SelectionCriteria": {"CampaignIds": ids},
                "FieldNames": ["Id", "CampaignId", "AdGroupId", "Keyword",
                               "State", "Status", "ServingStatus"],
            },
            entry.login,
            "Keywords",
            tally=tally,
        ))

    ext_ids = sorted({x for info in infos.values() for x in info["ext_ids"]})
    callout_rej: dict[int, tuple[str, str]] = {}
    if ext_ids:
        for ids in chunk(ext_ids, 1000):
            exts = await client.get_all(
                "adextensions",
                {
                    "SelectionCriteria": {"Ids": ids},
                    "FieldNames": ["Id", "Type", "Status", "StatusClarification"],
                    "CalloutFieldNames": ["CalloutText"],
                },
                entry.login,
                "AdExtensions",
                tally=tally,
            )
            for e in exts:
                if e.get("Status") == "REJECTED":
                    text = (e.get("Callout") or {}).get("CalloutText", "")
                    clar = e.get("StatusClarification")
                    callout_rej[e["Id"]] = (text or f"#{e['Id']}",
                                            str(clar) if clar else "")
    return camps, groups, ads, infos, keywords, callout_rej


def _check(entry: AccountEntry, fetched) -> tuple[list[dict], list[dict]]:
    """(строки отклонений, строки приостановленных вручную)."""
    camps, groups, ads, infos, keywords, callout_rej = fetched
    camp_name = {c.get("Id"): str(c.get("Name") or c.get("Id")) for c in camps}
    group_name = {g.get("Id"): str(g.get("Name") or g.get("Id")) for g in groups}
    group_camp = {g.get("Id"): g.get("CampaignId") for g in groups}
    bad: list[dict] = []
    held: list[dict] = []

    def _row(cid, gid, oid, obj, status, why):
        return {
            "Кампания": camp_name.get(cid, str(cid)),
            "Группа": group_name.get(gid, "—") if gid is not None else "—",
            "ID": oid,
            "Объект": obj,
            "Статус": status,
            "Причина": why or "—",
        }

    for c in camps:
        cid = c.get("Id")
        if c.get("Status") == "REJECTED":
            bad.append(_row(cid, None, cid, "Кампания", "REJECTED", "—"))
        if _suspended(c.get("State")):
            held.append(_row(cid, None, cid, "Кампания", str(c.get("State")), "—"))
    for g in groups:
        cid, gid = g.get("CampaignId"), g.get("Id")
        serving = g.get("ServingStatus")
        if g.get("Status") == "REJECTED":
            bad.append(_row(cid, gid, gid, "Группа", "REJECTED", "—"))
        elif serving and serving != "ELIGIBLE":
            bad.append(_row(cid, gid, gid, "Группа", str(serving), "—"))
    for ad in ads:
        cid, gid, aid = ad.get("CampaignId"), ad.get("AdGroupId"), ad.get("Id")
        info = infos.get(aid) or {}
        status = ad.get("Status")
        clar = str(ad.get("StatusClarification") or "—").replace("|", "/")
        if status == "REJECTED":
            bad.append(_row(cid, gid, aid,
                            f"Объявление {ad.get('Type')}", "REJECTED", clar))
            continue
        if status == "ACCEPTED":
            parts = []
            for name in info.get("partial") or []:
                why = (info.get("partial_why") or {}).get(name)
                parts.append(f"{name}: {why}" if why else name)
            for eid in info.get("ext_ids") or []:
                if eid in callout_rej:
                    text, why = callout_rej[eid]
                    parts.append(f"Уточнение «{text}»: {why}" if why
                                 else f"Уточнение «{text}»")
            if parts:
                bad.append(_row(cid, gid, aid,
                                f"Объявление {ad.get('Type')}",
                                "частично отклонено", "; ".join(parts)))
        if _suspended(ad.get("State")):
            held.append(_row(cid, gid, aid,
                             f"Объявление {ad.get('Type')}",
                             str(ad.get("State")), "—"))
        # Группа фразы может отсутствовать в выборке (фильтр кампаний):
        if gid is not None and gid not in group_name:
            group_name[gid] = str(group_camp.get(gid, "—"))
    for kw in keywords:
        cid, gid, kid = (kw.get("CampaignId"), kw.get("AdGroupId"),
                         kw.get("Id"))
        label = f"Фраза «{kw.get('Keyword') or kid}»"
        if kw.get("Status") == "REJECTED":
            bad.append(_row(cid, gid, kid, label, "REJECTED", "—"))
        elif kw.get("ServingStatus") and kw.get("ServingStatus") != "ELIGIBLE":
            bad.append(_row(cid, gid, kid, label,
                            str(kw.get("ServingStatus")), "—"))
        # v1.1.25-fix: ручная остановка фраз — SUSPENDED в блок;
        # OFF исключён (штатное состояние, шум).
        if kw.get("State") == "SUSPENDED":
            held.append(_row(cid, gid, kid, label, "SUSPENDED", "—"))
    key_cols = ("Кампания", "Группа", "ID")

    def _key(row: dict) -> tuple:
        return tuple(str(row[c]) for c in key_cols)

    bad.sort(key=_key)
    held.sort(key=_key)
    return bad, held


@action(
    "moderation_check",
    "read",
    "Модерация: отклонённые, частично отклонённые, RARELY_SERVED, приостановленные",
    ("модерация", "отклонено", "rejected", "статус", "moderation",
     "приостановлено", "редкие показы", "rarely", "проверка"),
    ModerationCheckParams,
)
async def _run(ctx: Ctx, params: BaseModel) -> str:
    """v1.1.25: сводка модерации одной таблицей + блок приостановленных."""
    assert isinstance(params, ModerationCheckParams)

    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        return await _fetch_login(entry, client, params, tally=tally)

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = ["Кампания", "Группа", "ID", "Объект", "Статус", "Причина"]
    bad: list[dict] = []
    held: list[dict] = []
    raw: dict[str, list] = {"campaigns": [], "adgroups": [], "ads": [],
                            "keywords": []}
    errors: list[str] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        camps, groups, ads, _infos, keywords, _rej = payload
        raw["campaigns"].extend(camps)
        raw["adgroups"].extend(groups)
        raw["ads"].extend(ads)
        raw["keywords"].extend(keywords)
        partial_bad, partial_held = _check(entry, payload)
        if len(entries) > 1:
            for row in (*partial_bad, *partial_held):
                row["Кампания"] = f"{entry.login}: {row['Кампания']}"
        bad.extend(partial_bad)
        held.extend(partial_held)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    scope = (f"кампании {', '.join(map(str, params.campaign_ids))}"
             if params.campaign_ids else "весь кабинет")
    context = (f"{mark}moderation_check: "
               f"{', '.join(e.login for e in entries)} ({scope}).")
    out = finalize(
        ctx, context, "moderation_check", columns, bad,
        params.limit, params.save_as, errors,
        with_totals=False, output=params.output, format=params.format,
        account=params.account, with_version=True,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="moderation_check",
        dump_params=params.model_dump(),
        dump_raw={"moderation_check": []},
        dump_extra={
            "campaigns": [
                dict(i, linked_to_campaign=True) for i in raw["campaigns"]],
            "adgroups": [
                dict(i, linked_to_campaign=True) for i in raw["adgroups"]],
            "ads": [dict(i, linked_to_campaign=True) for i in raw["ads"]],
            "keywords": [
                dict(i, linked_to_campaign=True) for i in raw["keywords"]],
        },
        dump_fields={
            "Campaigns": ["Id", "Name", "State", "Status", "Type"],
            "AdGroups": ["Id", "CampaignId", "Name", "Status",
                         "ServingStatus"],
            "Ads": {"FieldNames": _ads.FIELDS,
                      "TextAdFieldNames": _ads.TEXT_FIELDS,
                      "ResponsiveAdFieldNames": _ads.RESPONSIVE_FIELDS},
            "Keywords": ["Id", "CampaignId", "AdGroupId", "Keyword",
                         "State", "Status", "ServingStatus"],
        },
        dump_tally=tally,
        dump_logins=[e.login for e in entries],
        dump_scope="campaign" if params.campaign_ids else "cabinet",
    )
    # Пустой результат — сообщение вместо «Строк нет.» (везде, включая
    # сводку file-режима: там тот же render_table).
    if not bad:
        out = out.replace(
            "Строк нет.",
            "Отклонений нет: REJECTED, частично отклонённых "
            "и RARELY_SERVED не найдено.")
    out += "\n\nПриостановлено вручную:"
    if held:
        from directai_mcp.fmt import render_table

        held_rows = [
            {"Кампания": r["Кампания"], "Группа": r["Группа"],
             "ID": r["ID"], "Объект": r["Объект"], "Состояние": r["Статус"]}
            for r in held
        ]
        out += "\n\n" + render_table(
            "Приостановленные объекты",
            ["Кампания", "Группа", "ID", "Объект", "Состояние"],
            held_rows, max(len(held_rows), 1), with_totals=False,
        )
    else:
        out += " нет."
    # v1.1.11: версия запущенного процесса — самая последняя строка.
    return out + f"\n\n{version_footer()}"
