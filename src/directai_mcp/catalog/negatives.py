"""Read action negatives_audit (campaign + groups + shared sets)."""

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


class NegativesAuditParams(GetActionParams):
    campaign_ids: list[int] = Field(min_length=1)


def _items(value: object) -> list:
    if isinstance(value, dict) and isinstance(value.get("Items"), list):
        return value["Items"]
    if isinstance(value, list):
        return value
    return []


# Лимиты минус-фраз из документации API Директа (v1.1.31, DECISIONS):
# campaigns/update: фраза <=7 слов, слово <=35 символов, сумма <=20000
# (пробелы/дефисы/операторы не считаются); adgroups/update и
# negativekeywordsharedsets/add: те же, сумма <=4096.
MAX_NEG_WORDS = 7
MAX_NEG_WORD_LEN = 35
MAX_NEG_TOTAL_CAMPAIGN = 20000
MAX_NEG_TOTAL_GROUP = 4096
MAX_NEG_TOTAL_SET = 4096
_NEG_TOTAL_EXCLUDE = set(" \t-!+\"[]")


def _norm_negative(phrase: object) -> str:
    """Нормализованная фраза для сравнений (v1.1.31)."""
    return " ".join(str(phrase or "").split()).casefold()


def _neg_len(phrase: str) -> int:
    """Длина фразы для суммарного лимита (без пробелов/дефисов/операторов)."""
    return sum(1 for ch in phrase if ch not in _NEG_TOTAL_EXCLUDE)


def _validate_negatives(phrases: list[str], total_limit: int, where: str) -> None:
    """Валидация до вызова API (v1.1.31). Нарушение — ValueError."""
    total = 0
    for phrase in phrases:
        norm = " ".join(str(phrase or "").split())
        if not norm:
            raise ValueError(f"{where}: пустая минус-фраза.")
        words = norm.split(" ")
        if len(words) > MAX_NEG_WORDS:
            raise ValueError(
                f"{where}: фраза «{norm}» — {len(words)} слов "
                f"(лимит {MAX_NEG_WORDS})."
            )
        for word in words:
            if len(word) > MAX_NEG_WORD_LEN:
                raise ValueError(
                    f"{where}: слово «{word}» — {len(word)} символов "
                    f"(лимит {MAX_NEG_WORD_LEN})."
                )
        total += _neg_len(norm)
    if total > total_limit:
        raise ValueError(
            f"{where}: суммарная длина {total} (лимит {total_limit})."
        )


