# v1.17.0 — запись целей Метрики (`metrika_goal_create/update/delete`)

Дата: 2026-10-04. Репозиторий: `D:\github\directai-mcp` (было 1.16.1 → стало
1.17.0).

## 0. Проверка версии перед началом

`describe_action` без аргументов: `server_version` 1.16.1, `code_version`
1.16.1, `disk_version` 1.16.1, предупреждений нет. Работа начата.

---

## 1. Сверка с документацией Metrika Management API

### 1.1. Источники

| Что | Ссылка |
|---|---|
| Создание цели (POST) | https://yandex.com/dev/metrika/ru/management/openapi/goal/addGoal |
| Изменение цели (PUT) | https://yandex.com/dev/metrika/ru/management/openapi/goal/editGoal |
| Удаление цели (DELETE) | https://yandex.com/dev/metrika/ru/management/openapi/goal/deleteGoal |
| Список целей (GET) | https://yandex.com/dev/metrika/ru/management/openapi/goal/goals |
| Счётчик и его права | https://yandex.com/dev/metrika/ru/management/openapi/counter/counter , https://yandex.com/dev/metrika/ru/management/openapi/counter/counters |
| Типы целей в интерфейсе | https://yandex.ru/support/metrica/ru/general/goals.html |
| Мультицель (только интерфейс) | https://yandex.ru/support/metrica/ru/general/multi.html |

Локальные копии страниц использовались только при сверке и в репозиторий не
попадают — ссылки выше актуальные и достаточные для повторной проверки.

### 1.2. Методы

| Действие | Метод и путь | Ответ |
|---|---|---|
| Создать | `POST /management/v1/counter/{counterId}/goals`, тело `{"goal": {...}}` | 200: объект цели (в т. ч. `id`) |
| Изменить | `PUT /management/v1/counter/{counterId}/goal/{goalId}`, тело `{"goal": {...}}` | 200: объект цели |
| Удалить | `DELETE /management/v1/counter/{counterId}/goal/{goalId}` | 200: `{"success": true}` |
| Прочитать | `GET /management/v1/counter/{counterId}/goals` (есть `useDeleted`) | `{"goals": [...]}` |

Отдельного `GET .../goal/{goalId}` в API нет — read-back идёт через список и
поиск по `id` (это и делает сервер). У счётчика есть
`GET /management/v1/counter/{counterId}` с полем `permission`
(`own` / `edit` / `view`) — источник проверки права записи.

### 1.3. Типы целей (GoalE) и формат условий

GoalE перечисляет **13 типов**: `action`, `chat`, `email`, `file`,
`messenger`, `number`, `payment_system`, `phone`, `search`, `social`, `step`,
`url`, `visit_duration`. Общие поля: `name` (0–255), `default_price`,
`is_favorite`, `id` (при изменении/удалении), `status`, `goal_source`
(`user`/`auto`, только в ответе).

| Тип | Поле условий | Вид условия |
|---|---|---|
| `action` | `conditions[]` | `{type: contain\|exact\|start\|regexp, url}` (до 16384) |
| `url` | `conditions[]` | `{type: contain\|exact\|start\|regexp\|action\|regexp_action\|contain_action, url}`; «выполняется хотя бы одно из условий» |
| `email` | `conditions[]` | `{type, url}`, url до 1024 (операторы в доках не перечислены) |
| `phone` | `conditions[]` + `hide_phone_number` | url до 25 |
| `messenger` | `conditions[]` | `{type, url}` |
| `search` | `conditions[]` | `{type, url}` |
| `file` | `conditions[]` | `{type: all_files}` или `{type: file, url}` |
| `social` | `conditions[]` | `{type: all_social}` или `{type: social, url}` |
| `step` | `steps[]` | шаги типа `url` или `action` с `conditions[]` |
| `number` | `depth` | целое, минимум 2 |
| `visit_duration` | `duration` | целое, минимум 1 (секунды) |

### 1.4. Мультицель — поддержки в API нет

