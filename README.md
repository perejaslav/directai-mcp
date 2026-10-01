# DirectAI MCP — личный сервер статистики и управления Яндекс Директом

> ИИ-агент? Следуй [AGENTS.md](AGENTS.md).

DirectAI MCP is a local MCP server for Yandex Direct stats and management.
Runs on your Windows 11 PC for a single user. Tested on Windows 11 only;
macOS/Linux not supported. Start install at §1 below.

Локальный MCP-сервер: ИИ-ассистенты (OpenCode, Codex и др.) через него читают
статистику и управляют вашими аккаунтами Яндекс Директа.
Только для одного пользователя, работает на вашем компьютере, в интернет
ничего не выставляет.

## Прежде чем устанавливать

Обязательно:

- Windows 11 (инструкции — только для PowerShell; macOS/Linux
  не тестировались и не поддерживаются);
- аккаунт Яндекс Директа с хотя бы одной кампанией (можно черновик);
  для агентств — главный логин агентства;
- своё приложение на oauth.yandex.ru (анкета на 5 минут, права Директ
  и Метрика) — запомните ID приложения;
- одобренный доступ к API Директа (Инструменты → API → заявка с ID
  приложения); одобрение занимает до нескольких дней;
- ИИ-помощник с поддержкой MCP (OpenCode, Codex, Claude Code,
  Claude Desktop, Hermes); сам DirectAI бесплатный, работает локально
  на вашем компьютере, для одного пользователя.

По ходу установки: токен получаете по ссылке, вводите сами в своём
терминале, никогда не в чат.

Желательно: доступ к счётчику Метрики (иначе CRM-выручка не отличается
от условной ценности целей).

Не нужно: программировать, покупать сервер, заранее ставить git или Python.

### С чего начать

1. Создайте приложение и подайте заявку на API — это самое долгое,
   начните с этого (подробности — §1.1, шаг 5).
2. Пока ждёте одобрения — установите DirectAI (§1 или §1.1).
3. Когда заявку одобрят — получите токен и выполните проверку
   (`set-token`, затем `check`).

## 1. Установка с нуля

Нужно: Windows 11, `uv`, `git` (оба ставятся через `winget`, см. §1.1),
токен Яндекс Директа (как получить — см. §1.1, нужен доступ к API).

```powershell
cd $env:USERPROFILE
git clone https://github.com/perejaslav/directai-mcp.git directai-mcp
cd $env:USERPROFILE\directai-mcp
uv tool install --editable .
uv tool update-shell
directai-mcp init
directai-mcp set-token --login ВАШ_ЛОГИН
directai-mcp check
```

Впишите свой логин в `%USERPROFILE%\.directai\accounts.toml` (`[auth] login`,
замените демо-алиасы) между `init` и `set-token`; в `set-token` передайте
тот же логин флагом `--login`.

