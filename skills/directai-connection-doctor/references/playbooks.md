# Playbooks: готовые блоки PowerShell под вывод doctor

Каждый рецепт — один блок целиком. Человек выполняет сам в обычном
PowerShell при закрытых окнах харнесов. Агент kill не делает.

## Канонический блок остановки (основа всех рецептов)

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

Фильтр по `python.exe … hermes_cli.main … gateway run` не использовать
(direct spawn). Пустой результат последней команды = всё остановлено.

## Зависший exe / os error 32 (проверки c, d)

```powershell
Get-CimInstance Win32_Process -Filter 'Name="directai-mcp.exe"' |
  Select-Object ProcessId, ParentProcessId, CommandLine
```

Показать владельцем пользователю, дальше — канонический блок остановки,
затем переустановка (см. «Полная переустановка»).

## Hermes не видит сервер (проверка h)

```powershell
hermes -p default gateway start
hermes mcp test directai-mcp
```

Не помогло — проверить регистрацию по `examples/harness-configs.md`
(блок directai-mcp дописать, чужие MCP не трогать, копию конфига `*.bak`),
перезапустить харнес, открыть новую сессию. Тест соединения ≠ доступность
в сессии.

## Токен истёк / отсутствует (проверки f, g код 53)

```powershell
directai-mcp set-token --login ВАШ_ЛОГИН
directai-mcp check
```

Токен вводит человек в скрытое поле, never в чат. Вставил в чат —
отозвать в Яндекс ID и выпустить новый.

## 513 — логин не подключён

Проверить `Client-Login`/аккаунт в `%USERPROFILE%\.directai\accounts.toml`
(`[auth] login`, `[aliases.*]`), создать кампанию в интерфейсе Директа.

## 58 — нет доступа к API

Завершить заявку в интерфейсе Директа (Инструменты → API → Мои заявки),
дождаться одобрения (до нескольких дней). Ошибка 58 до одобрения — норма.

## 152 — кончились баллы

Подождать сброса лимита; остаток виден в `directai-mcp check`.

## Полная переустановка (канонический блок, README §7)

Без `exit` — он закрывает окно PowerShell:

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

Затем: `hermes -p default gateway start`, открыть харнесы заново,
реальный запрос в новой сессии со сверкой цифр с веб-интерфейсом.
Бэкап удаляется только при успехе, при ошибке — откат из бэкапа.
Никогда не делать `cd` внутрь `uv tool dir`.
