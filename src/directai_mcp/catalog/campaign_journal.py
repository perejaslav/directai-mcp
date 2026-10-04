"""Журнал кампании (v1.13.0, BACKLOG п.3).

Один Markdown-файл на кампанию: что меняли, почему и что стало с результатами.
Все три инструмента — только чтение API Директа/Метрики; заметки и снимки
пишут только локально (sqlite + md-файл), без write API Директа и без планов.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import GetActionParams
from directai_mcp.catalog.counters import _check_dates_not_future
from directai_mcp.catalog.metrika_goals import MetrikaError, campaign_counters
from directai_mcp.catalog.metrika_reports import (
    DATE_RE,
    resolve_goals,
    stat_table,
)
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import ConfigError, reports_base_dir, resolve_primary_goal
from directai_mcp.fmt import to_decimal
from directai_mcp.safety import journal as journal_mod

SNAPSHOT_SOURCES = ("direct", "metrika")
NOTE_KINDS = ("hypothesis", "decision", "observation", "todo")


class CampaignJournalParams(GetActionParams):
    campaign_id: int = Field(gt=0)
    days: int = Field(default=90, ge=1, le=365)


class CampaignJournalSnapshotParams(GetActionParams):
    campaign_id: int = Field(gt=0)
    date_from: str = ""
    date_to: str = ""
    sources: list[str] = Field(default_factory=lambda: ["direct", "metrika"])

    @field_validator("date_from", "date_to")
    @classmethod
    def _date_fmt(cls, value: str) -> str:
        if value and not DATE_RE.fullmatch(value):
            raise ValueError("dates must be YYYY-MM-DD")
        return value

    @field_validator("sources")
    @classmethod
    def _sources(cls, value: list[str]) -> list[str]:
        bad = [s for s in value if s not in SNAPSHOT_SOURCES]
        if bad:
            raise ValueError(f"unknown sources {bad}; allowed {list(SNAPSHOT_SOURCES)}")
        if not value:
            raise ValueError("sources пуст: укажите direct и/или metrika.")
        return value


class CampaignJournalNoteParams(GetActionParams):
    campaign_id: int = Field(gt=0)
    kind: Literal["hypothesis", "decision", "observation", "todo"] = "observation"
    text: str = Field(min_length=1, max_length=2000)


def _single_entry(ctx: Ctx, account: str):
    """Ровно один кабинет или текст ошибки (как metrika_direct_cpa)."""
    try:
        entries = ctx.accounts(account)
    except ConfigError as e:
        return None, f"Ошибка: {e}"
    if len(entries) != 1:
        return None, (
            "Ошибка: журнал ведётся ровно по одному кабинету "
            f"(account={account} дал {len(entries)}): укажите алиас/логин."
        )
    return entries[0], ""


def _check_period(date_from: str, date_to: str) -> str | None:
    if not date_from or not date_to:
        return "Ошибка: укажите date_from и date_to вместе (YYYY-MM-DD)."
    if date_from > date_to:
        return "Ошибка: date_from позже date_to."
    return _check_dates_not_future(date_from, date_to)


async def _campaign_header(ctx: Ctx, login: str, campaign_id: int) -> dict:
    """Шапка журнала: имя/тип/стратегия/статус (read-only, best-effort)."""
    header = {
        "name": "—",
        "type": "—",
        "strategy": "—",
        "state": "—",
        "warning": "",
    }
    client = ctx.direct()
    try:
        try:
            items = await client.get_all(
                "campaigns",
                {
                    "SelectionCriteria": {"Ids": [campaign_id]},
                    "FieldNames": ["Id", "Name", "Type", "State"],
                    "TextCampaignFieldNames": ["BiddingStrategy"],
                    "UnifiedCampaignFieldNames": ["BiddingStrategy"],
                },
                login,
                "Campaigns",
                "v501",
            )
        except DirectError as e:
            header["warning"] = f"шапка: {e.human_message()}"
            return header
    finally:
        await client.aclose()
    if not items:
        header["warning"] = "кампания не найдена в кабинете."
        return header
    item = items[0]
    header["name"] = str(item.get("Name") or "—")
    header["type"] = str(item.get("Type") or "—")
    header["state"] = str(item.get("State") or "—")
    strategy = (item.get("UnifiedCampaign") or item.get("TextCampaign") or {}).get(
        "BiddingStrategy"
    ) or {}
    parts = []
    for side in ("Search", "Network"):
        block = strategy.get(side) or {}
        stype = block.get("BiddingStrategyType")
        if stype:
            parts.append(f"{side}={stype}")
    if parts:
        header["strategy"] = ", ".join(parts)
    return header


def _num(value: object) -> Decimal:
    return to_decimal(value) or Decimal(0)


async def _direct_metrics(
    ctx: Ctx,
    login: str,
    campaign_id: int,
    date_from: str,
    date_to: str,
    goal_id: str | None,
) -> tuple[dict, str]:
    """Показы/клики/расход/конверсии кампании за период (Reports API)."""
    client = ctx.reports()
    fields = ["Impressions", "Clicks", "Cost"]
    definition: dict = {
        "SelectionCriteria": {
            "Filter": [
                {
                    "Field": "CampaignId",
                    "Operator": "IN",
                    "Values": [str(campaign_id)],
                }
            ],
            "DateFrom": date_from,
            "DateTo": date_to,
        },
        "FieldNames": list(fields),
        "ReportName": "campaign_journal_snapshot",
        "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
        "DateRangeType": "CUSTOM_DATE",
        "Format": "TSV",
        "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
    }
    if goal_id:
        definition["FieldNames"] = fields + ["Conversions"]
        definition["Goals"] = [goal_id]
        definition["AttributionModels"] = list(ctx.settings.attribution) or ["AUTO"]
    try:
        try:
            _, rows = await client.fetch(login, definition)
        except DirectError as e:
            if e.code in (1000, 506, 1020):
                await asyncio.sleep(5)
                try:
                    _, rows = await client.fetch(login, definition)
                except DirectError as retry_e:
                    return {}, f"Директ: {retry_e.human_message()}"
            else:
                return {}, f"Директ: {e.human_message()}"
    finally:
        await client.aclose()
    impressions = Decimal(0)
    clicks = Decimal(0)
    cost = Decimal(0)
    conversions = Decimal(0)
    for row in rows:
        if not isinstance(row, dict):
            continue
        impressions += _num(row.get("Impressions"))
        clicks += _num(row.get("Clicks"))
        cost += _num(row.get("Cost"))
        for key, val in row.items():
            if str(key) == "Conversions" or str(key).startswith("Conversions_"):
                conversions += _num(val)
    metrics: dict = {
        "impressions": int(impressions),
        "clicks": int(clicks),
        "cost": round(float(cost), 2),
        "conversions": int(conversions) if goal_id else None,
    }
    metrics["ctr"] = (
        round(float(clicks / impressions * 100), 2) if impressions else None
    )
    metrics["cpc"] = round(float(cost / clicks), 2) if clicks else None
    return metrics, ""


async def _metrika_metrics(
    ctx: Ctx,
    entry,
    account: str,
    campaign_name: str,
    campaign_id: int,
    date_from: str,
    date_to: str,
    cost: float,
) -> tuple[dict, str, str | None]:
    """Визиты/отказы/цели кампании за период (Reporting API Метрики)."""
    counters, counter_problems = await campaign_counters(ctx, [entry], [campaign_id])
    problems = list(counter_problems)
    found: list[int] = []
    for lst in counters.values():
        found.extend(lst)
    counter_ids = sorted(set(found))
    if not counter_ids and ctx.settings.counter_id:
        counter_ids = [ctx.settings.counter_id]
    if not counter_ids:
        return (
            {},
            (
                "Ошибка: счётчики не найдены — укажите counter_id или привяжите "
                "счётчик к кампании (CounterIds)."
            ),
            None,
        )
    counter_id = counter_ids[0]
    goal_ids, _names, _note = await resolve_goals(
        ctx, counter_id, [], account, campaign_id
    )
    goal_id = goal_ids[0] if len(goal_ids) == 1 else None
    metrics_names = ["ym:s:visits", "ym:s:bounceRate"] + [
        f"ym:s:goal{g}reaches" for g in goal_ids
    ]
    try:
        payload = await stat_table(
            ctx.metrika_read_token(),
            counter_id,
            date_from,
            date_to,
            ["ym:s:lastDirectClickOrder"],
            metrics_names,
            "lastsign",
            None,
        )
    except MetrikaError as e:
        from directai_mcp.catalog.metrika_reports import human_metrika_error

        return {}, human_metrika_error(counter_id, e), None

    def _f(value: object) -> float:
        try:
            return float(str(value).replace(" ", "").replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    rows: dict[str, dict] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        dims = item.get("dimensions") or []
        dim = dims[0] if dims else {}
        oid = str(dim.get("id") if isinstance(dim, dict) else dim or "")
        vals = item.get("metrics") or []
        rows[oid] = {
            "name": str((dim.get("name") if isinstance(dim, dict) else None) or "—"),
            "visits": _f(vals[0]) if len(vals) > 0 else 0.0,
            "bounce": _f(vals[1]) if len(vals) > 1 else 0.0,
            "goals": sum(_f(v) for v in vals[2:]),
        }
    meta = rows.pop(str(campaign_id), None)
    match = "ID" if meta else ""
    if meta is None:
        meta = rows.pop(str(campaign_id + 100_000_000), None)
        match = "ID+сдвиг" if meta else ""
    if meta is None:
        hits = [k for k, v in rows.items() if v["name"] == campaign_name]
        if len(hits) == 1:
            meta = rows.pop(hits[0])
            match = "имя"
    if meta is None:
        problems.append("в Метрике визитов кампании нет.")
        meta = {"visits": 0.0, "bounce": 0.0, "goals": 0.0}
    elif match != "ID":
        problems.append(f"связь с Метрикой: {match}.")
    visits = meta["visits"]
    goals = meta["goals"]
    metrics = {
        "visits": int(visits),
        "bounce_rate": round(meta["bounce"], 2) if visits else None,
        "goals": int(goals),
        "cpa": round(cost / goals, 2) if goals and cost else None,
        "counter_id": counter_id,
    }
    return metrics, "\n".join(problems), goal_id


def _fmt_num(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _delta_text(now: object, prev: object) -> str:
    """Δ = A−B, Δ% от B (как в audit). Пусто — если чисел нет."""
    try:
        a = float(str(now)) if now is not None else None
        b = float(str(prev)) if prev is not None else None
    except (TypeError, ValueError):
        return ""
    if a is None or b is None:
        return ""
    delta = a - b
    if b:
        pct = delta / b * 100
        return f"{delta:+.2f} ({pct:+.1f}%)"
    return f"{delta:+.2f}"


def build_journal_markdown(
    *,
    login: str,
    campaign_id: int,
    header: dict,
    goal: dict,
    ops: list[dict],
    snapshots: list[dict],
    notes: list[dict],
    built_at: str,
) -> str:
    """Сборка файла журнала (чистая функция — тестируется без сети)."""
    lines = [
        f"# Журнал кампании {header.get('name', '—')} ({campaign_id})",
        "",
        (
            f"Кабинет: {login}. Тип: {header.get('type', '—')}. "
            f"Стратегия: {header.get('strategy', '—')}. "
            f"Статус: {header.get('state', '—')}."
        ),
        (
            f"Основная цель: id={goal.get('id') or '—'}, "
            f"label={goal.get('label', '—')}, source={goal.get('source', 'none')}."
        ),
        f"Собрано: {built_at}.",
    ]
    if header.get("warning"):
        lines.append(f"⚠ {header['warning']}")
    lines += ["", "## Результаты по периодам", ""]
    # Пары direct+metrika по периоду.
    periods: dict[tuple[str, str], dict] = {}
    for snap in snapshots:
        key = (snap["date_from"], snap["date_to"])
        slot = periods.setdefault(key, {"direct": None, "metrika": None})
        if snap["source"] in slot:
            slot[snap["source"]] = snap
    ordered = sorted(periods)
    if not ordered:
        lines.append("нет данных")
    else:
        lines += [
            "| Период | Расход | Клики | Конверсии | CPA | Визиты | Отказы |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        prev_row: dict = {}
        for key in ordered:
            slot = periods[key]
            direct = (slot["direct"] or {}).get("metrics", {}) or {}
            metrika = (slot["metrika"] or {}).get("metrics", {}) or {}
            cost = direct.get("cost")
            clicks = direct.get("clicks")
            conv = direct.get("conversions")
            if conv is None:
                conv = metrika.get("goals")
            cpa = metrika.get("cpa")
            if cpa is None and cost is not None and conv:
                try:
                    cpa = round(float(cost) / float(conv), 2)
                except (TypeError, ValueError, ZeroDivisionError):
                    cpa = None
            visits = metrika.get("visits")
            bounce = metrika.get("bounce_rate")
            row = {
                "cost": cost,
                "clicks": clicks,
                "conv": conv,
                "cpa": cpa,
                "visits": visits,
                "bounce": bounce,
            }
            cells = [f"{key[0]}–{key[1]}"]
            for field in ("cost", "clicks", "conv", "cpa", "visits", "bounce"):
                cell = _fmt_num(row[field])
                if prev_row:
                    delta = _delta_text(row[field], prev_row.get(field))
                    if delta:
                        cell += f" ({delta})"
                cells.append(cell)
            lines.append("| " + " | ".join(cells) + " |")
            prev_row = row
    lines += ["", "## История правок", ""]
    if not ops:
        lines.append("нет данных")
    else:
        lines += ["| Дата | Действие | Кратко | Статус |", "| --- | --- | --- | --- |"]
        for op in ops:
            mark = " ⚠" if op["status"] == "unverified" else ""
            summary = str(op.get("summary") or "")[:300].replace("\n", " ")
            lines.append(
                f"| {op['created_at']} | {op['action']} | {summary} "
                f"| {op['status']}{mark} |"
            )
    lines += ["", "## Заметки", ""]
    if not notes:
        lines.append("нет данных")
    else:
        order = {"todo": 0, "hypothesis": 1, "decision": 2, "observation": 3}
        for note in sorted(notes, key=lambda n: (order.get(n["kind"], 9), n["id"])):
            lines.append(f"- [{note['kind']}] {note['created_at']}: {note['text']}")
    lines += ["", "## Правки ↔ результаты", ""]
    if not ops or not snapshots:
        lines.append("нет данных")
    else:
        by_time = sorted(snapshots, key=lambda s: (s["created_at"], s["id"]))
        for op in ops:
            moment = op["created_at"]
            before = [s for s in by_time if s["created_at"] <= moment]
            after = [s for s in by_time if s["created_at"] > moment]

            def _ref(snap: dict | None) -> str:
                if snap is None:
                    return "—"
                return (
                    f"#{snap['id']} {snap['source']} "
                    f"{snap['date_from']}–{snap['date_to']}"
                )

            lines.append(
                f"- #{op['id']} {op['action']} ({moment}): "
                f"до — {_ref(before[-1] if before else None)}, "
                f"после — {_ref(after[0] if after else None)}."
            )
        lines.append("")
        lines.append("Только факты рядом; выводов «из-за правки» нет.")
    lines += [
        "",
        f"Источник правды — journal.sqlite ({login}, кампания {campaign_id}).",
    ]
    return "\n".join(lines) + "\n"


def _journal_path(ctx: Ctx, login: str, campaign_id: int):
    base = reports_base_dir(ctx.settings) / "journals" / login
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{campaign_id}.md"


async def _run_journal(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, CampaignJournalParams)
    entry, err = _single_entry(ctx, params.account)
    if entry is None:
        return err
    login = entry.login
    header = await _campaign_header(ctx, login, params.campaign_id)
    gid, source = resolve_primary_goal(
        ctx.settings, params.account, [params.campaign_id], None
    )
    from directai_mcp.catalog.common import goal_label

    goal = {
        "id": gid,
        "label": goal_label(gid, ctx.settings.goal_names, ctx.settings.goal_counters)
        if gid
        else "—",
        "source": source,
    }
    conn = journal_mod.connect(ctx.data_dir) if ctx.data_dir else None
    if conn is None:
        from directai_mcp.config import data_dir as _data_dir

        conn = journal_mod.connect(_data_dir())
    try:
        ops = journal_mod.ops_for_campaign(conn, login, params.campaign_id)
        snapshots = journal_mod.list_snapshots(conn, login, params.campaign_id)
        notes = journal_mod.list_notes(conn, login, params.campaign_id)
    finally:
        conn.close()
    if params.days < 365:
        cutoff = (datetime.now().astimezone() - timedelta(days=params.days)).date()
        ops = [o for o in ops if o["created_at"][:10] >= cutoff.isoformat()]
    built_at = datetime.now().astimezone().isoformat(timespec="seconds")
    text = build_journal_markdown(
        login=login,
        campaign_id=params.campaign_id,
        header=header,
        goal=goal,
        ops=ops,
        snapshots=snapshots,
        notes=notes,
        built_at=built_at,
    )
    path = _journal_path(ctx, login, params.campaign_id)
    path.write_text(text, encoding="utf-8")
    return text + f"\nФайл: {path}."


async def _run_snapshot(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, CampaignJournalSnapshotParams)
    err = _check_period(params.date_from, params.date_to)
    if err:
        return err
    entry, acc_err = _single_entry(ctx, params.account)
    if entry is None:
        return acc_err
    login = entry.login
    gid, _source = resolve_primary_goal(
        ctx.settings, params.account, [params.campaign_id], None
    )
    header = await _campaign_header(ctx, login, params.campaign_id)
    name = header.get("name", "—")
    problems: list[str] = []
    saved: list[str] = []
    direct_metrics: dict = {}
    if "direct" in params.sources:
        direct_metrics, d_err = await _direct_metrics(
            ctx,
            login,
            params.campaign_id,
            params.date_from,
            params.date_to,
            gid,
        )
        if d_err:
            problems.append(d_err)
    metrika_metrics: dict = {}
    metrika_goal: str | None = None
    if "metrika" in params.sources:
        cost = float(direct_metrics.get("cost") or 0)
        metrika_metrics, m_err, metrika_goal = await _metrika_metrics(
            ctx,
            entry,
            params.account,
            name,
            params.campaign_id,
            params.date_from,
            params.date_to,
            cost,
        )
        if m_err:
            problems.append(m_err)
    if not direct_metrics and not metrika_metrics:
        return "\n".join(
            problems or ["Ошибка: снимок пуст (оба источника без данных)."]
        )
    conn = journal_mod.connect(ctx.data_dir) if ctx.data_dir else None
    if conn is None:
        from directai_mcp.config import data_dir as _data_dir

        conn = journal_mod.connect(_data_dir())
    try:
        if direct_metrics:
            sid = journal_mod.add_snapshot(
                conn,
                account_login=login,
                campaign_id=params.campaign_id,
                date_from=params.date_from,
                date_to=params.date_to,
                source="direct",
                metrics=direct_metrics,
                goal_id=gid,
            )
            saved.append(f"direct #{sid}")
        if metrika_metrics:
            sid = journal_mod.add_snapshot(
                conn,
                account_login=login,
                campaign_id=params.campaign_id,
                date_from=params.date_from,
                date_to=params.date_to,
                source="metrika",
                metrics=metrika_metrics,
                goal_id=metrika_goal or gid,
            )
            saved.append(f"metrika #{sid}")
    finally:
        conn.close()
    head = (
        f"Снимок {name} ({params.campaign_id}) @{login} "
        f"{params.date_from}–{params.date_to}: сохранено {', '.join(saved)}."
    )
    if direct_metrics:
        head += (
            f" Директ: показы {direct_metrics.get('impressions')}, "
            f"клики {direct_metrics.get('clicks')}, "
            f"расход {direct_metrics.get('cost')}."
        )
    if metrika_metrics:
        head += (
            f" Метрика: визиты {metrika_metrics.get('visits')}, "
            f"цели {metrika_metrics.get('goals')}, "
            f"CPA {metrika_metrics.get('cpa')}."
        )
    if problems:
        head += "\nПредупреждения:\n- " + "\n- ".join(problems)
    return head


async def _run_note(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, CampaignJournalNoteParams)
    entry, acc_err = _single_entry(ctx, params.account)
    if entry is None:
        return acc_err
    conn = journal_mod.connect(ctx.data_dir) if ctx.data_dir else None
    if conn is None:
        from directai_mcp.config import data_dir as _data_dir

        conn = journal_mod.connect(_data_dir())
    try:
        nid = journal_mod.add_note(
            conn,
            account_login=entry.login,
            campaign_id=params.campaign_id,
            kind=params.kind,
            text=params.text,
        )
    finally:
        conn.close()
    return (
        f"Заметка #{nid} [{params.kind}] к кампании {params.campaign_id} "
        f"@{entry.login} сохранена."
    )


action(
    "campaign_journal",
    "read",
    "Журнал кампании: правки, результаты по периодам, заметки (пишет md-файл)",
    (
        "журнал",
        "journal",
        "история кампании",
        "история правок",
        "что меняли",
        "контекст кампании",
    ),
    CampaignJournalParams,
)(_run_journal)


action(
    "campaign_journal_snapshot",
    "read",
    "Снимок результатов кампании за период (Директ + Метрика, локально)",
    (
        "снимок",
        "snapshot",
        "результаты периода",
        "зафиксировать результаты",
        "cpa периода",
    ),
    CampaignJournalSnapshotParams,
)(_run_snapshot)


action(
    "campaign_journal_note",
    "read",
    "Заметка к кампании: гипотеза, решение, наблюдение, задача (локально)",
    (
        "заметка",
        "note",
        "гипотеза",
        "решение",
        "наблюдение",
        "todo",
        "задача",
    ),
    CampaignJournalNoteParams,
)(_run_note)
