# CHANGELOG

## v1.14.0 (2026-10-03) — Wordstat API

- Четыре действия чтения Yandex Cloud Search API v2: `wordstat_top`,
  `wordstat_dynamics`, `wordstat_regions`, `wordstat_regions_tree`.
  Отдельный provider `wordstat` не требует OAuth-токена Директа.
- Настройка: `set-token --wordstat [--folder-id]`; API-ключ вводится скрыто
  и хранится в Credential Manager `directai-mcp-wordstat`, folderId —
  в `[wordstat] folder_id` пользовательского `accounts.toml`.
- Транспорт: `httpx.AsyncHTTPTransport(local_address="0.0.0.0")` для
  устранения наблюдавшейся живой ошибки ConnectError на Windows.
- Платные запросы Wordstat стоят примерно 0,02 ₽; лишние запросы не делать.
  В рамках проверки этого коммита живые запросы не выполнялись.
- Безопасность: секреты Ctx скрыты из repr — OAuth-токен Директа и
  API-ключ Wordstat; добавлен тест с фиктивными значениями обоих секретов.
- Проверки: профильные pytest — 13 passed; полный pytest — 675 passed,
  0 failed; `ruff check src tests` — All checks passed.

## v1.13.1 (2026-10-02) — порядок в репозитории (только документация)

- Git: v1.13.0 закоммичена и запушена; `check_blocklist.py` чист по всем
  153 отслеживаемым файлам. В репозитории нет файлов из `.gitignore`
  (`reports/`, `*.sqlite`, `accounts.toml`, `*.bak*`, `*.zip`, `__pycache__`).
- `DECISIONS.md`: добавлены записи v1.5–v1.10 (сводно), v1.11.0 (Δ = A−B,
  Δ% от B), v1.12.0 (сопоставление Директ↔Метрика ID → ID+1e8 → имя),
  v1.12.1 (будущие даты до API), v1.13.0 (отдельная таблица привязки,
  что не привязывается и почему, журнал видит только операции DirectAI).
- `AGENTS.md`: разделы 10–13 (скиллы-сценарии, отчёты Метрики, журнал
  кампании, ретаргетинг); правило «перед оптимизацией — `campaign_journal`;
  CPA по основной цели; после — snapshot и заметка `decision`».
- `docs/PUBLIC-OVERVIEW.md`: отчёты Метрики v1.12 вместо «счётчиков через
  goals.toml»; добавлены прогноз ставок/спроса, ретаргетинг (запись
  выключена по умолчанию), скиллы, журнал кампании; правило — ручные правки
  в кабинете журнал не видит, для них `changes_check`.
- README: §4 — таблица чтения дополнена тремя `campaign_journal*` (53 чтения);
  §9 «Что отложено» → ссылка на `BACKLOG.md`.
- `scripts/`: docstring помечают утилиты разработчика, ветка
  `feat/audience-api` влита в main (v1.4.1); файлы не удалены — на них
  ссылается `DECISIONS.md`.
- BACKLOG: известное ограничение журнала (не привязываются правки по id
  объявлений/фраз, расширения, shared-sets, ретаргетинг, сегменты) и идея
  `changes_check` → журнал.
- Код сервера не менялся; тесты и ruff без изменений (664 passed).

## v1.13.0 (2026-10-02) — журнал кампании (BACKLOG п.3)

- Привязка операций к кампаниям: новая таблица `operation_campaigns`
  (схема `operations` не менялась); `extract_campaign_ids` покрывает все
  22 write-действия (campaign_id из params/before/after; `campaigns_create` —
  из after; групповые правки — CampaignId в before: keywords_add, ads_create,
  adgroups_update, negatives_set, bids_set, bid_modifiers_set,
  audience_target_add); backfill старых записей при `connect` (идемпотентно);
  без привязки — warning в лог, не падение.
- Снимки: `campaign_snapshots` (direct: показы/клики/расход/CTR/CPC/конверсии;
  metrika: визиты/отказы/цели/CPA; цель по умолчанию — основная;
  будущие даты отклоняются до API, как в v1.12.1). Заметки: `campaign_notes`
  (hypothesis|decision|observation|todo).
