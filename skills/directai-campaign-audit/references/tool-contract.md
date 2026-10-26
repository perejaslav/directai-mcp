# Реестр действий DirectAI MCP

Проверено на DirectAI MCP **v1.17.2** (23.10.2026). Источник: `describe_action`
и реальные `run_read` с `output=file`.

## Как устроен доступ

`search_actions` и `describe_action` — **MCP-инструменты**, а не действия
`run_read`. Действия `search_actions` в реестре `run_read` нет: попытка
`run_read(name="search_actions")` возвращает список доступных действий.
Поэтому конвейер выглядит так:

```
search_actions(query)        → какие действия есть
describe_action(name)        → схема и ограничения
run_read(name, params)       → чтение
```

`get_operation_log` — тоже отдельный MCP-инструмент (не действие `run_read`).

## Конверт ответа

Любое JSON-действие возвращает:

```json
{"meta": {"action": "...", "account": "...", "context": "...",
          "created_at": "...", "total_rows": 0, "columns": ["..."]},
 "rows": [{"...": "..."}]}
```

Правила чтения:

- числа приходят **строками** (`"326 064"`, `"103 549.40"`, `"75.6%"`);
- `null` и `"—"` означают отсутствие значения, а не ноль;
- `meta.context` — единственный источник сведений о периоде, атрибуции, цели
  и предупреждений. Он сохраняется дословно;
- `campaign_journal` игнорирует `format=json` и всегда отдаёт Markdown;
- пустой `rows` при ошибке API (например, `error 56`) **не доказывает**
  отсутствие данных.

## Обязательный минимум

| Действие | Зачем | Аргументы | Условие |
|---|---|---|---|
| `stats_compare` | A/B по кампании | `campaign_ids`, `period_a_from/to`, `period_b_from/to`, `output=file`, `format=json` | всегда |
| `campaigns_get` | тип, состояние, стратегия, счётчики, атрибуция | `campaign_ids`, `full=true` | всегда |

Без `stats_compare` блокируется динамика, CPA, сегменты, рост и воронка.
Без `campaigns_get` блокируется весь аудит: кампания не идентифицирована.

## Полный реестр (только чтение)

| Действие | Назначение | Ключевые аргументы | Проверено |
|---|---|---|---|
| `stats_compare` | сравнение периодов | 4 даты, `group_by` | живой вызов |
| `stats_summary` | итоги по кабинетам | `period`/`date_from/to` | живой вызов |
| `stats_search_queries` | поисковые запросы | `date_from/to`, `query_grouping` | живой вызов (0 строк) |
| `stats_placements` | площадки РСЯ | `date_from/to`, `anomaly_min_clicks` | живой вызов (0 строк + error 56) |
| `stats_devices` | устройства | `date_from/to` | схема |
| `stats_regions` | регионы | `date_from/to` | схема |
| `stats_keywords` | фразы и автотаргетинг | `match_mode` | схема |
| `stats_adgroups` | группы | `date_from/to` | схема |
| `stats_ads` | объявления | `date_from/to` | схема |
| `stats_audiences` | аудитории | `date_from/to` | схема |
| `stats_custom` | произвольный отчёт | `field_names`, `filters`, `report_type` | схема |
| `metrika_direct_cpa` | расход + визиты + цели Метрики | `counter_id`, `goal_ids`, `attribution` | живой вызов |
| `metrika_traffic`, `metrika_bytime`, `metrika_goals_report` | Метрика | — | схема |
| `campaigns_get` | настройки кампании | `campaign_ids`, `full` | живой вызов |
| `campaigns_list` | список кампаний | `search`, `limit` | живой вызов |
| `adgroups_list`, `ads_list` | группы и объявления | `campaign_ids`, `states` | схема |
| `keywords_list`, `bids_get`, `bid_modifiers_get` | фразы, ставки, корректировки | `campaign_ids` | схема |
| `negatives_audit` | минус-фразы по уровням | `campaign_ids` | схема |
| `extensions_list` | расширения объявлений | `ad_ids` | схема |
| `moderation_check` | модерация | `campaign_ids` | схема |
| `counter_check`, `metrika_goals_list` | счётчик и цели | `campaign_ids`, `goals_only` | схема |
| `audiences_list`, `retargeting_lists_list` | аудитории | `campaign_ids` | схема |
| `changes_check` | что менялось с даты | `since`, `campaign_ids` | живой вызов |
| `campaign_journal` | журнал правок (Markdown) | `campaign_id`, `days` | живой вызов |
| `strategies_get`, `businesses_get`, `feeds_get`, `turbopages_get` | связанные сущности | по ID | схема |
| `keyword_bids_forecast`, `phrases_forecast` | прогнозы | фразы | схема |
| `wordstat_*` | спрос | фразы, регионы | схема |
| `webmaster_*` | Вебмастер | host | схема |
| `dictionaries_get` | справочники | `name` | схема |