@action(
    "negatives_audit",
    "read",
    "Все минус-фразы кампании: кампания, группы, общие наборы. "
    "Операторы: \"...\" — запрос только из этих слов; [] — порядок; "
    "! — словоформа; + — обязательность; полное пересечение минуса "
    "с ключом отменяет действие минуса.",
    (
        "минус-фразы",
        "минуса",
        "negatives",
        "минус-слова",
        "аудит минусов",
        "набор",
        "наборы",
        "shared set",
        "общий набор",
        "минус-фразы набор",
    ),
    NegativesAuditParams,
)
async def _audit(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, NegativesAuditParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}
    _NEG_FIELDS = {
        "Campaigns": {
            "FieldNames": ["Id", "Name", "NegativeKeywords"],
            "TextCampaignFieldNames": ["NegativeKeywordSharedSetIds"],
            "UnifiedCampaignFieldNames": ["NegativeKeywordSharedSetIds"],
        },
        "AdGroups": {
            "FieldNames": [
                "Id", "CampaignId", "Name", "NegativeKeywords",
                "NegativeKeywordSharedSetIds",
            ],
        },
        "NegativeKeywordSharedSets": {
            "FieldNames": ["Id", "Name", "NegativeKeywords"],
        },
    }

    async def fetch(entry: AccountEntry, client):
        campaigns = await client.get_all(
            "campaigns",
            dict(
                {"SelectionCriteria": {"Ids": params.campaign_ids}},
                **_NEG_FIELDS["Campaigns"],
            ),
            entry.login,
            "Campaigns",
            "v501",
            tally=tally,
        )
        groups: list[dict] = []
        for ids in chunk(params.campaign_ids, 10):
            groups.extend(
                await client.get_all(
                    "adgroups",
                    dict(
                        {"SelectionCriteria": {"CampaignIds": ids}},
                        **_NEG_FIELDS["AdGroups"],
                    ),
                    entry.login,
                    "AdGroups",
                    tally=tally,
                )
            )
        set_ids: set[int] = set()
        for obj in campaigns + groups:
            for key in ("TextCampaign", "UnifiedCampaign"):
                sub = obj.get(key) or {}
                set_ids.update(_items(sub.get("NegativeKeywordSharedSetIds")))
            set_ids.update(_items(obj.get("NegativeKeywordSharedSetIds")))
        shared: list[dict] = []
        if set_ids:
            shared = await client.get_all(
                "negativekeywordsharedsets",
                dict(
                    {"SelectionCriteria": {"Ids": sorted(set_ids)}},
                    **_NEG_FIELDS["NegativeKeywordSharedSets"],
                ),
                entry.login,
                "NegativeKeywordSharedSets",
                tally=tally,
            )
        return campaigns, groups, shared, _NEG_FIELDS

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = ["Уровень", "Владелец", "Фраза"]
    rows: list[dict] = []
    raw_camps: list[dict] = []
    raw_groups: list[dict] = []
    raw_sets: list[dict] = []
    errors: list[str] = []
    counts = {"Кампания": 0, "Группы": 0, "Наборы": 0}
    neg_fields: dict = {}
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        campaigns, groups, shared, neg_fields = payload
        raw_camps.extend(campaigns)
        raw_groups.extend(groups)
        raw_sets.extend(shared)
        for camp in campaigns:
            owner = f"{camp.get('Name')} ({camp.get('Id')})"
            for phrase in _items(camp.get("NegativeKeywords")):
                rows.append(
                    {
                        "_account": entry.login,
                        "Уровень": "Кампания",
                        "Владелец": owner,
                        "Фраза": phrase,
                    }
                )
                counts["Кампания"] += 1
        for group in groups:
            owner = f"{group.get('Name')} ({group.get('Id')})"
            for phrase in _items(group.get("NegativeKeywords")):
                rows.append(
                    {
                        "_account": entry.login,
                        "Уровень": "Группа",
                        "Владелец": owner,
                        "Фраза": phrase,
                    }
                )
                counts["Группы"] += 1
        for shared_set in shared:
            owner = f"{shared_set.get('Name')} ({shared_set.get('Id')})"
            for phrase in _items(shared_set.get("NegativeKeywords")):
                rows.append(
                    {
                        "_account": entry.login,
                        "Уровень": "Общий набор",
                        "Владелец": owner,
                        "Фраза": phrase,
                    }
                )
                counts["Наборы"] += 1
    order = {"Кампания": 0, "Группа": 1, "Общий набор": 2}
    rows.sort(key=lambda r: (order.get(r["Уровень"], 9), r["Владелец"], r["Фраза"]))
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = (
        f"{mark}negatives_audit: {', '.join(e.login for e in entries)}. "
        f"Кампания: {counts['Кампания']}; группы: {counts['Группы']}; "
        f"наборы: {counts['Наборы']}."
    )
    return finalize(
        ctx,
        context,
        "negatives_audit",
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
        dump_action="negatives_audit",
        dump_params=params.model_dump(),
        dump_raw={"negatives_audit": []},
        dump_extra={
            "campaigns": [
                dict(i, linked_to_campaign=True) for i in raw_camps],
            "adgroups": [
                dict(i, linked_to_campaign=True) for i in raw_groups],
            "negative_keyword_shared_sets": [
                dict(i, linked_to_campaign=True) for i in raw_sets],
        },
        dump_fields=neg_fields,
        dump_tally=tally,
        dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )


class SharedSetCreate(BaseModel):
    name: str
    negatives: list[str] = Field(min_length=1)


class SharedSetUpdate(BaseModel):
    id: int
    name: str | None = None
    negatives: list[str] | None = None


class NegativesSetParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    # v1.1.31: add — объединить с текущими (дефолт); replace — заменить.
    mode: Literal["add", "replace"] = "add"
    new_shared_set: SharedSetCreate | None = None
    update_shared_set: SharedSetUpdate | None = None


