"""Read action keywords_list (Keywords.get) + keywords_state (step 4 minimal)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    clean_phrase,
    finalize,
    map_accounts,
    micros_to_rubles,
    split_request,
    summarize,
)
from directai_mcp.catalog.registry import Ctx, action, write_action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import money

FIELDS = [
    "Id",
    "CampaignId",
    "AdGroupId",
    "Keyword",
    "State",
    "Status",
    "ServingStatus",
    "Bid",
    "ContextBid",
]

# v1.1.20: настройки автотаргетинга (Keywords.get, ref-v5/keywords/get).
AUTO_CATEGORIES = ("Exact", "Narrow", "Alternative", "Accessory", "Broader")
AUTO_BRANDS = ("WithoutBrands", "WithAdvertiserBrand", "WithCompetitorsBrand")
AUTO_CAT_LABELS = {
    "Exact": "целевые",
    "Narrow": "узкие",
    "Alternative": "альтернативные",
    "Accessory": "сопутствующие",
    "Broader": "широкие",
}
AUTO_BRAND_LABELS = {
    "WithoutBrands": "без брендов",
    "WithAdvertiserBrand": "бренд рекламодателя",
    "WithCompetitorsBrand": "бренд конкурентов",
}
AUTO_KEYWORD = "---autotargeting"


def autotargeting_summary(settings: dict | None) -> str | None:
    """Сводка AutotargetingSettings: «целевые; бренд рекламодателя»."""
    if not isinstance(settings, dict):
        return None
    cats = settings.get("Categories")
    brands = settings.get("BrandOptions")
    if not isinstance(cats, dict) and not isinstance(brands, dict):
        return None
    cats = cats if isinstance(cats, dict) else {}
    brands = brands if isinstance(brands, dict) else {}
    on = [AUTO_CAT_LABELS[k] for k in AUTO_CATEGORIES if cats.get(k) == "YES"]
    on += [AUTO_BRAND_LABELS[k] for k in AUTO_BRANDS if brands.get(k) == "YES"]
    return "; ".join(on) if on else None


async def autotargeting_by_group(
    client, login: str, adgroup_ids: list[int]
) -> dict[int, str | None]:
    """AdGroupId -> сводка настроек автотаргетинга (один Keywords.get)."""
    from directai_mcp.catalog.common import chunk as _chunk

    out: dict[int, str | None] = {}
    ids = sorted(set(adgroup_ids))
    for part in _chunk(ids, 1000):
        items = await client.get_all(
            "keywords",
            {
                "SelectionCriteria": {"AdGroupIds": part},
                "FieldNames": ["Id", "AdGroupId", "Keyword"],
                "AutotargetingSettingsCategoriesFieldNames": list(AUTO_CATEGORIES),
                "AutotargetingSettingsBrandOptionsFieldNames": list(AUTO_BRANDS),
            },
            login,
            "Keywords",
        )
        for item in items:
            if not isinstance(item, dict) or item.get("AdGroupId") is None:
                continue
            if item.get("Keyword") != AUTO_KEYWORD:
                continue
            try:
                gid = int(item["AdGroupId"])
            except (TypeError, ValueError):
                continue
            out[gid] = autotargeting_summary(item.get("AutotargetingSettings"))
    for gid in ids:
        out.setdefault(gid, None)
    return out


class KeywordsListParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)
    keyword_ids: list[int] = Field(default_factory=list)
    show_negatives: bool = False


@action(
    "keywords_list",
    "read",
    "Фразы группы или кампании: ставки, статусы",
    ("фразы", "keywords", "ключи", "фраза", "keyword"),
    KeywordsListParams,
)
async def _list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, KeywordsListParams)
    if not params.campaign_ids and not params.adgroup_ids and not params.keyword_ids:
        return "Ошибка: укажите campaign_ids, adgroup_ids или keyword_ids."
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""

    # v1.1.20: настройки автотаргетинга (только у ---autotargeting).
    auto_extra = {
        "AutotargetingSettingsCategoriesFieldNames": list(AUTO_CATEGORIES),
        "AutotargetingSettingsBrandOptionsFieldNames": list(AUTO_BRANDS),
    }

    async def fetch(entry: AccountEntry, client):
        items: list[dict] = []
        for ids in chunk(params.campaign_ids, 10):
            items.extend(
                await client.get_all(
                    "keywords",
                    dict(
                        {"SelectionCriteria": {"CampaignIds": ids},
                         "FieldNames": FIELDS},
                        **auto_extra,
                    ),
                    entry.login,
                    "Keywords",
                )
            )
        if params.adgroup_ids:
            items.extend(
                await client.get_all(
                    "keywords",
                    dict(
                        {
                            "SelectionCriteria": {"AdGroupIds": params.adgroup_ids},
                            "FieldNames": FIELDS,
                        },
                        **auto_extra,
                    ),
                    entry.login,
                    "Keywords",
                )
            )
        if params.keyword_ids:
            items.extend(
                await client.get_all(
                    "keywords",
                    dict(
                        {
                            "SelectionCriteria": {"Ids": params.keyword_ids},
                            "FieldNames": FIELDS,
                        },
                        **auto_extra,
                    ),
                    entry.login,
                    "Keywords",
                )
            )
        return items

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = [
        "Id",
        "AdGroupId",
        "Keyword",
        "State",
        "Status",
        "ServingStatus",
        "Bid",
        "ContextBid",
        "Autotargeting",
    ]
    rows: list[dict] = []
    errors: list[str] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, list)
        for item in payload:
            rows.append(
                {
                    "_account": entry.login,
                    "Id": item.get("Id"),
                    "AdGroupId": item.get("AdGroupId"),
                    "Keyword": clean_phrase(item.get("Keyword"), params.show_negatives),
                    "State": item.get("State"),
                    "Status": item.get("Status"),
                    "ServingStatus": item.get("ServingStatus"),
                    "Bid": money(micros_to_rubles(item.get("Bid"))),
                    "ContextBid": money(micros_to_rubles(item.get("ContextBid"))),
                    # v1.1.20: настройки — только у ---autotargeting.
                    "Autotargeting": (
                        autotargeting_summary(item.get("AutotargetingSettings"))
                        if item.get("Keyword") == AUTO_KEYWORD
                        else None
                    ),
                }
            )
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = f"{mark}keywords_list: {', '.join(e.login for e in entries)}."
    return finalize(
        ctx,
        context,
        "keywords_list",
        display,
        rows,
        params.limit,
        params.save_as,
        errors,
        money_cols=(),
        output=params.output,
        format=params.format,
        account=params.account,
    )


class KeywordsStateParams(GetActionParams):
    keyword_ids: list[int] = Field(min_length=1)
    operation: Literal["suspend", "resume"]


EXPECTED_KEYWORD_STATE = {"suspend": "SUSPENDED", "resume": "ON"}
_KEYWORD_RESULT_KEY = {"suspend": "SuspendResults", "resume": "ResumeResults"}


async def _prepare_keyword_state(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, KeywordsStateParams)
    client = ctx.direct()
    try:
        items = await client.get_all(
            "keywords",
            {
                "SelectionCriteria": {"Ids": params.keyword_ids},
                "FieldNames": ["Id", "Keyword", "State"],
            },
            entry.login,
            "Keywords",
        )
    finally:
        await client.aclose()
    found = {int(i["Id"]): i for i in items if i.get("Id") is not None}
    missing = [k for k in params.keyword_ids if k not in found]
    if missing:
        raise ValueError(f"фразы не найдены: {missing}.")
    expected = EXPECTED_KEYWORD_STATE[params.operation]
    before = {kid: found[kid].get("State") for kid in params.keyword_ids}
    lines = [
        f"{found[kid].get('Keyword')} ({kid}): {before[kid]} → {expected}"
        for kid in params.keyword_ids
    ]
    return {
        "before": before,
        "requests": [
            (
                "keywords",
                params.operation,
                {"SelectionCriteria": {"Ids": params.keyword_ids}},
            )
        ],
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in lines),
        "warnings": [],
    }


async def _apply_keyword_state(ctx: Ctx, entry: AccountEntry, plan) -> dict:
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
            return {
                "status": "failed",
                "lines": [f"Ошибка API: {e.human_message()}"],
                "response": {"error": e.human_message()},
            }
        items = result.get(_KEYWORD_RESULT_KEY[params["operation"]], [])
        lines: list[str] = []
        ok = 0
        for kid, res in zip(params["keyword_ids"], items):
            errors = res.get("Errors") or []
            if errors:
                lines.append(
                    f"{kid}: ОШИБКА "
                    + "; ".join(f"{e.get('Code')}: {e.get('Message')}" for e in errors)
                )
            else:
                ok += 1
                lines.append(f"{kid}: OK")
        total = len(params["keyword_ids"])
        status = "applied" if ok == total else "failed" if ok == 0 else "partial"
        return {"status": status, "lines": lines, "response": result}
    finally:
        await client.aclose()


async def _verify_keyword_state(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    expected = EXPECTED_KEYWORD_STATE[params["operation"]]
    client = ctx.direct()
    try:
        items = await client.get_all(
            "keywords",
            {
                "SelectionCriteria": {"Ids": params["keyword_ids"]},
                "FieldNames": ["Id", "State"],
            },
            entry.login,
            "Keywords",
        )
    finally:
        await client.aclose()
    after = {int(i["Id"]): i.get("State") for i in items if i.get("Id") is not None}
    bad = [
        f"{kid}: {after.get(kid)} != {expected}"
        for kid in params["keyword_ids"]
        if after.get(kid) != expected
    ]
    if bad:
        return {
            "after": after,
            "ok": False,
            "note": "read-back НЕ подтвердил: " + "; ".join(bad),
        }
    return {"after": after, "ok": True, "note": "подтверждено read-back."}


write_action(
    "keywords_state",
    "Остановка/возобновление фраз (минимум шага 4)",
    ("остановить фразу", "keywords", "suspend", "resume", "состояние фразы"),
    KeywordsStateParams,
    prepare=_prepare_keyword_state,
    apply=_apply_keyword_state,
    verify=_verify_keyword_state,
)


class KeywordAddItem(BaseModel):
    text: str
    bid: float | None = None
    context_bid: float | None = None


class KeywordsAddParams(GetActionParams):
    adgroup_id: int
    keywords: list[KeywordAddItem] = Field(min_length=1)


# v1.1.35: лимиты фраз — keywords/add
# (https://yandex.ru/dev/direct/doc/ru/keywords/add):
# не более 7 слов без учёта стоп-слов и минус-слов, слово ≤35 символов
# без учёта минуса, длина ≤4096 символов. Стоп-слова не исключаем
# (консервативно считает все слова) — API финальный арбитр.
MAX_KW_WORDS = 7
MAX_KW_WORD_LEN = 35
MAX_KW_LEN = 4096
# Мин. ставка 0.30 ₽ — справка/тарифы (тест 27). Максимум API
# не публикует (ограничения — в справочнике валют Dictionaries.Currencies,
# наш dictionaries_get его не отдаёт) — арбитр максимума сам API.
MIN_KW_BID = 0.30


def _kw_content_words(text: str) -> list[str]:
    """Слова фразы без операторов (минус-слова не считаются, по докам)."""
    words = []
    for token in text.split():
        word = token.lstrip("-!+\"'").rstrip("\"'")
        word = word.strip("()")
        if word:
            words.append(word)
    return words


def _validate_kw_text(text: str) -> None:
    norm = " ".join(str(text or "").split())
    if not norm:
        raise ValueError("пустая ключевая фраза.")
    if norm == "---autotargeting":
        return
    if len(norm) > MAX_KW_LEN:
        raise ValueError(
            f"фраза «{norm[:40]}…»: длина {len(norm)} > {MAX_KW_LEN}.")
    words = _kw_content_words(norm)
    if len(words) > MAX_KW_WORDS:
        raise ValueError(
            f"фраза «{norm}»: {len(words)} слов (лимит {MAX_KW_WORDS}).")
    for word in words:
        if len(word) > MAX_KW_WORD_LEN:
            raise ValueError(
                f"фраза «{norm}»: слово «{word[:20]}…» длиннее "
                f"{MAX_KW_WORD_LEN}.")


def _kw_blocked_by_minus(phrase_words: set[str], minus: str) -> str | None:
    """Эвристика: слова минуса целиком внутри слов фразы → показ заблокирован."""
    tokens = [t.lstrip("-!+\"'").rstrip("\"'").strip("()").casefold()
              for t in str(minus or "").split()]
    minus_words = {t for t in tokens if t}
    if minus_words and minus_words <= phrase_words:
        return str(minus)
    return None


def _to_micros(value: float | None) -> int | None:
    if value is None:
        return None
    return round(value * 1_000_000)


async def _prepare_keywords_add(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    from directai_mcp.catalog.negatives import _items, _norm_negative

    assert isinstance(params, KeywordsAddParams)
    texts = [k.text for k in params.keywords]
    if len(texts) > 1000:
        raise ValueError("не более 1000 фраз за один план.")
    # v1.1.35: валидация фраз до API (лимиты — keywords/add).
    for text in texts:
        _validate_kw_text(text)
    warnings: list[str] = []
    client = ctx.direct()
    try:
        groups = await client.get_all(
            "adgroups",
            {"SelectionCriteria": {"Ids": [params.adgroup_id]},
             "FieldNames": ["Id", "CampaignId", "NegativeKeywords"]},
            entry.login,
            "AdGroups",
        )
        if not groups or groups[0].get("CampaignId") is None:
            raise ValueError(f"группа {params.adgroup_id} не найдена.")
        campaign_id = int(groups[0]["CampaignId"])
        group_neg = [str(p) for p in _items(groups[0].get("NegativeKeywords"))]
        camps = await client.get_all(
            "campaigns",
            {"SelectionCriteria": {"Ids": [campaign_id]},
             "FieldNames": ["Id", "NegativeKeywords"],
             "TextCampaignFieldNames": ["BiddingStrategy"],
             "UnifiedCampaignFieldNames": ["BiddingStrategy"]},
            entry.login,
            "Campaigns",
        )
        camp = camps[0] if camps else {}
        existing = await client.get_all(
            "keywords",
            {"SelectionCriteria": {"AdGroupIds": [params.adgroup_id]},
             "FieldNames": ["Id", "Keyword"]},
            entry.login,
            "Keywords",
        )
    finally:
        await client.aclose()
    # v1.1.35: ставка только по ручной стратегии (keywords/add).
    strategy = {}
    for block in ("TextCampaign", "UnifiedCampaign"):
        strategy.update((camp.get(block) or {}).get("BiddingStrategy") or {})
    search_type = (strategy.get("Search") or {}).get("BiddingStrategyType")
    network_type = (strategy.get("Network") or {}).get("BiddingStrategyType")
    for k in params.keywords:
        if k.bid is not None:
            if k.bid < MIN_KW_BID:
                raise ValueError(
                    f"фраза «{k.text}»: ставка {k.bid} ₽ "
                    f"ниже минимума {MIN_KW_BID:.2f} ₽.")
            if search_type != "HIGHEST_POSITION":
                raise ValueError(
                    f"фраза «{k.text}»: Bid только для ручной стратегии "
                    f"(поиск: {search_type}).")
        if k.context_bid is not None:
            if k.context_bid < MIN_KW_BID:
                raise ValueError(
                    f"фраза «{k.text}»: ставка сети {k.context_bid} ₽ "
                    f"ниже минимума {MIN_KW_BID:.2f} ₽.")
            if network_type not in ("MAXIMUM_COVERAGE", "MANUAL_CPM"):
                raise ValueError(
                    f"фраза «{k.text}»: ContextBid только при независимом "
                    f"управлении ставками в сетях (сети: {network_type}).")
    # v1.1.35: дедуп — внутри пачки и с существующими фразами группы.
    have = {_norm_negative(i.get("Keyword")) for i in existing
            if isinstance(i, dict)} - {""}
    seen: set[str] = set()
    dupes: list[str] = []
    fresh: list = []
    for k in params.keywords:
        key = _norm_negative(k.text)
        if key in have or key in seen:
            dupes.append(k.text)
        else:
            seen.add(key)
            fresh.append(k)
    if dupes:
        warnings.append(
            "дубли пропущены (API их не сохраняет): " + ", ".join(dupes) + ".")
    if not fresh:
        raise ValueError("нечего добавлять: все фразы — дубли существующих.")
    # v1.1.35: пересечение с минусами кампании/группы — предупреждение.
    camp_neg = [str(p) for p in _items(camp.get("NegativeKeywords"))]
    for k in fresh:
        words = {w.casefold() for w in _kw_content_words(k.text)}
        for minus in camp_neg + group_neg:
            hit = _kw_blocked_by_minus(words, minus)
            if hit:
                warnings.append(
                    f"«{k.text}»: минус-фраза «{hit}» заблокирует показ "
                    "(эвристика: все слова минуса внутри фразы).")
                break
    if any(t.strip() == "---autotargeting" for t in texts):
        # Docs keywords/add: all targeting categories enabled by default.
        warnings.append(
            "Автотаргетинг создаётся со всеми категориями "
            "(точные, широкие, сопутствующие, альтернативные): "
            "отключите ненужные после создания."
        )
    preview_lines = [
        f"{t} (ставка {k.bid} ₽)" if k.bid else t
        for t, k in zip([k.text for k in fresh], fresh)
    ]
    if len(preview_lines) > 10:
        preview_lines = preview_lines[:10] + [f"… и ещё {len(fresh) - 10}"]
    return {
        "before": None,
        "requests": [
            (
                "keywords",
                "add",
                {
                    "Keywords": [
                        {
                            "Keyword": k.text,
                            "AdGroupId": params.adgroup_id,
                            **({"Bid": _to_micros(k.bid)} if k.bid is not None else {}),
                            **(
                                {"ContextBid": _to_micros(k.context_bid)}
                                if k.context_bid is not None
                                else {}
                            ),
                        }
                        for k in fresh
                    ]
                },
            )
        ],
        "preview": f"Будет добавлено фраз: {len(fresh)}:\n"
        + "\n".join(f"- {line}" for line in preview_lines),
        "warnings": warnings,
    }


async def _apply_keywords_add(ctx: Ctx, entry: AccountEntry, plan) -> dict:
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
        items = result.get("AddResults", [])
        labels = [k["Keyword"] for k in body["Keywords"]]
        lines, ok = summarize(labels, items)
        total = len(labels)
        status = "applied" if ok == total else "failed" if ok == 0 else "partial"
        return {"status": status, "lines": lines, "response": result}
    finally:
        await client.aclose()


async def _verify_keywords_add(ctx: Ctx, entry: AccountEntry, plan) -> dict:
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
            "note": "read-back: ни одна фраза не создана.",
        }
    client = ctx.direct()
    try:
        found = await client.get_all(
            "keywords",
            {"SelectionCriteria": {"Ids": created}, "FieldNames": ["Id", "Keyword"]},
            entry.login,
            "Keywords",
        )
    finally:
        await client.aclose()
    have = {int(i["Id"]) for i in found if i.get("Id") is not None}
    missing = [c for c in created if c not in have]
    after = {int(i["Id"]): i.get("Keyword") for i in found if i.get("Id") is not None}
    if missing:
        return {
            "after": after,
            "ok": False,
            "note": "read-back НЕ подтвердил id: " + ", ".join(map(str, missing)),
        }
    return {
        "after": after,
        "ok": True,
        "note": f"подтверждено read-back: создано {len(created)}.",
    }


write_action(
    "keywords_add",
    "Пакетное добавление фраз",
    ("добавить фразы", "keywords", "add", "новые ключи", "фраза"),
    KeywordsAddParams,
    prepare=_prepare_keywords_add,
    apply=_apply_keywords_add,
    verify=_verify_keywords_add,
)


class KeywordUpdateItem(BaseModel):
    id: int
    keyword: str | None = None


class KeywordsUpdateParams(GetActionParams):
    items: list[KeywordUpdateItem] = Field(min_length=1)


async def _prepare_keywords_update(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, KeywordsUpdateParams)
    ids = [i.id for i in params.items]
    client = ctx.direct()
    try:
        found = await client.get_all(
            "keywords",
            {"SelectionCriteria": {"Ids": ids}, "FieldNames": ["Id", "Keyword"]},
            entry.login,
            "Keywords",
        )
    finally:
        await client.aclose()
    current = {int(i["Id"]): i.get("Keyword") for i in found if i.get("Id") is not None}
    missing = [i for i in ids if i not in current]
    if missing:
        raise ValueError(f"фразы не найдены: {missing}.")
    lines = []
    for item in params.items:
        if item.keyword is None:
            raise ValueError(f"фраза {item.id}: нечего менять (keyword пуст).")
        lines.append(f"{current[item.id]} ({item.id}) → {item.keyword}")
    return {
        "before": current,
        "requests": [
            (
                "keywords",
                "update",
                {
                    "Keywords": [
                        {"Id": i.id, "Keyword": i.keyword} for i in params.items
                    ]
                },
            )
        ],
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in lines),
        "warnings": [],
    }


async def _apply_keywords_update(ctx: Ctx, entry: AccountEntry, plan) -> dict:
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
        items = result.get("UpdateResults", [])
        labels = [str(k["Id"]) for k in body["Keywords"]]
        lines, ok = summarize(labels, items)
        total = len(labels)
        status = "applied" if ok == total else "failed" if ok == 0 else "partial"
        return {"status": status, "lines": lines, "response": result}
    finally:
        await client.aclose()


async def _verify_keywords_update(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    assert isinstance(params, dict)
    requested = [(int(k["Id"]), k["Keyword"]) for k in plan.requests[0][2]["Keywords"]]
    last = getattr(plan, "last_response", None) or {}
    updated = (last.get("response") or {}).get("UpdateResults", [])
    check_ids = [
        r.get("Id") for _, r in zip(requested, updated) if r.get("Id") is not None
    ] or [kid for kid, _ in requested]
    wanted = dict(zip(check_ids, [text for _, text in requested]))
    client = ctx.direct()
    try:
        found = await client.get_all(
            "keywords",
            {"SelectionCriteria": {"Ids": check_ids}, "FieldNames": ["Id", "Keyword"]},
            entry.login,
            "Keywords",
        )
    finally:
        await client.aclose()
    current = {int(i["Id"]): i.get("Keyword") for i in found if i.get("Id") is not None}
    bad = [
        f"{kid}: {current.get(kid)!r} != {text!r}"
        for kid, text in wanted.items()
        if current.get(kid) != text
    ]
    if bad:
        return {
            "after": current,
            "ok": False,
            "note": "read-back НЕ подтвердил: " + "; ".join(bad),
        }
    return {"after": current, "ok": True, "note": "подтверждено read-back."}


write_action(
    "keywords_update",
    "Изменение текстов фраз",
    ("изменить фразу", "keywords", "update", "текст фразы"),
    KeywordsUpdateParams,
    prepare=_prepare_keywords_update,
    apply=_apply_keywords_update,
    verify=_verify_keywords_update,
)
