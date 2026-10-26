# Схема audit.json

Версия схемы: `2.0`. Реализация: `scripts/auditlib/build.py`,
проверка: `scripts/auditlib/validate.py`.

`audit.json` — единственный источник цифр для Markdown, Excel и HTML.

## Верхний уровень

| Поле | Тип | Смысл |
|---|---|---|
| `schema_version` | string | `"2.0"` |
| `audit_id` | string | `audit-<login>-<campaign>-<timestamp>` |
| `created_at` | string | ISO 8601 с часовым поясом |
| `mode` | string[] | выбранные режимы |
| `scope` | object | кабинет, кампания, тип, состояние, расхождения |
| `campaign` | object | настройки: счётчики, стратегия, даты, атрибуция |
| `periods` | object | `a`, `b`, длины, валидность, часовой пояс, задержка конверсий |
| `goal` | object | `id`, `label`, `source`, `configured`, `value_rub` |
| `attribution` | object | `requested`, `effective`, `source`, `consistent` |
| `thresholds` | object | целевой CPA, пороги объёма, чувствительность, допуски |
| `data_sources` | object[] | происхождение каждого файла |
| `data_quality` | object | статус, проверки, гейты, проблемы |
| `metrics` | object | A, B, сравнение, единицы |
| `segments` | object | доступность и итоги сегментов |
| `segment_rows` | object | строки сегментов (до 500 на срез) |
| `segments_checked` | object[] | проверка сопоставимости сегментов |
| `findings` | object[] | находки |
| `recommendations` | object[] | рекомендации |
| `hypotheses` | object[] | непроверенные гипотезы |
| `growth` | object[] | возможности роста |
| `spend_accounting` | object | расход, риск, экономия |
| `history` | object | изменения из журнала и `changes_check` |
| `monitor` | object/null | сравнение с прошлым аудитом |
| `monitor_compatibility` | object/null | сверка кабинета, кампании, цели и атрибуции перед сравнением || `funnel` | object/null | этапы воронки |
| `experiments` | object[] | планы проверки гипотез |
| `mode_status` | object | доступность каждого режима |
| `limitations` | object[] | что не проверено и почему |
| `validation` | object | `status`, `issues`, `summary` |

## `data_sources[]`

```json
{
  "action": "stats_compare",
  "file": "stats_compare.json",
  "params": {"campaign_ids": [900000001]},
  "account": "example-agency",
  "sha256": "…64 hex…",
  "bytes": 2048,
  "fetched_at": "2026-10-20T10:00:00+0300",
  "status": "ok | warning | blocked | unavailable | error",
  "known_action": true,
  "error": {"code": 56, "message": "Превышен лимит запросов метода"},
  "pagination_complete": true,
  "truncated": false,
  "period": {"from": "2026-09-26", "to": "2026-10-09"},
  "rows": 8,
  "tool_warning": null,
  "note": null
}
```

## `metrics`

```json
{
  "primary_source": "stats_compare",
  "units": {"cost": "RUB_VAT", "ctr": "percent", "cr": "percent", "cpa": "RUB"},
  "a": {"impressions": 47200, "clicks": 980, "cost": 37960.0,
        "conversions": 26, "ctr": 2.08, "cpc": 38.73, "cr": 2.65, "cpa": 1460.0},
  "b": {"...": "..."},
  "comparison": {
    "cpa": {"a": 1460.0, "b": 950.0, "kind": "money", "available": true,
            "abs": 510.0, "pct": 53.68, "pp": null, "base_zero": false,
            "display_hides_change": false, "note": null}
  }
}
```

Для долей `kind = "rate"`, заполняется `pp`. При `b = 0` — `pct: null`,
`base_zero: true`.

## `findings[]`

```json
{
  "id": "F-003",
  "rule_id": "RSY-02",
  "category": "rsy_placements",
  "title": "Площадка без конверсий ниже порога: video-fun.example",
  "object": {"type": "placement", "id": "video-fun.example",
             "path": "campaign:87654321/placement:video-fun.example"},
  "evidence": [{"source": "stats_placements", "field": "Cost", "value": 2700.0}],
  "metrics": {"cost": 2700.0, "conversions": 0, "threshold": 2920.0},
  "threshold": {"name": "2 × CPA", "value": 2920.0, "unit": "RUB",
                "meaning": "порог внимания: ниже порога исключение не обосновано"},
  "classification": "fact",
  "priority": "P3",
  "confidence": "low",
  "why": "Расход 2 700,00 ₽ ниже порога 2 920,00 ₽: исключение по порогу не обосновано.",
  "alternatives": ["мало данных", "конверсии придут позже"],
  "next_step": "Наблюдать; вернуться к вопросу при расходе выше порога.",
  "spend": {"actual": 2700.0, "at_risk": null, "potential_saving": null},
  "recommendation_id": null
}
```