async def _current_negatives(
    ctx: Ctx, entry: AccountEntry, campaign_ids: list[int],
    adgroup_ids: list[int],
) -> tuple[dict[int, list[str]], dict[int, list[str]], dict[int, int]]:
    """Текущие минус-фразы кампаний и групп (read-only, для add/preview).

    Третий элемент — кампания каждой группы {gid: cid} (v1.13.0, журнал).
    """
    camp: dict[int, list[str]] = {}
    groups: dict[int, list[str]] = {}
    group_campaigns: dict[int, int] = {}
    client = ctx.direct()
    try:
        if campaign_ids:
            for ids in chunk(campaign_ids, 100):
                items = await client.get_all(
                    "campaigns",
                    {"SelectionCriteria": {"Ids": ids},
                     "FieldNames": ["Id", "NegativeKeywords"]},
                    entry.login,
                    "Campaigns",
                    "v501",
                )
                for item in items:
                    if isinstance(item, dict) and item.get("Id") is not None:
                        camp[int(item["Id"])] = [
                            str(p) for p in _items(item.get("NegativeKeywords"))]
        if adgroup_ids:
            for ids in chunk(adgroup_ids, 100):
                items = await client.get_all(
                    "adgroups",
                    {"SelectionCriteria": {"Ids": ids},
                     "FieldNames": ["Id", "CampaignId", "NegativeKeywords"]},
                    entry.login,
                    "AdGroups",
                )
                for item in items:
                    if isinstance(item, dict) and item.get("Id") is not None:
                        groups[int(item["Id"])] = [
                            str(p) for p in _items(item.get("NegativeKeywords"))]
                        # v1.13.0: кампания группы — для привязки журнала.
                        if item.get("CampaignId") is not None:
                            try:
                                group_campaigns[int(item["Id"])] = int(
                                    item["CampaignId"])
                            except (TypeError, ValueError):
                                pass
    finally:
        await client.aclose()
    return camp, groups, group_campaigns


async def _current_set_negatives(
    ctx: Ctx, entry: AccountEntry, set_id: int
) -> list[str]:
    """Текущие фразы общего набора (read-only, для preview replace)."""
    client = ctx.direct()
    try:
        items = await client.get_all(
            "negativekeywordsharedsets",
            {"SelectionCriteria": {"Ids": [set_id]},
             "FieldNames": ["Id", "NegativeKeywords"]},
            entry.login,
            "NegativeKeywordSharedSets",
        )
    finally:
        await client.aclose()
    for item in items:
        if isinstance(item, dict) and item.get("Id") == set_id:
            return [str(p) for p in _items(item.get("NegativeKeywords"))]
    return []