- Инструменты (все read, без write API Директа и планов):
  `campaign_journal` (сборка + файл
  `reports/journals/<login>/<campaign_id>.md`: шапка, результаты с Δ=A−B/Δ% от B,
  история с пометкой ⚠ unverified, заметки с todo сверху, линковка
  «правка ↔ ближайший снимок до/после» без выводов), `campaign_journal_snapshot`,
  `campaign_journal_note`; `get_operation_log(campaign_id=...)`.
- Скиллы: audit — первым шагом `campaign_journal`, в конце snapshot + заметки;
  create — после создания заметка `decision` с брифом. README §13 + правило
  «перед оптимизацией — `campaign_journal`».
- Тесты: `tests/test_v1130_campaign_journal.py` (36: extract ×25+покрытие+игнор,
  backfill, рендер ×3, фильтр лога ×2, будущие даты, заметки, файл);
  обновлены `test_registry.py` (82 действия) и `test_v116_guard.py`.

## v1.12.1 (2026-10-02) — хотфикс counter_check: будущие даты

- Причина падения (воспроизведено живьём): Reports API отвечает 4001
  («Дата в параметре DateFrom должна быть не позднее текущей даты»), если
  `date_from`/`date_to` в будущем. `counter_check` теперь отклоняет такие
  даты до API понятной ошибкой (статистика без дат — только `goals_only`).
  HTTP 400 Метрики — другая причина (перевёрнутые/плохой формат дат);
  оба пути уже закрыты валидаторами форматов.
- Живая проверка: `counter_check` на поисковой кампании боевого кабинета
  (msk, 2026-09-18–2026-10-01) — блок конверсий вернул числа (клики 336,
  расход, итог LC 34 конв.), без 4001/HTTP 400.
- Тесты: `tests/test_v1121_counter_dates.py` (3, без сети).
- BACKLOG-хвост `counter_check` 4001/HTTP 400 — исправлен.

## v1.12.0 (2026-10-02) — отчёты Метрики (BACKLOG п.2)

- Ч1: `metrika_traffic` (визиты по source/utm/utm_full/direct/landing/device/
  region, фильтры utm, CR = цели/визиты), `metrika_goals_report` (строки цели,
  разрез group_by — опционально), `metrika_bytime` (динамика day|week).
  Общее: counter_id > campaign_id+логин > [metrika] counter_id; default —
  последние 14 полных дней; default-цель — основная, иначе все цели счётчика;
  атрибуция default `lastsign`; accuracy=full + пометка семплирования;
  403 — «нет доступа», 429 — один повтор; чанкинг при >20 метриках.
  Живая проверка: боевой счётчик (26 целей) — direct/utm/goals топ-5.
- Ч2: `metrika_direct_cpa` — расход/клики Директа + визиты/отказы/цели Метрики
  по кампаниям одного логина; CPA = расход / цели; флаг расхождения
  клики→визиты > 30%; несопоставленные — отдельными блоками; несколько
  счётчиков — поштучно с суммированием (цели — на каждый свои).
  Порядок сопоставления: ID → ID+1e8 (OrderID старых кампаний больше
  CampaignId на сто миллионов, подтверждено 6/6) → уникальное имя; способ — колонка
  «Связь». Живая проверка: msk, 14 дней, топ-5 по расходу — 12/12 сопоставлено.
- Скилл `directai-campaign-audit`: шаг 4 + пункт 9 checklist («клики→визиты»,
  CPA/CR по Метрике из инструмента).
- Тесты: `tests/test_v1120_metrika_reports.py` (8), `tests/test_v1121_metrika_cpa.py`
  (10: join, сдвиг, имя, флаги, несопоставленные, 403, мультисчётчик).

## v1.11.0 (2026-10-02) — скиллы «Аудит кампании» и «Создание кампании»

- Новый `skills/directai-campaign-audit` (только чтение): `campaigns_get` →
  `stats_compare` 14 vs 14 (Δ = A−B, Δ% = (A−B)/B × 100, формула подписывается;
  конверсии/CPA — только оттуда, срезы — лишь доли) → срезы → гигиена →
  отчёт `reports/audit_<login>_<campaign>_<дата>.md` + план без выполнения.
  Живой прогон: поисковая кампания боевого кабинета (~40 тыс. ₽ за 14 дней),
  отчёт не коммитится.
