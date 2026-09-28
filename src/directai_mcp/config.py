"""Settings, accounts and token handling (SPEC 6.1)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KEYRING_SERVICE = "directai-mcp"
TOKEN_ENV_VAR = "DIRECTAI_TOKEN"
# Отдельный токен для API, не влезающих в права основного приложения
# (у приложений «для авторизации пользователей» лимит 3 группы разрешений,
# поэтому Яндекс.Вебмастер живёт в отдельном приложении «для доступа к API»).
KEYRING_SERVICE_WEBMASTER = "directai-mcp-webmaster"
WEBMASTER_TOKEN_ENV_VAR = "DIRECTAI_WEBMASTER_TOKEN"
DEFAULT_AUTH_LOGIN = "agency-login"

# Шаг 1.1-2: кеш обнаруженных кабинетов и его свежесть.
CACHE_FILENAME = "accounts_cache.json"
CACHE_STALE_DAYS = 7


class ConfigError(Exception):
    """Raised for missing or invalid configuration."""


class TokenMissingError(ConfigError):
    """Raised when no token is available in keyring or environment."""


@dataclass(frozen=True)
class AccountEntry:
    alias: str
    login: str
    role: str = ""


@dataclass(frozen=True)
class Settings:
    auth_login: str
    include_vat: bool = True
    max_rows: int = 50
    goals: tuple[str, ...] = ()
    attribution: tuple[str, ...] = ()
    accounts: dict[str, AccountEntry] = field(default_factory=dict)
    counter_id: int | None = None
    accounts_path: Path | None = None
    guard: bool = True
    goal_names: dict[str, str] = field(default_factory=dict)
    # v1.1.5: счётчики неосновного счётчика (id -> counter).
    goal_counters: dict[str, int] = field(default_factory=dict)
    # v1.1.29: тип ценности цели (id -> crm|conditional), goals.toml fallback.
    goal_value_types: dict[str, str] = field(default_factory=dict)
    # Шаг 1.1-5: порог предупреждения об остатке баллов, % лимита.
    units_warn_pct: int = 10
    # v1.1.30: порог доли визитов/клики в counter_check, % (флаг ниже порога).
    counter_visits_warn_pct: int = 50
    exclude: tuple[str, ...] = ()
    aliases: dict[str, AccountEntry] = field(default_factory=dict)
    # Старые секции [accounts.*] (шаг 1.1-2, Q4): трактуются как aliases.
    legacy_sections: tuple[str, ...] = ()
    # Шаг 1.1-3: каталог отчётов из [paths] (дефолт — reports/ репозитория).
    reports_dir: Path | None = None


def data_dir() -> Path:
    """User data dir: DIRECTAI_HOME or %USERPROFILE%/.directai."""
    override = os.environ.get("DIRECTAI_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".directai"


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"config not found: {path} (run `directai-mcp init`)") from e
    if not isinstance(data, dict):
        raise ConfigError(f"invalid config: {path}")
    return data


def load_settings(path: Path | None = None) -> Settings:
    """Load accounts.toml into Settings. Secrets are never stored here."""
    import logging

    cfg_path = path or (data_dir() / "accounts.toml")
    data = _read_toml(cfg_path)

    auth = data.get("auth", {})
    auth_login = str(auth.get("login", DEFAULT_AUTH_LOGIN))

    defaults = data.get("defaults", {})
    include_vat = bool(defaults.get("include_vat", True))
    max_rows = int(defaults.get("max_rows", 50))
    goals = tuple(str(g) for g in defaults.get("goals", []) or [])
    attribution = tuple(str(a) for a in defaults.get("attribution", []) or [])
    try:
        units_warn_pct = int(defaults.get("units_warn_pct", 10))
    except (TypeError, ValueError):
        raise ConfigError(f"invalid units_warn_pct in {cfg_path}") from None
    try:
        counter_visits_warn_pct = int(defaults.get("counter_visits_warn_pct", 50))
    except (TypeError, ValueError):
        raise ConfigError(
            f"invalid counter_visits_warn_pct in {cfg_path}") from None
    if not 0 <= counter_visits_warn_pct <= 100:
        raise ConfigError(
            f"counter_visits_warn_pct out of 0..100 in {cfg_path}")

    raw_accounts = data.get("accounts", {})
    if not isinstance(raw_accounts, dict):
        raise ConfigError(f"invalid [accounts] section in {cfg_path}")
    # Шаг 1.1-2 (Q4): голый [accounts] несёт exclude; [accounts.*] — старый
    # формат, трактуется как aliases с предупреждением.
    exclude = tuple(
        str(x) for x in (raw_accounts.get("exclude", []) or []) if str(x)
    )
    aliases: dict[str, AccountEntry] = {}
    legacy: list[str] = []
    for alias, entry in raw_accounts.items():
        if alias == "exclude" or not isinstance(entry, dict) or not entry.get("login"):
            continue
        aliases[str(alias)] = AccountEntry(
            alias=str(alias),
            login=str(entry["login"]),
            role=str(entry.get("role", "")),
        )
        legacy.append(str(alias))
    if legacy:
        logging.getLogger(__name__).warning(
            "старый формат конфига: секции %s трактуются как aliases. "
            "Исправление: переименуйте [accounts.X] в [aliases.X] "
            "(содержимое секций не менять)",
            ", ".join(f"[accounts.{a}]" for a in legacy),
        )
    raw_aliases = data.get("aliases", {})
    if not isinstance(raw_aliases, dict):
        raise ConfigError(f"invalid [aliases] section in {cfg_path}")
    for alias, entry in raw_aliases.items():
        if not isinstance(entry, dict) or not entry.get("login"):
            raise ConfigError(f"alias '{alias}' misses login in {cfg_path}")
        aliases[str(alias)] = AccountEntry(
            alias=str(alias),
            login=str(entry["login"]),
            role=str(entry.get("role", "")),
        )
    if not aliases and not _managed_cache(cfg_path.parent).get("logins"):
        raise ConfigError(f"no [accounts.*]/[aliases.*] sections in {cfg_path}")

    metrika = data.get("metrika", {}) or {}
    counter_raw = metrika.get("counter_id")
    counter_id = int(counter_raw) if counter_raw else None

    guard_section = data.get("guard", {}) or {}
    guard = bool(guard_section.get("guard", True))

    goal_names = _load_goal_names(cfg_path.parent / "goals.toml")
    _, goal_counters, goal_value_types = _load_goals_file(cfg_path.parent / "goals.toml")

    paths = data.get("paths", {}) or {}
    raw_reports = paths.get("reports_dir") if isinstance(paths, dict) else None
    reports_dir = Path(str(raw_reports)).expanduser() if raw_reports else None

    return Settings(
        auth_login=auth_login,
        include_vat=include_vat,
        max_rows=max_rows,
        goals=goals,
        attribution=attribution,
        accounts=dict(aliases),
        counter_id=counter_id,
        accounts_path=cfg_path,
        guard=guard,
        goal_names=goal_names,
        goal_counters=goal_counters,
        goal_value_types=goal_value_types,
        exclude=exclude,
        aliases=dict(aliases),
        legacy_sections=tuple(legacy),
        reports_dir=reports_dir,
        units_warn_pct=units_warn_pct,
        counter_visits_warn_pct=counter_visits_warn_pct,
    )


def cache_path(home: Path) -> Path:
    """Путь кеша обнаруженных кабинетов (шаг 1.1-2)."""
    return home / CACHE_FILENAME


def all_goal_ids(home: Path | None) -> list[str]:
    """Все id из goals.toml [goals], включая безымянные (v1.1.3)."""
    if home is None:
        return []
    try:
        with (home / "goals.toml").open("rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
        return []
    goals = data.get("goals", {}) if isinstance(data, dict) else {}
    if not isinstance(goals, dict):
        return []
    return sorted(
        {str(gid) for gid in goals if str(gid).strip()},
        key=lambda x: (0, int(x)) if x.isdigit() else (1, x),
    )


def default_reports_dir() -> Path:
    """Шаг 1.1-3: reports/ репозитория, если [paths] не задан."""
    return Path(__file__).resolve().parent.parent.parent / "reports"


def reports_base_dir(settings: Settings) -> Path:
    """Каталог отчётов: конфиг [paths] reports_dir или дефолт (шаг 1.1-3)."""
    return settings.reports_dir or default_reports_dir()


def _managed_cache(home: Path) -> dict:
    """Кеш discover с диска; отсутствует/бит — {} (ленивое создание)."""
    import json

    try:
        with cache_path(home).open(encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def cache_age_days(cache: dict) -> int | None:
    """Возраст кеша в днях по updated_at; неизвестно — None."""
    from datetime import datetime

    updated = cache.get("updated_at")
    if not isinstance(updated, str) or not updated:
        return None
    try:
        moment = datetime.fromisoformat(updated)
    except ValueError:
        return None
    return max(0, (datetime.now().astimezone() - moment).days)


def _home_of(settings: Settings) -> Path | None:
    if settings.accounts_path is not None:
        return settings.accounts_path.parent
    return None


def _managed_logins(settings: Settings) -> list[str]:
    home = _home_of(settings)
    if home is None:
        return []
    logins = _managed_cache(home).get("logins")
    if not isinstance(logins, list):
        return []
    seen: list[str] = []
    for login in logins:
        if isinstance(login, str) and login and login not in seen:
            seen.append(login)
    return seen


def _load_goal_names(path: Path) -> dict[str, str]:
    """Optional goals.toml: goal id -> display name (empty = bare id).

    v1.1.5: значение может быть таблицей {name, counter} — счётчик
    указывается только для целей неосновного счётчика.
    """
    names, _, _ = _load_goals_file(path)
    return names


def _load_goals_file(path: Path) -> tuple[dict[str, str], dict[str, int],
                                         dict[str, str]]:
    """Возвращает (имена, счётчики, типы ценности) из goals.toml.

    v1.1.29: табличная запись может нести value_type: crm | conditional
    (default conditional). Неизвестный value_type — ConfigError.
    """
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}, {}, {}
    if not isinstance(data, dict):
        raise ConfigError(f"invalid goals file: {path}")
    goals = data.get("goals", {}) or {}
    if not isinstance(goals, dict):
        raise ConfigError(f"invalid [goals] section: {path}")
    names: dict[str, str] = {}
    counters: dict[str, int] = {}
    value_types: dict[str, str] = {}
    for gid, entry in goals.items():
        if isinstance(entry, dict):
            label = str(entry.get("name") or "").strip()
            try:
                counter = int(entry.get("counter")) if entry.get("counter") else None
            except (TypeError, ValueError):
                raise ConfigError(f"bad counter for goal '{gid}' in {path}") from None
            if counter is not None:
                counters[str(gid)] = counter
            raw_vtype = entry.get("value_type")
            if raw_vtype is None or str(raw_vtype).strip() == "":
                vtype = "conditional"
            else:
                vtype = str(raw_vtype).strip().lower()
                if vtype not in ("crm", "conditional"):
                    raise ConfigError(
                        f"bad value_type for goal '{gid}' in {path}: "
                        "want crm|conditional"
                    )
            value_types[str(gid)] = vtype
        else:
            label = str(entry or "").strip()
            value_types[str(gid)] = "conditional"
        if label:
            names[str(gid)] = label
    return names, counters, value_types


def _checks_of(settings: Settings) -> dict:
    home = _home_of(settings)
    if home is None:
        return {}
    checks = _managed_cache(home).get("checks")
    return checks if isinstance(checks, dict) else {}


def resolve_account(settings: Settings, value: str) -> list[AccountEntry]:
    """Resolve alias, login, 'all' или 'active' к записям кабинетов.

    'all' = auth_login + кеш discover − exclude; 'active' = из них те, у кого
    последний accounts_check нашёл ≥1 кампанию в ON. Без кеша — legacy-набор
    из конфига (до первого accounts_discover).
    Unknown value raises ConfigError with the list of valid values.
    """
    by_login: dict[str, AccountEntry] = {}
    for entry in list(settings.accounts.values()) + list(settings.aliases.values()):
        by_login.setdefault(entry.login, entry)
    if value in ("all", "active"):
        managed = _managed_logins(settings)
        if not managed:
            return list(settings.accounts.values())
        excluded = set(settings.exclude)
        checks = _checks_of(settings)
        out: list[AccountEntry] = []
        seen: set[str] = set()
        for login in [settings.auth_login, *managed]:
            if login in excluded or login in seen:
                continue
            seen.add(login)
            if value == "active":
                info = checks.get(login)
                if not isinstance(info, dict) or int(info.get("on") or 0) < 1:
                    continue
            known = by_login.get(login)
            out.append(
                known
                if known is not None
                else AccountEntry(alias=login, login=login)
            )
        return out
    for entry in by_login.values():
        if value == entry.alias or value == entry.login:
            return [entry]
    managed = _managed_logins(settings)
    if value in managed:
        return [AccountEntry(alias=value, login=value)]
    valid = ["all", "active"] + sorted(
        {v for e in by_login.values() for v in (e.alias, e.login)} | set(managed)
    )
    raise ConfigError(f"unknown account '{value}'. Valid: {', '.join(valid)}")


def get_token(auth_login: str) -> str:
    """Read token from DIRECTAI_TOKEN env or Windows Credential Manager.

    Never logs or prints the token; raises TokenMissingError with a hint.
    """
    env_token = os.environ.get(TOKEN_ENV_VAR)
    if env_token:
        return env_token
    import keyring

    token = keyring.get_password(KEYRING_SERVICE, auth_login)
    if token:
        return token
    raise TokenMissingError(
        f"token missing for login '{auth_login}'. Run `directai-mcp set-token` "
        f"or set {TOKEN_ENV_VAR}."
    )


def get_webmaster_token(auth_login: str) -> str | None:
    """Отдельный токен Вебмастера или None (тогда вызывающий берёт основной).

    Нужен, когда права не влезают в одно приложение: у приложений «для
    авторизации пользователей» лимит 3 группы разрешений, поэтому Вебмастер
    обычно выносят в отдельное приложение «для доступа к API».
    Токен никогда не логируется и не печатается.
    """
    env_token = os.environ.get(WEBMASTER_TOKEN_ENV_VAR)
    if env_token:
        return env_token
    import keyring

    return keyring.get_password(KEYRING_SERVICE_WEBMASTER, auth_login)
