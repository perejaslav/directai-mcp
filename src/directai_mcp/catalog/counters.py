"""Read action counter_check: диагностика счётчиков кампании (v1.1.30).

Сверяет кампанию Директа со счётчиками Метрики (read-only):
CounterIds -> инфо/цели счётчиков; PriorityGoals и цель стратегии ->
привязка к счётчикам (флаг чужого); конверсии по целям из Reports
с разбивкой по счётчикам; визиты Stat API против кликов (флаг ниже
порога counter_visits_warn_pct); домены посадочных против сайтов
счётчиков. Без рекомендаций по правке сайта.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.accounts import ensure_cache
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    finalize,
    goal_label,
)
from directai_mcp.catalog.metrika_goals import (
    MetrikaError,
    counter_goal_names,
    counter_goal_types,
    counter_info,
    stat_visits,
)
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import AccountEntry, reports_base_dir
from directai_mcp.fmt import money, num, render_table, save_text, to_decimal

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class CounterCheckParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    counter_ids: list[int] = Field(default_factory=list)
    date_from: str = ""
    date_to: str = ""
    goals_only: bool = Field(
        default=False,
        description=("Только цели и привязки (id, названия, счётчики), "
                     "без статистической части (конверсии/визиты). "
                     "Даты не требуются."),
    )

    @field_validator("date_from", "date_to")
    @classmethod
    def _date(cls, value: str) -> str:
        if not DATE_RE.fullmatch(value or ""):
            raise ValueError("dates must be YYYY-MM-DD")
        return value


def _norm_domain(url: object) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        # Сайт счётчика приходит без схемы (www.example.ru).
        text = "http://" + text
    try:
        host = urlparse(text).hostname or ""
    except ValueError:
        return ""
    host = host.lower().strip().removeprefix("www.")
    if " " in host or "." not in host:
        return ""
    return host


def _strategy_goal_id(strategy: object) -> int | None:
    """Первый int GoalId в дереве BiddingStrategy (цель стратегии)."""
    found: int | None = None

    def _walk(node: object) -> None:
        nonlocal found
        if found is not None:
            return
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "GoalId" and isinstance(val, int):
                    found = val
                    return
                _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(strategy)
    return found


def _fmt_pct(value: object) -> str:
    return num(value) + "%" if value is not None else "—"


@action(
    "counter_check",
    "read",
    "Диагностика счётчиков кампании: привязка целей, визиты vs клики",
    (
        "счётчик",
        "счетчик",
        "counter",
        "метрика",
        "metrika",
        "визиты",
        "привязка",
        "диагностика",
    ),
    CounterCheckParams,
)
async def _check(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, CounterCheckParams)
    if not params.campaign_ids:
        return "Ошибка: укажите campaign_ids."
    if params.date_from > params.date_to:
        return "Ошибка: date_from позже date_to."
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    entries = ctx.accounts(params.account)
    if params.account in ("all", "active"):
        for note in await ensure_cache(ctx, params.account):
            ctx.notes.append(note)
    threshold = ctx.settings.counter_visits_warn_pct
    out_parts: list[str] = []
    multi = len(entries) > 1
    tally: dict = {}
    collect: dict = {"counters": []}
    for entry in entries:
        part = await _check_campaigns(ctx, entry, params, threshold, multi,
                                      collect=collect, tally=tally)
        if part:
            out_parts.append(part)
    head = (f"{mark}counter_check: {params.date_from}–{params.date_to}, "
            f"кампании: {', '.join(str(c) for c in params.campaign_ids)}.")
    if params.goals_only:
        # v1.3.3: только цели — даты и статистика не нужны.
        head = (f"{mark}counter_check (только цели, без статистики), "
                f"кампании: {', '.join(str(c) for c in params.campaign_ids)}.")
    body = head + ("\n\n" + "\n\n".join(out_parts) if out_parts else " Строк нет.")
    output, _format = params.output, params.format
    if params.save_as:
        output = "file"
    if params.dump_dir:
        from directai_mcp.catalog.common import net_summary, write_dump_sections

        net_summary(ctx)
        goals = []
        for counter in collect["counters"]:
            for goal in counter.get("goals", []):
                goals.append({
                    "counter": counter.get("id"),
                    "counter_name": counter.get("name", ""),
                    "goal_id": goal.get("id"),
                    "goal_name": goal.get("name", ""),
                    "goal_type": goal.get("type", ""),
                })
        counters_raw = [dict(c, linked_to_campaign=True)
                        for c in collect["counters"]]
        for goal in goals:
            goal["linked_to_campaign"] = True
        body += "\n\n" + write_dump_sections(
            ctx,
            params.dump_dir,
            "counter_check",
            params.account,
            params.model_dump(),
            {
                "counters": {
                    "columns": ["id", "name", "site", "status", "goals"],
                    "display_rows": [
                        {"id": c.get("id"), "name": c.get("name", ""),
                         "site": c.get("site", ""),
                         "status": c.get("status", ""),
                         "goals": len(c.get("goals", []))}
                        for c in collect["counters"]],
                    "raw_items": counters_raw,
                },
                "goals": {
                    "columns": ["counter", "counter_name", "goal_id",
                                "goal_name", "goal_type"],
                    "display_rows": goals,
                    "raw_items": goals,
                },
            },
            {"Campaigns": ["Id", "Name", "CounterIds", "PriorityGoals",
                            "BiddingStrategy"],
             "Metrika": ["counter/{id}", "counter/{id}/goals"]},
            tally,
            [e.login for e in entries],
            "campaign",
            [],
            False,
            dump_tag=params.dump_tag,
        )
    if output == "file":
        path = save_text(reports_base_dir(ctx.settings), "counter_check",
                         params.account, "# counter_check\n\n" + body)
        return f"Полный результат: {path}.\n\n{body}"
    return body


async def _direct_campaigns(
    ctx: Ctx, entry: AccountEntry, campaign_ids: list[int],
    tally: dict | None = None,
) -> tuple[list[dict], list[str]]:
    """Настройки кампаний: CounterIds, PriorityGoals, BiddingStrategy."""
    client = ctx.direct()
    problems: list[str] = []
    items: list[dict] = []
    try:
        for ids in chunk(list(campaign_ids), 100):
            try:
                items.extend(
                    await client.get_all(
                        "campaigns",
                        {
                            "SelectionCriteria": {"Ids": ids},
                            "FieldNames": ["Id", "Name"],
                            "TextCampaignFieldNames": [
                                "CounterIds", "PriorityGoals", "BiddingStrategy"],
                            "UnifiedCampaignFieldNames": [
                                "CounterIds", "PriorityGoals", "BiddingStrategy"],
                        },
                        entry.login,
                        "Campaigns",
                        "v501",
                        tally=tally,
                    )
                )
            except DirectError as e:
                problems.append(f"⚠ {entry.login}: кампании: {e.human_message()}")
                break
    finally:
        await client.aclose()
    return items, problems


async def _campaign_hrefs(
    ctx: Ctx, entry: AccountEntry, campaign_id: int
) -> tuple[set[str], list[str]]:
    """Домены посадочных объявлений кампании (TextAd/ResponsiveAd Href)."""
    client = ctx.direct()
    domains: set[str] = set()
    problems: list[str] = []
    try:
        try:
            ads = await client.get_all(
                "ads",
                {
                    "SelectionCriteria": {"CampaignIds": [campaign_id]},
                    "FieldNames": ["Id"],
                    "TextAdFieldNames": ["Href"],
                    "ResponsiveAdFieldNames": ["Href"],
                },
                entry.login,
                "Ads",
            )
        except DirectError as e:
            return domains, [f"⚠ {entry.login}: объявления: {e.human_message()}"]
        for ad in ads:
            if not isinstance(ad, dict):
                continue
            for block in ("TextAd", "ResponsiveAd"):
                sub = ad.get(block)
                if isinstance(sub, dict) and sub.get("Href"):
                    dom = _norm_domain(sub["Href"])
                    if dom:
                        domains.add(dom)
    finally:
        await client.aclose()
    return domains, problems


def _campaign_blocks(item: dict) -> tuple[list[int], list[dict], object]:
    """(CounterIds, PriorityGoals items, BiddingStrategy) из Text/Unified."""
    counters: list[int] = []
    goals: list[dict] = []
    strategy: object = None
    for block in ("TextCampaign", "UnifiedCampaign"):
        sub = item.get(block)
        if not isinstance(sub, dict):
            continue
        cids = sub.get("CounterIds")
        seq = cids.get("Items") if isinstance(cids, dict) else cids
        if isinstance(seq, list):
            counters.extend(i for i in seq if isinstance(i, int))
        prio = sub.get("PriorityGoals")
        pseq = prio.get("Items") if isinstance(prio, dict) else prio
        if isinstance(pseq, list):
            goals.extend(g for g in pseq if isinstance(g, dict))
        if strategy is None and sub.get("BiddingStrategy") is not None:
            strategy = sub.get("BiddingStrategy")
    return sorted(set(counters)), goals, strategy


async def _report_conversions(
    ctx: Ctx,
    entry: AccountEntry,
    campaign_id: int,
    goal_ids: list[str],
    date_from: str,
    date_to: str,
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    """Конверсии по целям (чанки ≤10, AUTO) + LC-итог. Возвращает
    ({gid: conv}, {Clicks, Cost, Conversions LC}, ошибки)."""
    client = ctx.reports()
    problems: list[str] = []
    per_goal: dict[str, object] = {}
    base: dict[str, object] = {}
    try:
        sem = asyncio.Semaphore(3)

        async def _one(definition: dict):
            async with sem:
                try:
                    return await client.fetch(entry.login, definition)
                except DirectError as e:
                    # Транзиент (1000/506/1020): один повтор после паузы.
                    if e.code in (1000, 506, 1020):
                        await asyncio.sleep(5)
                        try:
                            return await client.fetch(entry.login, definition)
                        except DirectError as retry_e:
                            return retry_e
                    return e

        defs = []
        for i in range(0, len(goal_ids), 10):
            defs.append({
                "SelectionCriteria": {
                    "Filter": [{"Field": "CampaignId", "Operator": "IN",
                                "Values": [str(campaign_id)]}],
                    "DateFrom": date_from, "DateTo": date_to},
                "FieldNames": ["CampaignId", "Clicks", "Cost"],
                "Goals": goal_ids[i:i + 10], "AttributionModels": ["AUTO"],
                "ReportName": "counter_check", "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
                "DateRangeType": "CUSTOM_DATE", "Format": "TSV",
                "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
            })
        lc_def = {
            "SelectionCriteria": {
                "Filter": [{"Field": "CampaignId", "Operator": "IN",
                            "Values": [str(campaign_id)]}],
                "DateFrom": date_from, "DateTo": date_to},
            "FieldNames": ["CampaignId", "Clicks", "Cost", "Conversions"],
            "ReportName": "counter_check", "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE", "Format": "TSV",
            "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
        }
        results = await asyncio.gather(
            *(_one(d) for d in defs), _one(lc_def))
        *goal_results, lc_result = results
        for payload in goal_results:
            if isinstance(payload, DirectError):
                problems.append(
                    f"⚠ {entry.login}: конверсии: {payload.human_message()}")
                continue
            _, rows = payload
            for row in rows:
                for col, val in row.items():
                    match = re.fullmatch(r"Conversions_(\d+)_AUTO", str(col))
                    if match and val not in (None, "0"):
                        per_goal[match.group(1)] = val
        if isinstance(lc_result, DirectError):
            problems.append(
                f"⚠ {entry.login}: итог: {lc_result.human_message()}")
        else:
            _, rows = lc_result
            if rows:
                base = {k: rows[0].get(k)
                        for k in ("Clicks", "Cost", "Conversions")}
    finally:
        await client.aclose()
    return per_goal, base, problems


async def _check_campaigns(
    ctx: Ctx, entry: AccountEntry, params: CounterCheckParams, threshold: int,
    multi: bool, collect: dict | None = None, tally: dict | None = None,
) -> str:
    items, problems = await _direct_campaigns(
        ctx, entry, params.campaign_ids, tally=tally)
    found_ids = {i.get("Id") for i in items if isinstance(i, dict)}
    missing = [c for c in params.campaign_ids if c not in found_ids]
    for cid in missing:
        problems.append(f"⚠ {entry.login}: кампания {cid} не найдена.")
    blocks: list[str] = list(problems)
    for item in items:
        blocks.append(await _check_one(ctx, entry, item, params, threshold,
                                       collect=collect))
    if multi:
        head = f"## {entry.login}"
        return head + "\n\n" + "\n\n".join(blocks)
    return "\n\n".join(blocks)


async def _check_one(
    ctx: Ctx, entry: AccountEntry, item: dict, params: CounterCheckParams,
    threshold: int, collect: dict | None = None,
) -> str:
    cid = item.get("Id")
    name = item.get("Name") or "—"
    counter_ids, prio_items, strategy = _campaign_blocks(item)
    extra = [c for c in params.counter_ids if c not in counter_ids]
    inspect = counter_ids + extra
    names = ctx.settings.goal_names
    lines: list[str] = [f"### Кампания {cid} («{name}»)"]
    # Счётчики: инфо + цели.
    info_rows: list[dict] = []
    goal_counter: dict[str, int] = {}
    goal_names_all: dict[str, str] = {}
    for counter_id in inspect:
        try:
            info = await counter_info(ctx.token, counter_id)
            gtypes = await counter_goal_types(ctx.token, counter_id)
            gnames = await counter_goal_names(ctx.token, counter_id)
        except MetrikaError as e:
            lines.append(f"⚠ счётчик {counter_id}: {e}")
            continue
        if collect is not None:
            collect.setdefault("counters", []).append(dict(
                info, goals=[
                    {"id": gid, "name": gnames.get(gid, ""),
                     "type": gtypes.get(gid, "")}
                    for gid in gtypes]))
        for gid in gtypes:
            goal_counter.setdefault(gid, counter_id)
        goal_names_all.update(gnames)
        scope = "CounterIds" if counter_id in counter_ids else "дополнительно"
        info_rows.append({
            "Счётчик": counter_id,
            "Название": info.get("name") or "—",
            "Сайт": info.get("site") or "—",
            "Статус": info.get("status") or "—",
            "Код": info.get("code_status") or "—",
            "Целей": len(gtypes),
            "Связь": scope,
        })
    if info_rows:
        lines.append(render_table("Счётчики.", list(info_rows[0]),
                             info_rows, len(info_rows), with_totals=False))
    # Цели кампании -> счётчики.
    goal_rows: list[dict] = []
    flags: list[str] = []
    for g in prio_items:
        gid = g.get("GoalId")
        if gid is None:
            continue
        owner = goal_counter.get(str(gid))
        foreign = owner is not None and owner not in counter_ids
        if owner is None:
            owner_text = "счётчик неизвестен"
        else:
            owner_text = str(owner)
            if foreign:
                flags.append(
                    f"ФЛАГ: цель {gid} — счётчик {owner} вне CounterIds кампании.")
        goal_rows.append({
            "Цель": goal_label(gid, names),
            "Счётчик": owner_text,
            "Чужой": "да" if foreign else ("?" if owner is None else "нет"),
        })
    strat_gid = _strategy_goal_id(strategy)
    if strat_gid is not None:
        owner = goal_counter.get(str(strat_gid))
        foreign = owner is not None and owner not in counter_ids
        if foreign:
            flags.append(
                f"ФЛАГ: цель стратегии {strat_gid} — счётчик {owner} "
                "вне CounterIds кампании.")
        goal_rows.append({
            "Цель": goal_label(strat_gid, names) + " (стратегия)",
            "Счётчик": str(owner) if owner is not None else "счётчик неизвестен",
            "Чужой": "да" if foreign else ("?" if owner is None else "нет"),
        })
    if goal_rows:
        lines.append(render_table("Цели кампании.", list(goal_rows[0]),
                             goal_rows, len(goal_rows), with_totals=False))
    if params.goals_only:
        # v1.3.3: без статистической части (конверсии/визиты) — дат не надо.
        return "\n\n".join(lines)
    # Конверсии по целям.
    all_gids = sorted(set(goal_counter),
                      key=lambda x: (0, int(x)) if x.isdigit() else (1, x))
    per_goal, base, rep_problems = await _report_conversions(
        ctx, entry, cid, all_gids, params.date_from, params.date_to)
    lines.extend(rep_problems)
    clicks = to_decimal(base.get("Clicks")) or 0
    lc_conv = to_decimal(base.get("Conversions")) or 0
    conv_rows: list[dict] = []
    for gid in all_gids:
        val = per_goal.get(gid)
        if val is None:
            continue
        owner = goal_counter.get(gid)
        foreign = owner is not None and owner not in counter_ids
        if foreign:
            flags.append(
                f"ФЛАГ: конверсии ({val}) по цели {gid} — счётчик {owner} "
                "вне CounterIds кампании.")
        label = goal_names_all.get(gid) or names.get(gid, "")
        conv_rows.append({
            "Цель": f"{label} ({gid})" if label else gid,
            "Счётчик": str(owner) if owner is not None else "?",
            "Конверсии": val,
        })
    if conv_rows:
        lines.append(render_table("Конверсии по целям (AUTO).",
                             list(conv_rows[0]), conv_rows, len(conv_rows),
                             with_totals=False))
    else:
        lines.append("Конверсии по целям (AUTO): нет на известных целях.")
    if lc_conv and not conv_rows:
        flags.append(
            f"ФЛАГ: итог LC — {lc_conv} конв., но ни одна известная цель "
            "счётчиков кампании конверсий не дала (цель чужого счётчика).")
    # Визиты vs клики.
    visit_rows: list[dict] = []
    for counter_id in inspect:
        try:
            stat = await stat_visits(ctx.token, counter_id,
                                     params.date_from, params.date_to, cid)
        except MetrikaError as e:
            lines.append(f"⚠ визиты {counter_id}: {e}")
            continue
        matched, ad_total = stat["matched"], stat["ad_total"]
        if stat["matched_empty"]:
            lines.append(
                f"Сигнал: размерность lastDirectClickOrder счётчика "
                f"{counter_id} пустая (счётчик не связан с Директом?).")
        if not stat["matched_empty"]:
            used, how = matched, "по кампании"
        else:
            used, how = ad_total, "весь рекламный трафик (fallback)"
        share = (used / clicks * 100) if clicks else None
        low = share is not None and share < threshold
        if low:
            flags.append(
                f"ФЛАГ: счётчик {counter_id}: визиты {used} ({how}) против "
                f"кликов {int(clicks)} — доля {_fmt_pct(share)} ниже порога "
                f"{threshold}%.")
        visit_rows.append({
            "Счётчик": counter_id,
            "Визиты": f"{used} ({how})",
            "Клики": int(clicks),
            "Доля": _fmt_pct(share),
            "Флаг": "да" if low else "нет",
        })
    if visit_rows:
        lines.append(render_table("Визиты Метрики vs клики Директа.",
                             list(visit_rows[0]), visit_rows, len(visit_rows),
                             with_totals=False))
    # Посадочные vs сайты счётчиков.
    domains, ad_problems = await _campaign_hrefs(ctx, entry, cid)
    lines.extend(ad_problems)
    sites = set()
    for counter_id in counter_ids:
        try:
            site = (await counter_info(ctx.token, counter_id)).get("site") or ""
        except MetrikaError:
            continue
        if site:
            dom = _norm_domain(site)
            if dom:
                sites.add(dom)
    if domains:
        homeless = sorted(d for d in domains if d not in sites)
        lines.append("Домены посадочных: " + ", ".join(sorted(domains)) + ".")
        if homeless and sites:
            flags.append(
                "ФЛАГ: посадочная без счётчика кампании: "
                + ", ".join(homeless) + " (сайты счётчиков: "
                + ", ".join(sorted(sites)) + ").")
    # Вывод.
    verdict = "Вывод: " + ("; ".join(flags) if flags
                           else "флагов нет — счётчики видят трафик кампании.")
    lines.append(verdict)
    cost_val = base.get("Cost")
    if cost_val is not None:
        lines.append(f"Справка: клики {int(clicks)}, расход "
                     f"{money(to_decimal(cost_val) or 0)} ₽, итог LC "
                     f"{lc_conv} конв.")
    return "\n\n".join(lines)


class MetrikaGoalsListParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    counter_ids: list[int] = Field(default_factory=list)


@action(
    "metrika_goals_list",
    "read",
    "Цели счётчиков Метрики: id, название, тип (без статистики)",
    (
        "метрика",
        "metrika",
        "цели счётчика",
        "цели счетчика",
        "goals",
        "имя цели",
        "goal names",
        "тип цели",
        "справочник целей",
    ),
    MetrikaGoalsListParams,
)
async def _goals(ctx: Ctx, params: BaseModel) -> str:
    """v1.3.3: имена целей Метрики (напр. из правил ретаргетинга).

    Только Management API (counter/{id}/goals + info): дат и статистики нет.
    """
    assert isinstance(params, MetrikaGoalsListParams)
    if not params.campaign_ids and not params.counter_ids:
        return "Ошибка: укажите campaign_ids или counter_ids."
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    entries = ctx.accounts(params.account)
    counters: dict[str, list[int]] = {}
    tally: dict = {}
    errors: list[str] = []
    for entry in entries:
        items, problems = await _direct_campaigns(
            ctx, entry, params.campaign_ids, tally=tally)
        errors.extend(problems)
        found: list[int] = []
        for item in items:
            cids, _, _ = _campaign_blocks(item)
            found.extend(cids)
        extra = [c for c in params.counter_ids if c not in found]
        counters[entry.login] = sorted(set(found + extra))
        if not counters[entry.login]:
            errors.append(
                f"⚠ {entry.login}: счётчики не найдены — проверьте "
                "campaign_ids.")
    columns = ["Counter", "CounterName", "GoalId", "GoalName", "GoalType"]
    rows: list[dict] = []
    raw: list[dict] = []
    for entry in entries:
        for counter_id in counters.get(entry.login, []):
            try:
                info = await counter_info(ctx.token, counter_id)
                gtypes = await counter_goal_types(ctx.token, counter_id)
                gnames = await counter_goal_names(ctx.token, counter_id)
            except MetrikaError as e:
                errors.append(f"⚠ счётчик {counter_id}: {e}")
                continue
            raw.append({
                "counter": counter_id,
                "counter_name": info.get("name") or "",
                "site": info.get("site") or "",
                "goals": [
                    {"id": gid, "name": gnames.get(gid, ""),
                     "type": gtypes.get(gid, "")}
                    for gid in sorted(
                        gtypes,
                        key=lambda x: (0, int(x)) if x.isdigit() else (1, x))],
                "linked_to_campaign": True,
            })
            if not gtypes:
                rows.append({
                    "_account": entry.login,
                    "Counter": counter_id,
                    "CounterName": info.get("name") or "—",
                    "GoalId": "—",
                    "GoalName": "нет целей",
                    "GoalType": "—",
                })
                continue
            for gid in sorted(gtypes, key=lambda x: (
                    0, int(x)) if x.isdigit() else (1, x)):
                rows.append({
                    "_account": entry.login,
                    "Counter": counter_id,
                    "CounterName": info.get("name") or "—",
                    "GoalId": gid,
                    "GoalName": gnames.get(gid) or "—",
                    "GoalType": gtypes.get(gid) or "—",
                })
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = (f"{mark}metrika_goals_list: "
               f"{', '.join(e.login for e in entries)}.")
    return finalize(
        ctx, context, "metrika_goals_list", display, rows, params.limit,
        params.save_as, errors, money_cols=(), output=params.output,
        format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="metrika_goals_list",
        dump_params=params.model_dump(),
        dump_raw={"metrika_goals": raw},
        dump_fields={"Metrika": ["counter/{id}", "counter/{id}/goals"],
                     "Campaigns": ["Id", "CounterIds"]},
        dump_tally=tally, dump_logins=[e.login for e in entries],
        dump_scope="campaign",
    )