Что происходит: `init` создаёт `%USERPROFILE%\.directai\` и копирует примеры
конфигов (существующие не трогает); `set-token` маскированно спрашивает токен
и кладёт его в Credential Manager Windows (в файлах токена нет никогда);
`check` проверяет доступ. Ожидание — строки `OK` по каждому аккаунту с числом
кампаний и остатком баллов.

## 1.1. Установка через ИИ-агента — промпт для новичка

Новичок (свой аккаунт Директа, ничего общего с автором) копирует блок ниже
в любого агента (OpenCode, Codex, Claude Code) — агент ставит всё с нуля.

```
Ты помогаешь новичку установить DirectAI MCP с нуля на Windows 11.
Человек не знает git, uv и PowerShell. Объясняй простыми словами,
одно действие за раз. Каждую команду давай отдельно, жди результата.
Правила: бюджеты и стратегию не менять; guard не выключать никогда;
запись только через план с подтверждением; токен только в Credential Manager.
0. Сначала спроси, одобрен ли уже доступ к API Директа. Нет — начни
с шага 5 (создать приложение и подать заявку: это самое долгое),
затем ставь DirectAI (шаги 1–4), а токен и check — после одобрения.
Доступ уже одобрен — иди по шагам 1–4, затем токен и проверка.
1. Спроси разрешение и поставь git и uv (флаги снимают лишние вопросы):
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
winget install --id astral-sh.uv -e --accept-package-agreements --accept-source-agreements
Проверь: git --version и uv --version. Нет команды — обнови PATH в этой сессии
(давай строку одним блоком, проверь, что вставилась одной строкой):
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
Нет winget — поставь App Installer из Microsoft Store.
2. Склонируй репозиторий (три команды, по одной). Папка directai-mcp уже есть —
не клонируй, а выполни git pull внутри неё:
cd $env:USERPROFILE
git clone https://github.com/perejaslav/directai-mcp.git directai-mcp
cd $env:USERPROFILE\directai-mcp
3. Установи и инициализируй (по одной команде):
uv tool install --editable .
uv tool update-shell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
directai-mcp --version
directai-mcp init
Первая установка качает Python несколько минут — это не зависание.
4. Спроси логины кабинетов (это не секрет): главный — владелец будущего токена.
Если кабинетов несколько (агентство: главный логин видит клиентские) —
попроси все. ПОЛНОСТЬЮ замени демо-алиасы в %USERPROFILE%\.directai\accounts.toml:
[auth] login — реальный главный логин, под каждый логин свой [aliases.*].
goals.toml необязателен: названия целей Метрики, можно заполнить позже.
5. Токен. Объясни: создай приложение типа «Для доступа к API или отладки»
на https://oauth.yandex.ru/client/new с правами direct:api и metrika:read.
Redirect URI настраивать не нужно — он фиксирован. Затем открой в браузере ссылку
https://oauth.yandex.ru/authorize?response_type=token&client_id=ID_ПРИЛОЖЕНИЯ&redirect_uri=https://oauth.yandex.ru/verification_code
(подставь ID со страницы приложения) и нажми «Разрешить» — токен появится
в адресной строке. Скопируй ТОЛЬКО значение после access_token= и до &,
не весь URL (вид: access_token=y0_AgA...&token_type=...). Спроси «что видишь?»:
ошибка OAuth — проверить ID приложения и пробелы. Это штатный способ из доки:
https://yandex.ru/dev/direct/doc/ru/concepts/auth-token. Затем подай заявку
на доступ к API в интерфейсе Директа (Инструменты → API → Мои заявки),
инструкция: https://yandex.ru/dev/direct/doc/ru/concepts/register.
В описании заявки укажи «личный учёт статистики и управление своими кампаниями».
Одобрение занимает до нескольких дней — ошибка 58 до этого норма, просто ждём.
Без metrika:read типы ценностей берутся из goals.toml (по умолчанию условные).
6. Токен в чат писать ЗАПРЕЩЕНО. Пусть человек САМ выполнит в своём терминале:
directai-mcp set-token — и введёт токен в скрытое поле. Символы не отображаются —
это нормально: вставить и Enter. Если вставил в чат — останови, попроси
отозвать токен в Яндекс ID и выпустить новый.
7. Проверь: directai-mcp check. Расшифруй итог: OK — работает; 53 — неверный
токен, повторить шаг 6; 58 — нет доступа к API, вернуться к шагу 5; 513 —
у логина нет аккаунта в Директе (создай кампанию в интерфейсе); 152 —
кончились баллы API, подождать до завтра. Коды:
https://yandex.ru/dev/direct/doc/ref-v5/concepts/errors-list.html
8. Самопроверка сервера: directai-mcp probe. Ожидание — две строки OK
(версия сервера, число инструментов, stats_summary найден). Баллы API
не тратятся. Сырые stdio-пробы вручную не делать — только probe.
9. Подключи харнес: сначала спроси, какой — OpenCode, Codex или Claude Code.
Сделай копию его конфига (*.bak), потом ДОПИШИ блок directai-mcp, чужие MCP
не трогай. Готовые блоки — examples/harness-configs.md.
9. Попроси человека САМОГО перезапустить харнес (закрыть все его окна
и открыть заново). В новой сессии пусть спросит:
«Используй только directai-mcp: покажи расходы по всем аккаунтам за вчера» —
и сверит цифры с веб-интерфейсом Директа. Сошлось — готово.
10. Удали свои бэкапы (*.bak-*, *-tool-backup-*), чужие файлы не трогай.
```

## 2. Подключение к харнесам

Команда запуска везде одна — `directai-mcp` (без аргументов: STDIO-сервер).
После настройки перезапустите харнес.

**OpenCode** — файл `%USERPROFILE%\.config\opencode\opencode.json`:

```json
{
  "mcp": {
    "directai-mcp": {
      "type": "local",
      "command": ["directai-mcp"],
      "enabled": true
    }
  }
}
```

**Codex** — файл `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.directai-mcp]
command = "directai-mcp"
args = []
```

Остальные (Claude Desktop, Claude Code) — готовые фрагменты в
`examples/harness-configs.md`. Если харнес не видит команду, укажите полный
путь к `directai-mcp.exe` (покажет `(Get-Command directai-mcp).Source`).

Проверка (задать ИИ): «Покажи расходы по всем аккаунтам за последние 7 дней» —
итоги должны совпасть с веб-интерфейсом Директа.

## 3. Файлы в `%USERPROFILE%\.directai\`

| Файл | Что внутри и как править |
|---|---|
| `accounts.toml` | Ваши кабинеты: `[auth] login` — владелец токена, под каждый логин свой `[aliases.*]` (короткое имя и роль). Плюс `defaults` (`include_vat`, `max_rows`, `attribution`), секция `[guard]` и секция `[audience]` (`write_enabled`, дефолт `false` — запись в Аудитории выключена). Секретов здесь нет. Править любым текстовым редактором, применяется со следующего запроса. |
| `rules.toml` | Правила: обязательный DisplayUrlPath, слова для заголовков, пороги `max_budget_ratio` / `max_bid_ratio` (предупреждения при резких изменениях). |
| `goals.toml` | `id цели → Название` (цели Метрики). В отчётах цель видна как «Название (id)», без названия — голый id. Названия вписываете вы. |
| `journal.sqlite` | Журнал всех записей (не удаляйте). |
| `exports\` | CSV/MD-выгрузки из отчётов. |
| `logs\` | Логи сервера. |

Переопределить каталог: переменная `DIRECTAI_HOME`. Токен Директа: только
Credential Manager (`directai-mcp`) или переменная `DIRECTAI_TOKEN`. Для
Яндекс.Вебмастера можно хранить отдельный токен: `directai-mcp set-token
--webmaster` (Credential Manager `directai-mcp-webmaster` или переменная
`DIRECTAI_WEBMASTER_TOKEN`); без него используется основной токен.
Запись в Яндекс Аудитории выключена по умолчанию
(`[audience] write_enabled=false`); включается только явным
`write_enabled = true`, путь тот же: `plan_write` → ваше согласие →
`apply_write`, guard требует имя сегмента `[TEST DirectAI]*`.

## 4. Что умеет сервер

Порядок работы ИИ: `search_actions` → `describe_action` → `run_read`
(чтение) или `plan_write` → показать вам → `apply_write` (запись).

Чтение (42):

| Действие | Что делает |
|---|---|
| `stats_summary` | Итоги по аккаунтам: показы, клики, расход, конверсии |
| `stats_campaigns` | Статистика по кампаниям |
| `stats_adgroups` | Статистика по группам объявлений |
| `stats_ads` | Статистика по объявлениям |
| `stats_keywords` | Статистика по фразам и автотаргетингу |
| `stats_search_queries` | Поисковые запросы пользователей |
| `stats_regions` | Статистика по регионам местонахождения |
| `stats_placements` | Статистика по площадкам РСЯ |
| `stats_devices` | Статистика по устройствам |
| `stats_audiences` | Статистика по аудиториям и ретаргетингу |
| `stats_compare` | Сравнение двух периодов (только так, не вручную) |
| `stats_custom` | Произвольный отчёт: свои поля и фильтры |
| `campaigns_list`, `campaigns_get` | Список и полные настройки кампаний |
| `adgroups_list` | Группы кампании |
| `ads_list` | Объявления (ссылки, уточнения, DisplayUrlPath; фильтр States, вкл. архивные) |
| `keywords_list` | Фразы группы или кампании |
| `negatives_audit` | Все минус-фразы кампании одним ответом |
| `extensions_list` | Быстрые ссылки, уточнения, изображения |
| `audiences_list` | Аудиторные условия и списки ретаргетинга |
| `bids_get` | Ставки фраз (поиск и сети) |
| `bid_modifiers_get` | Корректировки ставок |
| `dictionaries_get` | Справочники (регионы по названию) |
| `changes_check` | Что менялось с даты |
| `accounts_discover`, `accounts_check`, `accounts_balance` | Кабинеты: поиск, проверка доступа, баллы |
| `counter_check` | Проверка счётчиков Метрики кампании |
| `metrika_goals_list` | Цели счётчиков Метрики: id, название, тип (без статистики) |
| `moderation_check` | Статусы модерации объявлений |
| `webmaster_hosts`, `webmaster_summary`, `webmaster_query` | Вебмастер: сайты и подтверждение прав, ИКС и проблемы, произвольный read-ресурс API v4 |
| `audience_segments_list`, `audience_segment_get` | Аудитории: сегменты пользователя (тип, статус, размер) |
| `strategies_get` | Пакетные стратегии: настройки, бюджеты и цели |
| `feeds_get` | Фиды: источник, статус обработки, кампании |
| `dynamic_targets_get`, `dynamic_feed_targets_get`, `smart_targets_get` | Условия динамических объявлений и фильтры смарт-баннеров |
| `businesses_get` | Профили организаций (только по ID) |
| `turbopages_get` | Турбо-страницы: метаданные без содержимого блоков |

### Режим выгрузки (dump)

Любое действие чтения принимает параметры `dump_dir` (папка сессии) и
`dump_tag` (суффикс имени). В этом режиме ответ дополнительно сохраняется
в файлы как есть, без преобразований:

- `<NN>_<action>[_<tag>].json` — конверт ответа: `raw_items` (объекты API:
  деньги в micros, enum, null, вложенность; единственное преобразование —
  ID-ключи `*Id(s)` числом → строкой), `requested_field_names`
  (по каким полям читали), `pagination_complete` (все ли страницы
  прочитаны — честно из пагинации, не дефолт), `truncated` (неполон ли
  именно raw), `display_truncated` (обрезан ли только показ для чата),
  `warnings`. Пример: `03_keywords_list.json`, `07_ads_list_morning.json`;
- `manifest.json` — порядок файлов и их sha256 (сборка только по манифесту,
  не по glob);
- `describe_<action>.json` — схема ответа (один раз на действие).

Пример вызова: `run_read({"name": "keywords_list", "params":
{"account": "client-a", "campaign_ids": [900000036],
"dump_dir": "C:/work/dump-session", "dump_tag": "morning"}})`.
Основной потребитель — скилл `yandex-direct-campaign-dump-directai`:
он вызывает чтения, собирает `dump.json`, проверяет полноту валидатором
и строит Markdown/Excel. Вручную данные не пересказываются.

Связанные правила чтения:

- `ads_list`, параметр `states`: по умолчанию архивные не возвращаются;
  `states: ["ARCHIVED"]` (или вместе с `ON`/`OFF`/`SUSPENDED`) включает их.
- Фразы — целиком по умолчанию (включая внутрифразные минус-слова);
  укороченный показ — только явным `short_phrases: true` и только
  для отображения, raw не трогает.

Запись (15, все — только через план, см. §5):

| Действие | Что делает |
|---|---|
| `campaigns_create`, `campaigns_update`, `campaigns_state` | Создание (по умолчанию ЕПК), изменение, остановка/архив кампаний |
| `adgroups_create`, `adgroups_update` | Создание и изменение групп (тип группы — из типа кампании) |
| `ads_create`, `ads_update`, `ads_state` | Создание (для ЕПК — RESPONSIVE_AD), изменение, состояние объявлений |
| `keywords_add`, `keywords_update`, `keywords_state` | Фразы: пакетное добавление, тексты, состояние |
| `negatives_set` | Минус-фразы кампании/групп и общие наборы |
| `extensions_create` | Ссылки, уточнения, изображения |
| `bids_set` | Ставки фраз (поиск и сети) |
| `bid_modifiers_set` | Корректировки: добавить, изменить, удалить |

Запись в Аудитории (экспериментально, выключена по умолчанию
`[audience] write_enabled=false`): `audience_segment_from_file` — создание
сегмента uploading из локального CSV/TXT (phone/email, в API только SHA256-хеши;
имя только `[TEST DirectAI]*`, файл с несколькими колонками без `id_column`
отклоняется) и `audience_segment_delete` — удаление сегмента только
`[TEST DirectAI]*` по живому имени из API. Путь тот же: `plan_write` → ваше
согласие → `apply_write`; без включённого `write_enabled` оба действия
отклоняются до API.

## 5. Как работает запись

1. ИИ вызывает `plan_write` — сервер показывает предпросмотр «было → станет»
   и предупреждения (например, ставка меняется больше чем в 2 раза).
2. Вы читаете предпросмотр и явно разрешаете.
3. ИИ вызывает `apply_write` (с `acknowledge_warnings=true`, если были
   предупреждения) — сервер выполняет, затем **повторно читает объект
   (read-back)** и пишет всё в журнал.
4. Итог: `applied` (подтверждено), `partial` (часть строк отклонена API),
   `failed`, `unverified` (результат неясен — проверить вручную).

Журнал: инструмент `get_operation_log` (последние операции) и файл
`journal.sqlite`. Прямых записей в обход плана не бывает. Что можно где:
бюджеты и смена стратегии запрещены везде; в боевых кампаниях разрешены
фразы (добавление, пауза/запуск), объявления (создание, тексты, ссылки),
ссылки/уточнения, регионы групп, минусы и площадки (добавление), ставки
и корректировки; замена целиком (replace), пауза кампаний и объявлений,
удаления — только в тестовых кампаниях `[TEST DirectAI]*`.

## 5.1. Создание кампаний: ЕПК по умолчанию

- `campaigns_create` создаёт `UNIFIED_CAMPAIGN` (ЕПК, запрос на `json/v501`).
  Legacy `TEXT_CAMPAIGN` — только явным `campaign_type="TEXT_CAMPAIGN"`
  с предупреждением «устаревший тип».
- `campaigns_update`: блок стратегии и версия запроса — по типу кампании
  (`UnifiedCampaign`/`v501` для ЕПК).
- `adgroups_create`: тип группы API выводит из кампании; неизвестная
  кампания или тип — отказ до API.
- `ads_create`: для ЕПК используйте `responsive_ads` (`RESPONSIVE_AD`,
  запрос на `v501`); `TEXT_AD` допустим, но API конвертирует его
  (10251) — предупреждение требует подтверждения. Совместимость типа
  группы/кампании проверяется до API. DisplayUrlPath обязателен
  для обоих типов и сверяется в read-back.
- Цепочка создания: кампания → группа → фразы → объявление отдельными
  планами; модерация (`ads_state moderate`) в цепочку не входит и
  заблокирована guard по умолчанию — только отдельным шагом
  с явного решения пользователя.

## 6. Guard (защита)

Опасные операции — строго внутри кампаний `[TEST DirectAI]*`.
Guard это контролирует: перед каждой записью сверяет
**живое имя кампании** через API.

Как завести тестовую кампанию: создайте в интерфейсе Директа кампанию
с именем, начинающимся на `[TEST DirectAI]` (можно черновик); только в ней
доступны replace, пауза кампаний/объявлений, удаления.

Коротко о правилах:
- бюджеты и смена стратегии — запрещены везде, даже в тестовых;
- в боевых кампаниях можно: фразы, объявления, ссылки/уточнения, регионы,
  минусы и ставки (см. §5);
- только в тестовых: замены целиком, пауза кампаний/объявлений, удаления.

Блокируется: запись вне разрешённого, переименование кампаний,
модерация, чужие общие объекты, неизвестные действия.

Включён по умолчанию (`[guard] guard=true` в `accounts.toml` плюс дефолт
в коде). Выключение — **только вашим решением**: поставьте `guard = false`
(и убедитесь, что нет переменной `DIRECTAI_TEST_GUARD=1`), после тестов
верните `true`.

### 6.1 Правило для агентов-клиентов MCP

Текст блокировки guard — это **защита, а не ошибка**.

При блокировке агент:

1. останавливается и сообщает вам: какая операция, какой объект, почему
   заблокирована;
2. **не ищет и не меняет** конфиг guard (`accounts.toml`, секция `[guard]`,
   переменная `DIRECTAI_TEST_GUARD`) и вообще ничего в `safety/`;
3. **не предлагает обход** защиты;
4. **не переносит** операцию на другую кампанию (в т.ч. тестовую) без вашего
   явного указания.

Снятие ограничения — только ваше ручное решение (см. §6). Ни один инструмент
сервера конфиг guard не пишет и не читает наружу: единственный источник —
`accounts.toml` и переменная окружения.

### 6.2 Сравнение периодов и выбор инструмента

1. Сравнение периодов агент выполняет **только через `stats_compare`**
   (A, B, Δ абс., Δ% одним вызовом). Два вызова `stats_*` с ручной
   арифметикой запрещены — они дают разную точность Δ%.
2. Если вы указали конкретный MCP-сервер или инструмент — агент использует
   **только его** и не подменяет другим.

## 7. Обновление

Перед обновлением: закройте все окна харнесов и остановите фоновый
шлюз Hermes (он держит `directai-mcp.exe` даже при закрытых окнах) —
команды выполняет человек в обычном PowerShell:

```powershell
Get-Process directai-mcp -ErrorAction SilentlyContinue | Stop-Process -Force
hermes -p default gateway stop 2>$null
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'hermes|openchamber|opencode' -and $_.ProcessId -ne $PID } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'directai-mcp.exe' -or $_.CommandLine -match 'hermes|openchamber|opencode' } |
  Select-Object ProcessId, Name
