#!/usr/bin/env python3
"""Нормализация сырых ответов в показатели и сегменты (ТЗ §5.3).

Правила:
- значения берутся из источника как есть; пересчёт только если поле отсутствует,
  и тогда это помечается `computed: true`;
- неизвестная схема колонок не превращается в нули: сегмент помечается
  `unavailable` с перечнем полученных колонок;
- `null` (нет данных) и `0` (ноль) различаются.
"""
from __future__ import annotations

from . import metrics as m
from .model import DEFAULT_THRESHOLDS, parse_number
from .rawio import first, pick_column, rows_of, source_context, table_of

SEGMENT_KEYS = {
    "devices": {"action": "stats_devices", "keys": ["Device", "Устройство", "Тип устройства", "_device"]},
    "regions": {"action": "stats_regions", "keys": ["Region", "Регион", "RegionName", "_region"]},
    "placements": {"action": "stats_placements", "keys": ["Placement", "Площадка", "Site", "SiteName", "_placement"]},
    "search_queries": {"action": "stats_search_queries", "keys": ["Query", "Поисковый запрос", "Запрос", "_query"]},
    "keywords": {"action": "stats_keywords", "keys": ["Keyword", "Фраза", "Ключевая фраза", "_keyword"]},
    "adgroups": {"action": "stats_adgroups", "keys": ["AdGroupName", "Группа", "AdGroup", "_adgroup"]},
    "ads": {"action": "stats_ads", "keys": ["AdId", "Ad", "Объявление", "AdName", "Id"]},
    "audiences": {"action": "stats_audiences", "keys": ["Audience", "Аудитория", "AudienceName"]},
}

METRIC_COLUMNS = {
    "impressions": ["Impressions", "Показы"],
    "clicks": ["Clicks", "Клики"],
    "cost": ["Cost", "Расход", "Расход, ₽", "CostRub"],
    "ctr": ["CTR", "CTR, %"],
    "cpc": ["CPC", "CPC, ₽", "Цена клика"],
    "conversions": ["Conversions", "Конверсии", "Конверсии (достижения)", "Conversions (сумма по целям)"],
    "cpa": ["CPA", "CPA, ₽", "Цена конверсии"],
    "cr": ["CR", "CR, %", "Конверсия"],
    "bounce_rate": ["BounceRate", "Отказы, %", "Отказы"],
    "clicks_to_visits": ["ClicksToVisits"],
    "visits": ["Visits", "Визиты"],
    "revenue": ["Revenue", "Выручка"],
}


def columns_of(source_map: dict) -> list:
    item = first(source_map, "stats_compare")
    return list((item or {}).get("meta", {}).get("columns") or [])


def normalize_row(row: dict, columns=None) -> dict:
    out = {"raw": row, "computed": []}
    for name, aliases in METRIC_COLUMNS.items():
        value = None
        for alias in aliases:
            if alias in row:
                value = row[alias]
                break
        out[name] = parse_number(value)
    for name in ("impressions", "clicks", "conversions"):
        if out[name] is not None:
            out[name] = int(round(out[name]))
    if out["ctr"] is None and out["impressions"]:
        out["ctr"] = m.ctr(out["clicks"], out["impressions"])
        out["computed"].append("ctr")
    if out["cpc"] is None and out["clicks"]:
        out["cpc"] = m.cpc(out["cost"], out["clicks"])
        out["computed"].append("cpc")
    if out["cr"] is None and out["clicks"]:
        out["cr"] = m.cr(out["conversions"], out["clicks"])
        out["computed"].append("cr")
    if out["cpa"] is None and out["conversions"]:
        out["cpa"] = m.cpa(out["cost"], out["conversions"])
        out["computed"].append("cpa")
    return out


def _first_present(columns, candidates):
    lower = {str(c).lower(): c for c in columns}
    for name in candidates:
        if name in columns:
            return name
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _conversions_column(columns):
    for name in columns:
        text = str(name)
        if text == "Conversions":
            return text
    for name in columns:
        if str(name).startswith("Conversions") and "CostPer" not in str(name):
            return str(name)
    return None