- Новый `skills/directai-campaign-create` (запись через планы): бриф одним
  списком (бюджет — только цифрой пользователя) → `phrases_forecast` →
  структура таблицей → «да» → `campaigns_create` (ЕПК, остановлена) →
  `adgroups_create` → `keywords_add` → `ads_create` → `extensions_create` →
  `negatives_set`, read-back каждого шага. Шаблон
  `references/campaign-template.yaml` (вымышленные данные). Живая проверка:
  `[TEST DirectAI] Проверка создания` в тестовом кабинете (создана, read-back
  по всем шагам, осталась OFF, не запускалась).
- Код сервера не менялся. Хвосты в BACKLOG: `negatives_set` → `unverified`
  при успехе; `counter_check` 4001/HTTP 400 на боевой кампании.

## v1.10.2 (2026-10-01) — read-back корректировок с BidModifier=0

- Причина журнала #73: verify `bid_modifiers_set` не запрашивал у API блоки
  `Retargeting/Demographics/SerpLayout/IncomeGrade/AdGroup` (`_MOD_SUBFIELDS`),
  поэтому такие корректировки никогда не подтверждались (включая −100%);
  плюс `add DESKTOP_ONLY` падал с `KeyError` (`_ADD_SINGLE`). Исправлено:
  недостающие `*AdjustmentFieldNames` добавлены, `_mod_value` расширен,
  `DESKTOP_ONLY` добавлен в `_ADD_SINGLE`.
- Тесты: `tests/test_v1102_modifiers_verify.py` (2: RETARGETING=0 и
  DESKTOP_ONLY=0 end-to-end; оба падают на старом коде).

## v1.10.1 (2026-10-01) — guard ретаргетинга: ЕПК-группы и служебные цели

- `audience_target_add`: `UNIFIED_AD_GROUP` совместим, если в
  `AvailableForTargetsInAdGroupTypes` есть `TEXT_AD_GROUP` (API v5 трактует
  ЕПК-группы как `TEXT_AD_GROUP`; живая проверка: привязка 48420621 на группе
  5203919471). Без `TEXT_AD_GROUP` в списке — отказ как раньше.
- `retargeting_list_create`/`update`: цели 12/13 — отказ до API («служебная
  цель, Директ не принимает её в условиях ретаргетинга (ошибка 8800)»);
  раньше было предупреждение, живьём `RetargetingLists.add` с GoalId=12
  вернул ошибку 8800 «Объект не найден».
- Тесты: `tests/test_v1101_retargeting_unified.py` (4).

## v1.10.0 (2026-10-01) — ретаргетинг и привязки (Б4, чтение + запись)

- Чтение (работает всегда): `retargeting_lists_list` — условия кабинета
  человеческим языком («выполнили всё … И НЕ выполнили ничего …»,
  расшифровка интересов 10/20/30, Scope, где используются через
  `AudienceTargets.get` по `RetargetingListIds`); `audience_targets_list` —
  привязки по кампании/группе/таргетингу/условию с именами условий.
  Существующий `audiences_list` не тронут (обратная совместимость).
- Запись (только при `[retargeting] write_enabled=true`, дефолт false):
  `retargeting_list_create` / `retargeting_list_update` /
  `retargeting_list_delete` (удаление — только неиспользуемых, без префикса
  `[TEST DirectAI]` нужно явное согласие), `audience_target_add`
  (автостратегия → ставка игнорируется, только приоритет; ручная → ставка
  0.30–5000 ₽; совместимость — живьём по `AvailableForTargetsInAdGroupTypes`,
  only-NONE — отказ) и `audience_target_state` (suspend/resume/delete).
  Валидация до API: лимиты 1–50 правил / 1–250 аргументов / срок 1–540 дней,
  запрет смены класса ALL-ANY ↔ только-NONE, цели 12/13 — предупреждение.
- `bid_modifiers_set`: вид RETARGETING (`RetargetingAdjustments`,
  до 100 на scope); −100% — явное предупреждение «показы этой аудитории
  будут полностью отключены» + заметки A4.
