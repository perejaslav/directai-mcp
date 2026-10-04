# v1.17.2 — Security: токен Директа не попадает в лог

Дата: 2026-10-04. Репозиторий: `D:\github\directai-mcp` (1.17.1 → 1.17.2).

Инцидент: `directai-mcp check` печатал в лог
`INFO httpx: HTTP Request: GET https://login.yandex.ru/info?format=json&oauth_token=y0__…`
— OAuth-токен Директа утекал в `%USERPROFILE%\.directai\logs\directai.log`.
Регрессия внесена v1.17.1 (проверка приложения, выпустившего токен Метрики).

---

## 1. Причина

`api/metrika.py::oauth_app_info` (v1.17.1) собирал URL с токеном в query:

```python
OAUTH_INFO_URL = "https://login.yandex.ru/info?format=json&oauth_token={token}"
```

Логгер `httpx` на уровне INFO пишет полный URL каждого запроса, а `check`
вызывал `_check_metrika` → `oauth_app_info`. Отсюда 3 строки с токеном в логе
(и 452 строки `HTTP Request` — со всеми URL, включая stat-запросы Метрики).

## 2. Исправления (три уровня)

1. **Причина устранена.** Токен передаётся только заголовком:

   ```python
   resp = await c.get(OAUTH_INFO_URL, headers={"Authorization": "OAuth " + token})
   # https://login.yandex.ru/info?format=json
   ```

   Живой проверкой подтверждено, что эндпоинт заголовок принимает: 200 и с
   `OAuth`, и с `Bearer`, в ответе `client_id`. Query-параметр удалён.

2. **Класс утечки закрыт.** `log.quiet_http_logging()` опускает `httpx`,
   `httpcore`, `httpcore.connection`, `httpcore.http11` до WARNING и
   вызывается:
   - в `setup_logging()` — то есть в `init`, `set-token`,
     `set-metrika-token`, `check`, `serve`;
   - в `cmd_doctor` (doctor сам `setup_logging` не вызывал);
   - в `_probe_async` (probe — тоже);
   - в `run_server` вызов `logging.getLogger("httpx").setLevel(...)` убран —
     теперь единая точка. Уровень обратно не поднимается: это логгеры чужой
     библиотеки, и один забытый `setLevel` снова утечёт секрет.

3. **Страховка в текстах.** `log.SecretFilter` висит на всех обработчиках
   логов и чистит значения `oauth_token`, `token`, `apikey`, `api_key`,
   `access_token`, `refresh_token` → `***`. Тексты сетевых ошибок Метрики
   (`httpx.HTTPError` иногда содержит URL) проходят через `redact()`.

   Важная деталь реализации: фильтр чистит **готовую** строку
   (`record.getMessage()`) и обнуляет `args`, а не правит шаблон `msg`.
   Первая версия правила шаблон — и сломала `%`-подстановку: `?oauth_token=%s`
   превращался в `?oauth_token=***` при живом `args`, на stderr шло
   «Logging error: not all arguments converted», то есть терялся весь лог
   вместе с возможностью заметить следующую утечку. Регрессия закрыта
   тестом `test_secret_filter_keeps_percent_formatting`.

## 3. Аудит: токены больше нигде не идут в URL и не печатаются

| Место | Как передаётся секрет |
|---|---|
| Директ (`api/direct.py`) | заголовок `Authorization: Bearer` |
| Отчёты (`api/reports.py`) | заголовок `Authorization: Bearer` |
| Метрика (`api/metrika.py`, `catalog/metrika_goals.py`) | заголовок `Authorization: OAuth`; в URL — только метрики, фильтры и параметры визитов |
| Аудитории (`api/audience.py`) | заголовок `Authorization: OAuth` |
| Wordstat (`api/wordstat.py`) | заголовок `Authorization: Api-Key` |
| Вебмастер (`catalog/webmaster.py`) | заголовок `Authorization: OAuth` |
| Приложение токена (`login.yandex.ru/info`) | заголовок `Authorization: OAuth` (v1.17.2) |

