# Подключение directai-mcp к ИИ-харнесам

Сначала один раз установить команду в систему:

```powershell
cd $env:USERPROFILE\directai-mcp
uv tool install --editable .
directai-mcp check
```

`directai-mcp` без аргументов запускает MCP-сервер (STDIO). Сервер читает
токен из Credential Manager (`directai-mcp set-token`) и конфиг из
`%USERPROFILE%\.directai\accounts.toml`. Логи — только в файл и stderr,
stdout занят протоколом MCP. Запись идёт через `plan_write` → ваше
подтверждение → `apply_write` (подробности в README.md §5–6).

## Claude Desktop

Файл `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "directai-mcp": {
      "command": "directai-mcp",
      "args": []
    }
  }
}
```

Если команда не на PATH, укажите полный путь к `directai-mcp.exe`
(покажет `(Get-Command directai-mcp).Source`). Перезапустите Claude Desktop.

## Claude Code

```powershell
claude mcp add --scope user directai-mcp -- directai-mcp
```

Проверка: `claude mcp list`. Конфиг хранится в `~/.claude.json`.

## OpenCode

Файл `%USERPROFILE%\.config\opencode\opencode.json`:

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

## Codex

Файл `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.directai-mcp]
command = "directai-mcp"
args = []
```

## Hermes

Hermes спрашивает подтверждение (`y`) — регистрация неинтерактивная,
ответ подаётся через pipe. Путь к exe — стабильный лаунчер (узнать:
`(Get-Command directai-mcp).Source`, обычно
`%USERPROFILE%\.local\bin\directai-mcp.exe`):

```powershell
echo y | hermes mcp add directai-mcp --command "<абсолютный путь к directai-mcp.exe>"
```

```bash
printf 'y\n' | hermes mcp add directai-mcp --command "<абсолютный путь к directai-mcp.exe>"
```

Проверка соединения:

```powershell
hermes mcp test directai-mcp
```

Затем `/reload-mcp` или новая сессия (человек перезапускает сам).
Важно: `mcp test` проверяет только соединение — доступность сервера
в сессии подтверждает лишь реальный запрос в новой сессии
(«Используй только directai-mcp: покажи расходы по всем аккаунтам
за вчера») со сверкой цифр с веб-интерфейсом Директа.

## Проверка после подключения (задать ИИ)

1. `Покажи расходы по всем аккаунтам за последние 7 дней`
   (ожидание: `stats_summary`, `account=all`, `period=LAST_7_DAYS`,
   итоги сверяются с веб-интерфейсом Директа).
2. `Покажи поисковые запросы по главной кампании за неделю и сохрани в CSV`
   (ожидание: `stats_search_queries`, таблица + путь к CSV,
   файл открывается в Excel с разделителем `;`).