# ------------------------------------------------------------ основной отчёт
def compare_block(source_map: dict) -> dict:
    """Показатели A/B из stats_compare + пересчитанные изменения."""
    table = table_of(source_map, "stats_compare")
    result = {"available": bool(table), "a": {}, "b": {}, "comparison": {}, "rows": {}, "note": None}
    if not table:
        result["note"] = "stats_compare недоступен"
        return result

    def find(prefixes):
        for name, row in table.items():
            low = name.lower()
            for prefix in prefixes:
                if low.startswith(prefix):
                    return row
        return None

    mapping = {
        "impressions": (["показы"], "count"),
        "clicks": (["клики"], "count"),
        "ctr": (["ctr"], "rate"),
        "cost": (["расход"], "money"),
        "cpc": (["cpc"], "per_click"),
        "conversions": (["конверсии"], "count"),
        "cr": (["cr"], "rate"),
        "cpa": (["cpa"], "money"),
        "bounce_rate": (["отказы"], "rate"),
    }
    for key, (prefixes, kind) in mapping.items():
        row = find(prefixes)
        if row is None:
            result["a"][key] = None
            result["b"][key] = None
            result["comparison"][key] = m.compare(None, None, kind=kind)
            continue
        a = parse_number(row.get("A"))
        b = parse_number(row.get("B"))
        if key in ("impressions", "clicks", "conversions"):
            a = int(round(a)) if a is not None else None
            b = int(round(b)) if b is not None else None
        result["a"][key] = a
        result["b"][key] = b
        result["rows"][key] = {"metric": row.get("Метрика"), "a": row.get("A"), "b": row.get("B"),
                               "tool_delta": row.get("Δ"), "tool_delta_pct": row.get("Δ%")}
        result["comparison"][key] = m.compare(a, b, kind=kind)

    # Значения, которых нет в stats_compare, но нужны для правил.
    a, b = result["a"], result["b"]
    if a.get("cpc") is None and a.get("cost") is not None and a.get("clicks"):
        a["cpc"] = a["cost"] / a["clicks"]
        result["comparison"]["cpc"] = m.compare(a["cpc"], b.get("cpc"), kind="per_click")

    # Источник отдаёт доли с двумя знаками. Если после разбора A и B равны,
    # а сырые числители различаются, изменение существует, но скрыто округлением.
    for key, numerator in (("ctr", "clicks"), ("cr", "conversions")):
        item = result["comparison"].get(key) or {}
        if item.get("available") and item.get("abs") == 0 and item.get("kind") == "rate":
            left, right = a.get(numerator), b.get(numerator)
            if left is not None and right is not None and left != right:
                item["display_hides_change"] = True
                item["note"] = ("источник отдаёт долю с двумя знаками; по сырым "
                                f"{numerator} значения различаются")
    return result


def get_goal(source_map: dict, session: dict) -> dict:
    """Основная цель: приоритет param > campaign > account > none (как в старом SKILL)."""
    override = session.get("goal") or {}
    if override.get("id"):
        return {"id": str(override["id"]), "label": override.get("label") or "",
                "source": override.get("source") or "param", "configured": True,
                "value_rub": override.get("value_rub")}
    context = source_context(source_map, "stats_compare") or source_context(source_map, "stats_summary")
    from .rawio import parse_context
    parsed = parse_context(context).get("goal") or {}
    goal_id = (parsed.get("id") or "").strip()
    if goal_id and goal_id not in ("—", "-", "none"):
        return {"id": goal_id, "label": parsed.get("label") or "", "source": parsed.get("source") or "campaign",
                "configured": True, "value_rub": override.get("value_rub")}
    return {"id": None, "label": "", "source": "none", "configured": False,
            "value_rub": override.get("value_rub"), "context": context}


def get_attribution(source_map: dict, session: dict | None = None) -> dict:
    from .rawio import parse_context
    context = source_context(source_map, "stats_compare")
    parsed = parse_context(context).get("attribution") or {}
    declared = (session or {}).get("attribution") or {}
    result = {
        "requested": parsed.get("requested"),
        "effective": parsed.get("effective"),
        "source": parsed.get("source"),
        "raw": context,
        "consistent": None,
    }
    if not result["effective"] and declared:
        result.update({
            "requested": declared.get("requested"),
            "effective": declared.get("effective"),
            "source": declared.get("source") or "session",
        })
    return result