## `recommendations[]`

```json
{
  "id": "R-002",
  "finding_id": "F-004",
  "rule_id": "SQ-01",
  "priority": "P2",
  "confidence": "high",
  "problem": "Нерелевантный запрос тратит бюджет: «окна даром»",
  "object": {"type": "search_query", "id": "окна даром",
             "path": "campaign:87654321/query:окна даром"},
  "evidence": [{"source": "stats_search_queries", "field": "Cost", "value": 1800.0}],
  "proposed_change": "Добавить минус-фразу «даром»…",
  "expected_effect": {"type": "estimate", "value": 1800.0, "unit": "RUB/период",
                      "method": "расход запроса за период A при полном исключении",
                      "conditions": ["минус-фраза действительно блокирует запрос"]},
  "risk": {"level": "medium", "notes": ["минус может отсечь похожие запросы"]},
  "apply": {"method": "tool", "tool": "negatives_set", "manual": null},
  "verify": {"baseline_period": {"from": "2026-09-26", "to": "2026-10-09"},
             "control_metrics": ["расход на запрос", "конверсии", "CPA"],
             "expected_direction": "расход вниз, конверсии не падают",
             "sufficiency": {"min_clicks": 100, "min_conversions": 10},
             "side_effects": ["потеря части трафика"],
             "success": "…", "failure": "…"},
  "status": "proposed"
}
```

## `spend_accounting`

```json
{
  "actual_spend_rub": 37960.0,
  "candidate_actual_spend": {"sum": 5000.0, "objects": [], "overlaps": [],
                             "overlap_free": true, "summed": true},
  "at_risk_spend": {"sum": 3200.0, "sum_by_type": {"placement": 3200.0},
                    "objects": [], "overlaps": [], "overlap_free": true,
                    "summed": true, "mixed_levels": [], "note": null},
  "potential_saving": {"value": 3200.0, "conditional": true,
                       "method": "…оценка, а не гарантия…", "items": []},
  "note": "фактический расход, расход под риском и потенциальная экономия — разные величины"
}
```

Если в выборку попали разные уровни одной иерархии (группа и фраза), `sum`
равен `null`, заполняется `sum_by_type`, а `mixed_levels` перечисляет типы.

## `mode_status`

```json
{
  "rsy": {"available": true, "reason": null, "missing_sources": []},
  "funnel": {"available": false, "reason": "нет источников: metrika_direct_cpa",
             "missing_sources": ["metrika_direct_cpa"]},
  "deep": {"available": true, "reason": "часть блоков недоступна: campaign_journal",
           "degraded": true}
}
```

## monitor и сравнение снимков

Перед сравнением проверяются четыре поля: кабинет, ID кампании, основная цель
и модель атрибуции. **Все четыре обязательны.** Если цель или атрибуция
не указаны хотя бы в одном снимке, сравнение запрещено: неизвестно, что
именно и по какой модели считается.

При расхождении или отсутствии:

- `monitor_compare.py` возвращает код 2 и печатает причины;
- в аудите режим `monitor` недоступен, в ограничениях остаётся запись
  «предыдущий снимок несовместим»;
- в `audit.json` сохраняется блок `monitor_compatibility` с построчной сверкой
  и флагом `conversion_comparable`.

Флаг `conversion_comparable` равен true только когда цель и атрибуция
подтверждены и совпадают. CPA, CR и конверсии сравниваются лишь в этом случае.

## Валидация

`validate_audit.py` возвращает:

- `PASS` — публикация разрешена;
- `WARN` — публикация с оговорками, они попадают в отчёт;
- `FAIL` — публикация подтверждённого отчёта запрещена, разрешён
  диагностический (`--diagnostic`).

Проверяется: структура, идентификация, даты, цель, атрибуция, типы чисел,
единицы, знаки изменений, процентные пункты, нулевые знаменатели, пороги,
доказательства, оценки без метода, пересечения расходов, состояния
отсутствующих данных, согласованность режимов.