```

Фильтр по `python.exe … hermes_cli.main … gateway run` не использовать:
Hermes запущен отдельным процессом (direct spawn) и таким фильтром не ловится.
Чужие процессы агент не убивает — команды выполняет человек.

Проверка перед переустановкой (c и d должны быть OK):

```powershell
directai-mcp doctor --preinstall
```

Обновление с бэкапом вне uv tool dir и откатом при ошибке
(без `exit` — он закрывает окно PowerShell):

```powershell
cd $env:USERPROFILE\directai-mcp
git pull
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$tooldir = uv tool dir
Copy-Item "$tooldir\directai-mcp" "$env:USERPROFILE\directai-mcp-tool-backup-$stamp" -Recurse
uv tool install --force --editable .
uv tool update-shell
if ($LASTEXITCODE -ne 0) {
  Remove-Item "$tooldir\directai-mcp" -Recurse -Force
  Copy-Item "$env:USERPROFILE\directai-mcp-tool-backup-$stamp" "$tooldir\directai-mcp" -Recurse
  Write-Output "ОТКАТ: инструмент восстановлен из бэкапа"
} else {
  directai-mcp check
  directai-mcp probe
  Remove-Item "$env:USERPROFILE\directai-mcp-tool-backup-$stamp" -Recurse -Force
}
```

`init` после обновления можно повторить: существующие конфиги не затрутся,
недостающие (например, новые примеры) докопируются. После обновления
откройте харнесы заново и запустите Hermes (сервер подхватывается при
старте). Никогда не делайте `cd` внутрь каталога установки (`uv tool dir`):
бэкап перед установкой — только вне tool dir (копии внутри tool dir `uv`
считает инструментами и выдаёт `malformed`); бэкап удаляется только при
успехе, при ошибке — откат из бэкапа.

## 8. Типичные ошибки

| Симптом | Что делать |
|---|---|
| Ошибка 53 (авторизация) | Токен недействителен: `directai-mcp set-token`, затем `check` |
| Ошибка 58 (регистрация приложения) | Завершите заявку на доступ к API в интерфейсе Директа |
| Ошибка 513 (логин не подключён) | Логин не привязан к Директу — проверьте `Client-Login`/аккаунт |
| Не хватает баллов API | Подождите сброса лимита; остаток виден в `check` |
| Харнес не видит сервер | Стабильный путь к exe — `(Get-Command directai-mcp).Source` (обычно `%USERPROFILE%\.local\bin\directai-mcp.exe`); перезапуск харнеса, логи в `.directai\logs` |
| План с предупреждениями не применяется | Это защита: повторите `apply_write` с `acknowledge_warnings=true` только после вашего согласия |
| `token missing for login 'X'` | Токен сохранён под другим логином: выполните `directai-mcp set-token --login <[auth] login>` с логином из `accounts.toml` |
| Вебмастер: «нет права» / «токен не принят» | Нужен отдельный токен: `directai-mcp set-token --webmaster` (приложение «для доступа к API» с правом `webmaster:hostinfo`) |
| `Ignoring malformed tool` | Битая копия/рецепт, не повод сносить рабочий инструмент: закройте все окна харнесов и переустановите с `--force` (бэкап — вне tool dir). `uninstall` — только для заведомо мусорных записей |
| `os error 32` при переустановке | exe занят MCP-клиентами: покажите владельцев (`Get-CimInstance Win32_Process -Filter 'Name="directai-mcp.exe"'`, поле `ParentProcessId`), чужие процессы не убивайте. Шлюз Hermes (`python.exe … hermes_cli.main … gateway run`) работает в фоне и держит exe даже при закрытых окнах — человек останавливает его сам (см. §7: остановка сервера и шлюза), после установки запускает Hermes заново. Висящие `opencode serve` — тоже владельцы: их закрывают штатно, не `kill` |
| Как быстро проверить сервер | `directai-mcp --version` → `check` → `probe` (две строки OK, `stats_summary` найден; баллы не тратятся). Сырые stdio-пробы вручную не делать |

## 8.1. Диагностика (doctor)

При проблеме подключения — сначала doctor (агент — см. AGENTS.md §4.1):

```powershell
directai-mcp doctor
directai-mcp doctor --skip-api     # без сети
directai-mcp doctor --json         # машинный вывод для скилла
directai-mcp doctor --preinstall   # строгая проверка перед переустановкой
```

Девять проверок по порядку (версия, exe, процессы, блокировка файла,
конфиг, токены, API, Hermes, Аудитории) — каждая OK / WARN / FAIL
с причиной и рекомендуемым действием. Код возврата: 0 — всё OK,
1 — есть WARN, 2 — есть FAIL. Токены не печатаются (только есть/нет/длина).
Занятый exe и работающие сессии по умолчанию — INFO, а не тревога;
строго (WARN/FAIL) — только с `--preinstall` перед переустановкой.
Диагностика read-only: ничего не останавливает и не правит, чинит человек
готовым блоком из `skills/directai-connection-doctor/references/playbooks.md`.

Скилл `directai-connection-doctor` (триггеры: «MCP не работает»,
«os error 32», «hermes не видит», коды 513/58/53) лежит в репозитории
(`skills/directai-connection-doctor/`); установка локально — скопировать
папку в `%USERPROFILE%\.agents\skills\directai-connection-doctor`.

## 9. Что отложено

Вордстат, удалённый HTTP-режим, многопользовательский режим.
Метрика частично уже внутри: типы целей — из API Метрики (`metrika:read`),
`goals.toml` — названия и запасной тип; счётчики проверяет `counter_check`.

## 10. Знания Директа (пакет A1–A11 + B2)

Авторство части знаний: см. NOTICE.md (awaik/direct-mcp-ai-project, MIT).

- A1: `ENABLE_AREA_OF_INTEREST_TARGETING` отменено 31.08.2026 — только чтение
  (`campaigns_get` + `campaign_setting_notices`); запись отклоняется.
  Поля из `notices` не писать никогда.
- A2: Директ сам добавляет минус-слова в пересекающиеся фразы группы
  (`keywords_list`); автоминус не считать ошибкой.
- A3: операторы минус-фраз — см. раздел «Минус-фразы» ниже.
- A4: корректировки — разные категории перемножаются, внутри категории
  наибольшая, −100% низший приоритет, групповая перекрывает кампанию;
  в конверсионных — влияет на CPA/ДРР (`bid_modifiers_set`).
- A5: GoalId 12 = вовлечённые сессии, 13 = все приоритетные цели (служебные).
- A6: атрибуция — Метрика с 25.06.2026 cross-device, у Директа свои enum;
  каждый `stats_*` показывает `attribution {requested, effective, source}`.
- A7: `WB_MAXIMUM_CLICKS` требует `WeeklySpendLimit`; `AVERAGE_CPC` отдельная
  стратегия с `AverageCpc` (`strategies_get`).
- A8: ЕПК/текстово-графические — `RETARGETING` с goal_id; `AUDIENCE` только
  медийные; поиск AND, сети OR (`audiences_list`).
- A9: макросы трекинга (`campaigns_update`/`adgroups_update`, TrackingParams).
- A10: Турбо-блоки, `clients.site`, CPM-видео — только интерфейс.
- A11: интенты — 5 классов; цену/отзывы/гео не минусовать автоматически.

### Минус-фразы

- `"..."` — запрос только из этих слов; `[]` — порядок; `!` — словоформа;
  `+` — обязательность; полное пересечение минуса с ключом отменяет минус.
- Проверка конфликтов — в `dump_to_xlsx.py` (функция `neg_match`), тесты
  на вымышленных фразах.

### Атрибуция

| Система | Модели |
|---|---|
| Директ | FCCD, LC, LSCCD, AUTO (прямого соответствия Метрике нет) |
| Метрика (legacy → cross-device с 25.06.2026) | lastsign → cross_device_last_significant и др. |

Фактическая модель Reports API отдельно не возвращает: в `effective`
подставляется запрошенная модель (`effective_attribution`), `source` —
откуда взялась (`api` — явно в параметрах, `config` — дефолт accounts.toml).
`effective=null`, `source="not_reported"` — только если модели действительно
нет. Без целей Reports игнорирует AttributionModels (фактически LC) —
это помечается в ответе отдельно.

## 11. Статусы поиска (B1, v1.6.0)

Каждый ответ `campaigns_get`/`campaigns_list` несёт строку
`Статус поиска: <lookup_status>, presence=..., proves_account_empty=...`
и то же в dump-конверте/manifest (`lookup_status`).

| lookup_status | Когда | presence |
|---|---|---|
| `resolved` | объект найден в Campaigns API | `configured` |
| `resolved` | в Campaigns API нет, в Reports есть данные (архив/удалена/чужой доступ) | `statistics_only` |
| `not_observed` | нет ни в API, ни в Reports за проверенный период (период указан в message). Не утверждается, что кампании не существует | `null` |
| `ambiguous` | поиск по имени/подстроке дал >1 совпадения; возвращаются кандидаты (id, name, state) | `null` |
| `incomplete` | ответ частичный (пагинация/лимит/ошибка части запросов); указано, что не проверено | `null` |
| `failed` | ошибка API/сети; код ошибки Директа, без трактовки как «пусто» | `null` |

Проверка Reports для `statistics_only` — только когда Campaigns API вернул
пусто по явному ID; один лёгкий отчёт (`CAMPAIGN_PERFORMANCE_REPORT`,
`CampaignId`, `Impressions`/`Clicks`, период по умолчанию 90 дней,
параметр `lookup_days` переопределяет). Пустой `campaigns_list` —
`proves_account_empty=false` + причины (фильтр, права, логин):
пусто ≠ объекта нет. Запись разрешена только при `resolved+configured`
(guard отклоняет остальное до API).

## 12. Основная цель (B3, v1.6.0)

`accounts.toml`: необязательное поле кабинета `primary_conversion_goal_id`
(вымышленный пример: `900000123`) и переопределение по кампании
`[aliases.<имя>.campaigns.<campaign_id>] primary_conversion_goal_id`
(также `[accounts.<имя>.campaigns.<id>]`, пример: `900000456`).
Приоритет: явный параметр запроса `primary_goal` > кампания > кабинет > нет.
Валидация: целое > 0; служебные `12`/`13` допустимы, но `check` предупреждает
с расшифровкой (вовлечённые сессии / все приоритетные цели); сверка с целями
Метрики — best-effort, отсутствие доступа — предупреждение, не ошибка.
`stats_*`/`stats_compare`: блок `Цель: id=..., label=..., source=param|campaign|account|none`
рядом с атрибуцией; CPA/CR — по этой цели; при `source=none` — как в v1.5.0
+ warning «основная цель не задана, CPA по всем целям».
Правило для агентов: перед оптимизацией читать журнал операций
(`get_operation_log`) и считать CPA по основной цели.
