"""File + stderr logging. Never stdout (MCP STDIO rule, SPEC 3.6).

SECURITY (v1.17.2): секреты не должны попадать в логи.

- `quiet_http_logging()` опускает `httpx` и `httpcore` до WARNING во всех
  режимах (check, doctor, probe, MCP-сервер). На INFO логгер httpx пишет
  полный URL запроса, а значит и query-параметры вида `oauth_token=…`.
  Поднимать их обратно нельзя: это логгеры чужой библиотеки, и один
  забытый `setLevel` снова утечёт секрет.
- `SecretFilter` дополнительно вычищает из текста записей параметры
  `oauth_token`, `token`, `apikey`, `api_key`, `access_token`,
  `refresh_token` — на случай, если такой URL придёт из чужого кода или из
  текста ошибки. Фильтр стоит на обработчиках по умолчанию, но применяется и
  к уже созданным обработчикам в `setup_logging`.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

#: Имя параметров, значение которых никогда не пишем в лог.
SECRET_PARAMS = (
    "oauth_token",
    "access_token",
    "refresh_token",
    "apikey",
    "api_key",
    "token",
)

_SECRET_RE = re.compile(
    r"(?i)\b(" + "|".join(SECRET_PARAMS) + r")=([^&\s\"'\\]+)"
)

#: Логгеры HTTP-клиентов: INFO пишет URL целиком, поэтому только WARNING.
NOISY_LOGGERS = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11")

REDACTED = "***"


def redact(text: str) -> str:
    """Заменить значения секретных query-параметров на `***`."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)


class SecretFilter(logging.Filter):
    """Вычищает секреты из готовой строки записи.

    Важно: нельзя чистить только `record.msg` — в шаблоне может быть
    `%s` рядом с параметром (`?oauth_token=%s`), и подстановка сломается
    («not all arguments converted»). Поэтому запись сначала
    форматируется целиком (`getMessage`), затем чистится текст, а
    `args` обнуляется: обработчик форматировать уже нечего.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 — лог не должен ронять процесс
            text = str(record.msg)
        record.msg = redact(text)
        record.args = ()
        return True


def quiet_http_logging() -> None:
    """Опустить HTTP-логгеры до WARNING (идемпотентно, режим-независимо)."""
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _add_filter(handler: logging.Handler) -> None:
    if not any(isinstance(f, SecretFilter) for f in handler.filters):
        handler.addFilter(SecretFilter())


def setup_logging(data_dir: Path, level: int = logging.INFO) -> Path:
    """Configure root logger: file + stderr only. Returns log file path."""
    log_file = data_dir / "logs" / "directai.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    eh = logging.StreamHandler(sys.stderr)
    eh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(eh)
    for handler in root.handlers:
        _add_filter(handler)
    # Секреты в логах: HTTP-логгеры молчат, фильтр-страховка на обработчиках.
    quiet_http_logging()
    return log_file