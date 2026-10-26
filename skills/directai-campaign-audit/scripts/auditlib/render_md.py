#!/usr/bin/env python3
"""Markdown-отчёт из audit.json (ТЗ §11.1). Никаких собственных расчётов."""
from __future__ import annotations

from .model import (fmt_delta, fmt_id, fmt_money, fmt_number, fmt_percent, fmt_pp)

PRIORITY_RU = {"P0": "P0 — критический", "P1": "P1 — высокий", "P2": "P2 — средний", "P3": "P3 — низкий"}
CONFIDENCE_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}
CLASS_RU = {"fact": "факт", "likely_cause": "вероятная причина", "hypothesis": "гипотеза"}
DQ_RU = {"passed": "ок", "warning": "предупреждение", "blocked": "блок",
         "unavailable": "нет данных", "not_applicable": "не применимо"}
EFFECT_RU = {"technical": "технический результат", "estimate": "оценка",
             "qualitative": "качественный эффект", "none": "без численной оценки"}


def _escape(text) -> str:
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _metric_rows(audit: dict) -> list[str]:
    metrics = audit.get("metrics") or {}
    a, b = metrics.get("a") or {}, metrics.get("b") or {}
    comparison = metrics.get("comparison") or {}
    rows = [
        ("Показы", "impressions", "count"),
        ("Клики", "clicks", "count"),
        ("CTR", "ctr", "rate"),
        ("Расход", "cost", "money"),
        ("CPC", "cpc", "per_click"),
        ("Конверсии", "conversions", "count"),
        ("CR", "cr", "rate"),
        ("CPA", "cpa", "money"),
        ("Отказы", "bounce_rate", "rate"),
    ]
    out = ["| Метрика | A (свежий) | B (прошлый) | Δ | Δ% |",
           "|---|---|---|---|---|"]
    for label, key, kind in rows:
        item = comparison.get(key) or {}
        if kind == "money":
            fa, fb = fmt_money(a.get(key)), fmt_money(b.get(key))
            delta = fmt_delta(item.get("abs"), money=True) if item.get("abs") is not None else "нет данных"
        elif kind == "rate":
            fa, fb = fmt_percent(a.get(key)), fmt_percent(b.get(key))
            delta = fmt_delta(item.get("abs")) if item.get("abs") is not None else "нет данных"
        else:
            fa, fb = fmt_number(a.get(key), 2), fmt_number(b.get(key), 2)
            delta = fmt_delta(item.get("abs")) if item.get("abs") is not None else "нет данных"
        pct = "нет базы" if item.get("base_zero") else (
            fmt_percent(item.get("pct")) if item.get("pct") is not None else "нет данных")
        if kind == "rate":
            delta = fmt_pp(item.get("abs")) if item.get("abs") is not None else "нет данных"
        out.append(f"| {label} | {fa} | {fb} | {delta} | {pct} |")
    hidden = [label for label, key, _kind in rows
              if (comparison.get(key) or {}).get("display_hides_change")]
    if hidden:
        out.append("")
        out.append(f"Изменение по метрикам {', '.join(hidden)} не видно после округления "
                   "до двух знаков, но в данных оно есть. Для решения используется сырое значение.")
    return out


