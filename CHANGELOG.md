# CHANGELOG

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
