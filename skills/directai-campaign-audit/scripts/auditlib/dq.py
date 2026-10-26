#!/usr/bin/env python3
"""Проверка качества данных до аналитики (ТЗ §5.5).

Каждая проверка возвращает один из статусов: passed / warning / blocked /
unavailable / not_applicable. `unavailable` и `warning` не считаются успехом.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .model import iso_date, period_days
from .rawio import first, source_context


def _check(check_id, title, status, detail, *, blocks=None, severity=None):
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "detail": detail,
        "blocks": list(blocks or []),
        "severity": severity or ("high" if status == "blocked" else "medium" if status == "warning" else "low"),
    }


def _period_from_context(context: str):
    """Даты из шапки: '09.10.2026–22.10.2026' или 'A: 2026-09-26, B: 2026-09-12'."""
    text = context or ""
    dotted = re.search(r"(\d{2})\.(\d{2})\.(\d{4})[–-](\d{2})\.(\d{2})\.(\d{4})", text)
    if dotted:
        d1, m1, y1, d2, m2, y2 = dotted.groups()
        return {"from": f"{y1}-{m1}-{d1}", "to": f"{y2}-{m2}-{d2}", "partial": False}
    pair = re.search(r"A:\s*(\d{4}-\d{2}-\d{2}).{0,12}?B:\s*(\d{4}-\d{2}-\d{2})", text)
    if pair:
        return {"from": pair.group(1), "to": None, "b_from": pair.group(2), "partial": True}
    single = re.search(r"(\d{4}-\d{2}-\d{2})[–-](\d{4}-\d{2}-\d{2})", text)
    if single:
        return {"from": single.group(1), "to": single.group(2), "partial": False}
    return None


def run(*, session, collected, metrics_block, campaign, goal, attribution,
        segments, thresholds, today: date | None = None) -> dict:
    today = today or date.today()
    checks: list[dict] = []
    source_map = collected["data"]
    sources = collected["sources"]
    periods = session.get("periods") or {}
    a, b = periods.get("a") or {}, periods.get("b") or {}

    # ---- источники -------------------------------------------------------
    have = {item["action"] for item in sources if item["status"] in ("ok", "warning")}
    declared = {item["action"] for item in sources}

    if "stats_compare" not in declared:
        checks.append(_check("DQ-SRC-01", "Основной источник статистики",
                             "blocked", "stats_compare не объявлен — сравнение периодов невозможно",
                             blocks=["dynamics", "cpa", "segments", "growth", "funnel", "experiment"]))
    elif "stats_compare" not in have:
        checks.append(_check("DQ-SRC-01", "Основной источник статистики",
                             "blocked", "stats_compare объявлен, но данные не получены",
                             blocks=["dynamics", "cpa", "segments", "growth", "funnel", "experiment"]))
    else:
        checks.append(_check("DQ-SRC-01", "Основной источник статистики", "passed",
                             "stats_compare получен"))

    if "campaigns_get" not in have:
        checks.append(_check("DQ-SRC-02", "Настройки кампании",
                             "blocked", "campaigns_get не получен — идентификация кампании не подтверждена",
                             blocks=["identity", "cpa", "segments", "semantic", "rsy", "growth"]))
    else:
        checks.append(_check("DQ-SRC-02", "Настройки кампании", "passed", "campaigns_get получен"))

    broken = [s for s in sources if s["status"] in ("blocked", "unavailable", "error")]
    if broken:
        detail = "; ".join(
            f"{s['action'] or s['file']}: {s['status']}"
            + (f" (код {(s.get('error') or {}).get('code')})" if (s.get("error") or {}).get("code") else "")
            for s in broken)
        checks.append(_check("DQ-SRC-03", "Доступность источников", "warning",
                             f"часть источников не прочитана: {detail}"))
    else:
        checks.append(_check("DQ-SRC-03", "Доступность источников", "passed",
                             "все объявленные источники прочитаны"))

    rate_limited = []
    api_errors = []
    for item in sources:
        error = item.get("error") or {}
        warning = str(item.get("tool_warning") or "")
        text = f"{error.get('message') or ''} {warning}"
        code = error.get("code")
        if code == 56 or "error 56" in text or "Превышен лимит запросов" in text:
            rate_limited.append(f"{item['action'] or item['file']} (код {code or 56})")
        elif code or warning.strip():
            api_errors.append(f"{item['action'] or item['file']} (код {code or '—'}): "
                              f"{(error.get('message') or warning).strip()[:160]}")
    if rate_limited:
        checks.append(_check(
            "DQ-API-01", "Лимит запросов API", "blocked",
            "отказ по лимиту Reports API: " + ", ".join(sorted(set(rate_limited))) +
            ". Лимит — 1 запрос / 10 секунд на кабинет; пустой ответ при такой ошибке "
            "не доказывает отсутствие данных",
            blocks=["dynamics", "cpa", "segments", "rsy", "semantic", "growth"]))
    elif api_errors:
        checks.append(_check("DQ-API-01", "Лимит запросов API", "warning",
                             "ошибки API: " + "; ".join(sorted(set(api_errors)))))
    else:
        checks.append(_check("DQ-API-01", "Лимит запросов API", "passed", "ошибок API нет"))

    other_warnings = [item for item in sources
                      if str(item.get("tool_warning") or "").strip()
                      and not (item.get("error") or {}).get("code")]
    for item in other_warnings:
        if any(item["action"] in entry for entry in rate_limited):
            continue
        checks.append(_check("DQ-API-02", "Предупреждение API", "warning",
                             f"{item['action']}: {str(item['tool_warning']).strip()[:200]}",
                             blocks=["segments"] if str(item["action"]).startswith("stats_") else []))
    if not other_warnings:
        checks.append(_check("DQ-API-02", "Предупреждение API", "passed", "предупреждений нет"))

    # ---- кабинет и кампания ---------------------------------------------
    mismatch = [p for p in collected.get("problems", []) if "кабинет" in p.lower()]
    login = session.get("account_login")
    row_accounts = {str(r.get("_account")) for item in sources for r in (item.get("rows") or [])
                    if isinstance(r, dict) and r.get("_account")}
    if len(row_accounts) > 1:
        checks.append(_check("DQ-ACC-01", "Единство кабинета", "blocked",
                             f"в данных строки разных кабинетов: {', '.join(sorted(row_accounts))}",
                             blocks=["all"]))
    elif login and row_accounts and login not in row_accounts:
        checks.append(_check("DQ-ACC-01", "Единство кабинета", "blocked",
                             f"данные кабинета {', '.join(sorted(row_accounts))}, ожидался {login}",
                             blocks=["all"]))
    else:
        checks.append(_check("DQ-ACC-01", "Единство кабинета", "passed",
                             f"кабинет: {login or (sorted(row_accounts)[0] if row_accounts else 'не указан')}"))

    # Принадлежность источников: meta.account каждого ответа против задания.
    meta_aliases: dict[str, list[str]] = {}
    unproven: list[str] = []
    for item in sources:
        if item["status"] not in ("ok", "warning"):
            continue
        if not str(item.get("file") or "").lower().endswith(".json"):
            continue  # журнал и прочие текстовые источники конверта не имеют
        alias = str((item.get("meta") or {}).get("account") or "").strip()
        if alias:
            meta_aliases.setdefault(alias, []).append(item["action"] or item["file"])
        has_row_account = any(isinstance(row, dict) and row.get("_account")
                              for row in (item.get("rows") or []))
        if not alias and not has_row_account:
            unproven.append(item["action"] or item["file"])
    service = {"active", "all"}
    distinct = {alias for alias in meta_aliases if alias.lower() not in service}
    expected = {value for value in (session.get("account"), login) if value}
    if len(distinct) > 1:
        checks.append(_check(
            "DQ-ACC-02", "Принадлежность источников", "blocked",
            "источники прочитаны по разным кабинетам: " +
            ", ".join(f"{alias} ({', '.join(actions)})" for alias, actions in sorted(meta_aliases.items())),
            blocks=["all"]))
    elif distinct and expected and not (distinct & expected):
        checks.append(_check(
            "DQ-ACC-02", "Принадлежность источников", "blocked",
            f"meta.account источников: {', '.join(sorted(distinct))}; в задании: "
            f"{', '.join(sorted(expected))}", blocks=["all"]))
    elif unproven:
        checks.append(_check(
            "DQ-ACC-02", "Принадлежность источников", "blocked",
            "нет признака принадлежности (ни meta.account, ни _account): " +
            ", ".join(sorted(unproven)), blocks=["all"]))
    else:
        checks.append(_check(
            "DQ-ACC-02", "Принадлежность источников", "passed",
            "meta.account: " + (", ".join(sorted(meta_aliases)) or "нет") +
            "; строки: " + (", ".join(sorted(row_accounts)) or "нет")))

    if campaign.get("found"):
        if campaign.get("id") and str(campaign["id"]) != str(session.get("campaign_id")):
            checks.append(_check("DQ-CMP-01", "Идентификация кампании", "blocked",
                                 f"campaigns_get вернул кампанию {campaign['id']}, "
                                 f"в задании {session.get('campaign_id')}",
                                 blocks=["all"]))
        else:
            checks.append(_check("DQ-CMP-01", "Идентификация кампании", "passed",
                                 f"кампания {session.get('campaign_id')} подтверждена"))
    else:
        checks.append(_check("DQ-CMP-01", "Идентификация кампании", "blocked",
                             "настройки кампании не прочитаны — анализ остановлен",
                             blocks=["all"]))

    # Охват источников: параметры чтения должны относиться к той же кампании.
    foreign = []
    unconfirmed = []
    expected_campaign = str(session.get("campaign_id") or "")
    for item in sources:
        if item["status"] not in ("ok", "warning", "blocked"):
            continue
        declared = {str(value) for value in (item.get("params") or {}).get("campaign_ids") or []}
        if declared and expected_campaign and expected_campaign not in declared:
            foreign.append(f"{item['action'] or item['file']}: {', '.join(sorted(declared))}")
        elif str(item.get("action") or "").startswith("stats_") and not declared:
            unconfirmed.append(item["action"] or item["file"])
    if foreign:
        checks.append(_check("DQ-CMP-02", "Охват источников", "blocked",
                             "источники относятся к другим кампаниям: " + "; ".join(sorted(set(foreign))),
                             blocks=["all"]))
    elif unconfirmed:
        checks.append(_check("DQ-CMP-02", "Охват источников", "warning",
                             "охват кампании не указан в параметрах: " +
                             ", ".join(sorted(set(unconfirmed)))))
    else:
        checks.append(_check("DQ-CMP-02", "Охват источников", "passed",
                             "все источники объявлены для кампании " + expected_campaign))

    # ---- периоды ---------------------------------------------------------
    pa = {"from": iso_date(a.get("from")), "to": iso_date(a.get("to"))}
    pb = {"from": iso_date(b.get("from")), "to": iso_date(b.get("to"))}
    len_a, len_b = period_days(pa), period_days(pb)
    if not len_a or not len_b:
        checks.append(_check("DQ-PRD-01", "Периоды сравнения", "blocked",
                             "даты периодов не заданы или некорректны", blocks=["dynamics"]))
        periods_block = {"valid": False, "length_a": len_a, "length_b": len_b}
    else:
        issues = []
        if len_a != len_b:
            issues.append(f"длины периодов различаются: A {len_a} дн., B {len_b} дн.")
        if pa["from"] and pb["to"] and pb["to"] >= pa["from"]:
            issues.append("периоды пересекаются или идут не по порядку")
        if pa["to"] and iso_date(pa["to"]) >= today.isoformat():
            issues.append("период A включает сегодняшний или будущий день")
        periods_block = {"valid": not issues, "length_a": len_a, "length_b": len_b, "issues": issues}
        checks.append(_check("DQ-PRD-01", "Периоды сравнения",
                             "warning" if issues else "passed",
                             "; ".join(issues) if issues else f"A {len_a} дн., B {len_b} дн.",
                             blocks=["dynamics"] if len_a != len_b else []))

    context_period = _period_from_context(source_context(source_map, "stats_compare"))
    if context_period and pa["from"]:
        if context_period.get("partial"):
            mismatch = (context_period["from"] != pa["from"]
                        or context_period.get("b_from") != pb["from"])
            detail = (f"шапка: A с {context_period['from']}, B с {context_period.get('b_from')}; "
                      f"задание: A с {pa['from']}, B с {pb['from']}")
        else:
            mismatch = context_period != {"from": pa["from"], "to": pa["to"], "partial": False}
            detail = (f"в шапке {context_period['from']}–{context_period['to']}, "
                      f"в задании {pa['from']}–{pa['to']}")
        checks.append(_check("DQ-PRD-02", "Период в ответе инструмента",
                             "warning" if mismatch else "passed", detail))

    prev = metrics_block.get("b") or {}
    prev_known = prev.get("impressions") is not None or prev.get("cost") is not None
    if not prev_known:
        checks.append(_check("DQ-PRD-03", "База предыдущего периода", "unavailable",
                             "данные периода B не получены — сравнивать не с чем",
                             blocks=["dynamics"]))
    elif not prev.get("impressions") and not prev.get("cost"):
        checks.append(_check("DQ-PRD-03", "База предыдущего периода", "warning",
                             "в периоде B нет показов и расхода — динамика недоступна",
                             blocks=["dynamics"]))
    else:
        checks.append(_check("DQ-PRD-03", "База предыдущего периода", "passed",
                             "в периоде B есть данные"))

    # ---- показатели ------------------------------------------------------
    current = metrics_block.get("a") or {}
    missing = [name for name in ("impressions", "clicks", "cost") if current.get(name) is None]
    if missing:
        checks.append(_check("DQ-MET-01", "Обязательные показатели", "blocked",
                             f"в периоде A нет: {', '.join(missing)}",
                             blocks=["dynamics", "cpa", "segments"]))
    else:
        checks.append(_check("DQ-MET-01", "Обязательные показатели", "passed",
                             "показы, клики и расход получены"))

    if current.get("impressions") is None and current.get("clicks") is None:
        checks.append(_check("DQ-MET-02", "Наличие трафика", "unavailable",
                             "трафик не измерен: показы и клики не получены",
                             blocks=["dynamics", "cpa"]))
    elif not current.get("impressions") and not current.get("clicks"):
        checks.append(_check("DQ-MET-02", "Наличие трафика", "warning",
                             "в периоде A не было показов — анализировать нечего",
                             blocks=["dynamics", "cpa", "segments", "growth"]))
    else:
        impressions = current.get("impressions")
        clicks = current.get("clicks")
        checks.append(_check("DQ-MET-02", "Наличие трафика", "passed",
                             f"показов {impressions}, кликов {clicks}"))

    conversions_available = current.get("conversions") is not None
    if not conversions_available:
        checks.append(_check("DQ-CNV-01", "Данные о конверсиях", "unavailable",
                             "конверсии не получены — CPA и правила эффективности блокируются",
                             blocks=["cpa", "segments", "funnel", "growth"]))
    else:
        checks.append(_check("DQ-CNV-01", "Данные о конверсиях", "passed",
                             f"конверсий в периоде A: {current.get('conversions')}"))

    if goal.get("configured"):
        checks.append(_check("DQ-GOAL-01", "Основная цель", "passed",
                             f"цель {goal.get('id')} ({goal.get('label')}), источник: {goal.get('source')}"))
    else:
        checks.append(_check("DQ-GOAL-01", "Основная цель", "warning",
                             "основная цель не задана — CPA считается по всем целям, "
                             "рекомендации по CPA заблокированы",
                             blocks=["cpa", "experiment"]))

    if attribution.get("effective") or attribution.get("requested"):
        if attribution.get("requested") and attribution.get("effective") \
                and attribution["requested"] != attribution["effective"]:
            checks.append(_check("DQ-ATR-01", "Модель атрибуции", "warning",
                                 f"запрошена {attribution['requested']}, применена {attribution['effective']}",
                                 blocks=["cpa"]))
        else:
            checks.append(_check("DQ-ATR-01", "Модель атрибуции", "passed",
                                 f"{attribution.get('effective')} (источник: {attribution.get('source')})"))
    else:
        checks.append(_check("DQ-ATR-01", "Модель атрибуции", "warning",
                             "атрибуция не указана в ответе инструмента",
                             blocks=["cpa"]))

    # ---- гигиена данных ---------------------------------------------------
    stats_actions = ("stats_compare", "stats_devices", "stats_regions", "stats_keywords",
                     "stats_adgroups", "stats_ads", "stats_placements", "stats_search_queries")
    incomplete_pagination = [s for s in sources
                             if s.get("pagination_complete") is False]
    unknown_pagination = [s["action"] for s in sources
                          if s["action"] in ("campaigns_get", "adgroups_list", "ads_list", "keywords_list",
                                             "negatives_audit", "stats_placements", "stats_search_queries")
                          and s.get("pagination_complete") is None]
    if incomplete_pagination:
        blocked_actions = sorted({s["action"] for s in incomplete_pagination})
        checks.append(_check("DQ-PAG-01", "Полнота выгрузки", "warning",
                             "пагинация не завершена: " + ", ".join(blocked_actions),
                             blocks=["segments"] if any(a in stats_actions for a in blocked_actions) else []))
    elif unknown_pagination:
        checks.append(_check("DQ-PAG-01", "Полнота выгрузки", "warning",
                             "не подтверждена пагинация: " + ", ".join(sorted(set(unknown_pagination)))))
    else:
        checks.append(_check("DQ-PAG-01", "Полнота выгрузки", "passed",
                             "пагинация подтверждена или не требуется"))

    truncated = [s["action"] for s in sources if s.get("truncated") is True]
    if truncated:
        checks.append(_check("DQ-PAG-02", "Усечение выгрузки", "warning",
                             "выгрузка усечена: " + ", ".join(sorted(set(truncated))),
                             blocks=["segments"]))
    else:
        checks.append(_check("DQ-PAG-02", "Усечение выгрузки", "passed", "усечения нет"))

    duplicates = []
    for item in sources:
        rows = item.get("rows") or []
        keys = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = tuple(str(row.get(k)) for k in ("Id", "AdGroupId", "Keyword", "Query", "Placement")
                        if k in row)
            if key:
                keys.append(key)
        seen, dup = set(), set()
        for key in keys:
            (dup if key in seen else seen).add(key)
        if dup:
            duplicates.append(f"{item['action']}: {len(dup)}")
    if duplicates:
        checks.append(_check("DQ-DUP-01", "Повторяющиеся записи", "warning",
                             "дубли строк: " + "; ".join(duplicates)))
    else:
        checks.append(_check("DQ-DUP-01", "Повторяющиеся записи", "passed", "дублей не найдено"))

    # Сегменты против итога: только срезы, которые делят весь бюджет кампании.
    seg_issues = []
    partition_available = False
    for name, block in (segments or {}).items():
        if name not in ("adgroups", "devices", "regions"):
            continue
        if not isinstance(block, dict) or not block.get("available"):
            continue
        partition_available = True
        total_cost = metrics_block.get("a", {}).get("cost")
        seg_cost = (block.get("total") or {}).get("cost")
        if total_cost and seg_cost is not None:
            deviation = abs(seg_cost - total_cost) / total_cost
            if deviation > thresholds.get("tolerance_segment_sum", 0.05):
                seg_issues.append(f"{block['action']}: {seg_cost:.0f} ₽ против {total_cost:.0f} ₽ "
                                  f"({deviation * 100:.0f}%)")
    if seg_issues:
        checks.append(_check("DQ-SEG-01", "Совместимость сегментов и итога", "warning",
                             "расход сегментов расходится с итогом: " + "; ".join(seg_issues)))
    elif not partition_available:
        checks.append(_check("DQ-SEG-01", "Совместимость сегментов и итога", "not_applicable",
                             "сегментные срезы не загружены — сверять нечего"))
    else:
        checks.append(_check("DQ-SEG-01", "Совместимость сегментов и итога", "passed",
                             "сегменты согласуются с итогом в пределах допуска"))

    nulls = [name for name in ("impressions", "clicks", "cost", "conversions")
             if current.get(name) is None]
    if nulls:
        checks.append(_check("DQ-NUL-01", "Пропущенные значения", "warning",
                             f"нет значений: {', '.join(nulls)} (не заменяются нулями)"))
    else:
        checks.append(_check("DQ-NUL-01", "Пропущенные значения", "passed", "пропусков нет"))

    negative_values = [name for name in ("impressions", "clicks", "cost")
                       if isinstance(current.get(name), (int, float)) and current[name] < 0]
    if negative_values:
        checks.append(_check("DQ-NEG-01", "Отрицательные значения", "blocked",
                             f"отрицательные значения: {', '.join(negative_values)}",
                             blocks=["dynamics", "cpa"]))
    else:
        checks.append(_check("DQ-NEG-01", "Отрицательные значения", "passed", "отрицательных значений нет"))

    context = source_context(source_map, "stats_compare")
    if re.search(r"USD|EUR|доллар|евро", context, re.I):
        checks.append(_check("DQ-UNI-01", "Валюта и НДС", "blocked",
                             "в ответе указана валюта, отличная от рубля", blocks=["cpa", "dynamics"]))
    else:
        vat = "с НДС" if "с НДС" in context else ("без НДС" if "без НДС" in context else "не указан")
        checks.append(_check("DQ-UNI-01", "Валюта и НДС", "passed" if vat != "не указан" else "warning",
                             f"расход в рублях, НДС: {vat}"))

    delay = thresholds.get("conversion_delay_days")
    a_to = pa.get("to")
    if a_to:
        end = date.fromisoformat(a_to)
        if delay is None and (today - end).days < 3:
            checks.append(_check("DQ-DLY-01", "Зрелость конверсий", "warning",
                                 "период закончился менее 3 дней назад, задержка конверсий неизвестна — "
                                 "свежие заявки могут быть недоучтены",
                                 blocks=["dynamics"]))
        else:
            checks.append(_check("DQ-DLY-01", "Зрелость конверсий", "passed",
                                 f"задержка конверсий: {delay if delay is not None else 'не задана'}, "
                                 f"период закрыт {(today - end).days} дн. назад"))

    state = str(campaign.get("state") or "").upper()
    if state in ("OFF", "ARCHIVED", "SUSPENDED"):
        checks.append(_check("DQ-STATE-01", "Состояние кампании", "warning",
                             f"кампания в состоянии {state} — динамика может быть следствием остановки"))
    elif state:
        checks.append(_check("DQ-STATE-01", "Состояние кампании", "passed", f"состояние {state}"))
    else:
        checks.append(_check("DQ-STATE-01", "Состояние кампании", "unavailable",
                             "состояние кампании неизвестно"))

    # ---- итог -------------------------------------------------------------
    blocked_groups = sorted({group for check in checks for group in check["blocks"]})
    statuses = [check["status"] for check in checks]
    overall = "blocked" if "blocked" in statuses else ("warning" if "warning" in statuses
                                                       else ("unavailable" if "unavailable" in statuses else "passed"))
    return {
        "status": overall,
        "checks": checks,
        "gates": {group: True for group in blocked_groups},
        "periods": periods_block,
        "counts": {state: statuses.count(state) for state in ("passed", "warning", "blocked",
                                                              "unavailable", "not_applicable")},
    }
