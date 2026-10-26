#!/usr/bin/env python3
"""Автономный HTML-отчёт из audit.json (ТЗ §11.3).

Без внешних зависимостей: стили и скрипты внутри файла. Работает при открытии
локально в Windows. Все числа — из audit.json, без пересчёта в браузере.
"""
from __future__ import annotations

import html
import json

from .model import fmt_id, fmt_money, fmt_number, fmt_percent

PRIORITY_RU = {"P0": "Критический", "P1": "Высокий", "P2": "Средний", "P3": "Низкий"}
CLASS_RU = {"fact": "Факт", "likely_cause": "Вероятная причина", "hypothesis": "Гипотеза"}
DQ_RU = {"passed": "ОК", "warning": "Предупреждение", "blocked": "Блок",
         "unavailable": "Нет данных", "not_applicable": "Не применимо"}
EFFECT_RU = {"technical": "Технический результат", "estimate": "Оценка",
             "qualitative": "Качественный эффект", "none": "Без оценки"}


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _money(value) -> str:
    return fmt_money(value) if value is not None else "нет данных"


def _pct(value) -> str:
    return fmt_percent(value) if value is not None else "нет данных"


def _delta_html(item: dict, *, money=False, percent=False, good="neutral") -> str:
    """Цвет зависит от смысла метрики, а не только от знака."""
    if not item or item.get("abs") is None:
        return '<span class="muted">нет данных</span>'
    value = item["abs"]
    if value == 0 and item.get("display_hides_change"):
        return '<span class="muted">изменение скрыто округлением</span>'
    tone = "neutral"
    if value != 0:
        better = (value > 0) if good == "up" else (value < 0)
        tone = "good" if better else "bad"
    body = fmt_number(abs(value), 2) + (" ₽" if money else "")
    suffix = " п.п." if percent else ""
    sign = "−" if value < 0 else "+"
    pct = ""
    if item.get("base_zero"):
        pct = " · нет базы"
    elif item.get("pct") is not None:
        pct_value = fmt_number(item["pct"], 1)
        if item["pct"] > 0:
            pct_value = "+" + pct_value
        pct = f" · {pct_value}%"
    return f'<span class="delta {tone}">{sign}{body}{suffix}{pct}</span>'