- В GoalE типа `multi` нет (13 типов, перечислены выше).
- Мультицель — функция интерфейса Метрики: https://yandex.ru/support/metrica/ru/general/multi.html
- Эквивалент «несколько условий через ИЛИ» в API дают несколько `conditions`
  внутри одной цели (документация `UrlGoal` прямо говорит: «достигается, когда
  выполняется хотя бы одно из условий»).

**Решение:** `type=multi` не поддерживается и отклоняется **до API** с текстом,
объясняющим, что ИЛИ задаётся несколькими `conditions` одной цели. Выдумывать
несуществующий тип или молча превращать его в `url` было бы враньём.

### 1.5. Лимиты

| Лимит | Значение | Источник |
|---|---|---|
| Целей на счётчик | до 200 | справка Метрики (multi.html) |
| Условий в мультицели | до 10 | справка Метрики (только для мультицели) |
| Шагов составной цели | 2–5 | лимит интерфейса; в схеме API не документирован |
| Условий внутри обычной цели | не задокументирован | — |
| Длина `name` | 255 | схема GoalE |
| Длина `url` в условии | 1024 (`email`), 25 (`phone`), 16384 (остальные) | схемы условий |

Лимит 200 и границы шагов проверяются в `prepare` до API. Незадокументированный
лимит на число `conditions` не выдумывался — ограничивает API.

### 1.6. Права OAuth-токена

Запись целей требует права **`metrika:write`** (создание/изменение счётчиков и
целей, загрузка данных); чтению целей хватает `metrika:read`. Тот же токен,
что и у Директа. Проверено живьём: чтение целей счётчика 54578446 прошло на
текущем токене, а `POST` цели получил `403 {"error_type":"access_denied"}` —
то есть в этом окружении права `metrika:write` у токена нет (раздел 7).

---

## 2. Что сделано в коде

### 2.1. Новые файлы

- `src/directai_mcp/api/metrika.py` — транспорт Management API для записи:
  `get` / `post` / `put` / `delete`, заголовок `Authorization: OAuth <token>`
  (не Bearer), принудительный IPv4 (`local_address="0.0.0.0"`, как у Аудиторий
  и Вебмастера), тела ошибок усечены, токен не попадает в тексты.
- `src/directai_mcp/catalog/metrika_goal_write.py` — три действия записи,
  сериализация, валидация, проверки, read-back.

### 2.2. Изменённые файлы

- `src/directai_mcp/api/errors.py` — `MetrikaApiError` (с флагом `unverified`),
  `metrika_hint(status)`, `METRIKA_WRITE_SCOPE_HINT` (инструкция по
  перевыпуску токена без вывода токена).
- `src/directai_mcp/safety/guard.py` — `METRIKA_GOAL_WRITE_ACTIONS`,
  `require_owner_confirm()` + сбор причин, работающий в любом режиме guard;
  три новых действия разрешены в `check_write` (проверки — в prepare).
- `src/directai_mcp/server.py` — импорт нового модуля, сбор причин
  `owner_confirmed` при каждом `plan_write`.
- `tests/test_registry.py`, `tests/test_v116_guard.py` — счётчики действий
  (25 записывающих, 25 модулей регистрации).

### 2.3. Три действия

`metrika_goal_create` — `counter_id`, `type`, `name`, `conditions`,
`file_all`/`file_url`, `social_all`/`social_url`, `steps`, `depth`,
`duration`, `hide_phone_number`, `default_price`, `is_favorite`.

`metrika_goal_update` — `counter_id`, `goal_id` + частичное изменение:
`name`, `conditions`, `default_price`, `is_favorite`, `depth`, `duration`.
Хотя бы одно поле обязательно; в API `PUT` заменяет цель целиком, поэтому
сервер читает живую цель и собирает полное тело сам.

`metrika_goal_delete` — `counter_id`, `goal_id`.

### 2.4. Защита от ошибок (пункт 3 задачи)