async def _prepare_negatives_set(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, NegativesSetParams)
    if (
        not params.campaign_ids
        and not params.adgroup_ids
        and params.new_shared_set is None
        and params.update_shared_set is None
    ):
        raise ValueError("укажите campaign_ids, adgroup_ids или набор.")
    if (
        (params.campaign_ids or params.adgroup_ids)
        and not params.negatives
        and params.new_shared_set is None
        and params.update_shared_set is None
    ):
        raise ValueError("negatives пуст: нечего устанавливать.")
    requests: list[tuple[str, str, dict]] = []
    preview: list[str] = []
    # v1.1.31: текущие списки — для add-мержа и превью было/станет.
    # Валидация — до любых write-запросов (только read).
    current_camp: dict[int, list[str]] = {}
    current_groups: dict[int, list[str]] = {}
    group_campaigns: dict[int, int] = {}
    if params.campaign_ids or params.adgroup_ids:
        current_camp, current_groups, group_campaigns = await _current_negatives(
            ctx, entry, params.campaign_ids, params.adgroup_ids)
    if params.campaign_ids:
        merged: dict[int, list[str]] = {}
        for cid in params.campaign_ids:
            current = current_camp.get(cid, [])
            if params.mode == "add":
                seen: dict[str, str] = {}
                for phrase in list(current) + list(params.negatives):
                    key = _norm_negative(phrase)
                    if key and key not in seen:
                        seen[key] = str(phrase)
                final = list(seen.values())
                dups = len(params.negatives) - (len(final) - len(current))
                preview.append(
                    f"кампании {cid}: было {len(current)}, добавляется "
                    f"{len(params.negatives)}, дублей {max(dups, 0)}, "
                    f"станет {len(final)}"
                )
            else:
                final = [str(p) for p in params.negatives]
                preview.append(
                    f"кампании {cid}: было {len(current)} → станет "
                    f"{len(final)}. ВНИМАНИЕ: существующие фразы "
                    "будут удалены."
                )
            _validate_negatives(final, MAX_NEG_TOTAL_CAMPAIGN,
                                f"кампания {cid}")
            merged[cid] = final
        requests.append(
            (
                "campaigns",
                "update",
                {
                    "Campaigns": [
                        {"Id": cid, "NegativeKeywords": {"Items": merged[cid]}}
                        for cid in params.campaign_ids
                    ]
                },
            )
        )
    if params.adgroup_ids:
        merged_groups: dict[int, list[str]] = {}
        for gid in params.adgroup_ids:
            current = current_groups.get(gid, [])
            if params.mode == "add":
                seen = {}
                for phrase in list(current) + list(params.negatives):
                    key = _norm_negative(phrase)
                    if key and key not in seen:
                        seen[key] = str(phrase)
                final = list(seen.values())
                dups = len(params.negatives) - (len(final) - len(current))
                preview.append(
                    f"группы {gid}: было {len(current)}, добавляется "
                    f"{len(params.negatives)}, дублей {max(dups, 0)}, "
                    f"станет {len(final)}"
                )
            else:
                final = [str(p) for p in params.negatives]
                preview.append(
                    f"группы {gid}: было {len(current)} → станет "
                    f"{len(final)}. ВНИМАНИЕ: существующие фразы "
                    "будут удалены."
                )
            _validate_negatives(final, MAX_NEG_TOTAL_GROUP, f"группа {gid}")
            merged_groups[gid] = final
        requests.append(
            (
                "adgroups",
                "update",
                {
                    "AdGroups": [
                        {"Id": gid, "NegativeKeywords": {"Items": merged_groups[gid]}}
                        for gid in params.adgroup_ids
                    ]
                },
            )
        )
    if params.new_shared_set is not None:
        _validate_negatives(params.new_shared_set.negatives, MAX_NEG_TOTAL_SET,
                            f"набор «{params.new_shared_set.name}»")
        requests.append(
            (
                "negativekeywordsharedsets",
                "add",
                {
                    "NegativeKeywordSharedSets": [
                        {
                            "Name": params.new_shared_set.name,
                            "NegativeKeywords": params.new_shared_set.negatives,
                        }
                    ]
                },
            )
        )
        preview.append(
            f"новый набор «{params.new_shared_set.name}»: "
            f"{len(params.new_shared_set.negatives)} фраз"
        )
    if params.update_shared_set is not None:
        upd = params.update_shared_set
        body: dict = {"Id": upd.id}
        if upd.name is not None:
            body["Name"] = upd.name
        if upd.negatives is not None:
            body["NegativeKeywords"] = upd.negatives
        if len(body) == 1:
            raise ValueError(f"набор {upd.id}: нечего менять.")
        if upd.negatives is not None:
            # Наборы — всегда replace: превью было → станет + предупреждение.
            _validate_negatives(upd.negatives, MAX_NEG_TOTAL_SET,
                                f"набор {upd.id}")
            before = await _current_set_negatives(ctx, entry, upd.id)
            preview.append(
                f"набор {upd.id}: было {len(before)} → станет "
                f"{len(upd.negatives)}. ВНИМАНИЕ: существующие фразы "
                "будут удалены."
            )
        else:
            preview.append(f"набор {upd.id}: обновление")
        requests.append(
            (
                "negativekeywordsharedsets",
                "update",
                {"NegativeKeywordSharedSets": [body]},
            )
        )
        if upd.negatives is None:
            preview.append(f"набор {upd.id}: обновление")
    group_cids = sorted({c for c in group_campaigns.values() if c})
    return {
        # v1.13.0: кампании правок (прямые + через группы) — для журнала.
        "before": {
            "campaign_ids": sorted(set(params.campaign_ids) | set(group_cids)),
            "adgroup_campaigns": {
                gid: group_campaigns.get(gid) for gid in params.adgroup_ids
            } if params.adgroup_ids else {},
        },
        "requests": requests,
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in preview),
        "warnings": [],
    }


