#!/usr/bin/env python3
"""Сборка audit.json из сессии чтения (ТЗ §5.1, §5.3, §11.4).

Все числа в отчётах берутся отсюда: Markdown, Excel и HTML не пересчитывают
показатели и не переписывают значения вручную.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from . import extract, rules
from . import dq as dq_mod
from . import validate as validate_mod
from .model import SCHEMA_VERSION, fmt_id, iso_date, now_iso, paths_overlap
from .rawio import (SessionError, account_mismatch, collect_sources, load_session,
                    period_bounds, provenance_record, rows_of)

MODE_REQUIREMENTS = {
    "quick": ["stats_compare", "campaigns_get"],
    "deep": ["stats_compare", "campaigns_get"],
    "semantic": ["stats_search_queries", "keywords_list", "negatives_audit"],
    "rsy": ["stats_placements"],
    "funnel": ["metrika_direct_cpa"],
    "growth": ["stats_adgroups"],
    "monitor": [],
    "experiment": [],
}


def _dedupe_spend(items: list[dict], field: str) -> dict:
    """Суммирование расходов без пересечений объектов (§8.3).

    Общая сумма считается только когда все объекты одного типа. Расходы
    устройства, фразы и площадки описывают разные разрезы одного бюджета,
    поэтому их нельзя складывать.
    """
    chosen, overlaps = [], []
    for item in items:
        value = (item.get("spend") or {}).get(field)
        path = (item.get("object") or {}).get("path") or ""
        if not value or not path:
            continue
        clash = next((c for c in chosen if paths_overlap(path, c["path"])), None)
        if clash:
            overlaps.append({"kept": clash["path"], "skipped": path, "reason": "объекты пересекаются"})
            continue
        chosen.append({"path": path, "value": value, "finding_id": item.get("id"),
                       "type": (item.get("object") or {}).get("type")})

    by_type: dict[str, float] = {}
    for item in chosen:
        by_type[item["type"]] = round(by_type.get(item["type"], 0) + item["value"], 2)
    types = {item["type"] for item in chosen}
    mixed = len(types) > 1
    return {
        "sum": None if mixed else (round(sum(i["value"] for i in chosen), 2) if chosen else None),
        "sum_by_type": by_type,
        "objects": chosen,
        "overlaps": overlaps,
        "overlap_free": not overlaps,
        "summed": bool(chosen) and not mixed,
        "mixed_levels": sorted(types) if mixed else [],
        "note": ("объекты разных типов описывают разные разрезы одного бюджета: "
                 "общая сумма не считается" if mixed else None),
    }


def _potential_saving(recommendations: list[dict]) -> dict:
    items, overlaps = [], []
    for rec in recommendations:
        effect = rec.get("expected_effect") or {}
        if effect.get("type") != "estimate" or not effect.get("value"):
            continue
        path = (rec.get("object") or {}).get("path") or ""
        clash = next((c for c in items if paths_overlap(path, c["path"])), None)
        if clash:
            overlaps.append({"kept": clash["path"], "skipped": path})
            continue
        items.append({"path": path, "value": effect["value"], "recommendation_id": rec["id"],
                      "unit": effect.get("unit"), "method": effect.get("method"),
                      "type": (rec.get("object") or {}).get("type")})
    types = {item["type"] for item in items}
    mixed = len(types) > 1
    by_type: dict[str, float] = {}
    for item in items:
        by_type[item["type"]] = round(by_type.get(item["type"], 0) + item["value"], 2)
    return {
        "value": None if mixed else (round(sum(i["value"] for i in items), 2) if items else None),
        "sum_by_type": by_type,
        "unit": "RUB/период",
        "conditional": True,
        "method": "сумма расходов объектов из оценок рекомендаций; это оценка, а не гарантия экономии",
        "items": items,
        "overlaps": overlaps,
        "overlap_free": not overlaps,
        "mixed_levels": sorted(types) if mixed else [],
    }


def _mode_status(session, collected, dq_result, segments, strategy, previous, experiments,
                 monitor_reason: str | None = None) -> dict:
    have = {s["action"] for s in collected["sources"] if s["status"] in ("ok", "warning")}
    blocked_all = bool(dq_result["gates"].get("all"))
    status = {}
    for mode, requires in MODE_REQUIREMENTS.items():
        missing = [action for action in requires if action not in have]
        entry = {"available": True, "reason": None, "missing_sources": missing,
                 "selected": mode in (session.get("modes") or [])}
        if blocked_all:
            entry.update({"available": False, "reason": "заблокирована идентификация кампании или кабинета"})
        elif missing:
            entry.update({"available": False,
                          "reason": "нет источников: " + ", ".join(missing)})
        if mode == "semantic":
            product = session.get("product") or {}
            if not product.get("what"):
                entry["degraded"] = True
                entry["reason"] = (entry["reason"] + "; " if entry["reason"] else "") + \
                    "нет описания продукта — минус-фразы не предлагаются автоматически"
        if mode == "rsy":
            from .extract import network_enabled
            if not network_enabled(strategy) and not (segments.get("placements") or {}).get("rows"):
                entry.update({"available": False,
                              "reason": "; ".join(filter(None, [entry["reason"],
                                                                "кампания не работает в сетях"]))})
        if mode == "funnel" and "metrika_direct_cpa" not in have:
            entry.update({"available": False,
                          "reason": "; ".join(filter(None, [entry["reason"],
                                                            "нет данных Метрики для этапов воронки"]))})
        if mode == "monitor" and not previous:
            entry.update({"available": False,
                          "reason": "; ".join(filter(None, [entry["reason"],
                                                            monitor_reason
                                                            or "нет предыдущего снимка аудита"]))})
        if mode == "experiment" and not experiments:
            entry.update({"available": False,
                          "reason": "; ".join(filter(None, [entry["reason"],
                                                            "нет гипотез для проверки"]))})
        if mode == "deep":
            deep_sources = ("stats_search_queries", "keywords_list", "negatives_audit",
                            "stats_placements", "stats_adgroups", "metrika_direct_cpa",
                            "campaign_journal")
            absent = [action for action in deep_sources if action not in have]
            if absent:
                entry["degraded"] = True
                entry["reason"] = "часть блоков недоступна: " + ", ".join(absent)
        status[mode] = entry
    return status


def build(session_dir: Path, *, previous_audit: Path | None = None,
          today: date | None = None) -> dict:
    session_dir = Path(session_dir)
    session = load_session(session_dir)
    collected = collect_sources(session_dir, session)
    source_map = collected["data"]

    campaign = extract.campaign_facts(source_map)
    strategy = extract.strategy_flags(campaign)
    metrics_block = extract.compare_block(source_map)
    goal = extract.get_goal(source_map, session)
    attribution = extract.get_attribution(source_map, session)
    segments = extract.build_segments(source_map)
    history = extract.history_block(source_map, collected["sources"])
    negatives_block = extract.negatives_block(source_map)
    thresholds = extract.default_thresholds(session.get("thresholds"), metrics_block, goal)

    periods = period_bounds(session)
    if not periods["a"].get("from") or not periods["b"].get("to"):
        raise SessionError("session.json: периоды A и B обязательны")

    dq_result = dq_mod.run(
        session=session, collected=collected, metrics_block=metrics_block,
        campaign=campaign, goal=goal, attribution=attribution, segments=segments,
        thresholds=thresholds, today=today,
    )

    limitations = []
    for check in dq_result["checks"]:
        if check["status"] in ("blocked", "unavailable"):
            limitations.append({
                "id": f"L-DQ-{check['id']}",
                "text": f"{check['title']}: {check['detail']}",
                "detail": check["status"],
                "group": ",".join(check["blocks"]) or None,
            })

    previous = None
    if previous_audit:
        previous = json.loads(Path(previous_audit).read_text(encoding="utf-8"))
    elif session.get("previous_audit"):
        candidate = Path(session["previous_audit"])
        if not candidate.is_absolute():
            candidate = session_dir / candidate
        if candidate.exists():
            previous = json.loads(candidate.read_text(encoding="utf-8"))

    account_login = session.get("account_login")
    if not account_login:
        accounts = {str(r.get("_account")) for s in collected["sources"]
                    for r in (s.get("rows") or []) if isinstance(r, dict) and r.get("_account")}
        account_login = sorted(accounts)[0] if len(accounts) == 1 else None

    monitor_compatibility = None
    monitor_reason = None
    if previous is not None:
        from . import monitor as monitor_mod
        current_minimal = {
            "scope": {"account_login": account_login, "account": session.get("account"),
                      "campaign_id": session["campaign_id"]},
            "goal": goal,
            "attribution": attribution,
        }
        monitor_compatibility = monitor_mod.compatibility(previous, current_minimal)
        if not monitor_compatibility["compatible"]:
            monitor_reason = "предыдущий снимок несовместим: " + monitor_compatibility["summary"]
            limitations.append({
                "id": "L-MON-01",
                "text": "Предыдущий снимок несовместим с текущим аудитом: сравнение не выполнялось. "
                        + monitor_reason,
                "detail": "monitor",
                "group": "monitor",
            })
            previous = None

    ctx = {
        "session": session,
        "collected": collected,
        "campaign": campaign,
        "strategy": strategy,
        "metrics": metrics_block,
        "periods": periods,
        "goal": goal,
        "attribution": attribution,
        "segments": segments,
        "history": history,
        "negatives": negatives_block,
        "thresholds": thresholds,
        "dq": dq_result,
        "limitations": limitations,
        "previous_audit": previous,
        "source_rows": {action: rows_of(source_map, action) for action in
                        ("moderation_check", "ads_list", "keywords_list", "campaigns_get")},
    }

    result = rules.run(ctx)

    # Классификация запросов сохраняется в audit.json: отчёты не считают её заново.
    from .negatives import classify_intent
    product = session.get("product") or {}
    for row in (segments.get("search_queries") or {}).get("rows") or []:
        intent = classify_intent(row.get("key") or "", product_terms=product)
        row["intent"] = intent["intent"]
        row["intent_reason"] = intent["reason"]

    at_risk = _dedupe_spend(result["findings"], "at_risk")
    actual = _dedupe_spend(result["findings"], "actual")
    saving = _potential_saving(result["recommendations"])

    created = now_iso()
    audit_id = f"audit-{account_login or session['account']}-{session['campaign_id']}-" \
               f"{datetime.now().strftime('%Y%m%dT%H%M%S')}"

    experiments = ctx.get("experiments") or []

    audit = {
        "schema_version": SCHEMA_VERSION,
        "audit_id": audit_id,
        "created_at": created,
        "mode": session.get("modes"),
        "scope": {
            "account": session.get("account"),
            "account_login": account_login,
            "campaign_id": str(session["campaign_id"]),
            "campaign_name": campaign.get("name"),
            "campaign_type": campaign.get("type"),
            "campaign_state": campaign.get("state"),
            "account_mismatch": account_mismatch(source_map, account_login),
        },
        "campaign": {
            "id": str(session["campaign_id"]),
            "name": campaign.get("name"),
            "type": campaign.get("type"),
            "state": campaign.get("state"),
            "status": campaign.get("status"),
            "start_date": iso_date(campaign.get("start_date")),
            "end_date": iso_date(campaign.get("end_date")),
            "timezone": campaign.get("timezone"),
            "counters": [fmt_id(c) for c in (campaign.get("counters") or [])],
            "attribution": campaign.get("attribution"),
            "strategy": strategy,
            "negatives_raw": campaign.get("negatives_raw"),
        },
        "periods": {
            "a": periods["a"], "b": periods["b"],
            "length_days": dq_result["periods"].get("length_a"),
            "valid": dq_result["periods"].get("valid"),
            "timezone": campaign.get("timezone") or "Europe/Moscow",
            "conversion_delay_days": thresholds.get("conversion_delay_days"),
            "excludes_today": True,
        },
        "goal": goal,
        "attribution": attribution,
        "thresholds": thresholds,
        "data_sources": [provenance_record(item) for item in collected["sources"]],
        "data_quality": {
            "status": dq_result["status"],
            "checks": dq_result["checks"],
            "counts": dq_result["counts"],
            "gates": dq_result["gates"],
            "problems": collected["problems"],
        },
        "metrics": {
            "primary_source": "stats_compare",
            "units": {"cost": "RUB_VAT", "ctr": "percent", "cr": "percent", "cpa": "RUB"},
            "a": metrics_block["a"],
            "b": metrics_block["b"],
            "comparison": metrics_block["comparison"],
            "tool_notes": {key: value for key, value in (metrics_block.get("rows") or {}).items()},
        },
        "segments": {
            name: {key: value for key, value in block.items() if key != "rows"}
            for name, block in segments.items()
        },
        "segment_rows": {
            name: (block.get("rows") or [])[:500] if isinstance(block, dict) else []
            for name, block in segments.items() if name != "metrika"
        },
        "segments_checked": ctx.get("segments_checked") or [],
        "findings": result["findings"],
        "recommendations": result["recommendations"],
        "hypotheses": result["hypotheses"],
        "growth": result["growth"],
        "spend_accounting": {
            "actual_spend_rub": metrics_block["a"].get("cost"),
            "candidate_actual_spend": actual,
            "at_risk_spend": at_risk,
            "potential_saving": saving,
            "note": "фактический расход, расход под риском и потенциальная экономия — "
                    "разные величины; экономия — оценка, а не гарантия",
        },
        "history": {
            "available": history.get("available"),
            "changes": history.get("changes") or [],
            "notes": history.get("notes"),
            "changes_check": history.get("changes_check") or [],
            "note": history.get("note"),
        },
        "monitor": ctx.get("monitor"),
        "funnel": ctx.get("funnel"),
        "experiments": experiments,
        "mode_status": _mode_status(session, collected, dq_result, segments, strategy,
                                    previous, experiments, monitor_reason),
        "monitor_compatibility": monitor_compatibility,
        "limitations": result["limitations"],
        "validation": None,
    }

    audit["validation"] = validate_mod.validate_doc(audit)
    return audit


def write(audit: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
