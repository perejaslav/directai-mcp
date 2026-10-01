# CHANGELOG

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
