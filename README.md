# DirectAI MCP — личный сервер статистики и управления Яндекс Директом

> ИИ-агент? Следуй [AGENTS.md](AGENTS.md).

DirectAI MCP is a local MCP server for Yandex Direct stats and management.
Runs on your Windows 11 PC for a single user. Start install at §1 below.

Локальный MCP-сервер: ИИ-ассистенты (OpenCode, Codex и др.) через него читают
статистику и управляют вашими аккаунтами Яндекс Директа.
Только для одного пользователя, работает на вашем компьютере, в интернет
ничего не выставляет.

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
путь к `directai-mcp.exe` (покажет `uv tool dir`).

Проверка (задать ИИ): «Покажи расходы по всем аккаунтам за последние 7 дней» —
итоги должны совпасть с веб-интерфейсом Директа.

## 3. Файлы в `%USERPROFILE%\.directai\`

| Файл | Что внутри и как править |
|---|---|
| `accounts.toml` | Ваши кабинеты: `[auth] login` — владелец токена, под каждый логин свой `[aliases.*]` (короткое имя и роль). Плюс `defaults` (`include_vat`, `max_rows`, `attribution`) и секция `[guard]`. Секретов здесь нет. Править любым текстовым редактором, применяется со следующего запроса. |
| `rules.toml` | Правила: обязательный DisplayUrlPath, слова для заголовков, пороги `max_budget_ratio` / `max_bid_ratio` (предупреждения при резких изменениях). |
| `goals.toml` | `id цели → Название` (цели Метрики). В отчётах цель видна как «Название (id)», без названия — голый id. Названия вписываете вы. |
| `journal.sqlite` | Журнал всех записей (не удаляйте). |
| `exports\` | CSV/MD-выгрузки из отчётов. |
| `logs\` | Логи сервера. |

Переопределить каталог: переменная `DIRECTAI_HOME`. Токен: только Credential
Manager (`directai-mcp`) или переменная `DIRECTAI_TOKEN`.

## 4. Что умеет сервер

Порядок работы ИИ: `search_actions` → `describe_action` → `run_read`
(чтение) или `plan_write` → показать вам → `apply_write` (запись).

Чтение (29):

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
| `ads_list` | Объявления (ссылки, уточнения, DisplayUrlPath) |
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
| `moderation_check` | Статусы модерации объявлений |

Запись (15, все — только через план, см. §5):

| Действие | Что делает |
|---|---|
| `campaigns_create`, `campaigns_update`, `campaigns_state` | Создание, изменение, остановка/архив кампаний |
| `adgroups_create`, `adgroups_update` | Создание и изменение групп |
| `ads_create`, `ads_update`, `ads_state` | Создание, изменение, состояние объявлений |
| `keywords_add`, `keywords_update`, `keywords_state` | Фразы: пакетное добавление, тексты, состояние |
| `negatives_set` | Минус-фразы кампании/групп и общие наборы |
| `extensions_create` | Ссылки, уточнения, изображения |
| `bids_set` | Ставки фраз (поиск и сети) |
| `bid_modifiers_set` | Корректировки: добавить, изменить, удалить |

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
Get-Process directai-mcp -ErrorAction SilentlyContinue | Stop-Process
Get-CimInstance Win32_Process -Filter 'Name="python.exe"' |
  Where-Object { $_.CommandLine -like '*hermes_cli.main*gateway run*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId }
```

Обновление с бэкапом вне uv tool dir и откатом при ошибке
(без `exit` — он закрывает окно PowerShell):

```powershell
cd $env:USERPROFILE\directai-mcp
git pull
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$tooldir = uv tool dir
Copy-Item "$tooldir\directai-mcp" "$env:USERPROFILE\directai-mcp-tool-backup-$stamp" -Recurse
uv tool install --editable .
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
| `Ignoring malformed tool` | Битая копия/рецепт, не повод сносить рабочий инструмент: закройте все окна харнесов и переустановите с `--force` (бэкап — вне tool dir). `uninstall` — только для заведомо мусорных записей |
| `os error 32` при переустановке | exe занят MCP-клиентами: покажите владельцев (`Get-CimInstance Win32_Process -Filter 'Name="directai-mcp.exe"'`, поле `ParentProcessId`), чужие процессы не убивайте. Шлюз Hermes (`python.exe … hermes_cli.main … gateway run`) работает в фоне и держит exe даже при закрытых окнах — человек останавливает его сам (см. §7: остановка сервера и шлюза), после установки запускает Hermes заново. Висящие `opencode serve` — тоже владельцы: их закрывают штатно, не `kill` |
| Как быстро проверить сервер | `directai-mcp --version` → `check` → `probe` (две строки OK, `stats_summary` найден; баллы не тратятся). Сырые stdio-пробы вручную не делать |

## 9. Что отложено

Вордстат, удалённый HTTP-режим, многопользовательский режим.
Метрика частично уже внутри: типы целей — из API Метрики (`metrika:read`),
`goals.toml` — названия и запасной тип; счётчики проверяет `counter_check`.