def render(audit: dict) -> str:
    scope = audit.get("scope") or {}
    periods = audit.get("periods") or {}
    metrics = audit.get("metrics") or {}
    a, b = metrics.get("a") or {}, metrics.get("b") or {}
    comparison = metrics.get("comparison") or {}
    dq = audit.get("data_quality") or {}
    validation = audit.get("validation") or {}
    goal = audit.get("goal") or {}
    attribution = audit.get("attribution") or {}
    findings = audit.get("findings") or []
    recommendations = sorted(audit.get("recommendations") or [],
                             key=lambda r: {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(r.get("priority"), 9))
    limitations = audit.get("limitations") or []
    sources = audit.get("data_sources") or []
    hypotheses = audit.get("hypotheses") or []
    growth = audit.get("growth") or []
    accounting = audit.get("spend_accounting") or {}

    kpis = [
        ("Расход", _money(a.get("cost")), _delta_html(comparison.get("cost") or {}, money=True)),
        ("Конверсии", fmt_number(a.get("conversions"), 0),
         _delta_html(comparison.get("conversions") or {}, good="up")),
        ("CPA", _money(a.get("cpa")), _delta_html(comparison.get("cpa") or {}, money=True, good="down")),
        ("CTR", _pct(a.get("ctr")),
         _delta_html(comparison.get("ctr") or {}, percent=True, good="up")),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{_e(name)}</div>'
        f'<div class="kpi-value">{_e(value)}</div>{delta}</div>'
        for name, value, delta in kpis)

    def rec_rows():
        for rec in recommendations:
            obj = rec.get("object") or {}
            effect = rec.get("expected_effect") or {}
            verify = rec.get("verify") or {}
            yield (
                f'<tr data-priority="{_e(rec.get("priority"))}" '
                f'data-confidence="{_e(rec.get("confidence"))}">'
                f'<td><span class="badge p{_e(rec.get("priority"))[-1]}">{_e(rec.get("priority"))}</span></td>'
                f'<td><strong>{_e(rec.get("problem"))}</strong>'
                f'<div class="muted small">{_e(obj.get("type"))} · {_e(fmt_id(obj.get("id")))} · '
                f'правило {_e(rec.get("rule_id"))}</div></td>'
                f'<td>{_e(rec.get("proposed_change"))}</td>'
                f'<td>{_e(EFFECT_RU.get(effect.get("type"), effect.get("type")))}'
                + (f'<div class="muted small">{_e(effect.get("method"))}</div>' if effect.get("method") else "")
                + f'</td>'
                f'<td>{_e(rec.get("risk", {}).get("level"))}</td>'
                f'<td>{_e(verify.get("expected_direction"))}</td></tr>')

    def finding_cards():
        order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        for finding in sorted(findings, key=lambda f: order.get(f.get("priority"), 9)):
            cls = finding.get("classification")
            evidence = "; ".join(
                f'{_e(e.get("source"))}:{_e(e.get("field"))}={_e(e.get("value"))}'
                for e in finding.get("evidence") or [])
            alternatives = "; ".join(map(str, finding.get("alternatives") or []))
            yield (
                f'<article class="finding" data-priority="{_e(finding.get("priority"))}" '
                f'data-class="{_e(cls)}">'
                f'<header><span class="badge p{_e(finding.get("priority"))[-1]}">{_e(finding.get("priority"))}</span>'
                f'<h3>{_e(finding.get("title"))}</h3>'
                f'<span class="tag {_e(cls)}">{_e(CLASS_RU.get(cls, cls))}</span></header>'
                f'<p>{_e(finding.get("why"))}</p>'
                f'<dl><dt>Доказательства</dt><dd>{evidence or "нет"}</dd>'
                + (f'<dt>Альтернативы</dt><dd>{_e(alternatives)}</dd>' if alternatives else "")
                + (f'<dt>Следующий шаг</dt><dd>{_e(finding.get("next_step"))}</dd>'
                   if finding.get("next_step") else "")
                + '</dl></article>')

    def segment_tabs():
        tabs, panels = [], []
        names = [("devices", "Устройства"), ("regions", "Регионы"), ("search_queries", "Запросы"),
                 ("placements", "Площадки"), ("keywords", "Фразы"), ("adgroups", "Группы")]
        for index, (key, label) in enumerate(names):
            rows = (audit.get("segment_rows") or {}).get(key) or []
            block = (audit.get("segments") or {}).get(key) or {}
            active = "active" if index == 0 else ""
            tabs.append(f'<button class="tab {active}" data-tab="{key}">{label}</button>')
            if not rows:
                panels.append(f'<div class="panel" data-panel="{key}">'
                              f'<p class="muted">Нет данных: {_e(block.get("note") or "источник не получен")}</p></div>')
                continue
            body = "".join(
                f'<tr><td>{_e(r.get("key"))}</td>'
                f'<td class="num">{fmt_number(r.get("impressions"), 0)}</td>'
                f'<td class="num">{fmt_number(r.get("clicks"), 0)}</td>'
                f'<td class="num">{_pct(r.get("ctr"))}</td>'
                f'<td class="num">{_money(r.get("cost"))}</td>'
                f'<td class="num">{fmt_number(r.get("conversions"), 0)}</td>'
                f'<td class="num">{_money(r.get("cpa"))}</td></tr>'
                for r in rows[:200])
            panels.append(
                f'<div class="panel {active}" data-panel="{key}">'
                f'<div class="table-wrap"><table><thead><tr><th>Сегмент</th><th>Показы</th><th>Клики</th>'
                f'<th>CTR</th><th>Расход</th><th>Конверсии</th><th>CPA</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div></div>')
        return "".join(tabs), "".join(panels)

    tabs_html, panels_html = segment_tabs()

    q_checks = "".join(
        f'<li><span class="dot {_e(c.get("status"))}"></span>'
        f'<strong>{_e(c.get("id"))}</strong> {_e(c.get("title"))} — '
        f'{_e(DQ_RU.get(c.get("status"), c.get("status")))}'
        f'<div class="muted small">{_e(c.get("detail"))}</div></li>'
        for c in dq.get("checks") or [])

    sources_html = "".join(
        f'<tr><td>{_e(s.get("action"))}</td><td class="mono">{_e(s.get("file"))}</td>'
        f'<td class="num">{_e(s.get("rows"))}</td><td>{_e(s.get("fetched_at"))}</td>'
        f'<td>{_e(s.get("period") or "")}</td><td>{_e(s.get("status"))}</td>'
        f'<td class="mono small">{(s.get("sha256") or "")[:12]}</td></tr>'
        for s in sources)

    limitations_html = "".join(f"<li>{_e(item.get('text'))}</li>" for item in limitations)

    hypotheses_html = "".join(
        f'<article class="hypo"><h4>{_e(h.get("title"))}</h4><p>{_e(h.get("statement"))}</p>'
        f'<dl><dt>Основания</dt><dd>{_e("; ".join(map(str, h.get("basis") or [])))}</dd>'
        f'<dt>Альтернативы</dt><dd>{_e("; ".join(map(str, h.get("alternatives") or [])))}</dd>'
        f'<dt>Проверка</dt><dd>{_e(h.get("check"))}</dd></dl></article>'
        for h in hypotheses) or '<p class="muted">Гипотез нет.</p>'

    growth_html = "".join(
        f'<li><strong>{_e(g.get("type"))}</strong> · {_e(fmt_id((g.get("object") or {}).get("id")))} — '
        f'{_e(g.get("why"))} <span class="muted">{_e(g.get("promise"))}</span></li>'
        for g in growth) or '<li class="muted">Возможностей роста не найдено.</li>'

    validation_banner = ""
    if validation.get("status") == "FAIL":
        validation_banner = ('<div class="banner fail"><strong>Диагностический отчёт.</strong> '
                             'Валидация вернула FAIL — цифры нельзя публиковать как подтверждённые.</div>')
    elif validation.get("status") == "WARN":
        validation_banner = ('<div class="banner warn"><strong>Есть предупреждения валидации.</strong> '
                             'Они перечислены в разделе «Ограничения».</div>')

    payload = json.dumps({"audit_id": audit.get("audit_id")}, ensure_ascii=False)
    at_risk_sum = (accounting.get("at_risk_spend") or {}).get("sum")
    at_risk_text = _money(at_risk_sum) if at_risk_sum is not None else "не суммируется (разные типы объектов)"
    saving_text = _money((accounting.get("potential_saving") or {}).get("value"))

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Аудит кампании {_e(scope.get('campaign_id'))}</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1d232b;--muted:#6b7480;--line:#e2e6ea;
--p0:#b42318;--p1:#b54708;--p2:#8a6d00;--p3:#5c6470;--ok:#067647;--warn:#b54708;--bad:#b42318;}}
@media (prefers-color-scheme:dark){{:root{{--bg:#15181c;--card:#1d2126;--ink:#e8eaed;--muted:#9aa3ad;
--line:#2c3238;--p0:#ff8a80;--p1:#ffb86b;--p2:#e8d27a;--p3:#a9b2bd;--ok:#6fd18f;--warn:#ffb86b;--bad:#ff8a80;}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
main{{max-width:1120px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:1.6rem;margin:0 0 6px}} h2{{font-size:1.15rem;margin:34px 0 12px}} h3{{font-size:1rem;margin:0}}
h4{{margin:0 0 6px;font-size:.98rem}}
.sub{{color:var(--muted);margin:0 0 18px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.kpi-label{{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}}
.kpi-value{{font-size:1.5rem;font-weight:600;margin:4px 0}}
.delta{{font-size:.85rem}} .delta.up{{color:var(--bad)}} .delta.down{{color:var(--ok)}}
.banner{{border-radius:8px;padding:10px 14px;margin:14px 0;border:1px solid var(--line);background:var(--card)}}
.banner.fail{{border-color:var(--bad)}} .banner.warn{{border-color:var(--warn)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
.muted{{color:var(--muted)}} .small{{font-size:.82rem}} .mono{{font-family:ui-monospace,Consolas,monospace}}
.table-wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--muted);font-weight:600;font-size:.78rem;text-transform:uppercase;letter-spacing:.03em}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.badge{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.75rem;font-weight:700;border:1px solid currentColor}}
.badge.p0{{color:var(--p0)}} .badge.p1{{color:var(--p1)}} .badge.p2{{color:var(--p2)}} .badge.p3{{color:var(--p3)}}
.tag{{font-size:.72rem;padding:2px 8px;border-radius:999px;background:var(--bg);color:var(--muted);border:1px solid var(--line)}}
.finding{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}}
.finding header{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.finding dl{{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;margin:10px 0 0;font-size:.88rem}}
.finding dt{{color:var(--muted)}} .finding dd{{margin:0}}
.toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}
.chip{{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
padding:5px 12px;font-size:.85rem;cursor:pointer}}
.chip[aria-pressed="true"]{{background:var(--ink);color:var(--bg);border-color:var(--ink)}}
.tab{{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px 8px 0 0;
padding:7px 13px;cursor:pointer;font-size:.88rem;border-bottom:none}}
.tab.active{{background:var(--ink);color:var(--bg)}}
.panel{{display:none;background:var(--card);border:1px solid var(--line);border-radius:0 10px 10px 10px;padding:8px}}
.panel.active{{display:block}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--muted)}}
.dot.passed{{background:var(--ok)}} .dot.warning{{background:var(--warn)}}
.dot.blocked{{background:var(--bad)}} .dot.unavailable{{background:var(--muted)}}
ul.checks{{list-style:none;padding:0;margin:0}} ul.checks li{{padding:7px 0;border-bottom:1px solid var(--line)}}
.hypo{{background:var(--card);border:1px dashed var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}}
.hypo dl{{display:grid;grid-template-columns:120px 1fr;gap:3px 10px;margin:8px 0 0;font-size:.86rem}}
.hypo dt{{color:var(--muted)}} .hypo dd{{margin:0}}
footer{{margin-top:36px;color:var(--muted);font-size:.82rem}}
@media (max-width:760px){{
  #recommendations th:nth-child(4),#recommendations td:nth-child(4),
  #recommendations th:nth-child(5),#recommendations td:nth-child(5),
  #recommendations th:nth-child(6),#recommendations td:nth-child(6){{display:none}}
  table{{font-size:.85rem}}
  .finding dl,.hypo dl{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<main>
<h1>Аудит кампании {_e(scope.get('campaign_id'))}</h1>
<p class="sub">{_e(scope.get('campaign_name'))} · {_e(scope.get('account_login') or scope.get('account'))} ·
A: {_e(periods.get('a', {}).get('from'))}–{_e(periods.get('a', {}).get('to'))} ·
B: {_e(periods.get('b', {}).get('from'))}–{_e(periods.get('b', {}).get('to'))} ·
цель: {_e(goal.get('label') or 'не задана')} ·
атрибуция: {_e(attribution.get('effective') or '—')}</p>
{validation_banner}
<div class="kpis">{kpi_html}</div>

<h2>Рекомендации</h2>
<div class="toolbar" data-target="recommendations">
<button class="chip" data-filter="all" aria-pressed="true">Все</button>
<button class="chip" data-filter="P0" aria-pressed="false">P0</button>
<button class="chip" data-filter="P1" aria-pressed="false">P1</button>
<button class="chip" data-filter="P2" aria-pressed="false">P2</button>
<button class="chip" data-filter="P3" aria-pressed="false">P3</button>
</div>
<div class="card table-wrap">
<table id="recommendations">
<thead><tr><th>Приоритет</th><th>Проблема</th><th>Что сделать</th><th>Эффект</th><th>Риск</th><th>Проверка</th></tr></thead>
<tbody>{''.join(rec_rows()) or '<tr><td colspan="6" class="muted">Рекомендаций нет при текущих данных.</td></tr>'}</tbody>
</table>
</div>

<h2>Проблемы и факты</h2>
<div class="toolbar" data-target="findings">
<button class="chip" data-filter="all" aria-pressed="true">Все</button>
<button class="chip" data-filter="fact" aria-pressed="false">Факты</button>
<button class="chip" data-filter="likely_cause" aria-pressed="false">Вероятные причины</button>
<button class="chip" data-filter="hypothesis" aria-pressed="false">Гипотезы</button>
</div>
<div id="findings">{''.join(finding_cards()) or '<p class="muted">Находок нет.</p>'}</div>

<h2>Сегменты</h2>
<div class="toolbar">{tabs_html}</div>
{panels_html}

<h2>Качество данных</h2>
<div class="card"><ul class="checks">{q_checks}</ul></div>

<h2>Возможности роста</h2>
<div class="card"><ul>{growth_html}</ul></div>

<h2>Непроверенные гипотезы</h2>
{hypotheses_html}

<h2>Ограничения и пробелы в данных</h2>
<div class="card"><ul>{limitations_html or '<li class="muted">Ограничений не зафиксировано.</li>'}</ul></div>

<h2>Источники</h2>
<div class="card table-wrap">
<table><thead><tr><th>Действие</th><th>Файл</th><th>Строк</th><th>Получено</th><th>Период</th>
<th>Статус</th><th>SHA-256</th></tr></thead><tbody>{sources_html}</tbody></table>
</div>

<footer>
<p>Расход: {_money(a.get('cost'))} · под риском: {at_risk_text} ·
потенциальная экономия: {saving_text} (оценка при условиях, не гарантия).</p>
<p>Отчёт только читает данные. Рекламные кампании не изменялись. Данные: {_e(payload)}</p>
</footer>
</main>
<script>
(function () {{
  var toolbarState = {{}};
  document.querySelectorAll('.toolbar[data-target]').forEach(function (bar) {{
    var target = bar.getAttribute('data-target');
    var isPriority = bar.querySelector('[data-filter="P0"]') !== null;
    toolbarState[target] = 'all';
    bar.addEventListener('click', function (event) {{
      var button = event.target.closest('.chip');
      if (!button) return;
      var value = button.getAttribute('data-filter');
      bar.querySelectorAll('.chip').forEach(function (chip) {{
        chip.setAttribute('aria-pressed', String(chip === button));
      }});
      var items = target === 'findings'
        ? document.querySelectorAll('#findings .finding')
        : document.querySelectorAll('#recommendations tbody tr');
      items.forEach(function (item) {{
        var key = isPriority ? item.getAttribute('data-priority') : item.getAttribute('data-class');
        item.style.display = (value === 'all' || key === value) ? '' : 'none';
      }});
    }});
  }});
  document.querySelectorAll('.tab').forEach(function (tab) {{
    tab.addEventListener('click', function () {{
      var key = tab.getAttribute('data-tab');
      document.querySelectorAll('.tab').forEach(function (t) {{ t.classList.toggle('active', t === tab); }});
      document.querySelectorAll('.panel').forEach(function (panel) {{
        panel.classList.toggle('active', panel.getAttribute('data-panel') === key);
      }});
    }});
  }});
}})();
</script>
</body>
</html>
"""