# ------------------------------------------------------------ сегменты
def segment_block(source_map: dict, name: str) -> dict:
    spec = SEGMENT_KEYS[name]
    item = first(source_map, spec["action"])
    out = {
        "available": bool(item),
        "action": spec["action"],
        "key_column": None,
        "rows": [],
        "total": {"cost": None, "conversions": None, "clicks": None, "impressions": None},
        "columns": [],
        "note": None,
        "source_period": None,
        "attribution": None,
        "truncated": bool(item.get("truncated")) if item else None,
        "pagination_complete": item.get("pagination_complete") if item else None,
    }
    if not item:
        out["note"] = "источник не получен"
        return out
    rows = item.get("rows") or []
    out["columns"] = list((item.get("meta") or {}).get("columns") or [])
    if not rows:
        out["note"] = "строк нет"
        return out
    key = _first_present(out["columns"], spec["keys"]) or _first_present(list(rows[0].keys()), spec["keys"])
    if key is None:
        out["available"] = False
        out["note"] = f"не найдена колонка ключа; получены колонки: {', '.join(map(str, out['columns']))}"
        return out
    out["key_column"] = key
    for row in rows:
        normalized = normalize_row(row)
        normalized["key"] = str(row.get(key) or "").strip()
        normalized["id"] = str(pick_column(row, "Id", "CampaignId", "AdGroupId", "AdId") or normalized["key"])
        out["rows"].append(normalized)
    for field in ("cost", "conversions", "clicks", "impressions"):
        values = [r[field] for r in out["rows"] if r[field] is not None]
        out["total"][field] = sum(values) if values else None
    return out


def metrika_block(source_map: dict) -> dict:
    item = first(source_map, "metrika_direct_cpa")
    out = {"available": bool(item), "rows": [], "note": None, "context": ""}
    if not item:
        out["note"] = "metrika_direct_cpa недоступен"
        return out
    out["context"] = str((item.get("meta") or {}).get("context") or "")
    for row in item.get("rows") or []:
        out["rows"].append({
            "campaign": row.get("Campaign"),
            "cost": parse_number(row.get("Cost")),
            "clicks": parse_number(row.get("Clicks")),
            "visits": parse_number(row.get("Visits")),
            "clicks_to_visits": parse_number(row.get("ClicksToVisits")),
            "bounce_rate": parse_number(row.get("BounceRate")),
            "goals": parse_number(row.get("Goals")),
            "cr": parse_number(row.get("CR")),
            "cpa": parse_number(row.get("CPA")),
            "flag": row.get("Flag"),
            "raw": row,
        })
    return out


def campaign_facts(source_map: dict) -> dict:
    rows = rows_of(source_map, "campaigns_get")
    facts = {
        "found": bool(rows),
        "id": None, "name": None, "type": None, "state": None, "status": None,
        "start_date": None, "end_date": None, "timezone": None, "strategy_raw": None,
        "counters": [], "attribution": None, "negatives_raw": None, "raw": None,
    }
    if not rows:
        return facts
    row = rows[0]
    facts.update({
        "id": str(pick_column(row, "Id") or ""),
        "name": pick_column(row, "Name"),
        "type": pick_column(row, "Type"),
        "state": pick_column(row, "State"),
        "status": pick_column(row, "Status"),
        "start_date": pick_column(row, "StartDate"),
        "end_date": pick_column(row, "EndDate"),
        "timezone": pick_column(row, "TimeZone"),
        "strategy_raw": pick_column(row, "Strategy"),
        "attribution": pick_column(row, "Attrib"),
        "negatives_raw": pick_column(row, "Negatives"),
        "raw": row,
    })
    counters = pick_column(row, "Counters")
    if isinstance(counters, str):
        facts["counters"] = [c.strip() for c in counters.split(",") if c.strip() and c.strip() != "—"]
    elif counters:
        facts["counters"] = [str(counters)]
    return facts