| Требование | Реализация |
|---|---|
| Поиск точного дубля до create | `find_duplicate` по отпечатку (тип + нормализованные условия; порядок условий не значит, порядок шагов значит) → отказ с id и именем существующей цели |
| Точный `counter_id` обязателен | Поле обязательное, без разбора кампаний; счётчик читается живым GET |
| Проверка права edit/own | `GET counter/{id}` → `permission`; `view` и отсутствие поля → отказ до записи |
| При таймауте не повторять вслепую | Таймаут/обрыв → `MetrikaApiError(unverified=True)` → статус `unverified`, текст «Запись НЕ повторяем вслепую», решение — по перечитанным целям |

Дополнительно: лимит 200 целей и «ничего не меняется» при update — отказ до
API; цель не найдена — отказ до API.

### 2.5. Read-back (пункт 4 задачи)

После каждой записи сервер заново читает `GET counter/{id}/goals` (без кеша
процесса) и сверяет:

- create: цель найдена по `id` ответа POST (иначе по имени + отпечатку),
  отпечаток совпал → `подтверждено read-back`;
- update: имя, тип/условия и цена совпали с запрошенными;
- delete: цели с таким `id` нет.

Несовпадение → статус `unverified` с честным текстом, что именно разошлось.

### 2.6. Права токена (пункт 5 задачи)

- 401 → «токен не принят Метрикой — перевыпустите `directai-mcp set-token
  --login <ваш логин>`»;
- 403 → «у токена нет права metrika:write … в приложении Яндекс ID откройте
  доступ «Яндекс Метрика» и включите право metrika:write, затем выполните
  `directai-mcp set-token --login <ваш логин>` и вставьте новый токен в
  скрытое поле. Токен в чат не пишите»;
- 404 → «счётчик или цель не найдены».

Токен не печатается ни в одном тексте, логе или записи журнала (проверено
тестами).

---

## 3. Тесты (пункт 6 задачи)

`tests/test_v1170_metrika_goal_write.py` — 50 тестов, только моки (respx);
живые счётчики не трогаются.

- сериализация всех 11 поддерживаемых типов (включая «ИЛИ» из нескольких
  условий, порядок шагов, `all_files`/`all_social`, `hide_phone_number`);
- валидация: `multi` (с текстом про ИЛИ), неизвестный тип, пустые условия,
  недопустимый оператор, пустое значение, превышение длины, 1 и 6 шагов,
  шаг не `action|url`, `depth < 2`, `duration < 1`, `file`/`social` без
  «ровно одного» флага, пустое и слишком длинное имя, отрицательная цена,
  параметры не от того типа, update без изменений, отсутствие `counter_id`;
- отпечаток цели и поиск дубля (порядок/пробелы не значимы, порядок шагов
  значим, `number`/`visit_duration`);
- prepare: отказ при `permission: view`, отказ при отсутствии подтверждения
  права, отказ на дубль (с id и именем), отказ на лимите 200, отказ при
  отсутствии цели, отказ при «значения не меняются», сохранение незаданных
  полей при update;
- полный цикл `plan_write` → `apply_write` → read-back для create, update,
  delete (включая «повторный apply — уже применён»);
- удаление: план «⚠ ОПАСНАЯ ОПЕРАЦИЯ», отказ apply без `owner_confirmed`,
  предупреждение о необратимости, поведение одинаковое в режиме `block`,
  прямой вызов prepare вне плана — отказ;
- таймаут: статус `unverified`, ровно один вызов POST (повтора нет),
  read-back всё равно состоялся и разрешил исход;
- 401/403: текст про `metrika:write` и `set-token`, токена в тексте нет.

Попутно обновлены счётчики в `tests/test_registry.py` (реестр действий и
модулей) и `tests/test_v116_guard.py` (число записывающих действий).

**Полный прогон:** `pytest` — 786 passed, 0 failed (было 736); `ruff check
src tests` — чисто.

---

## 4. Документация и версия (пункт 8 задачи)

