"""Connection doctor: диагностика «почему Direct AI MCP не работает».

Read-only: ничего не убивает, не ставит, конфиги не правит. По каждой
проверке — OK / WARN / FAIL + причина + рекомендуемое действие.
Секреты (токены) в вывод никогда не попадают — только «есть/нет/длина».

Использование: `directai-mcp doctor [--json] [--skip-api]`.
Код возврата: 0 — всё OK, 1 — есть WARN, 2 — есть FAIL.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from directai_mcp import __version__

DOCTOR_SCHEMA_VERSION = 1

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"

# Таблица кодов ошибок Директа в одном месте: код -> (что значит, что делать).
# Человеческие формулировки; технические тексты — в api/errors.py.
ERROR_GUIDE: dict[int, tuple[str, str]] = {
    52: (
        "OAuth недоступен (сеть или сервис авторизации)",
        "подождать и повторить; если повторяется — проверить сеть",
    ),
    53: (
        "неверный токен (авторизация не прошла)",
        "выполнить `directai-mcp set-token --login <логин>`, затем `directai-mcp check`",
    ),
    58: (
        "нет доступа к API (приложение не зарегистрировано)",
        "завершить заявку на доступ к API в интерфейсе Директа (Инструменты → API)",
    ),
    152: (
        "кончились баллы API",
        "подождать сброса лимита; остаток виден в `directai-mcp check`",
    ),
    506: (
        "ошибка соединения с API",
        "повторить позже",
    ),
    513: (
        "логин не подключён к Директу",
        "проверить Client-Login/аккаунт; создать кампанию в интерфейсе Директа",
    ),
    1000: (
        "сервис Директа временно недоступен",
        "повторить позже",
    ),
    1020: (
        "внутренняя ошибка Директа",
        "повторить позже; при повторе — в поддержку с request_id",
    ),
}

# Процессы, которые держат directai-mcp.exe (имена образов, нижний регистр).
HOLDER_PROCESS_NAMES = ("directai-mcp.exe", "hermes.exe", "openchamber", "opencode")


@dataclass
class CheckResult:
    id: str
    name: str
    status: str
    detail: str = ""
    hint: str = ""
    skipped: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _repo_root() -> Path | None:
    root = Path(__file__).resolve().parent.parent.parent
    if (root / "pyproject.toml").is_file():
        return root
    return None


def _pyproject_version(root: Path) -> str | None:
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _decode(out: bytes) -> str:
    """UTF-8, иначе cp1251 (русская Windows): tasklist — OEM, hermes — UTF-8."""
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return out.decode("cp1251", errors="replace")


def _run_text(args: list[str], timeout: float, cwd: str | None = None):
    """Запуск с безопасной декодировкой; None — не запустилось."""
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 and args[0] != "hermes":
        return None
    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)


def _git_describe(root: Path | None) -> str | None:
    cwd = str(root) if root else None
    res = _run_text(["git", "describe", "--tags", "--dirty", "--always"], 10, cwd)
    if res is None:
        return None
    code, out, _ = res
    if code != 0:
        return None
    return out.strip() or None


def check_version() -> CheckResult:
    """a. Версия пакета vs pyproject vs git describe."""
    pkg = __version__
    root = _repo_root()
    parts = [f"пакет {pkg}"]
    if root is None:
        return CheckResult(
            id="a",
            name="Версия",
            status=STATUS_WARN,
            detail=f"{', '.join(parts)}; pyproject не найден (не исходная установка)",
            hint="переустановить: `uv tool install --force --editable .`",
        )
    proj = _pyproject_version(root)
    git = _git_describe(root)
    if proj:
        parts.append(f"pyproject {proj}")
    if git:
        parts.append(f"git {git}")
    mismatch = (proj is not None and proj != pkg) or (
        git is not None and not git.startswith(pkg) and pkg not in git
    )
    unknown = proj is None or git is None
    if mismatch:
        return CheckResult(
            id="a",
            name="Версия",
            status=STATUS_WARN,
            detail="; ".join(parts) + " — расхождение",
            hint="переустановить: `uv tool install --force --editable .`",
        )
    if unknown:
        missing = []
        if proj is None:
            missing.append("pyproject")
        if git is None:
            missing.append("git")
        return CheckResult(
            id="a",
            name="Версия",
            status=STATUS_WARN,
            detail="; ".join(parts) + f" (не проверено: {', '.join(missing)})",
            hint="переустановить: `uv tool install --force --editable .`",
        )
    return CheckResult(id="a", name="Версия", status=STATUS_OK, detail="; ".join(parts))


def _which_all(name: str) -> list[str]:
    found = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file():
            found.append(str(candidate))
    seen, unique = set(), []
    for path in found:
        key = path.lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def exe_candidates() -> list[str]:
    names = (
        ["directai-mcp.exe", "directai-mcp"]
        if sys.platform == "win32"
        else ["directai-mcp"]
    )
    out: list[str] = []
    for name in names:
        out.extend(_which_all(name))
    seen, unique = set(), []
    for path in out:
        key = path.lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def check_exe() -> CheckResult:
    """b. Путь exe; несколько exe в PATH — WARN."""
    found = exe_candidates()
    if not found:
        return CheckResult(
            id="b",
            name="Exe",
            status=STATUS_FAIL,
            detail="directai-mcp не найден в PATH",
            hint="переустановить: `uv tool install --force --editable .`, затем `uv tool update-shell`",
        )
    if len(found) > 1:
        return CheckResult(
            id="b",
            name="Exe",
            status=STATUS_WARN,
            detail="несколько exe в PATH: " + "; ".join(found),
            hint="оставить один (%USERPROFILE%\\.local\\bin), убрать дубль из PATH",
        )
    return CheckResult(id="b", name="Exe", status=STATUS_OK, detail=found[0])


def _tasklist_rows() -> list[tuple[str, int]] | None:
    """Список (имя_образа, pid) через tasklist; None — не Windows/ошибка."""
    if sys.platform != "win32":
        return None
    res = _run_text(["tasklist", "/FO", "CSV", "/NH"], 15)
    if res is None:
        return None
    code, out, _ = res
    if code != 0:
        return None
    rows = []
    try:
        for line in csv.reader(io.StringIO(out)):
            if len(line) < 2:
                continue
            try:
                rows.append((line[0].strip().lower(), int(line[1])))
            except ValueError:
                continue
    except csv.Error:
        return None
    return rows


def check_processes(preinstall: bool = False) -> CheckResult:
    """c. Процессы, использующие exe/шлюз (только список PID, без kill).

    По умолчанию — INFO (работающие сессии — норма); с --preinstall —
    WARN (перед переустановкой всё должно быть остановлено).
    """
    rows = _tasklist_rows()
    if rows is None:
        return CheckResult(
            id="c",
            name="Процессы",
            status=STATUS_WARN,
            detail="список процессов недоступен (поддерживается только Windows 11)",
            hint="проверить вручную в диспетчере задач",
        )
    holders = [
        (name, pid)
        for name, pid in rows
        if any(key in name for key in HOLDER_PROCESS_NAMES)
    ]
    if not holders:
        return CheckResult(
            id="c", name="Процессы", status=STATUS_OK, detail="процессов нет"
        )
    detail = ", ".join(f"{name} pid={pid}" for name, pid in sorted(set(holders)))
    if preinstall:
        return CheckResult(
            id="c",
            name="Процессы",
            status=STATUS_WARN,
            detail=f"держат exe/шлюз: {detail}",
            hint="перед переустановкой остановить полным блоком (README §7); direct spawn Hermes ловится по имени, фильтр python.exe не использовать",
        )
    return CheckResult(
        id="c",
        name="Процессы",
        status=STATUS_OK,
        detail=f"INFO: exe используется {len(set(holders))} процессами: {detail} — для переустановки остановить полным блоком (README §7)",
    )


def check_file_lock(
    exe_path: str | None = None, preinstall: bool = False
) -> CheckResult:
    """d. Блокировка файла (os error 32): пробное открытие без изменения.

    Занятый файл по умолчанию — INFO (сессии работают — норма);
    с --preinstall — FAIL (перед переустановкой exe должен быть свободен).
    """
    path = exe_path
    if path is None:
        found = exe_candidates()
        path = found[0] if found else None
    if path is None:
        return CheckResult(
            id="d",
            name="Блокировка файла",
            status=STATUS_WARN,
            detail="exe неоднозначен или не найден — нечего проверять",
            hint="сначала починить установку (проверка b)",
        )
    try:
        with open(path, "r+b"):
            pass
    except PermissionError as e:
        if preinstall:
            return CheckResult(
                id="d",
                name="Блокировка файла",
                status=STATUS_FAIL,
                detail=f"файл занят (os error 32): {path}: {e}",
                hint="закрыть процессы из проверки c полным блоком (README §7), затем повторить установку",
            )
        return CheckResult(
            id="d",
            name="Блокировка файла",
            status=STATUS_OK,
            detail=f"INFO: файл занят (os error 32): {path} — для переустановки остановить полным блоком (README §7)",
        )
    except OSError as e:
        return CheckResult(
            id="d",
            name="Блокировка файла",
            status=STATUS_WARN,
            detail=f"не удалось открыть {path}: {e}",
            hint="проверить права на файл и антивирус",
        )
    return CheckResult(
        id="d", name="Блокировка файла", status=STATUS_OK, detail=f"открывается: {path}"
    )


def check_config() -> CheckResult:
    """e. Конфиг: accounts.toml найден, парсится, валидация v1.6.0."""
    from directai_mcp.config import ConfigError, load_settings, primary_goal_warnings

    try:
        settings = load_settings()
    except ConfigError as e:
        return CheckResult(
            id="e",
            name="Конфиг",
            status=STATUS_FAIL,
            detail=f"accounts.toml: {e}",
            hint="выполнить `directai-mcp init` (существующие не затрутся), затем заполнить [auth] login и [aliases.*]",
        )
    warns = primary_goal_warnings(settings)
    goal_warns = [w for w in warns if "12" in w or "13" in w or "служебн" in w]
    detail = f"кабинетов: {len(settings.accounts)}"
    if goal_warns:
        return CheckResult(
            id="e",
            name="Конфиг",
            status=STATUS_WARN,
            detail=detail + "; " + "; ".join(goal_warns),
            hint="служебные цели 12/13 — вовлечённые сессии / все приоритетные цели; для CPA выбрать обычную цель",
        )
    return CheckResult(id="e", name="Конфиг", status=STATUS_OK, detail=detail)


def _token_presence(label: str, token: str | None) -> str:
    if token:
        return f"{label}: есть (длина {len(token)}, срок неизвестен — до отзыва)"
    return f"{label}: нет"


def check_tokens() -> CheckResult:
    """f. Токены: наличие и длина; значений в выводе нет."""
    from directai_mcp.config import ConfigError, TokenMissingError, load_settings

    try:
        settings = load_settings()
    except ConfigError as e:
        return CheckResult(
            id="f",
            name="Токены",
            status=STATUS_WARN,
            detail=f"конфиг не прочитан ({e}) — токены не проверены",
            hint="сначала починить конфиг (проверка e)",
        )
    from directai_mcp.config import (
        get_audience_token,
        get_metrika_token,
        get_token,
        get_webmaster_token,
    )

    try:
        main = get_token(settings.auth_login)
    except TokenMissingError:
        main = None
    audience = get_audience_token(settings.auth_login)
    webmaster = get_webmaster_token(settings.auth_login)
    metrika = get_metrika_token(settings.auth_login)
    parts = [
        _token_presence("основной", main),
        _token_presence("метрика", metrika),
        _token_presence("аудитории", audience),
        _token_presence("вебмастер", webmaster),
    ]
    if main is None:
        return CheckResult(
            id="f",
            name="Токены",
            status=STATUS_FAIL,
            detail="; ".join(parts),
            hint="выполнить `directai-mcp set-token --login <логин>` и ввести токен в скрытое поле (не в чат)",
        )
    return CheckResult(id="f", name="Токены", status=STATUS_OK, detail="; ".join(parts))


async def _light_api_call(token: str, login: str) -> None:
    from directai_mcp.api.direct import DirectClient

    client = DirectClient(token=token)
    try:
        await client.call(
            "clients", "get", {"FieldNames": ["Login", "ClientId"]}, login
        )
    finally:
        await client.aclose()


def check_api(skip_api: bool = False) -> CheckResult:
    """g. Лёгкий вызов API с разбором кодов 513/58/53/52/1000+."""
    if skip_api:
        return CheckResult(
            id="g",
            name="API",
            status=STATUS_OK,
            detail="пропущено флагом --skip-api",
            skipped=True,
        )
    from directai_mcp.api.errors import DirectError
    from directai_mcp.config import ConfigError, TokenMissingError, load_settings

    try:
        settings = load_settings()
    except ConfigError as e:
        return CheckResult(
            id="g",
            name="API",
            status=STATUS_WARN,
            detail=f"конфиг не прочитан ({e}) — API не проверен",
            hint="сначала починить конфиг (проверка e)",
        )
    from directai_mcp.config import get_token

    try:
        token = get_token(settings.auth_login)
    except TokenMissingError:
        return CheckResult(
            id="g",
            name="API",
            status=STATUS_WARN,
            detail="нет основного токена — API не проверен",
            hint="сначала получить токен (проверка f)",
        )
    try:
        asyncio.run(_light_api_call(token, settings.auth_login))
    except DirectError as e:
        title, action = ERROR_GUIDE.get(e.code, ("ошибка API", "повторить позже"))
        status = STATUS_FAIL if e.code in (53, 58, 513) else STATUS_WARN
        return CheckResult(
            id="g",
            name="API",
            status=status,
            detail=f"код {e.code}: {title}",
            hint=action,
        )
    except Exception as e:  # noqa: BLE001 — транспортная неопределённость
        return CheckResult(
            id="g",
            name="API",
            status=STATUS_WARN,
            detail=f"транспортная ошибка (не ответ API): {type(e).__name__}",
            hint="проверить сеть и повторить",
        )
    return CheckResult(id="g", name="API", status=STATUS_OK, detail="clients.get OK")


def check_hermes() -> CheckResult:
    """h. Hermes: доступен ли CLI, есть ли сервер, ответ теста."""
    if shutil.which("hermes") is None:
        return CheckResult(
            id="h",
            name="Hermes",
            status=STATUS_WARN,
            detail="Hermes CLI не найден (нужен только если используется шлюз Hermes)",
            hint="для Hermes: зарегистрировать сервер по examples/harness-configs.md; для других харнесов — игнорировать",
        )
    res = _run_text(["hermes", "mcp", "test", "directai-mcp"], 30)
    if res is None:
        return CheckResult(
            id="h",
            name="Hermes",
            status=STATUS_WARN,
            detail="не удалось запустить `hermes mcp test directai-mcp`",
            hint="проверить установку Hermes и повторить",
        )
    code, out, err = res
    output = (out + err).strip()
    tail = output[-500:] if len(output) > 500 else output
    if code == 0:
        return CheckResult(
            id="h", name="Hermes", status=STATUS_OK, detail=f"test OK: {tail}"
        )
    return CheckResult(
        id="h",
        name="Hermes",
        status=STATUS_WARN,
        detail=f"test FAIL: {tail}",
        hint="проверить регистрацию сервера (examples/harness-configs.md), перезапустить шлюз `hermes -p default gateway start`, затем открыть новую сессию",
    )


async def _metrika_probe(token: str) -> tuple[dict, int]:
    """Приложение токена и число доступных счётчиков (для doctor)."""
    from directai_mcp.api import metrika as mk

    app = await mk.oauth_app_info(token)
    payload = await mk.get(token, "/management/v1/counters")
    counters = payload.get("counters") if isinstance(payload, dict) else []
    return app, len(counters or [])


def check_metrika(skip_api: bool = False) -> CheckResult:
    """l. Метрика: отдельный токен или токен Директа, приложение, чтение.

    Право metrika:write не проверяется: Яндекс не отдаёт scopes токена, а
    выяснить его можно только записью цели. Поэтому здесь факты: чей токен,
    какое приложение его выпустило, читаются ли счётчики.
    """
    import asyncio

    from directai_mcp.config import (
        METRIKA_APP_CLIENT_ID,
        METRIKA_SOURCE_LABELS,
        ConfigError,
        load_settings,
        metrika_token_info,
    )

    try:
        settings = load_settings()
    except ConfigError as e:
        return CheckResult(
            id="l",
            name="Метрика",
            status=STATUS_WARN,
            detail=f"конфиг не прочитан ({e})",
            hint="сначала починить конфиг (проверка e)",
        )
    info = metrika_token_info(settings.auth_login)
    source = info["source"]
    label = METRIKA_SOURCE_LABELS[source]
    detail = f"{label} (длина {info['length']})" if info["length"] else label
    if source == "direct":
        return CheckResult(
            id="l",
            name="Метрика",
            status=STATUS_OK,
            detail=detail + "; INFO: чтение токеном Директа, запись целей "
            "недоступна",
            hint="отдельный токен: `directai-mcp set-metrika-token --login "
            f"{settings.auth_login}`",
        )
    if skip_api:
        return CheckResult(
            id="l",
            name="Метрика",
            status=STATUS_OK,
            detail=detail + "; чтение пропущено флагом --skip-api; "
            "metrika:write без записи не проверяется",
        )
    try:
        from directai_mcp.config import get_metrika_token

        settings_token = get_metrika_token(settings.auth_login) or ""
        if not settings_token:
            raise ValueError("нет отдельного токена Метрики")
        app, counters = asyncio.run(_metrika_probe(settings_token))
        app_id = str(app.get("client_id") or "")
        detail += f"; приложение {app_id[:8]}…" if app_id else "; приложение ?"
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            id="l",
            name="Метрика",
            status=STATUS_WARN,
            detail=detail + f"; проверка не удалась: {type(exc).__name__}",
            hint="directai-mcp check (строка «Метрика:») даёт подробности",
        )
    if app_id and app_id != METRIKA_APP_CLIENT_ID:
        return CheckResult(
            id="l",
            name="Метрика",
            status=STATUS_WARN,
            detail=detail + f"; счётчиков {counters}",
            hint=f"токен выдан не приложению Метрики (ожидался "
            f"{METRIKA_APP_CLIENT_ID[:8]}…): перевыпустите токен",
        )
    return CheckResult(
        id="l",
        name="Метрика",
        status=STATUS_OK,
        detail=detail + f"; счётчиков {counters}; metrika:write без записи "
        "не проверяется",
    )


def check_audience() -> CheckResult:
    """i. Аудитории: запись выключена — INFO (в шкале OK)."""
    from directai_mcp.config import ConfigError, load_settings

    try:
        settings = load_settings()
    except ConfigError as e:
        return CheckResult(
            id="i",
            name="Аудитории",
            status=STATUS_WARN,
            detail=f"конфиг не прочитан ({e})",
            hint="сначала починить конфиг (проверка e)",
        )
    if not settings.audience_write_enabled:
        return CheckResult(
            id="i",
            name="Аудитории",
            status=STATUS_OK,
            detail="INFO: запись выключена ([audience] write_enabled=false, по умолчанию)",
        )
    return CheckResult(
        id="i",
        name="Аудитории",
        status=STATUS_WARN,
        detail="запись включена (write_enabled=true, экспериментально)",
        hint="создание/удаление только [TEST DirectAI]* через plan_write → согласие → apply_write",
    )


def check_retargeting() -> CheckResult:
    """k. Ретаргетинг: запись выключена — INFO (в шкале OK)."""
    from directai_mcp.config import ConfigError, load_settings

    try:
        settings = load_settings()
    except ConfigError as e:
        return CheckResult(
            id="k",
            name="Ретаргетинг",
            status=STATUS_WARN,
            detail=f"конфиг не прочитан ({e})",
            hint="сначала починить конфиг (проверка e)",
        )
    if not settings.retargeting_write_enabled:
        return CheckResult(
            id="k",
            name="Ретаргетинг",
            status=STATUS_OK,
            detail="INFO: запись выключена ([retargeting] write_enabled=false, по умолчанию)",
        )
    return CheckResult(
        id="k",
        name="Ретаргетинг",
        status=STATUS_WARN,
        detail="запись включена (write_enabled=true, экспериментально)",
        hint="условия/привязки только через plan_write → согласие → apply_write; "
        "удаление условий — только неиспользуемых ([TEST DirectAI]* без доп. подтверждения)",
    )


def check_plans_dir() -> CheckResult:
    """j. Каталог планов: доступен на запись (общий для всех процессов)."""
    from directai_mcp.config import ConfigError, data_dir, load_settings
    from directai_mcp.safety.plans import plans_dir_for

    try:
        settings = load_settings()
        home = (
            settings.accounts_path.parent
            if settings.accounts_path is not None
            else data_dir()
        )
    except ConfigError as e:
        return CheckResult(
            id="j",
            name="Каталог планов",
            status=STATUS_WARN,
            detail=f"конфиг не прочитан ({e}) — каталог не проверен",
            hint="сначала починить конфиг (проверка e)",
        )
    target = plans_dir_for(home)
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return CheckResult(
            id="j",
            name="Каталог планов",
            status=STATUS_FAIL,
            detail=f"каталог недоступен на запись: {target}: {e}",
            hint="проверить права на каталог данных (%USERPROFILE%\\.directai\\plans)",
        )
    return CheckResult(
        id="j",
        name="Каталог планов",
        status=STATUS_OK,
        detail=f"запись доступна: {target}",
    )


def run_doctor(
    skip_api: bool = False, preinstall: bool = False
) -> tuple[list[CheckResult], int]:
    """Все проверки по порядку; возвращает (результаты, код возврата)."""
    exe = check_exe()
    file_lock = check_file_lock(
        exe.detail if exe.status == STATUS_OK else None, preinstall=preinstall
    )
    if (
        exe.status == STATUS_WARN
        and file_lock.status == STATUS_OK
        and file_lock.detail.startswith("открывается")
    ):
        # Дубль exe: проверен первый попавшийся — честно понижаем до WARN.
        file_lock = CheckResult(
            id=file_lock.id,
            name=file_lock.name,
            status=STATUS_WARN,
            detail=f"{file_lock.detail} (первый из дублей; сначала убрать дубль — проверка b)",
            hint=file_lock.hint,
        )
    results = [
        check_version(),
        exe,
        check_processes(preinstall=preinstall),
        file_lock,
        check_config(),
        check_tokens(),
        check_api(skip_api=skip_api),
        check_hermes(),
        check_audience(),
        check_retargeting(),
        check_metrika(skip_api=skip_api),
        check_plans_dir(),
    ]
    if any(r.status == STATUS_FAIL for r in results):
        return results, 2
    if any(r.status == STATUS_WARN for r in results):
        return results, 1
    return results, 0


def format_human(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        line = f"[{r.status}] {r.id}. {r.name}: {r.detail}"
        if r.hint:
            line += f" → {r.hint}"
        lines.append(line)
    return "\n".join(lines)


def format_json(
    results: list[CheckResult], exit_code: int, preinstall: bool = False
) -> str:
    overall = "ok" if exit_code == 0 else ("warn" if exit_code == 1 else "fail")
    payload = {
        "schema": DOCTOR_SCHEMA_VERSION,
        "overall": overall,
        "exit_code": exit_code,
        "preinstall": preinstall,
        "checks": [r.to_dict() for r in results],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def cmd_doctor(
    json_output: bool = False, skip_api: bool = False, preinstall: bool = False
) -> int:
    # v1.17.2: doctor сам настраивает логи (в нём нет вызова setup_logging),
    # поэтому секретный фильтр и тихие HTTP-логгеры включаем явно.
    from directai_mcp.log import quiet_http_logging

    quiet_http_logging()
    results, code = run_doctor(skip_api=skip_api, preinstall=preinstall)
    if json_output:
        print(format_json(results, code, preinstall=preinstall))
    else:
        print(format_human(results))
    return code
