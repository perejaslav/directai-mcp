#!/usr/bin/env python3
"""Проверка audit.json перед публикацией (ТЗ §13).

PASS — отчёт можно публиковать как подтверждённый.
WARN — публикация допустима с оговорками, они попадают в отчёт.
FAIL — публиковать как подтверждённый отчёт запрещено; допускается только
диагностический отчёт с объяснением проблемы.
"""
from __future__ import annotations

import math
from datetime import date

from .model import (CLASSIFICATIONS, CONFIDENCES, DQ_STATES, PRIORITIES,
                    SCHEMA_VERSION, iso_date, period_days, paths_overlap)
from .rawio import READ_ONLY_ACTIONS

REQUIRED_TOP = ("schema_version", "audit_id", "created_at", "scope", "campaign", "periods",
                "goal", "attribution", "data_sources", "data_quality", "metrics",
                "segments", "findings", "recommendations", "limitations")

REQUIRED_REC_FIELDS = ("id", "rule_id", "finding_id", "priority", "confidence", "problem",
                       "object", "evidence", "proposed_change", "expected_effect", "risk",
                       "apply", "verify")

KNOWN_RULES = {
    "TRK-00", "TRK-01", "TRK-02", "TRK-03", "TRK-04", "CMP-01",
    "MOD-01", "EXT-01", "STR-02", "STR-03",
    "DYN-00", "DYN-01", "STAT-01",
    "SEG-00", "SEG-01", "SQ-01", "KW-01", "KW-02", "NEG-01", "NEG-02", "NEG-03",
    "RSY-01", "RSY-02", "FUN-01", "FUN-02", "HIS-01", "MON-01",
}

FORBIDDEN_ACTIONS = {
    "plan_write", "apply_write", "campaigns_create", "campaigns_update", "campaigns_state",
    "adgroups_create", "adgroups_update", "ads_create", "ads_update", "ads_state",
    "keywords_add", "keywords_update", "keywords_state", "negatives_set",
    "bid_modifiers_set", "bids_set", "extensions_create",
}

# Реестр действий DirectAI MCP v1.17.2 (проверен через describe_action).
# Нужен, чтобы рекомендация не ссылалась на выдуманный инструмент.
KNOWN_TOOLS = {
    "accounts_balance", "accounts_check", "accounts_discover", "adgroups_create",
    "adgroups_list", "adgroups_update", "ads_create", "ads_list", "ads_state",
    "ads_update", "audience_segment_delete", "audience_segment_from_file",
    "audience_segment_get", "audience_segments_list", "audience_target_add",
    "audience_target_state", "audience_targets_list", "audiences_list",
    "bid_modifiers_get", "bid_modifiers_set", "bids_get", "bids_set",
    "businesses_get", "campaign_journal", "campaign_journal_note",
    "campaign_journal_snapshot", "campaigns_create", "campaigns_get",
    "campaigns_list", "campaigns_state", "campaigns_update", "changes_check",
    "counter_check", "dictionaries_get", "dynamic_feed_targets_get",
    "dynamic_targets_get", "extensions_create", "extensions_list", "feeds_get",
    "keyword_bids_forecast", "keywords_add", "keywords_list", "keywords_state",
    "keywords_update", "metrika_bytime", "metrika_direct_cpa", "metrika_goal_create",
    "metrika_goal_delete", "metrika_goal_update", "metrika_goals_list",
    "metrika_goals_report", "metrika_traffic", "moderation_check",
    "negatives_audit", "negatives_set", "phrases_forecast",
    "retargeting_list_create", "retargeting_list_delete", "retargeting_list_update",
    "retargeting_lists_list", "smart_targets_get", "stats_adgroups", "stats_ads",
    "stats_audiences", "stats_campaigns", "stats_compare", "stats_custom",
    "stats_devices", "stats_keywords", "stats_placements", "stats_regions",
    "stats_search_queries", "stats_summary", "strategies_get", "turbopages_get",
    "webmaster_hosts", "webmaster_query", "webmaster_summary", "wordstat_dynamics",
    "wordstat_regions", "wordstat_regions_tree", "wordstat_top",
}


def _issue(level: str, code: str, message: str, path: str = "") -> dict:
    return {"level": level, "code": code, "message": message, "path": path}


# Блокировки, при которых нельзя подтвердить сам аудит: неясно, что за кампания
# и кабинет, либо денежные данные заведомо испорчены. Остальные блокировки
# ограничивают отдельные разделы, но не отменяют техническую часть.
CRITICAL_DQ = {"DQ-SRC-02", "DQ-ACC-01", "DQ-ACC-02", "DQ-CMP-01", "DQ-CMP-02",
               "DQ-NEG-01", "DQ-UNI-01"}


