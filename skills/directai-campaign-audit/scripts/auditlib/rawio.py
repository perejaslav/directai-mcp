#!/usr/bin/env python3
"""Чтение сырых ответов DirectAI MCP и проверка происхождения данных (ТЗ §5.2–5.4).

Контракт: агент вызывает только `run_read` (output=file, format=json) и кладёт
ответы в `<session>/raw/`. `session.json` объявляет источники. Скрипт ничего
не додумывает: отсутствующий файл — это `unavailable`, а не ноль.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .model import iso_date, now_iso, read_json

READ_ONLY_ACTIONS = frozenset({
    "accounts_balance", "accounts_check", "accounts_discover",
    "adgroups_list", "ads_list", "audiences_list", "bid_modifiers_get",
    "bids_get", "businesses_get", "campaign_journal", "campaigns_get",
    "campaigns_list", "changes_check", "counter_check", "dictionaries_get",
    "dynamic_feed_targets_get", "dynamic_targets_get", "extensions_list",
    "feeds_get", "keyword_bids_forecast", "keywords_list", "metrika_bytime",
    "metrika_direct_cpa", "metrika_goals_list", "metrika_goals_report",
    "metrika_traffic", "moderation_check", "negatives_audit", "phrases_forecast",
    "retargeting_lists_list", "smart_targets_get", "stats_adgroups", "stats_ads",
    "stats_audiences", "stats_campaigns", "stats_compare", "stats_custom",
    "stats_devices", "stats_keywords", "stats_placements", "stats_regions",
    "stats_search_queries", "stats_summary", "strategies_get", "turbopages_get",
    "webmaster_hosts", "webmaster_query", "webmaster_summary", "wordstat_dynamics",
    "wordstat_regions", "wordstat_regions_tree", "wordstat_top",
})

# Действия изменения рекламы: в аудите запрещены полностью (ТЗ §1.2, §15).
FORBIDDEN_ACTIONS = frozenset({
    "plan_write", "apply_write", "campaigns_create", "campaigns_update",
    "campaigns_state", "adgroups_create", "adgroups_update", "ads_create",
    "ads_update", "ads_state", "keywords_add", "keywords_update", "keywords_state",
    "negatives_set", "bid_modifiers_set", "bids_set", "extensions_create",
    "audience_segment_delete", "audience_segment_from_file", "audience_target_add",
    "audience_target_state", "metrika_goal_create", "metrika_goal_delete",
    "metrika_goal_update", "retargeting_list_create", "retargeting_list_delete",
    "retargeting_list_update", "feeds_get",  # feeds_get остаётся только чтением
}) - {"feeds_get"}

JSON_SUFFIXES = (".json",)


class SessionError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_session(session_dir: Path) -> dict:
    session_file = Path(session_dir) / "session.json"
    if not session_file.exists():
        raise SessionError(f"нет session.json в {session_dir}")
    session = read_json(session_file)
    for key in ("account", "campaign_id", "modes", "periods"):
        if key not in session:
            raise SessionError(f"session.json: нет обязательного поля {key}")
    if isinstance(session["campaign_id"], (int, float)):
        session["campaign_id"] = str(int(session["campaign_id"]))
    else:
        session["campaign_id"] = str(session["campaign_id"])
    if isinstance(session["modes"], str):
        session["modes"] = [m.strip() for m in session["modes"].split("+") if m.strip()]
    session.setdefault("sources", [])
    session.setdefault("thresholds", {})
    session.setdefault("product", {})
    session.setdefault("notes", [])
    return session


def collect_sources(session_dir: Path, session: dict) -> dict:
    """Загружает объявленные источники, считает SHA-256, сверяет метаданные.

    Возвращает {"sources": [...], "data": {action: payload}, "problems": [...]}.
    Один и тот же action может встречаться дважды (например, stats_compare за
    разные периоды) — тогда в data он лежит списком.
    """
    session_dir = Path(session_dir)
    raw_dir = session_dir / "raw"
    out = []
    problems = []
    data: dict[str, list] = {}

    declared_files = set()
    for index, entry in enumerate(session["sources"], start=1):
        action = str(entry.get("action", "")).strip()
        rel = str(entry.get("file", "")).strip()
        record = {
            "index": index,
            "action": action,
            "file": rel,
            "params": entry.get("params") or {},
            "declared_status": entry.get("status", "ok"),
            "note": entry.get("note"),
            "tool_warning": entry.get("tool_warning"),
            "pagination_complete": entry.get("pagination_complete"),
            "truncated": entry.get("truncated"),
            "period": entry.get("period"),
            "status": "ok",
            "sha256": None,
            "bytes": None,
            "fetched_at": entry.get("fetched_at"),
            "rows": None,
            "meta": None,
            "payload": None,
            "known": True,
            "error": entry.get("error"),
        }
        if action in FORBIDDEN_ACTIONS:
            record["status"] = "blocked"
            problems.append(f"источник {rel or index}: действие {action} изменяет рекламу и запрещено в аудите")
            out.append(record)
            continue

        declared_campaigns = {str(value) for value in
                              (entry.get("params") or {}).get("campaign_ids") or []}
        expected_campaign = str(session.get("campaign_id") or "")
        if declared_campaigns and expected_campaign and expected_campaign not in declared_campaigns:
            record["status"] = "blocked"
            problems.append(
                f"источник {rel or index}: объявлен для кампаний {', '.join(sorted(declared_campaigns))}, "
                f"а аудит идёт по {expected_campaign}")
            out.append(record)
            continue
        if action.startswith("stats_") and not declared_campaigns:
            record["status"] = "warning"
            problems.append(f"источник {rel or index}: охват кампании не указан в параметрах")

        if action and action not in READ_ONLY_ACTIONS:
            record["status"] = "blocked"
            record["known"] = False
            problems.append(f"источник {rel or index}: действие {action} не зарегистрировано — "
                            "схема не проверена, в анализ не берётся")

        if not rel:
            if record["declared_status"] in ("error", "blocked") or record.get("error"):
                record["status"] = ("blocked" if record["declared_status"] == "blocked" else "error")
                code = (record.get("error") or {}).get("code")
                message = (record.get("error") or {}).get("message")
                problems.append(
                    f"источник #{index} ({action or 'без действия'}): вызов не выполнен"
                    + (f", код {code}" if code is not None else "")
                    + (f" — {message}" if message else ""))
            else:
                record["status"] = "blocked"
                problems.append(f"источник #{index}: не указан файл и нет описания ошибки")
            out.append(record)
            continue
        declared_files.add(rel)
        path = raw_dir / rel
        if not path.exists():
            record["status"] = "unavailable"
            problems.append(f"источник {rel}: файл не найден — данные недоступны")
            out.append(record)
            continue

        record["bytes"] = path.stat().st_size
        record["sha256"] = sha256_file(path)
        expected = entry.get("sha256")
        if expected and expected != record["sha256"]:
            record["status"] = "blocked"
            problems.append(f"источник {rel}: SHA-256 не совпал с заявленным")
            out.append(record)
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = read_json(path)
            except json.JSONDecodeError as exc:
                record["status"] = "blocked"
                problems.append(f"источник {rel}: файл не является корректным JSON ({exc})")
                out.append(record)
                continue
            meta = payload.get("meta") if isinstance(payload, dict) else None
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(meta, dict) or not isinstance(rows, list):
                record["status"] = "blocked"
                problems.append(
                    f"источник {rel}: неизвестный формат ответа (ожидался конверт {{meta, rows}})"
                )
                out.append(record)
                continue
            record["meta"] = meta
            record["rows"] = rows
            record["payload"] = payload
            record["fetched_at"] = meta.get("created_at") or record["fetched_at"]
            meta_action = str(meta.get("action") or "").strip()
            if action and meta_action and meta_action != action:
                record["status"] = "blocked"
                problems.append(
                    f"источник {rel}: в файле действие {meta_action}, объявлено {action}"
                )
            elif not action:
                record["action"] = meta_action
                action = meta_action
                if action not in READ_ONLY_ACTIONS:
                    record["status"] = "blocked"
                    record["known"] = False
                    problems.append(f"источник {rel}: действие {action} не зарегистрировано — "
                                    "схема не проверена, в анализ не берётся")
        else:
            try:
                record["payload"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                record["status"] = "blocked"
                problems.append(f"источник {rel}: не читается как UTF-8")
        if record["status"] == "ok" and record["declared_status"] in ("error", "blocked"):
            record["status"] = record["declared_status"]
        out.append(record)
        if record["status"] in ("ok", "warning"):
            data.setdefault(record["action"], []).append(record)

    # Файлы в raw/, которых нет в объявлении: не используем, но фиксируем.
    if raw_dir.is_dir():
        extra = sorted(p.name for p in raw_dir.iterdir() if p.is_file() and p.name not in declared_files)
        for name in extra:
            problems.append(f"файл raw/{name} не объявлен в session.json и не использован")

    return {"sources": out, "data": data, "problems": problems, "raw_dir": str(raw_dir)}


def first(source_map: dict, action: str):
    items = source_map.get(action) or []
    return items[0] if items else None


def rows_of(source_map: dict, action: str) -> list:
    item = first(source_map, action)
    return list(item.get("rows") or []) if item else []


def table_of(source_map: dict, action: str) -> dict:
    """stats_compare: строки ключуются по названию метрики."""
    item = first(source_map, action)
    if not item:
        return {}
    out = {}
    for row in item.get("rows") or []:
        name = str(row.get("Метрика") or row.get("metric") or "").strip()
        if name:
            out[name] = row
    return out


def parse_md_tables(text: str) -> list[list[dict]]:
    """Разбор markdown-таблиц из campaign_journal (журнал отдаётся только текстом)."""
    tables, current, header = [], [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if header is None:
                header = cells
            elif all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
                continue
            elif header:
                if len(cells) == len(header):
                    current.append(dict(zip(header, cells)))
        else:
            if current and header:
                tables.append(current)
            current, header = [], None
    if current and header:
        tables.append(current)
    return tables


def journal_changes(text: str) -> list[dict]:
    """История правок из журнала кампании: дата, действие, кратко, статус."""
    changes = []
    for table in parse_md_tables(text or ""):
        if not table:
            continue
        keys = set(table[0].keys())
        if {"Дата", "Действие"} <= keys:
            for row in table:
                changes.append({
                    "at": row.get("Дата"),
                    "action": row.get("Действие"),
                    "summary": row.get("Кратко"),
                    "status": row.get("Статус"),
                    "source": "campaign_journal",
                })
    return changes


def journal_notes(text: str) -> str:
    match = re.search(r"##\s*Заметки\s*(.+?)(?:\n##|\Z)", text or "", re.S)
    return match.group(1).strip() if match else ""


def source_context(source_map: dict, action: str) -> str:
    item = first(source_map, action)
    if not item or not item.get("meta"):
        return ""
    return str(item["meta"].get("context") or "")


def parse_context(context: str) -> dict:
    """Достаёт из шапки инструмента атрибуцию, цель и оговорки.

    Шапка — единственный документированный источник этих сведений,
    поэтому она сохраняется дословно и разбирается только по явным меткам.
    """
    out = {"raw": context, "attribution": {}, "goal": {}, "notes": []}
    if not context:
        return out
    for key in ("requested", "effective", "source"):
        match = re.search(rf"{key}=([A-Za-z_]+)", context)
        if match:
            out["attribution"][key] = match.group(1)
    goal_block = re.search(r"Цель:\s*id=([^,]+),\s*label=([^,]+),\s*source=([A-Za-z_]+)", context)
    if goal_block:
        out["goal"] = {
            "id": goal_block.group(1).strip(),
            "label": goal_block.group(2).strip(),
            "source": goal_block.group(3).strip(),
        }
    for marker in ("Внимание:", "В строках возможны"):
        index = context.find(marker)
        if index >= 0:
            out["notes"].append(context[index:].split(". ")[0].strip())
    return out


def account_mismatch(source_map: dict, expected_login: str | None) -> list[str]:
    """Проверка, что все строки относятся к нужному кабинету (ТЗ §5.4)."""
    problems = []
    if not expected_login:
        return problems
    for action, items in source_map.items():
        for item in items:
            for row in item.get("rows") or []:
                actual = row.get("_account")
                if actual and str(actual) != str(expected_login):
                    problems.append(
                        f"{action}: строка кабинета {actual}, ожидался {expected_login}"
                    )
                    break
    return problems


def pick_column(row: dict, *names):
    """Значение по первому совпавшему имени колонки (регистр не важен)."""
    if not isinstance(row, dict):
        return None
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row[name]
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def conversion_column(rows: list, columns: list | None = None):
    """Имя колонки конверсий: сначала сводная `Conversions`, иначе по цели."""
    columns = columns or []
    if "Conversions" in columns or any("Conversions" in (r or {}) for r in rows[:1]):
        return "Conversions"
    for name in columns:
        if str(name).startswith("Conversions_"):
            return name
    return None


def period_bounds(session: dict) -> dict:
    periods = session.get("periods") or {}
    a = periods.get("a") or {}
    b = periods.get("b") or {}
    return {
        "a": {"from": iso_date(a.get("from")), "to": iso_date(a.get("to"))},
        "b": {"from": iso_date(b.get("from")), "to": iso_date(b.get("to"))},
    }


def provenance_record(item: dict) -> dict:
    meta = item.get("meta") or {}
    return {
        "action": item["action"],
        "file": item["file"],
        "params": item["params"],
        "account": meta.get("account"),
        "sha256": item["sha256"],
        "bytes": item["bytes"],
        "fetched_at": item.get("fetched_at") or now_iso(),
        "status": item["status"] if item["status"] != "ok" else "ok",
        "known_action": item.get("known", True),
        "pagination_complete": item.get("pagination_complete"),
        "truncated": item.get("truncated"),
        "period": item.get("period"),
        "rows": len(item.get("rows") or []) if item.get("rows") is not None else None,
        "tool_warning": item.get("tool_warning"),
        "error": item.get("error"),
        "note": item.get("note"),
    }