- `README.md`: новая §5.3 «Цели счётчика Метрики» (типы, параметры, проверки,
  read-back, таймаут), строки в таблице записи, счётчик записывающих действий
  (25), §4 (отчёты остаются только на чтение), §6 (guard для целей Метрики),
  §8 (типичные ошибки: 403 по `metrika:write`, `permission: view`).
- `docs/PUBLIC-OVERVIEW.md`: блок про цели Метрики и уточнение guard.
- `AGENTS.md` §11.1: протокол работы с записью целей.
- Skill `directai-workflow` (плагин `directai` для Codex, файл вне
  репозитория: `%USERPROFILE%\.codex\plugins\directai\skills\directai-workflow\SKILL.md`):
  пункт «как создавать цели» — типы, операторы, отсутствие `multi`, update как
  частичное изменение, удаление с `owner_confirmed`. В коммит не входит.
- `CHANGELOG.md` (v1.17.0), `DECISIONS.md` (v1.17.0 — 8 решений с обоснованием),
  версия `pyproject.toml` и `src/directai_mcp/__init__.py` → **1.17.0**.

---

## 5. Живая проверка (пункт 7 задачи) — частично, честно

Счётчик 54578446, одна попытка, больше в счётчике ничего не менялось.

1. **Чтение (прошло).** `permission=own`, 46 целей. Среди них есть
   автоцели с типами вне GoalE: `e_purchase`, `e_cart`, `a_purchase`,
   `a_create_order`, `a_begin_checkout`, `call`, `conditional_call`, `form`,
   `contact_data`, `contact_data_sent`, `payment_system`. Отсюда решение:
   update/delete автоцелей не поддержаны (в prepare — честный отказ).
2. **План создания (прошёл).** Тип `action`, имя «DirectAI test — удалить»,
   условие `exact «directai_test_goal»`. Предпросмотр показал счётчик,
   право `own`, число целей, тип и условие; предупреждение про то, что цель
   сама по себе не влияет на оптимизацию.
3. **Запись (отклонена API).**

   ```
   Применение 55b686681f3f (metrika_goal_create): статус failed.
   Ошибка API Метрики: HTTP 403: у токена нет права metrika:write … 
   (ответ: {"errors":[{"error_type":"access_denied","message":"Access Denied"}],"code":403,…})
   Read-back: read-back НЕ подтвердил: цели «DirectAI test — удалить» в списке счётчика нет.
   ```

   Цель **не создана**; read-back это подтвердил, счётчик остался с 46 целями.
   Обход не предпринимался (токен и права — решение владельца).
4. **Удаление не выполнялось** — удалять нечего.

Что нужно, чтобы замкнуть полный цикл: перевыпустить токен с правом
`metrika:write` (`directai-mcp set-token --login <логин>`) и повторить шаги
2–5 проверки. Команда проверки лежит в
`%LOCALAPPDATA%\Temp\opencode\metrika_live_check.py` (вне репозитория).

---

## 6. Что сознательно не сделано

- Типы `chat` (условия по полям/платформам/тегам чата) и `payment_system`
  (создаётся самой Метрикой) — в DirectAI не заведены, попытка даёт понятный
  текст.
- `multi` — не поддерживается, потому что такого типа в API нет.
- Изменение типа цели и шагов существующей цели (`update`) — не сделано.
- Лимит на число `conditions` внутри цели не выдумывался: в схеме API его
  нет, ограничивает сервер Яндекса.
- Проверка наличия права `metrika:write` до записи не делается отдельным
  запросом (такого «scope-check» в Management API нет) — 403 разбирается в
  тексте ошибки.
- `live_check` целиком на живом счётчике (раздел 5) ждёт токен с
  `metrika:write`.

## 7. Коммит и релиз

Изменения закоммичены одним коммитом релиза, тег `v1.17.0` поставлен на него
(AGENTS.md §16), изменения отправлены в `main`. Детали — в выводе сессии.