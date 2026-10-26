#!/usr/bin/env python3
"""Собирает демонстрационную сессию и полный аудит на вымышленных данных.

    python examples/build_example.py

Никаких обращений к API: данные синтетические, но формат ответов повторяет
реальные ответы DirectAI MCP. Результат: session.json, raw/, audit.json,
audit.md, audit.xlsx, audit.html.
"""
from __future__ import annotations

import datetime
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import factory  # noqa: E402
from auditlib import build, render_html, render_md, render_xlsx  # noqa: E402

OUT = ROOT / "examples" / "example-87654321"
TODAY = datetime.date(2026, 10, 23)

JOURNAL = """# Журнал кампании «Окна под ключ» (87654321)

Кабинет: example-agency. Тип: TEXT_CAMPAIGN. Статус: ON.
Основная цель: id=11111111, label=Заявка с сайта, source=campaign.
Собрано: 2026-10-20T09:30:00+03:00.

## История правок

| Дата | Действие | Кратко | Статус |
| --- | --- | --- | --- |
| 2026-10-02T12:10:00+03:00 | ads_update | 1923568665500247917: правка заголовка | applied |
| 2026-09-30T10:05:00+03:00 | keywords_state | 55501: пауза фразы | applied |

## Заметки

нет данных
"""


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    root = factory.make_session(
        OUT,
        campaign_id="87654321",
        campaign_name="Окна под ключ",
        account="example",
        login="example-agency",
        modes=["deep"],
        strategy="поиск: WB_MAXIMUM_CONVERSION_RATE; сети: WB_MAXIMUM_CONVERSION_RATE",
        a=dict(impressions=47200, clicks=980, cost=37960, conversions=26),
        b=dict(impressions=48000, clicks=1000, cost=38000, conversions=40),
        periods={"a": {"from": "2026-09-26", "to": "2026-10-09"},
                 "b": {"from": "2026-09-12", "to": "2026-09-25"}},
        goal=("11111111", "Заявка с сайта"),
        product={"what": "окна ПВХ под ключ, замер и монтаж",
                 "exclude_terms": ["вакансии"],
                 "protected_terms": ["цена", "отзывы", "обзор"]},
        economics={"revenue_rub": 250000, "gross_margin_rub": 90000},
        queries=[
            ("окна даром", dict(impressions=1400, clicks=45, cost=1800, conversions=0)),
            ("работа монтажником окон", dict(impressions=1200, clicks=40, cost=1400, conversions=0)),
            ("купить окна пвх", dict(impressions=6000, clicks=150, cost=9000, conversions=9)),
        ],
        placements=[
            ("video-fun.example", dict(impressions=60000, clicks=300, cost=2700, conversions=0)),
            ("shop-fun.example", dict(impressions=70000, clicks=400, cost=3200, conversions=0)),
        ],
        devices=[
            ("MOBILE", dict(impressions=26000, clicks=620, cost=24000, conversions=8)),
            ("DESKTOP", dict(impressions=21200, clicks=360, cost=13960, conversions=18)),
        ],
        regions=[
            ("Москва", dict(impressions=30000, clicks=600, cost=25000, conversions=18)),
            ("Краснодар", dict(impressions=17200, clicks=380, cost=12960, conversions=8)),
        ],
        keywords=[
            ("окна пвх", dict(impressions=20000, clicks=300, cost=12000, conversions=4)),
            ("окна даром", dict(impressions=1400, clicks=45, cost=1800, conversions=0)),
            ("балконы эконом", dict(impressions=3000, clicks=80, cost=3100, conversions=0)),
        ],
        adgroups=[
            ("Окна ПВХ", dict(impressions=24000, clicks=520, cost=24960, conversions=18)),
            ("Балконы", dict(impressions=12000, clicks=260, cost=13000, conversions=8)),
        ],
        ads=[("1923568665500247917", ["DisplayUrlPath", "Callouts"])],
        moderation=[("1923568665500247917", "REJECTED", "превышение лимита заглавных букв")],
        negatives=[("campaign", "87654321", ["даром"])],
        metrika=dict(clicks=980, visits=600, goals=26, cr=2.65, cpa=1460.0),
        changes=[("Ad", "1923568665500247917", "2026-10-02"),
                 ("AdGroup", "55501", "2026-09-30")],
        journal=JOURNAL,
    )

    audit = build.build(root, today=TODAY)
    build.write(audit, root / "audit.json")
    markdown = render_md.render(audit)
    (root / "audit.md").write_text(markdown, encoding="utf-8")
    (root / "audit.xlsx").write_bytes(render_xlsx.render(audit))
    (root / "audit.html").write_text(render_html.render(audit), encoding="utf-8")

    preamble = (
        "<!-- Пример отчёта. Все данные вымышлены и созданы examples/build_example.py. -->\n\n"
        "> **Пример отчёта на вымышленных данных.** Кампания, кабинет, площадки и цифры\n"
        "> сгенерированы скриптом, реальные клиенты не использованы. Отчёт собран\n"
        "> конвейером `build_audit.py → validate_audit.py → render_md.py`.\n\n"
    )
    (ROOT / "references" / "example-audit.md").write_text(preamble + markdown, encoding="utf-8")

    validation = audit["validation"]
    print(f"сессия: {root}")
    print(f"валидация: {validation['status']} "
          f"(ошибок {validation['summary']['errors']}, предупреждений {validation['summary']['warnings']})")
    print(f"качество данных: {audit['data_quality']['status']}")
    print(f"находок: {len(audit['findings'])}, рекомендаций: {len(audit['recommendations'])}, "
          f"гипотез: {len(audit['hypotheses'])}, рост: {len(audit['growth'])}")
    for finding in sorted(audit["findings"], key=lambda f: f["priority"]):
        print(f"  {finding['priority']} {finding['rule_id']:8} {finding['title'][:70]}")
    return 0 if validation["status"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
