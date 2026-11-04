# Обновление и диагностика

Что делать, если сервер не работает, не виден в харнесе или требует
обновления. Все команды — в обычном PowerShell.

## Порядок проверки

1. `directai-mcp --version` — версия совпадает с версией репозитория.
2. `directai-mcp check` — строки `OK` по каждому аккаунту.
3. `directai-mcp probe` — две строки `OK`: версия сервера, число
   инструментов, найденный `stats_summary`. Баллы API не тратятся.
4. Если проблема осталась — `directai-mcp doctor`.

Сырые stdio-пробы вручную (echo JSON в stdin процесса) не делайте —
только `probe`.

## doctor

```powershell
directai-mcp doctor
directai-mcp doctor --skip-api     # без сети
directai-mcp doctor --json         # машинный вывод для скилла
directai-mcp doctor --preinstall   # строгая проверка перед переустановкой
```

Двенадцать проверок по порядку: версия, exe, процессы, блокировка файла,
конфиг, токены, API, Hermes, Аудитории, ретаргетинг, Метрика, каталог
планов. Каждая — OK, WARN или FAIL с причиной и рекомендуемым действием.

Код возврата: `0` — всё OK, `1` — есть WARN, `2` — есть FAIL.

Токены не печатаются (только есть/нет/длина). Занятый exe и работающие
сессии по умолчанию — INFO, а не тревога; строго (WARN/FAIL) — только
с `--preinstall`.

Диагностика read-only: ничего не останавливает и не правит. Чинит человек
готовым блоком из
[`skills/directai-connection-doctor/references/playbooks.md`](../skills/directai-connection-doctor/references/playbooks.md).

Скилл `directai-connection-doctor` (триггеры: «MCP не работает»,
«os error 32», «hermes не видит», коды 513/58/53) лежит в репозитории;
установка локально — скопировать папку
в `%USERPROFILE%\.agents\skills\directai-connection-doctor`.

Версии сверяет скрипт
`skills/directai-connection-doctor/scripts/check-versions.ps1`: он читает
`metadata.directai_mcp` из скиллов и сравнивает с `directai-mcp --version`.
Сервер новее — WARN (перед записью перепроверить `describe_action`),
сервер старее — FAIL (запись не выполнять).

## Обновление

Перед обновлением закройте все окна харнесов и остановите фоновый шлюз
Hermes: он держит `directai-mcp.exe` даже при закрытых окнах. Команды
выполняет человек в обычном PowerShell:

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
Hermes запущен отдельным процессом (direct spawn) и таким фильтром
не ловится. Чужие процессы агент не убивает — команды выполняет человек.

Проверка перед переустановкой (проверки `c` и `d` должны быть OK):

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

После обновления:

- `init` можно повторить: существующие конфиги не затрутся, недостающие
  докопируются;
- откройте харнесы заново; Hermes запустите снова — сервер подхватывается
  при старте.

Никогда не делайте `cd` внутрь каталога установки (`uv tool dir`):
бэкап перед установкой — только вне tool dir (копии внутри tool dir `uv`
считает инструментами и выдаёт `malformed`). Бэкап удаляется только
при успехе; при ошибке — откат из бэкапа.

## Устаревшая схема MCP

Признак: инструмент работает по старым правилам, новых параметров нет.

1. `describe_action` без аргументов покажет `server_version`,
   `code_version`, `disk_version`.
2. `code_version` ≠ `disk_version` — перезапустите MCP-сервер
   (закройте и откройте харнес).
3. `server_version` ≠ `code_version` — переустановите пакет
   (`uv tool install --force --editable .`).

У editable-установки метаданные обновляются только переустановкой,
перезапуск сервера не поможет.

## Типичные ошибки

| Симптом | Что делать |
|---|---|
| Ошибка 53 (авторизация) | Токен недействителен: `directai-mcp set-token`, затем `check` |
| Ошибка 58 (регистрация приложения) | Завершите заявку на доступ к API в интерфейсе Директа |
| Ошибка 513 (логин не подключён) | Логин не привязан к Директу — проверьте `Client-Login` и аккаунт |
| Не хватает баллов API | Подождите сброса лимита; остаток виден в `check` |
| Харнес не видит сервер | Стабильный путь к exe — `(Get-Command directai-mcp).Source` (обычно `%USERPROFILE%\.local\bin\directai-mcp.exe`); перезапуск харнеса; логи в `.directai\logs` |
| План с предупреждениями не применяется | Это защита: повторите `apply_write` с `acknowledge_warnings=true` только после вашего согласия |
| `token missing for login 'X'` | Токен сохранён под другим логином: выполните `directai-mcp set-token --login <[auth] login>` с логином из `accounts.toml` |
| Вебмастер: «нет права» или «токен не принят» | Нужен отдельный токен: `directai-mcp set-token --webmaster` (приложение «для доступа к API» с правом `webmaster:hostinfo`) |
| Метрика: 403 при записи целей | Нет отдельного токена Метрики или в нём нет права `metrika:write`: `directai-mcp set-metrika-token --login <логин>`. Токен в чат не пишите |
| Метрика: «запись целей Метрики требует отдельный токен» | Тот же текст без обращения к API: запись не начинается, пока не выпущен отдельный токен |
| Метрика: «у вас доступ только просмотр» | Счётчик открыт гостю с уровнем `view`: запись целей невозможна, нужен `edit` |
| Wordstat: «API key missing» или «folderId не настроен» | Выполните `directai-mcp set-token --wordstat`; ключ вводится скрыто, `folderId` хранится в `[wordstat]` |
| Wordstat: 403 | Сначала проверьте `folder_id` в `accounts.toml`, затем права ключа |
| Wordstat: сетевая ошибка | Проверьте транспорт `local_address="0.0.0.0"` |
| `Ignoring malformed tool` | Битая копия или рецепт — не повод сносить рабочий инструмент: закройте все окна харнесов и переустановите с `--force` (бэкап — вне tool dir). `uninstall` — только для заведомо мусорных записей |
| `os error 32` при переустановке | exe занят MCP-клиентами: покажите владельцев (`Get-CimInstance Win32_Process -Filter 'Name="directai-mcp.exe"'`, поле `ParentProcessId`), чужие процессы не убивайте. Шлюз Hermes работает в фоне и держит exe даже при закрытых окнах — человек останавливает его сам (см. «Обновление»), после установки запускает Hermes заново. Висящие `opencode serve` — тоже владельцы: их закрывают штатно, не `kill` |
| Как быстро проверить сервер | `directai-mcp --version` → `check` → `probe`. `check` обращается к API (тратит немного баллов), `probe` — локальная проверка MCP: две строки OK, `stats_summary` найден, баллы не тратит |

## Если ничего не помогло

Запустите `directai-mcp doctor --json` и передайте вывод агенту или
в issue репозитория: <https://github.com/perejaslav/directai-mcp/issues>.
Без значения токена и без данных кабинетов — они в выводе не печатаются.