- Guard: неизвестные записи больше не падают в общий запрет — новый флаг
  с понятным сообщением о включении; `doctor`: пункт k (INFO).
- Живая разведка чтения (боевой кабинет, маскировано): ЕПК-группа с AUDIENCE-
  привязкой (краткосрочные интересы), Scope FOR_TARGETS_ONLY; записи живьём
  не проверялись (только моки).
- Тесты: `tests/test_v1100_retargeting.py` (20); обновлены `test_registry.py`
  (68 действий) и `test_doctor.py` (11 проверок).
- Доки: README «Ретаргетинг и аудитории», AGENTS.md §10, пример
  `[retargeting]` в `examples/accounts.toml`.

## v1.9.0 (2026-10-01) — прогноз ставок и спроса (Б2, только чтение)

- `keyword_bids_forecast`: объём трафика → ставка → цена по существующим
  фразам (`KeywordBids.get` + тексты `Keywords.get` + тип/стратегия кампании
  через `v501`); ровно один scope и один кабинет; медианы цены для объёмов
  100 и 15; автостратегии/ЕПК — пометка без ошибки; RARELY_SERVED/
  модерация/низкий спрос — статусом; автотаргетинг — отдельно;
  пагинация `get_all` без потери строк; оценка баллов в ответе + факт
  (`net_summary`), 152 — с остатком.
- `phrases_forecast`: новые фразы до добавления (Live v4
  `CreateNewForecast`/`GetForecastList`/`GetForecast`/`DeleteForecastReport`;
  ≤100 фраз, `region_ids[]` обязателен, `currency`, таймаут ≤60 с с возвратом
  `forecast_id`, удаление отчёта после успеха; лимит 5 отчётов — ошибка 31).
- Live-транспорт расширен общим `_call` (токен в теле, `locale: ru`);
  Live-прогноз баллов не тратит (нет Units).
- `keywords_add`: подсказка про `phrases_forecast`; новые действия —
  «только чтение, прогноз — не гарантия» в описаниях; каждый ответ помечен
  «прогноз Яндекса, не гарантия» + дата/регионы/валюта.
- Тесты: `tests/test_v190_forecast.py` (9: ручная таблица, авто/ЕПК,
  пагинация, автотаргетинг, цикл create→wait→get→delete, таймаут,
  лимит фраз, регистрация); обновлён `test_registry.py` (44 действия).
- Обратная совместимость: только добавления (новый модуль `forecast.py`,
  методы Live, 2 действия); поля аукциона в dump не добавлялись.

## v1.8.1 (2026-10-01) — общее хранилище планов записи

- Планы больше не живут в памяти процесса (`safety/plans.py`): один файл
  на план в `<data_dir>/plans/` (рядом с `journal.sqlite`), атомарная
  запись (temp + rename). `apply_write` из любого процесса видит план,
  созданный другим, — живая ошибка «plan_id неизвестен» устранена.
- Применение ровно один раз: смена статуса
  `pending → applying → applied/failed` через эксклюзивный lock-файл
  (протухший lock подбирается через 60 с); гонка двух `apply` — ровно один
  `applied`, второй — «уже применён». TTL 15 минут как раньше, просрочка
  чистится при обращении; исполненные планы хранятся сутки (повтор даёт
  «уже применён», а не «не найден»).
- Ошибка разделена на три: «не найден» / «просрочен (TTL 15 минут)» /
  «уже применён» (было одно сообщение на всё).
- `doctor`: новая проверка j — каталог планов доступен на запись (OK/FAIL).
- Процессы: 8 `directai-mcp.exe` от одного родителя — пул клиента
  (OpenChamber/opencode), сервер завершается по EOF stdin штатно через
  FastMCP; изменений в жизненном цикле не потребовалось.
- Тесты: `tests/test_v181_plan_store.py` (7: кросс-экземпляр, повтор,
  просрочка, гонка, мусорный id, applied после prune, конец-в-конец через
  `do_apply_write`); `tests/conftest.py` изолирует `DIRECTAI_HOME`.
- Guard/подтверждения/TTL не ослаблены; только вымышленные данные.

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