Поиск по коду (`oauth_token=`, `apikey=`, `?token=`, f-строки URL с
токеном, `print`/`log.*` с токеном) — других мест нет. Значения токенов не
попадают в ответы инструментов, журнал операций (`journal.sqlite`) и
`preview` планов; `check`/`doctor` показывают только источник и длину.

## 4. Живая проверка фикса

`directai-mcp check` выполнен после фикса (с уже выпущенным токеном Метрики):

```
OK Метрика: отдельный токен Метрики (приложение 4365b214…) metrika:write:
без записи не проверяется (прав нет в API) чтение OK: счётчиков 28
```

Сверка лога по снимку до/после: **новых строк `HTTP Request` — 0, новых
строк с токеном — 0** (до фикса в том же файле было 452 и 3). Временная
копия лога, сделанная для сверки, удалена.

## 5. Живая проверка записи целей Метрики (закрыта)

Выполнена после фикса, как и просили. Счётчик 54578446, отдельный токен
Метрики, `permission=own`, 46 целей.

| Шаг | Результат |
|---|---|
| план `metrika_goal_create` (type `action`, условие `exact «directai_test_goal»`) | предпросмотр с типом и условием, предупреждение про оптимизацию |
| `apply_write` (с `acknowledge_warnings`) | **статус `applied`** — цель создана |
| read-back | подтверждён: `type=action`, условия `[{'type': 'exact', 'url': 'directai_test_goal'}]` совпали |
| план `metrika_goal_delete` | «⚠ ОПАСНАЯ ОПЕРАЦИЯ» с причиной «удаление цели … необратимо» |
| `apply_write` (`owner_confirmed=true`) | **статус `applied`** — цель удалена |
| итог | счётчик снова 46 целей, тестовой цели нет — исходное состояние |

Ничего, кроме тестовой цели, в счётчике не менялось; id и имена целей
в репозиторий не попали.

## 6. Тесты

`tests/test_v1172_no_secret_in_logs.py` — 16 тестов, только моки:

- `oauth_app_info` отправляет токен заголовком, в URL его нет
  (`request.url.query == b"format=json"`);
- текст сетевой ошибки очищается от секретов;
- `quiet_http_logging()` ставит WARNING всем перечисленным логгерам;
- `setup_logging` тишит HTTP-логгеры и вешает `SecretFilter`;
- **регрессия утечки:** INFO-запись httpx с токеном в URL не доходит до файла
  лога вообще, а URL с секретом в тексте нашего лога заменяется на `***`;
- `redact` маскирует 5 видов секретных параметров и не трогает
  несекретные (`ids`, `date1`, `metrics`);
- `check` и `doctor --json` на моках: строки токена нет ни в выводе, ни в
  логе;
- `%`-форматирование фильтром не ломается.

Полный прогон: **826 passed, 0 failed**; `ruff check src tests` — чисто.

## 7. Что делать владельцу (токен уже засветился)

1. Отозвать старый токен Директа: https://oauth.yandex.ru/mydev → «Мои
   приложения» → приложение Директа → «Отозвать токены» (или «Сменить
   токен»).
2. Удалить лог, где он засветился:
   `Remove-Item "$env:USERPROFILE\.directai\logs\directai.log"`.
3. Переустановить пакет 1.17.2 (блок команд — в ответе сессии).
4. Выпустить новый токен Директа: `directai-mcp set-token --login <логин>`.
5. Проверить: `directai-mcp check` — и убедиться, что в логе нет строк
   `HTTP Request`:

   ```powershell
   Select-String -Path "$env:USERPROFILE\.directai\logs\directai.log" -Pattern "HTTP Request|oauth_token"
   ```

   Пустой вывод — ожидаемый результат.

## 8. Документация и релиз

- `README.md` §7.2 «Секреты в логах» (+ команда удаления лога), §8.
- `AGENTS.md` §11.2: правило безопасности (токены только в заголовках,
  уровни логгеров не поднимать, увидел токен в логе — это баг).
- `CHANGELOG.md` — раздел **Security** в v1.17.2; `DECISIONS.md` —
  обоснование трёх уровней защиты и ошибки с `%`-форматированием.
- Версия `pyproject.toml` и `src/directai_mcp/__init__.py` → **1.17.2**.