«Схема» = действие описано через `describe_action`, но в этом аудите живой
вызов не выполнялся. Парсер колонок в `extract.py` гибкий: если схема
отличается, сегмент помечается `unavailable` с перечнем полученных колонок,
а не заполняется нулями.

## Запрещённые действия

`plan_write`, `apply_write`, `campaigns_create/update/state`,
`adgroups_create/update`, `ads_create/update/state`, `keywords_add/update/state`,
`negatives_set`, `bid_modifiers_set`, `bids_set`, `extensions_create`,
`audience_*`, `retargeting_list_*`, `metrika_goal_*`.

Если такое действие объявлено в `session.json`, источник помечается `blocked`,
а в `data_quality.problems` появляется запись. Это защита, а не ошибка.

## Незарегистрированные действия

Если действие не входит в реестр выше, оно тоже помечается `blocked`:
схема не проверена, значит данные нельзя использовать в расчётах.
Валидатор в этом случае возвращает `FAIL` (`SAFE-03`), а не `PASS`.
Исключение — действие, взятое из конверта, если оно есть в реестре.

## Принадлежность источника

У каждого JSON-ответа проверяется `meta.account`:

- все источники должны быть прочитаны по одному кабинету;
- значение сверяется с `session.account` / `session.account_login`;
- если `meta.account` нет и в строках нет `_account`, принадлежность считается
  недоказанной — это блокировка `DQ-ACC-02`, а не «всё хорошо».

Значение `account` и флаг `known_action` сохраняются в `data_sources`.

## Неудачные вызовы

Отказ API — это тоже факт, который нужно сохранить. В `session.json`
добавляется запись без файла:

```json
{
  "action": "stats_compare",
  "params": {"campaign_ids": [87654321]},
  "status": "error",
  "error": {"code": 56, "message": "Превышен лимит запросов метода"}
}
```

Правила:

- запись с `status: "error"` попадает в `data_sources` со статусом `error`
  и кодом ошибки;
- `DQ-API-01` показывает реальную причину: «отказ по лимиту Reports API:
  stats_compare (код 56)», а не «ошибок нет»;
- пустой ответ при ошибке 56 не считается доказательством отсутствия данных;
- если ошибка не описана и файла нет, источник блокируется как неполный.

## Инструменты для плана (не выполняются)

| Задача | Подтверждённый инструмент | Оговорка |
|---|---|---|
| минус-фразы | `negatives_set` | только после проверки в прогнозе |
| ставки/корректировки | `bid_modifiers_set` | нужен объём данных |
| тексты объявлений | `ads_update`, `extensions_create` | лимиты Директа до prepare |
| исключённые площадки | `campaigns_update {excluded_sites}` | **вручную**: текущий список через API не читается, замена небезопасна |

## Особенности `stats_compare`, которые нельзя игнорировать

1. `Δ%` считается от базы **A**, а не B. Пример: A=326 064, B=257 550 →
   инструмент даёт `-21.01%`, правильное значение `+26.60%`.
2. У процентных метрик знак `Δ` перевёрнут: CTR 0.36 против 0.43 показан как
   `+0.07 п.п.`.
3. У конверсий знак тоже может быть перевёрнут (`3` против `4` → `+1`).
4. В шапке бывает `⚠ Direct API error 56` — лимит 1 запрос / 10 секунд.

Поэтому `build_audit.py` **не использует** `Δ` и `Δ%` инструмента: они
пересчитываются из A и B по формулам из `metrics-contract.md`.

## Лимиты и деградация

- Reports API: не чаще 1 запроса / 10 секунд на кабинет. При ошибке 56
  источник помечается `blocked`, а не «нет данных».
- `campaigns_get` не отдаёт `ExcludedSites` (18 колонок, список площадок
  отсутствует). Следствие: автоматическое управление исключениями невозможно.
- `campaign_journal` не отдаёт JSON: история разбирается из Markdown-таблицы.
- Если действие отсутствует — фиксируется ограничение и продолжается
  доступная часть аудита. Имитировать успех запрещено.