def _summary(audit: dict) -> list[str]:
    findings = audit.get("findings") or []
    top = sorted(findings, key=lambda f: ({"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(f.get("priority"), 9),
                                          -(f.get("metrics", {}).get("cost") or 0)))[:5]
    lines = []
    metrics = audit.get("metrics") or {}
    conv = (metrics.get("comparison") or {}).get("conversions") or {}
    if conv.get("available"):
        if conv.get("base_zero"):
            lines.append("Конверсии: в прошлом периоде их не было — сравниваются абсолютные значения.")
        else:
            cpa_cmp = (metrics.get("comparison") or {}).get("cpa") or {}
            decimals = 4 if cpa_cmp.get("display_hides_change") else 2
            lines.append(f"Конверсии {metrics['b'].get('conversions')} → {metrics['a'].get('conversions')} "
                         f"({conv.get('pct'):+.1f}%), CPA {fmt_money(metrics['b'].get('cpa'), decimals)} → "
                         f"{fmt_money(metrics['a'].get('cpa'), decimals)}.")
    for finding in top:
        if len(lines) >= 5:
            break
        lines.append(f"[{finding['priority']}] {finding['title']}")
    if not lines:
        lines.append("Значимых отклонений не найдено при текущем объёме данных.")
    return lines[:5]


def render(audit: dict) -> str:
    scope = audit.get("scope") or {}
    periods = audit.get("periods") or {}
    goal = audit.get("goal") or {}
    attribution = audit.get("attribution") or {}
    dq = audit.get("data_quality") or {}
    validation = audit.get("validation") or {}
    lines: list[str] = []
    add = lines.append

    add(f"# Аудит кампании {scope.get('campaign_id')} — {scope.get('campaign_name') or 'без названия'}")
    add("")
    if validation.get("status") == "FAIL":
        add("> **Диагностический отчёт.** Валидация вернула FAIL: цифры ниже нельзя публиковать "
            "как подтверждённые, пока не устранены ошибки из раздела 4.")
        add("")

    # 1. Паспорт
    add("## 1. Паспорт аудита")
    add("")
    add(f"- **audit_id:** `{audit.get('audit_id')}`")
    add(f"- **Создан:** {audit.get('created_at')}")
    add(f"- **Кабинет:** {scope.get('account_login') or scope.get('account')}")
    add(f"- **Кампания:** {scope.get('campaign_id')} · {scope.get('campaign_name')}")
    add(f"- **Тип / состояние:** {scope.get('campaign_type')} / {scope.get('campaign_state')}")
    add(f"- **Режимы:** {', '.join(audit.get('mode') or [])}")
    add(f"- **Период A:** {periods.get('a', {}).get('from')} — {periods.get('a', {}).get('to')}")
    add(f"- **Период B:** {periods.get('b', {}).get('from')} — {periods.get('b', {}).get('to')}")
    add(f"- **Часовой пояс:** {periods.get('timezone')}")
    add(f"- **Основная цель:** {goal.get('label') or 'не задана'} "
        f"({goal.get('id') or '—'}, источник: {goal.get('source')})")
    add(f"- **Атрибуция:** запрошена {attribution.get('requested') or '—'}, "
        f"применена {attribution.get('effective') or '—'} (источник: {attribution.get('source') or '—'})")
    add(f"- **Качество данных:** {DQ_RU.get(dq.get('status'), dq.get('status'))}")
    add(f"- **Валидация:** {validation.get('status')} "
        f"(ошибок {validation.get('summary', {}).get('errors')}, "
        f"предупреждений {validation.get('summary', {}).get('warnings')})")
    add("")

    # 2. Резюме
    add("## 2. Краткое резюме")
    add("")
    for line in _summary(audit):
        add(f"- {line}")
    add("")

    # 3. Показатели
    add("## 3. Основные показатели")
    add("")
    lines.extend(_metric_rows(audit))
    add("")
    add("Δ = A − B; Δ% = (A − B) / B × 100. Для CTR, CR и отказов в колонке Δ "
        "показаны процентные пункты: 4% → 5% это +1 п.п. и +25% одновременно.")
    add("")
    if (audit.get("metrics") or {}).get("tool_notes"):
        add("Значения Δ и Δ% инструмента не используются: они пересчитаны из A и B, "
            "потому что `stats_compare` считает относительное изменение от базы A "
            "и меняет знак у части метрик.")
        add("")

    # 4. Качество данных
    add("## 4. Качество данных")
    add("")
    add("| Проверка | Статус | Детали |")
    add("|---|---|---|")
    for check in dq.get("checks") or []:
        add(f"| {check.get('id')} {_escape(check.get('title'))} | "
            f"{DQ_RU.get(check.get('status'), check.get('status'))} | {_escape(check.get('detail'))} |")
    add("")
    counts = dq.get("counts") or {}
    add(f"Итого: ок {counts.get('passed', 0)}, предупреждений {counts.get('warning', 0)}, "
        f"блоков {counts.get('blocked', 0)}, нет данных {counts.get('unavailable', 0)}.")
    add("")
    if dq.get("status") == "blocked":
        add("> Часть анализа заблокирована. Выводы ниже ограничены доступными данными.")
        add("")

    # 5. Проблемы
    add("## 5. Ключевые проблемы")
    add("")
    findings = audit.get("findings") or []
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    for finding in sorted(findings, key=lambda f: order.get(f.get("priority"), 9)):
        add(f"### {finding.get('id')} · {finding.get('title')}")
        add("")
        add(f"- **Правило:** {finding.get('rule_id')}")
        add(f"- **Класс:** {CLASS_RU.get(finding.get('classification'), finding.get('classification'))}")
        add(f"- **Приоритет / уверенность:** {PRIORITY_RU.get(finding.get('priority'))} / "
            f"{CONFIDENCE_RU.get(finding.get('confidence'), finding.get('confidence'))}")
        obj = finding.get("object") or {}
        add(f"- **Объект:** {obj.get('type')} `{fmt_id(obj.get('id'))}`")
        if finding.get("why"):
            add(f"- **Что видно:** {_escape(finding['why'])}")
        if finding.get("alternatives"):
            add("- **Альтернативные объяснения:** " + "; ".join(map(str, finding["alternatives"])))
        if finding.get("next_step"):
            add(f"- **Следующий шаг:** {_escape(finding['next_step'])}")
        evidence = finding.get("evidence") or []
        if evidence:
            add("- **Доказательства:** " + "; ".join(
                f"{e.get('source')} → {e.get('field')} = {_escape(e.get('value'))}" for e in evidence))
        add("")

    # 6. Рост
    add("## 6. Возможности роста")
    add("")
    growth = audit.get("growth") or []
    if not growth:
        add("Возможности роста не найдены при текущем объёме данных.")
    for item in growth:
        add(f"- **{item.get('type')}** `{fmt_id((item.get('object') or {}).get('id'))}` — {item.get('why')} "
            f"Действие: {item.get('action')}. Ограничение: {item.get('promise')}.")
    add("")

    # 7. Рекомендации
    add("## 7. Рекомендации по приоритету")
    add("")
    recommendations = sorted(audit.get("recommendations") or [],
                             key=lambda r: order.get(r.get("priority"), 9))
    if not recommendations:
        add("Рекомендаций нет: либо данных недостаточно, либо отклонений не найдено.")
    for rec in recommendations:
        add(f"### {rec.get('id')} · {rec.get('priority')} · {_escape(rec.get('problem'))}")
        add("")
        add(f"- **Объект:** {rec.get('object', {}).get('type')} "
            f"`{fmt_id(rec.get('object', {}).get('id'))}`")
        add(f"- **Уверенность:** {CONFIDENCE_RU.get(rec.get('confidence'), rec.get('confidence'))}")
        add(f"- **Что сделать:** {_escape(rec.get('proposed_change'))}")
        effect = rec.get("expected_effect") or {}
        effect_text = EFFECT_RU.get(effect.get("type"), effect.get("type"))
        if effect.get("value") is not None:
            unit = (effect.get("unit") or "").replace("RUB", "₽")
            effect_text += f": {fmt_money(effect['value']) if 'RUB' in (effect.get('unit') or '') else fmt_number(effect['value'])} {unit}".strip()
        add(f"- **Ожидаемый эффект:** {effect_text}. Метод: {effect.get('method') or '—'}.")
        if effect.get("conditions"):
            add(f"- **Условия:** {'; '.join(map(str, effect['conditions']))}")
        risk = rec.get("risk") or {}
        add(f"- **Риск:** {risk.get('level')} — {'; '.join(map(str, risk.get('notes') or []))}")
        apply_block = rec.get("apply") or {}
        if apply_block.get("method") == "tool":
            add(f"- **Как выполнить:** инструмент записи `{apply_block.get('tool')}` "
                "(в этом аудите не вызывается).")
        else:
            add(f"- **Как выполнить:** вручную — {apply_block.get('manual')}")
        verify = rec.get("verify") or {}
        add(f"- **Как проверить:** база {verify.get('baseline_period', {}).get('from')}–"
            f"{verify.get('baseline_period', {}).get('to')}; метрики "
            f"{', '.join(map(str, verify.get('control_metrics') or []))}; "
            f"ожидаемое направление — {verify.get('expected_direction')}.")
        add(f"- **Успех:** {verify.get('success')}. **Неуспех:** {verify.get('failure')}.")
        add("")

    # 8. Гипотезы
    add("## 8. Непроверенные гипотезы")
    add("")
    hypotheses = audit.get("hypotheses") or []
    if not hypotheses:
        add("Гипотез нет.")
    for item in hypotheses:
        add(f"- **{item.get('id')} {_escape(item.get('title'))}** — {_escape(item.get('statement'))}")
        add(f"  - Основания: {'; '.join(map(str, item.get('basis') or []))}")
        add(f"  - Альтернативы: {'; '.join(map(str, item.get('alternatives') or []))}")
        add(f"  - Проверка: {_escape(item.get('check'))}")
    add("")

    # 9. Ограничения
    add("## 9. Ограничения анализа")
    add("")
    limitations = audit.get("limitations") or []
    if not limitations:
        add("Ограничений не зафиксировано.")
    for item in limitations:
        add(f"- {_escape(item.get('text'))}")
    modes = audit.get("mode_status") or {}
    if modes:
        add("")
        add("| Режим | Доступен | Причина |")
        add("|---|---|---|")
        for name, status in modes.items():
            add(f"| {name} | {'да' if status.get('available') else 'нет'} | "
                f"{_escape(status.get('reason') or '—')} |")
    add("")

    # 10. План
    add("## 10. План дальнейшей проверки")
    add("")
    for index, rec in enumerate(recommendations, start=1):
        verify = rec.get("verify") or {}
        sufficiency = verify.get("sufficiency") or {}
        limits = []
        if sufficiency.get("min_clicks"):
            limits.append(f"кликов ≥ {sufficiency['min_clicks']}")
        if sufficiency.get("min_conversions"):
            limits.append(f"конверсий ≥ {sufficiency['min_conversions']}")
        limit_text = ", ".join(limits) or "достаточный объём данных"
        add(f"{index}. **{_escape(rec.get('problem'))}** — проверить "
            f"{', '.join(map(str, verify.get('control_metrics') or []))}; "
            f"ожидаемое направление: {verify.get('expected_direction')}; "
            f"минимум данных: {limit_text}.")
        if (rec.get("expected_effect") or {}).get("type") == "none":
            add("   Действий не предлагается: сначала наблюдение и проверка данных.")
        else:
            add(f"   Успех: {verify.get('success')}. Неуспех: {verify.get('failure')}.")
    if not recommendations:
        add("План не требуется: изменений не предлагается.")
    add("")

    # Деньги
    accounting = audit.get("spend_accounting") or {}
    at_risk_block = accounting.get("at_risk_spend") or {}
    saving_block = accounting.get("potential_saving") or {}
    if at_risk_block.get("sum") is not None:
        add(f"Расход под риском: {fmt_money(at_risk_block['sum'])}.")
    elif at_risk_block.get("sum_by_type"):
        parts = ", ".join(f"{key}: {fmt_money(value)}"
                          for key, value in at_risk_block["sum_by_type"].items())
        add(f"Расход под риском по типам объектов (общая сумма не считается): {parts}.")
    if saving_block.get("value") is not None:
        add(f"Потенциальная экономия: {fmt_money(saving_block['value'])} — оценка при условиях "
            "из рекомендаций, не гарантия.")
    add("")
    add("---")
    add("Аудит только читает данные. Ни одна рекламная кампания не изменялась.")
    add("")
    return "\n".join(lines)