def _is_critical_check(check: dict) -> bool:
    if "all" in (check.get("blocks") or []):
        return True
    return check.get("id") in CRITICAL_DQ


def _approx(left, right, tolerance=1e-6) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= tolerance * max(1.0, abs(right))


def validate_doc(audit: dict) -> dict:
    issues: list[dict] = []

    def err(code, message, path=""):
        issues.append(_issue("error", code, message, path))

    def warn(code, message, path=""):
        issues.append(_issue("warning", code, message, path))

    # --- структура --------------------------------------------------------
    if not isinstance(audit, dict):
        return {"status": "FAIL", "issues": [_issue("error", "SCHEMA-00", "audit.json не объект")],
                "summary": {"errors": 1, "warnings": 0}}
    for key in REQUIRED_TOP:
        if key not in audit:
            err("SCHEMA-01", f"нет обязательного блока {key}", key)
    if audit.get("schema_version") != SCHEMA_VERSION:
        err("SCHEMA-02", f"schema_version {audit.get('schema_version')} вместо {SCHEMA_VERSION}")

    # --- идентификация ----------------------------------------------------
    scope = audit.get("scope") or {}
    campaign = audit.get("campaign") or {}
    if not scope.get("campaign_id"):
        err("ID-01", "не указан ID кампании", "scope.campaign_id")
    elif not isinstance(scope.get("campaign_id"), str):
        err("ID-02", "ID кампании должен быть строкой", "scope.campaign_id")
    if campaign.get("id") and scope.get("campaign_id") and str(campaign["id"]) != str(scope["campaign_id"]):
        err("ID-03", "ID кампании в scope и campaign различаются", "campaign.id")
    if scope.get("account_mismatch"):
        err("ID-04", "строки относятся к разным кабинетам: " + "; ".join(scope["account_mismatch"]),
            "scope.account_mismatch")
    for source in audit.get("data_sources") or []:
        action = str(source.get("action") or "").strip()
        if not action:
            err("SAFE-02", "источник без имени действия", "data_sources")
        elif action in FORBIDDEN_ACTIONS:
            err("SAFE-01", f"источник {action} изменяет рекламу", "data_sources")
        elif action not in READ_ONLY_ACTIONS:
            err("SAFE-03", f"незарегистрированное действие {action}: схема не проверена",
                "data_sources")

    # --- даты -------------------------------------------------------------
    periods = audit.get("periods") or {}
    pa, pb = periods.get("a") or {}, periods.get("b") or {}
    for label, period in (("a", pa), ("b", pb)):
        for edge in ("from", "to"):
            if not iso_date(period.get(edge)):
                err("DATE-01", f"период {label}.{edge}: неверная дата {period.get(edge)!r}", f"periods.{label}.{edge}")
    if iso_date(pa.get("from")) and iso_date(pb.get("to")):
        if pb["to"] >= pa["from"]:
            err("DATE-02", "период B не предшествует периоду A", "periods")
    len_a, len_b = period_days(pa), period_days(pb)
    if len_a and len_b and len_a != len_b:
        warn("DATE-03", f"длины периодов различаются: {len_a} и {len_b} дн.", "periods")
    if iso_date(pa.get("to")) and iso_date(pa["to"]) >= date.today().isoformat():
        warn("DATE-04", "период A включает сегодняшний день или будущее", "periods.a.to")

    # --- цель и атрибуция --------------------------------------------------
    goal = audit.get("goal") or {}
    limitations_text = " ".join(str(item.get("text", "")) for item in audit.get("limitations") or [])
    if not goal.get("configured"):
        if "цель" not in limitations_text.lower():
            err("GOAL-01", "цель не задана, но ограничение не зафиксировано", "goal")
        cpa_recs = [r for r in audit.get("recommendations") or [] if "CPA" in str(r.get("proposed_change", ""))]
        if cpa_recs:
            err("GOAL-02", "рекомендации по CPA при отсутствии основной цели", "recommendations")
    attribution = audit.get("attribution") or {}
    if not (attribution.get("effective") or attribution.get("requested")):
        if "атрибуц" not in limitations_text.lower():
            err("ATTR-01", "атрибуция не указана и не зафиксирована как ограничение", "attribution")

    # --- числовые типы и единицы ------------------------------------------
    metrics = audit.get("metrics") or {}
    units = metrics.get("units") or {}
    for unit in ("cost", "ctr", "cr", "cpa"):
        if unit not in units:
            warn("UNIT-01", f"не указана единица измерения {unit}", "metrics.units")
    for period_key in ("a", "b"):
        for name, value in (metrics.get(period_key) or {}).items():
            if value is not None and not isinstance(value, (int, float)):
                err("NUM-01", f"metrics.{period_key}.{name} не число: {value!r}",
                    f"metrics.{period_key}.{name}")
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                err("NUM-02", f"metrics.{period_key}.{name} не конечное число",
                    f"metrics.{period_key}.{name}")

    # --- изменения ---------------------------------------------------------
    comparison = metrics.get("comparison") or {}
    a, b = metrics.get("a") or {}, metrics.get("b") or {}
    for key, item in comparison.items():
        if not isinstance(item, dict):
            continue
        value_a, value_b = item.get("a"), item.get("b")
        if value_a != a.get(key) or value_b != b.get(key):
            err("CMP-01", f"{key}: значения в сравнении не совпадают с исходными", f"metrics.comparison.{key}")
        if value_a is None or value_b is None:
            continue
        expected_abs = value_a - value_b
        if item.get("abs") is not None and not _approx(item["abs"], expected_abs):
            err("CMP-02", f"{key}: Δ ≠ A−B ({item.get('abs')} против {expected_abs})",
                f"metrics.comparison.{key}")
        if value_b == 0:
            if item.get("pct") is not None:
                err("CMP-03", f"{key}: относительное изменение при B=0", f"metrics.comparison.{key}")
            if not item.get("base_zero"):
                err("CMP-04", f"{key}: не отмечена нулевая база", f"metrics.comparison.{key}")
        elif item.get("pct") is not None:
            expected_pct = (value_a - value_b) / value_b * 100
            if not _approx(item["pct"], expected_pct, 1e-6):
                err("CMP-05", f"{key}: Δ% ≠ (A−B)/B×100", f"metrics.comparison.{key}")
        if item.get("kind") == "rate" and item.get("abs") is not None:
            if not _approx(item.get("pp"), item.get("abs")):
                err("CMP-06", f"{key}: процентные пункты не совпадают с Δ", f"metrics.comparison.{key}")
        if item.get("pct") is not None and abs(item["pct"]) > 100000 and not item.get("note"):
            warn("CMP-07", f"{key}: экстремальное относительное изменение без пояснения",
                 f"metrics.comparison.{key}")

    # --- сегментный CPA ----------------------------------------------------
    segments_checked = audit.get("segments_checked") or []
    incompatible = {item["segment"] for item in segments_checked if not item.get("comparable")}
    for finding in audit.get("findings") or []:
        rule = finding.get("rule_id")
        if rule and rule not in KNOWN_RULES:
            warn("RULE-01", f"неизвестное правило {rule}", finding.get("id", ""))
        if finding.get("classification") not in CLASSIFICATIONS:
            err("CLASS-01", f"{finding.get('id')}: неверная классификация",
                finding.get("id", ""))
        if finding.get("priority") not in PRIORITIES:
            err("PRIO-01", f"{finding.get('id')}: неверный приоритет", finding.get("id", ""))
        if finding.get("confidence") not in CONFIDENCES:
            err("CONF-01", f"{finding.get('id')}: неверная уверенность", finding.get("id", ""))
        if not finding.get("evidence"):
            err("EVID-01", f"{finding.get('id')}: нет доказательств", finding.get("id", ""))
        if rule == "RSY-01":
            metrics_f = finding.get("metrics") or {}
            threshold = (finding.get("threshold") or {}).get("value")
            if threshold is None or metrics_f.get("cost") is None or metrics_f["cost"] < threshold:
                err("THR-01", f"{finding.get('id')}: RSY-01 при расходе ниже порога",
                    finding.get("id", ""))
        if rule == "RSY-02":
            metrics_f = finding.get("metrics") or {}
            threshold = (finding.get("threshold") or {}).get("value")
            if threshold is not None and metrics_f.get("cost") is not None and metrics_f["cost"] >= threshold:
                err("THR-02", f"{finding.get('id')}: RSY-02 при расходе выше порога",
                    finding.get("id", ""))
        if rule == "SEG-01" and not segments_checked:
            err("SEG-02", f"{finding.get('id')}: сегментный CPA без проверки сопоставимости",
                finding.get("id", ""))

    # --- рекомендации ------------------------------------------------------
    finding_ids = {f.get("id") for f in audit.get("findings") or []}
    for rec in audit.get("recommendations") or []:
        for field in REQUIRED_REC_FIELDS:
            if field not in rec:
                err("REC-01", f"{rec.get('id')}: нет поля {field}", rec.get("id", ""))
        if rec.get("finding_id") not in finding_ids:
            err("REC-02", f"{rec.get('id')}: ссылка на несуществующую находку", rec.get("id", ""))
        if not rec.get("evidence"):
            err("REC-03", f"{rec.get('id')}: нет доказательств", rec.get("id", ""))
        effect = rec.get("expected_effect") or {}
        if effect.get("type") in (None, "", "promise"):
            err("EFF-01", f"{rec.get('id')}: недопустимый тип ожидаемого эффекта", rec.get("id", ""))
        if effect.get("value") is not None and not effect.get("method"):
            err("EFF-02", f"{rec.get('id')}: численная оценка без метода", rec.get("id", ""))
        if effect.get("value") is not None and effect.get("type") != "estimate":
            err("EFF-03", f"{rec.get('id')}: число только для типа estimate", rec.get("id", ""))
        apply_block = rec.get("apply") or {}
        if apply_block.get("method") not in ("tool", "manual"):
            err("APPLY-01", f"{rec.get('id')}: не указан способ выполнения", rec.get("id", ""))
        if apply_block.get("method") == "tool":
            tool = apply_block.get("tool")
            if not tool:
                err("APPLY-03", f"{rec.get('id')}: метод tool без имени инструмента", rec.get("id", ""))
            elif tool not in KNOWN_TOOLS:
                err("APPLY-04", f"{rec.get('id')}: инструмент {tool} отсутствует в реестре DirectAI",
                    rec.get("id", ""))
        verify = rec.get("verify") or {}
        for field in ("baseline_period", "control_metrics", "expected_direction", "success", "failure"):
            if not verify.get(field):
                err("VERIFY-01", f"{rec.get('id')}: в проверке нет {field}", rec.get("id", ""))

    # --- деньги ------------------------------------------------------------
    accounting = audit.get("spend_accounting") or {}
    at_risk = accounting.get("at_risk_spend") or {}
    if not at_risk.get("overlap_free", True):
        err("MONEY-01", "расходы суммируются по пересекающимся объектам", "spend_accounting")
    saving = accounting.get("potential_saving") or {}
    if saving.get("value") is not None and not saving.get("conditional"):
        err("MONEY-02", "потенциальная экономия подана как гарантия", "spend_accounting")
    for left in (at_risk.get("objects") or []):
        for right in (at_risk.get("objects") or []):
            if left["path"] != right["path"] and paths_overlap(left["path"], right["path"]):
                err("MONEY-03", f"пересечение объектов: {left['path']} и {right['path']}",
                    "spend_accounting")

    # --- качество данных ---------------------------------------------------
    dq = audit.get("data_quality") or {}
    checks = dq.get("checks") or []
    for check in checks:
        if check.get("status") not in DQ_STATES:
            err("DQ-01", f"{check.get('id')}: недопустимый статус {check.get('status')}", check.get("id", ""))
    blocked_checks = [c for c in checks if c.get("status") == "blocked"]
    critical = [c for c in blocked_checks if _is_critical_check(c)]
    section = [c for c in blocked_checks if not _is_critical_check(c)]

    if blocked_checks and dq.get("status") == "passed":
        err("DQ-02", "есть блокирующие проверки, но статус passed", "data_quality.status")
    if dq.get("status") == "blocked" and not blocked_checks:
        err("DQ-05", "статус blocked не подтверждён ни одной проверкой", "data_quality.status")
    if dq.get("status") == "passed" and any(c.get("status") in ("warning", "unavailable") for c in checks):
        err("DQ-06", "есть предупреждения или пропуски, но статус passed", "data_quality.status")
    if critical:
        err("DQ-07", "критические блокировки: " +
            ", ".join(f"{c.get('id')} ({c.get('title')})" for c in critical), "data_quality")
    elif section:
        warn("DQ-03", "разделы ограничены блокировками: " +
             ", ".join(f"{c.get('id')}" for c in section), "data_quality")
    if dq.get("status") == "warning" and not any(c.get("status") == "warning" for c in checks):
        warn("DQ-08", "статус warning не подтверждён проверками", "data_quality.status")

    # --- режимы ------------------------------------------------------------
    for mode, status in (audit.get("mode_status") or {}).items():
        if status.get("available") and status.get("missing_sources"):
            err("MODE-01", f"режим {mode} доступен при отсутствии источников", f"mode_status.{mode}")

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    return {
        "status": "FAIL" if errors else ("WARN" if warnings else "PASS"),
        "issues": issues,
        "summary": {"errors": len(errors), "warnings": len(warnings)},
    }
