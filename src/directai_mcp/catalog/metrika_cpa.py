"""Metrika + Direct: цена заявки по кампаниям (v1.12.1, Часть 2).

Только ЧТЕНИЕ. Расход/клики — Reports API Директа
(CAMPAIGN_PERFORMANCE_REPORT); визиты/отказы/цели — Reporting API Метрики
(группировка ym:s:lastDirectClickOrder, в `id` — ID кампании Директа).
Сопоставление строк — по ID кампании; несопоставленные — отдельным блоком.
Расхождение кликов и визитов >30% — флаг «проверить разметку/счётчик».
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field, field_validator

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import GetActionParams, finalize
from directai_mcp.catalog.metrika_goals import MetrikaError, campaign_counters
from directai_mcp.catalog.metrika_reports import (
    DATE_RE,
    default_period,
    human_metrika_error,
    resolve_goals,
    stat_table,
    truncation_note,
)
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import ConfigError

DIVERGENCE_PCT = 30


class MetrikaDirectCpaParams(GetActionParams):
    campaign_ids: list[int] = Field(default_factory=list)
    counter_id: int | None = Field(default=None)
    date_from: str = ""
    date_to: str = ""
    goal_ids: list[str] = Field(default_factory=list)
    attribution: str = Field(default="lastsign")

    @field_validator("date_from", "date_to")
    @classmethod
    def _date_fmt(cls, value: str) -> str:
        if value and not DATE_RE.fullmatch(value):
            raise ValueError("dates must be YYYY-MM-DD")
        return value


async def _direct_campaigns(
    ctx: Ctx, login: str, campaign_ids: list[int], date_from: str, date_to: str
) -> tuple[dict[str, dict], list[str]]:
    """{campaign_id: {name, clicks, cost}}. Ошибки — списком."""
    client = ctx.reports()
    problems: list[str] = []
    rows: dict[str, dict] = {}
    try:
        filt: list[dict] = []
        if campaign_ids:
            filt = [{
                "Field": "CampaignId",
                "Operator": "IN",
                "Values": [str(c) for c in campaign_ids],
            }]
        definition = {
            "SelectionCriteria": {
                "Filter": filt,
                "DateFrom": date_from,
                "DateTo": date_to,
            },
            "FieldNames": ["CampaignId", "CampaignName", "Clicks", "Cost"],
            "ReportName": "metrika_direct_cpa",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
        }
        try:
            _, data = await client.fetch(login, definition)
        except DirectError as e:
            if e.code in (1000, 506, 1020):
                await asyncio.sleep(5)
                try:
                    _, data = await client.fetch(login, definition)
                except DirectError as retry_e:
                    return {}, [f"Директ: {retry_e.human_message()}"]
            else:
                return {}, [f"Директ: {e.human_message()}"]
        for row in data:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("CampaignId") or "")
            if not cid:
                continue
            try:
                clicks = int(float(str(row.get("Clicks") or 0)))
            except (TypeError, ValueError):
                clicks = 0
            rows[cid] = {
                "name": str(row.get("CampaignName") or "—"),
                "clicks": clicks,
                "cost": str(row.get("Cost") or 0),
            }
    finally:
        await client.aclose()
    return rows, problems


async def _login_campaigns(
    ctx: Ctx, login: str
) -> tuple[dict[str, str] | None, list[str]]:
    """Все кампании логина {id: name} (для классификации несопоставленных).

    None + problems — при ошибке API (классификация грубая, как раньше).
    """
    client = ctx.direct()
    try:
        try:
            items = await client.get_all(
                "campaigns",
                {
                    "SelectionCriteria": {},
                    "FieldNames": ["Id", "Name"],
                },
                login,
                "Campaigns",
                "v501",
            )
        except DirectError as e:
            return None, [f"⚠ {login}: список кампаний: {e.human_message()}"]
        out = {}
        for item in items:
            if isinstance(item, dict) and item.get("Id") is not None:
                out[str(item["Id"])] = str(item.get("Name") or "—")
        return out, []
    finally:
        await client.aclose()


def _num(value: object) -> float:
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


@action(
    "metrika_direct_cpa",
    "read",
    "Цена заявки по кампаниям: расход Директа + визиты/цели Метрики, CPA",
    (
        "метрика",
        "metrika",
        "cpa",
        "цена заявки",
        "стоимость заявки",
        "клики визиты",
        "связка директ",
    ),
    MetrikaDirectCpaParams,
)
async def _cpa(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, MetrikaDirectCpaParams)
    try:
        entries = ctx.accounts(params.account)
    except ConfigError as e:
        return f"Ошибка: {e}"
    if len(entries) != 1:
        return (
            "Ошибка: metrika_direct_cpa работает ровно с одним кабинетом "
            f"(account={params.account} дал {len(entries)}): укажите алиас/логин."
        )
    entry = entries[0]
    if params.date_from and params.date_to:
        if params.date_from > params.date_to:
            return "Ошибка: date_from позже date_to."
        date_from, date_to = params.date_from, params.date_to
    elif not params.date_from and not params.date_to:
        date_from, date_to = default_period()
    else:
        return "Ошибка: укажите date_from и date_to вместе (YYYY-MM-DD)."
    errors: list[str] = []
    direct_rows, direct_problems = await _direct_campaigns(
        ctx, entry.login, params.campaign_ids, date_from, date_to
    )
    errors.extend(direct_problems)
    if not direct_rows and not errors:
        errors.append("В Директе строк нет за период (расхода/кликов нет).")
    # Счётчики: явный counter_id > счётчики кампаний > [metrika] counter_id.
    counter_ids: list[int] = []
    if params.counter_id:
        counter_ids = [params.counter_id]
    else:
        probe_ids = params.campaign_ids or [int(c) for c in direct_rows if c.isdigit()]
        if probe_ids:
            counters, counter_problems = await campaign_counters(
                ctx, [entry], probe_ids
            )
            errors.extend(counter_problems)
            found: list[int] = []
            for lst in counters.values():
                found.extend(lst)
            counter_ids = sorted(set(found))
        if not counter_ids and ctx.settings.counter_id:
            counter_ids = [ctx.settings.counter_id]
    if not counter_ids:
        return "\n".join(errors + [("Ошибка: счётчики не найдены — укажите "
                                       "counter_id или привяжите счётчик "
                                       "к кампаниям (CounterIds).")])
    goal_ids, goal_names, goal_note = await resolve_goals(
        ctx, counter_ids[0], params.goal_ids, params.account,
        params.campaign_ids[0] if len(params.campaign_ids) == 1 else None,
    )
    # Несколько счётчиков — цели резолвятся на каждый отдельно
    # (у счётчиков свои наборы целей; чужие goal_ids дали бы 400).
    # Явные params.goal_ids применяются ко всем как есть.
    goals_by_counter: dict[int, list[str]] = {}
    names_by_counter: dict[int, dict[str, str]] = {}
    goal_notes: list[str] = []
    if len(counter_ids) > 1 and not params.goal_ids:
        from directai_mcp.catalog.metrika_goals import counter_goal_types

        for cid in counter_ids:
            gids, gnames, _ = await resolve_goals(
                ctx, cid, [], params.account,
                params.campaign_ids[0] if len(params.campaign_ids) == 1 else None,
            )
            try:
                own = set(await counter_goal_types(ctx.token, cid))
            except MetrikaError:
                own = set()
            if len(gids) == 1 and own and gids[0] not in own:
                # Основной цели на этом счётчике нет — берём все его цели.
                gids = sorted(
                    own, key=lambda x: (0, int(x)) if x.isdigit() else (1, x)
                )
                goal_notes.append(
                    f"счётчик {cid}: основной цели {goal_ids[0] if goal_ids else '?'} "
                    f"нет — взяты все {len(gids)} цели счётчика"
                )
            goals_by_counter[cid] = gids
            names_by_counter[cid] = gnames
        goal_names = {
            gid: name
            for names in names_by_counter.values()
            for gid, name in names.items()
        }
        goal_ids = sorted(
            {g for gids in goals_by_counter.values() for g in gids},
            key=lambda x: (0, int(x)) if x.isdigit() else (1, x),
        )
    metrics = ["ym:s:visits", "ym:s:bounceRate"] + [
        f"ym:s:goal{g}reaches" for g in goal_ids
    ]
    # Несколько счётчиков — поштучные запросы с суммированием (_fetch_multi).
    try:
        if len(counter_ids) > 1:
            payload, multi_problems = await _fetch_multi(
                ctx, counter_ids, goals_by_counter or None, date_from,
                date_to, params.attribution,
            )
            errors.extend(multi_problems)
        else:
            payload = await stat_table(
                ctx.token, counter_ids[0], date_from, date_to,
                ["ym:s:lastDirectClickOrder"], metrics,
                params.attribution, None,
            )
    except MetrikaError as e:
        return human_metrika_error(counter_ids[0], e)
    if note := truncation_note(payload):
        errors.append(note)
    sampled = bool(payload.get("sampled"))
    # Метрика: order_id -> {name, visits, bounce, goals}.
    metrika: dict[str, dict] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        dims = item.get("dimensions") or []
        dim = dims[0] if dims else {}
        oid = str(dim.get("id") if isinstance(dim, dict) else dim or "")
        vals = item.get("metrics") or []
        visits = _num(vals[0]) if len(vals) > 0 else 0.0
        bounce = _num(vals[1]) if len(vals) > 1 else 0.0
        goals = sum(_num(v) for v in vals[2:])
        name = dim.get("name") if isinstance(dim, dict) else None
        metrika[oid] = {
            "name": str(name or "—"),
            "visits": visits,
            "bounce": bounce,
            "goals": goals,
        }
    foreign = metrika.pop("other", None)
    login_map, login_problems = await _login_campaigns(ctx, entry.login)
    errors.extend(login_problems)
    rows: list[dict] = []
    raw: list[dict] = []
    unmatched_direct: list[str] = []

    def _take_by_name(name: str) -> tuple[dict | None, str]:
        """Запасной ключ 2: точное совпадение имени (ID не совпал).

        Только при единственном кандидате; иначе — несопоставлено.
        """
        hits = [k for k, v in metrika.items() if v["name"] == name]
        if len(hits) == 1:
            return metrika.pop(hits[0]), "имя"
        return None, ""

    for cid, direct in sorted(
        direct_rows.items(), key=lambda kv: _num(kv[1]["cost"]), reverse=True
    ):
        meta = metrika.pop(cid, None)
        match = "ID" if meta else ""
        if meta is None and cid.isdigit():
            # Запасной ключ 1: у старых кампаний OrderID = CampaignId + 1e8.
            meta = metrika.pop(str(int(cid) + 100_000_000), None)
            match = "ID+сдвиг" if meta else ""
        if meta is None:
            meta, match = _take_by_name(direct["name"])
        visits = meta["visits"] if meta else 0.0
        bounce = meta["bounce"] if meta else 0.0
        goals = meta["goals"] if meta else 0.0
        clicks = direct["clicks"]
        cost = _num(direct["cost"])
        conv = (visits / clicks * 100) if clicks else None
        flag = ""
        if clicks and abs(visits - clicks) / clicks * 100 > DIVERGENCE_PCT:
            flag = "проверить разметку/счётчик"
        cr = round(goals / visits * 100, 2) if visits else 0.0
        cpa = round(cost / goals, 2) if goals else None
        if meta is None:
            unmatched_direct.append(f"{direct['name']} ({cid})")
        row = {
            "Campaign": f"{direct['name']} ({cid})",
            "Cost": round(cost, 2),
            "Clicks": clicks,
            "Visits": int(visits),
            "ClicksToVisits": f"{conv:.1f}%" if conv is not None else "—",
            "BounceRate": round(bounce, 2) if visits else "—",
            "Goals": int(goals),
            "CR": cr,
            "CPA": cpa if cpa is not None else "—",
            "Flag": flag or "—",
            "Связь": match or "—",
        }
        rows.append(row)
        raw.append(dict(row))
    extra_lines: list[str] = []
    if unmatched_direct:
        extra_lines.append(
            "Без визитов в Метрике (есть расход в Директе): "
            + "; ".join(unmatched_direct) + "."
        )
    leftovers = sorted(
        metrika.items(), key=lambda kv: kv[1]["visits"], reverse=True
    )
    if leftovers:
        if login_map is None:
            extra_lines.append(
                "Визиты без сопоставления "
                f"(расхода в {entry.login} нет): "
                + "; ".join(
                    f"{v['name']} ({k}): {int(v['visits'])} визитов"
                    for k, v in leftovers
                )
                + "."
            )
        else:
            own = [(k, v) for k, v in leftovers if k in login_map]
            alien = [(k, v) for k, v in leftovers if k not in login_map]
            if own:
                extra_lines.append(
                    f"Кампании {entry.login} без расхода за период: "
                    + "; ".join(
                        f"{v['name']} ({k}): {int(v['visits'])} визитов"
                        for k, v in own
                    )
                    + "."
                )
            if alien:
                extra_lines.append(
                    "Визиты кампаний других логинов: "
                    + "; ".join(
                        f"{v['name']} ({k}): {int(v['visits'])} визитов"
                        for k, v in alien
                    )
                    + "."
                )
    if foreign:
        extra_lines.append(
            f"Чужие кампании (клики других логинов): "
            f"{int(foreign['visits'])} визитов."
        )
    if not rows and not errors:
        errors.append("Строк нет за период (пустой ответ API).")
    head = (
        f"metrika_direct_cpa: {entry.login}, {date_from}–{date_to}, "
        f"счётчик {', '.join(map(str, counter_ids))}, "
        f"атрибуция {params.attribution}."
    )
    if goal_note:
        head += f" {goal_note}."
    def _goal_label(gid: str) -> str:
        name = goal_names.get(gid, "")
        return f"{name} ({gid})" if name else gid

    if goal_notes:
        head += " " + " ".join(goal_notes) + "."
    if goals_by_counter:
        parts = []
        for cid in counter_ids:
            gids = goals_by_counter.get(cid, [])
            if len(gids) == 1:
                parts.append(f"{cid}: {_goal_label(gids[0])}")
            else:
                parts.append(f"{cid}: все {len(gids)} цели")
        head += " Цели по счётчикам (сумма): " + "; ".join(parts) + "."
    elif goal_ids:
        head += " Цели (" + ", ".join(_goal_label(g) for g in goal_ids) + ", сумма)."
    if sampled:
        head += " Выборка: данные семплированы (accuracy=full не хватило)."
    context = head
    columns = (
        ["Campaign", "Cost", "Clicks", "Visits", "ClicksToVisits",
         "BounceRate", "Goals", "CR", "CPA", "Flag", "Связь"]
        if rows else []
    )
    out = finalize(
        ctx, context, "metrika_direct_cpa", columns, rows,
        params.limit or 20, params.save_as, errors,
        money_cols=("Cost", "CPA"), with_totals=False,
        output=params.output, format=params.format, account=params.account,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="metrika_direct_cpa",
        dump_params=params.model_dump(),
        dump_raw={"metrika_direct_cpa": raw},
        dump_fields={"Metrika": ["stat/v1/data", "counter/{id}/goals"],
                      "Direct": ["CAMPAIGN_PERFORMANCE_REPORT"]},
        dump_tally={}, dump_logins=[entry.login],
        dump_scope="campaign",
        dump_complete=not errors, dump_truncated=bool(errors),
    )
    if extra_lines:
        out += "\n\n" + "\n".join(extra_lines)
    return out


async def _fetch_multi(
    ctx: Ctx,
    counter_ids: list[int],
    goals_by_counter: dict[int, list[str]] | None,
    date_from: str,
    date_to: str,
    attribution: str,
) -> tuple[dict, list[str]]:
    """Сумма по нескольким счётчикам (поштучные запросы, merge по order id).

    goals_by_counter — свои цели каждого счётчика (None = общие goal_ids
    вызывающего контекста невозможны: caller передаёт всегда). Визиты и цели
    суммируются; отказы — средневзвешенные по визитам. Упавший счётчик —
    в problems, остальные суммируются.
    """
    merged: dict[str, dict] = {}
    order: list[str] = []
    sampled = False
    problems: list[str] = []
    for cid in counter_ids:
        gids = (goals_by_counter or {}).get(cid, [])
        metrics = ["ym:s:visits", "ym:s:bounceRate"] + [
            f"ym:s:goal{g}reaches" for g in gids
        ]
        try:
            payload = await stat_table(
                ctx.token, cid, date_from, date_to,
                ["ym:s:lastDirectClickOrder"], metrics, attribution, None,
            )
        except MetrikaError as e:
            problems.append(f"⚠ счётчик {cid} исключён: {e}")
            continue
        if note := truncation_note(payload):
            problems.append(f"⚠ счётчик {cid}: {note}")
        sampled = sampled or bool(payload.get("sampled"))
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            dims = item.get("dimensions") or []
            dim = dims[0] if dims else {}
            oid = str(dim.get("id") if isinstance(dim, dict) else dim or "")
            slot = merged.get(oid)
            if slot is None:
                slot = {"dim": dim, "visits": 0.0, "bounce_w": 0.0, "goals": 0.0}
                merged[oid] = slot
                order.append(oid)
            vals = item.get("metrics") or []
            visits = _num(vals[0]) if len(vals) > 0 else 0.0
            bounce = _num(vals[1]) if len(vals) > 1 else 0.0
            slot["visits"] += visits
            slot["bounce_w"] += bounce * visits
            slot["goals"] += sum(_num(v) for v in vals[2 : 2 + len(gids)])
    if not merged and problems:
        raise MetrikaError("; ".join(problems))
    data = []
    for key in order:
        slot = merged[key]
        visits = slot["visits"]
        bounce = slot["bounce_w"] / visits if visits else 0.0
        data.append({
            "dimensions": [slot["dim"]],
            "metrics": [visits, bounce, slot["goals"]],
        })
    return {
        "sampled": sampled,
        "data": data,
        "total_rows": len(data),
    }, problems
