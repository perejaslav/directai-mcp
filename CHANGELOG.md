# CHANGELOG

## v1.8.0 (2026-10-01) — запись создаёт ЕПК (UNIFIED)

- `campaigns_create`: дефолт `UNIFIED_CAMPAIGN` (запрос на `json/v501`,
  требование справки); явный `TEXT_CAMPAIGN` — `v5` + предупреждение
  «устаревший тип».
- `campaigns_update`: резолв типа через `v501` (v5 отдаёт устаревший
  TEXT_CAMPAIGN для ЕПК); блок стратегии и tracking — по типу кампании;
  версия `v501` при ЕПК, иначе `v5`.
- `adgroups_create`: резолв типа кампании (`v501`), пометка `[ЕПК]`,
  отказ при неизвестной кампании/типе (создание групп — `v5`: сервиса
  AdGroups в `v501` нет, тип группы API выводит сам).
- `ads_create`: `ad_type` по умолчанию — по переданным объявлениям
  (для ЕПК — `responsive_ads`); совместимость группы/кампании до API
  (оба типа через `v501`); TEXT_AD в ЕПК — предупреждение о конвертации;
  DisplayUrlPath обязателен и сверяется в read-back (было, зафиксировано).
- Цепочка без модерации зафиксирована: `moderate` отсутствует в планах
  создания и заблокирован guard по умолчанию.
- Тесты: `tests/test_v180_unified.py` (15); обновлены моки v501/Type
  в v1118/v1135/v117/step5/v1134/excluded_sites.

## v1.7.1 (2026-10-01) — doctor: INFO по умолчанию, --preinstall

- Проверки c (процессы) и d (блокировка файла): занятый exe при
  работающих сессиях — INFO, а не ложная тревога; строго (c → WARN,
  d → FAIL) — только флаг `doctor --preinstall` перед переустановкой.
- README §7/§8.1, playbooks, SKILL: `doctor --preinstall` после
  остановки процессов в каноническом блоке переустановки.

## v1.7.0 (2026-10-01) — connection doctor

- CLI `directai-mcp doctor` (`src/directai_mcp/doctor.py`): 9 read-only
  проверок (a — версия пакет/pyproject/git, b — exe и дубли в PATH,
  c — зависшие процессы списком PID без kill, d — блокировка файла
  пробным открытием без изменения, e — конфиг и валидация v1.6.0,
  f — токены без вывода значений, g — лёгкий clients.get с разбором
  кодов 52/53/58/152/506/513/1000/1020, h — `hermes mcp test`
  без фильтра python.exe gateway run, i — Аудитории write_enabled);
  флаги `--json`/`--skip-api`; коды возврата 0/1/2.
- Скилл `skills/directai-connection-doctor/` (SKILL.md +
  references/playbooks.md): doctor --json → один готовый блок PowerShell;
  fallback вручную, если exe сломан.
- Шаг 0: README §5 — audience write-действия с правилами; README §7
  и AGENTS.md §4 — полный блок остановки + `uv tool install --force
  --editable .`; README §8.1 и AGENTS.md §4.1 — «сначала doctor»;
  идея lidfly-connection-doctor (MIT) — в NOTICE.md, код не копировался.
- Тесты: `tests/test_doctor.py` (27: a–i OK/WARN/FAIL, коды возврата,
  --json-схема, отсутствие секретов, таблица кодов).

## v1.6.0 (2026-10-01) — пакет «Поиск и цель» (B1 + B3)

- B1: типизированный статус поиска (`catalog/lookup.py`):
  `resolved`/`ambiguous`/`not_observed`/`incomplete`/`failed` +
  `presence` (`configured`/`statistics_only`), `proves_account_empty=false`,
  русское `message`. Применение в `campaigns_get`/`campaigns_list`;
  проверка Reports для `statistics_only` — только при пусто по явному ID
  (один лёгкий `CAMPAIGN_PERFORMANCE_REPORT`, 90 дней, `lookup_days`).
  Guard отклоняет write при не-`resolved+configured`. Пустой список —
  не доказательство пустоты. `lookup_status` в dump-конверте и manifest.
- B3: основная цель (`primary_conversion_goal_id` на кабинете и
  `[aliases.<имя>.campaigns.<id>]`): приоритет param > campaign > account
  > none; валидация (>0; 12/13 с предупреждением check); сверка с Метрикой
  best-effort. `stats_*`/`stats_compare`: блок `goal {id, label, source}`
  рядом с атрибуцией; CPA/CR по цели; при none — как v1.5.0 + warning.
  Правило журнала: перед оптимизацией читать `get_operation_log`.
- Только вымышленные данные в примерах и тестах. Бюджеты не меняются.
  Запись стратегий и Аудиторий — без изменений (guard / write_enabled=false).
  Обратная совместимость: только добавление полей.

## v1.5.0 (2026-10-01) — пакет «Знания» (A1–A11 + B2)

- B2: реестр `campaign_setting_notices` (`catalog/notices.py`), вывод в
  `campaigns_get`, перенос в dump.json, показ в MD/Excel.
- A1: `ENABLE_AREA_OF_INTEREST_TARGETING` — read_only (с 31.08.2026),
  запрет записи в `campaigns_update` до API.
- A2: `keywords_list` — пометка про автоминус Директа.
- A3: операторы минус-фраз (`"..."`, `[]`, `!`, `+`, полное пересечение);
  проверка конфликтов переписана, тесты.
- A4: правила корректировок + `calc_effective_multiplier()` с тестами;
  колонка «Итоговый множитель» в Excel.
- A5: GoalId 12/13 — служебные (вовлечённые сессии / все приоритетные цели).
- A6: `stats_*`/`stats_compare` — блок `attribution {requested, effective,
  source}`; таблица моделей в README.
- A7: стратегии API vs интерфейс (только документация, запись под guard).
- A8: ретаргетинг/Аудитории (RETARGETING vs AUDIENCE, AND/OR).
- A9: `validate_tracking_macros()` + список макросов; предупреждения
  в `campaigns_update`/`adgroups_update`; пометки в dump.
- A10: Турбо-блоки, `clients.site`, CPM-видео — недоступны через API.
- A11: пять интентов; правило «цену/отзывы/гео не минусовать автоматически».

Только вымышленные данные в примерах и тестах. Бюджеты не меняются.
Запись в Аудитории остаётся выключенной.