def strategy_flags(facts: dict) -> dict:
    """Тип размещения из строки стратегии. Значение читается, а не угадывается."""
    import re
    raw = str(facts.get("strategy_raw") or "")
    out = {"search": None, "network": None, "raw": raw, "source": "campaigns_get.Strategy"}
    if not raw or raw == "—":
        return out
    for chunk in re.split(r"[;/]", raw):
        chunk = chunk.strip()
        if chunk.startswith("поиск:"):
            out["search"] = chunk.split(":", 1)[1].strip().split(";")[0].strip()
        elif chunk.startswith("сети:"):
            out["network"] = chunk.split(":", 1)[1].strip().split(";")[0].strip()
        elif chunk.startswith("PlacementTypes"):
            out["placement_types"] = chunk
    for key in ("search", "network"):
        if out[key] and "SERVING_OFF" in out[key]:
            out[key] = "OFF"
    # API отдаёт строку стратегии обрезанной: «сети: SERVIN…». Тогда тип
    # размещения неизвестен, и делать выводы о площадках или изображениях нельзя.
    network_truncated = str(out.get("network") or "").endswith(("…", "..."))
    search_truncated = str(out.get("search") or "").endswith(("…", "..."))
    out["truncated"] = network_truncated or search_truncated
    out["network_known"] = bool(out["network"]) and not network_truncated
    if network_truncated:
        out["network"] = None
    return out


def network_enabled(strategy: dict) -> bool:
    """Работает ли кампания в сетях (значение SERVING_OFF считается отключением)."""
    network = (strategy or {}).get("network")
    if network and network not in ("OFF", "SERVING_OFF"):
        return True
    return (strategy or {}).get("search") == "OFF"


def history_block(source_map: dict, source_list: list) -> dict:
    journal = first(source_map, "campaign_journal")
    changes_rows = rows_of(source_map, "changes_check")
    out = {"available": bool(journal or changes_rows), "changes": [], "notes": "",
           "changes_check": [], "note": None}
    if journal and isinstance(journal.get("payload"), str):
        from .rawio import journal_changes, journal_notes
        out["changes"] = journal_changes(journal["payload"])
        out["notes"] = journal_notes(journal["payload"])
    for row in changes_rows:
        out["changes_check"].append({
            "status": pick_column(row, "Статус", "Status"),
            "type": pick_column(row, "Тип", "Type"),
            "id": str(pick_column(row, "Id") or ""),
            "date": pick_column(row, "BorderDate", "Date"),
        })
    if not out["available"]:
        out["note"] = "журнал и changes_check недоступны"
    return out


def negatives_block(source_map: dict) -> dict:
    item = first(source_map, "negatives_audit")
    out = {"available": bool(item), "campaign": [], "groups": [], "shared": [], "raw_rows": []}
    if not item:
        return out
    for row in item.get("rows") or []:
        out["raw_rows"].append(row)
        level = str(pick_column(row, "Level", "Уровень", "Scope") or "").lower()
        text = pick_column(row, "Negatives", "Минус-фразы", "Value", "Фраза")
        values = []
        if isinstance(text, list):
            values = [str(v) for v in text]
        elif isinstance(text, str):
            values = [v.strip() for v in text.split(",") if v.strip()]
        entry = {"id": str(pick_column(row, "Id", "AdGroupId") or ""), "values": values, "raw": row}
        if "group" in level or "групп" in level:
            out["groups"].append(entry)
        elif "shared" in level or "общ" in level:
            out["shared"].append(entry)
        else:
            out["campaign"].append(entry)
    return out


def build_segments(source_map: dict) -> dict:
    out = {}
    for name in SEGMENT_KEYS:
        out[name] = segment_block(source_map, name)
    out["metrika"] = metrika_block(source_map)
    return out


def default_thresholds(session_thresholds: dict, metrics_block: dict, goal: dict) -> dict:
    th = dict(DEFAULT_THRESHOLDS)
    th.update({k: v for k, v in (session_thresholds or {}).items() if v is not None})
    if th.get("target_cpa_rub") is None and goal.get("value_rub"):
        th["target_cpa_rub"] = None  # ценность цели — не цель по CPA
    a = metrics_block.get("a") or {}
    cpa_a = a.get("cpa")
    th["observed_cpa_rub"] = cpa_a
    th["cpa_basis"] = "target" if th.get("target_cpa_rub") else ("observed" if cpa_a else "none")
    if th.get("target_cpa_rub"):
        th["attention_threshold_rub"] = th["target_cpa_rub"] * th["attention_multiplier"]
    elif cpa_a:
        th["attention_threshold_rub"] = cpa_a * th["attention_multiplier"]
    else:
        th["attention_threshold_rub"] = None
    return th