async def _apply_negatives_set(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    keys = {
        "campaigns": "UpdateResults",
        "adgroups": "UpdateResults",
        "negativekeywordsharedsets": "AddResults",
    }
    client = ctx.direct()
    all_lines: list[str] = []
    oks = totals = 0
    response: dict = {}
    try:
        for service, method, body, version in (split_request(r) for r in plan.requests):
            key = (
                "UpdateResults"
                if method == "update"
                else keys.get(service, "AddResults")
            )
            if service == "negativekeywordsharedsets" and method == "update":
                key = "UpdateResults"
            try:
                result = await client.call(service, method, body, entry.login, version)
            except DirectUnverifiedError:
                raise
            except DirectError as e:
                return {
                    "status": "failed",
                    "lines": all_lines + [f"Ошибка API: {e.human_message()}"],
                    "response": {"error": e.human_message()},
                }
            response[f"{service}.{method}"] = result
            ids = _request_ids(service, body)
            lines, ok = summarize(ids, result.get(key, []))
            all_lines += lines
            oks += ok
            totals += len(ids)
    finally:
        await client.aclose()
    status = "applied" if oks == totals else "failed" if oks == 0 else "partial"
    return {"status": status, "lines": all_lines, "response": response}


def _request_ids(service: str, body: dict) -> list[str]:
    if service == "campaigns":
        return [str(c.get("Id")) for c in body.get("Campaigns", [])]
    if service == "adgroups":
        return [str(g.get("Id")) for g in body.get("AdGroups", [])]
    return [
        s.get("Name", str(i))
        for i, s in enumerate(body.get("NegativeKeywordSharedSets", []))
    ]


async def _verify_negatives_set(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.catalog.common import split_request

    # Ожидаемое — из тел update-запросов плана (одинаково для add/replace).
    expected_camp: dict[int, set[str]] = {}
    expected_groups: dict[int, set[str]] = {}
    for request in plan.requests:
        service, method, body, _version = split_request(request)
        if service == "campaigns" and method == "update":
            for camp in body.get("Campaigns", []):
                expected_camp[int(camp.get("Id"))] = {
                    _norm_negative(p)
                    for p in _items(camp.get("NegativeKeywords"))
                } - {""}
        if service == "adgroups" and method == "update":
            for group in body.get("AdGroups", []):
                expected_groups[int(group.get("Id"))] = {
                    _norm_negative(p)
                    for p in _items(group.get("NegativeKeywords"))
                } - {""}
    notes = []
    ok = True
    client = ctx.direct()
    try:
        if expected_camp:
            found = await client.get_all(
                "campaigns",
                {
                    "SelectionCriteria": {"Ids": sorted(expected_camp)},
                    "FieldNames": ["Id", "NegativeKeywords"],
                },
                entry.login,
                "Campaigns",
            )
            for item in found:
                cid = int(item.get("Id"))
                items = _items(item.get("NegativeKeywords"))
                got = {_norm_negative(p) for p in items} - {""}
                if got != expected_camp.get(cid, set()):
                    ok = False
                    notes.append(f"кампания {cid}: {len(got)} шт")
        if expected_groups:
            found = await client.get_all(
                "adgroups",
                {
                    "SelectionCriteria": {"Ids": sorted(expected_groups)},
                    "FieldNames": ["Id", "NegativeKeywords"],
                },
                entry.login,
                "AdGroups",
            )
            for item in found:
                gid = int(item.get("Id"))
                items = _items(item.get("NegativeKeywords"))
                got = {_norm_negative(p) for p in items} - {""}
                if got != expected_groups.get(gid, set()):
                    ok = False
                    notes.append(f"группа {gid}: {len(got)} шт")
    finally:
        await client.aclose()
    params = plan.params
    assert isinstance(params, dict)
    if params.get("new_shared_set") or params.get("update_shared_set"):
        notes.append("наборы: см. id в строках результата")
    note = (
        "подтверждено read-back."
        if ok
        else "read-back НЕ подтвердил: " + "; ".join(notes)
    )
    return {"after": None, "ok": ok, "note": note}


write_action(
    "negatives_set",
    "Минус-фразы кампании/групп и общие наборы. "
    "Цену, отзывы, гео-слова никогда не минусовать автоматически "
    "(только вручную после анализа).",
    ("минус-фразы", "negatives", "установить минусы", "общий набор"),
    NegativesSetParams,
    prepare=_prepare_negatives_set,
    apply=_apply_negatives_set,
    verify=_verify_negatives_set,
)
