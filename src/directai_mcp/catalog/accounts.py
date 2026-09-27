"""Read actions accounts_discover, accounts_check (шаг 1.1-2)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from directai_mcp.api.errors import DirectError
from directai_mcp.api.live import LiveClient, LiveError
from directai_mcp.catalog.common import GetActionParams, finalize
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import CACHE_STALE_DAYS, cache_age_days, cache_path
from directai_mcp.fmt import money, to_decimal

# Clients WSDL существует и в v5, и в v501, но оба отдают v5-неймспейсы
# с одинаковым ClientFieldEnum (без ManagedLogins) — работаем через v5.
CLIENTS_VERSION = "v5"
DISCOVER_FIELDS = ["Login", "ClientId", "ManagedLogins"]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _home(ctx: Ctx):
    return ctx.data_dir


def read_cache(ctx: Ctx) -> dict:
    home = _home(ctx)
    if home is None:
        return {}
    try:
        with cache_path(home).open(encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(ctx: Ctx, cache: dict) -> None:
    home = _home(ctx)
    if home is None:
        return
    home.mkdir(parents=True, exist_ok=True)
    with cache_path(home).open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def _managed_list(cache: dict) -> list[str]:
    logins = cache.get("logins")
    if not isinstance(logins, list):
        return []
    seen: list[str] = []
    for login in logins:
        if isinstance(login, str) and login and login not in seen:
            seen.append(login)
    return seen


def _parse_managed(payload: object) -> list[str]:
    """ManagedLogins из ответа: живьём — плоский список строк."""
    if isinstance(payload, dict):
        payload = payload.get("Items", [])
    if not isinstance(payload, list):
        return []
    out: list[str] = []
    for entry in payload:
        if isinstance(entry, str) and entry and entry not in out:
            out.append(entry)
        elif isinstance(entry, dict):
            login = entry.get("Login")
            if isinstance(login, str) and login and login not in out:
                out.append(login)
    return out


async def _discover_logins(client, login: str) -> tuple[list[str], str, list[str]]:
    """Clients.get -> fallback AgencyClients.get. Возвращает (логины, метод, ошибки)."""
    errors: list[str] = []
    try:
        res = await client.call(
            "clients", "get", {"FieldNames": DISCOVER_FIELDS}, login, CLIENTS_VERSION
        )
    except DirectError as e:
        errors.append(f"Clients.get: {e.human_message()}")
        res = None
    if res is not None:
        items = res.get("Clients", [])
        if items:
            found = _parse_managed(items[0].get("ManagedLogins"))
            return found, "Clients.get → ManagedLogins", errors
        errors.append("Clients.get: пустой список Clients")
    try:
        res2 = await client.get_all(
            "agencyclients",
            {"SelectionCriteria": {}, "FieldNames": ["Login", "ClientId"]},
            login,
            "AgencyClients",
            CLIENTS_VERSION,
        )
        found = [
            str(i.get("Login"))
            for i in res2
            if isinstance(i, dict) and i.get("Login")
        ]
        return found, "AgencyClients.get", errors
    except DirectError as e:
        errors.append(f"AgencyClients.get: {e.human_message()}")
    return [], "", errors


async def _check_many(
    ctx: Ctx, logins: list[str], include_archived: bool = False
) -> tuple[list[dict], dict]:
    """Один Campaigns.get (Id, State) на логин. Возвращает (строки, checks)."""

    async def one(login: str) -> dict:
        client = ctx.direct()
        try:
            try:
                criteria: dict = {}
                if not include_archived:
                    # Архив жрёт Units (agency-login: 357 объектов → 381 Units):
                    # по умолчанию только неархивные.
                    criteria["States"] = ["ON", "SUSPENDED", "OFF", "ENDED"]
                items = await client.get_all(
                    "campaigns",
                    {"SelectionCriteria": criteria, "FieldNames": ["Id", "State"]},
                    login,
                    "Campaigns",
                    "v501",
                )
            except DirectError as e:
                return {"login": login, "error": e.human_message()}
            counts = {"on": 0, "suspended": 0, "off": 0, "ended": 0, "archived": 0}
            for item in items:
                state = item.get("State")
                if state == "ON":
                    counts["on"] += 1
                elif state == "SUSPENDED":
                    counts["suspended"] += 1
                elif state == "OFF":
                    counts["off"] += 1
                elif state == "ENDED":
                    counts["ended"] += 1
                elif state in ("ARCHIVED", "CONVERTED"):
                    counts["archived"] += 1
            units = client.last_units.get(login)
            return {
                "login": login,
                "counts": counts,
                "units": (
                    f"{units.used}/{units.rest}/{units.limit}" if units else "—"
                ),
            }
        finally:
            await client.aclose()

    sem = asyncio.Semaphore(3)

    async def guarded(login: str) -> dict:
        async with sem:
            return await one(login)

    rows = list(await asyncio.gather(*(guarded(login) for login in logins)))
    checks: dict = {}
    for row in rows:
        if "error" in row:
            checks[row["login"]] = {"error": row["error"], "checked_at": _now_iso()}
        else:
            checks[row["login"]] = {**row["counts"], "checked_at": _now_iso()}
    return rows, checks


async def _refresh(ctx: Ctx) -> tuple[bool, str]:
    """Discover с записью кеша. Возвращает (ок, пометка)."""
    client = ctx.direct()
    try:
        logins, method, errors = await _discover_logins(client, ctx.settings.auth_login)
    finally:
        await client.aclose()
    if not logins:
        return False, "discover не дал списка: " + "; ".join(errors)
    fresh = read_cache(ctx)
    _save_cache(
        ctx,
        {
            "updated_at": _now_iso(),
            "method": method,
            "manager": ctx.settings.auth_login,
            "logins": logins,
            "checks": fresh.get("checks") if isinstance(fresh.get("checks"), dict) else {},
            "errors": errors,
        },
    )
    return True, ""


async def ensure_cache(ctx: Ctx, value: str = "all") -> list[str]:
    """Ленивый discover/refresh (шаг 1.1-2, Q3/Q5). Возвращает пометки."""
    notes: list[str] = []
    cache = read_cache(ctx)
    if not _managed_list(cache):
        ok, note = await _refresh(ctx)
        notes.append("кеш создан" if ok else note)
        if not ok:
            return notes
        cache = read_cache(ctx)
    elif (cache_age_days(cache) or 0) > CACHE_STALE_DAYS:
        age = cache_age_days(cache)
        ok, _ = await _refresh(ctx)
        notes.append(f"кеш обновлён (возраст {age} дн.)" if ok else "refresh не удался")
        if not ok:
            return notes
        cache = read_cache(ctx)
    if value == "active" and not cache.get("checks"):
        rows, checks = await _check_many(
            ctx, [ctx.settings.auth_login, *_managed_list(cache)]
        )
        merged = read_cache(ctx)
        merged["checks"] = checks
        _save_cache(ctx, merged)
        notes.append(f"accounts_check выполнен автоматически ({len(rows)} кабинетов)")
    return notes


class AccountsDiscoverParams(GetActionParams):
    account: str = ""


class AccountsCheckParams(GetActionParams):
    include_archived: bool = False


@action(
    "accounts_discover",
    "read",
    "Все кабинеты токена через Clients.get ManagedLogins; кеш",
    ("кабинеты", "аккаунты", "accounts", "клиенты", "login", "логины",
     "кабинет", "аккаунт", "управляющий", "discover", "список кабинетов",
     "активные кабинеты", "все кабинеты", "список логинов"),
    AccountsDiscoverParams,
)
async def _discover(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AccountsDiscoverParams)
    if params.account:
        entries = ctx.accounts(params.account)
        if len(entries) != 1:
            return "Ошибка: discover требует ровно один кабинет."
        login = entries[0].login
    else:
        login = ctx.settings.auth_login
    client = ctx.direct()
    try:
        logins, method, errors = await _discover_logins(client, login)
    finally:
        await client.aclose()
    if not logins:
        return "accounts_discover: список не получен.\n" + "\n".join(errors)
    fresh = read_cache(ctx)
    _save_cache(
        ctx,
        {
            "updated_at": _now_iso(),
            "method": method,
            "manager": login,
            "logins": logins,
            "checks": fresh.get("checks") if isinstance(fresh.get("checks"), dict) else {},
            "errors": errors,
        },
    )
    known = {e.login: e for e in ctx.settings.accounts.values()}
    rows = [
        {
            "Логин": login_,
            "Алиас/роль": (
                f"{known[login_].alias} ({known[login_].role})"
                if login_ in known
                else "—"
            ),
            "Exclude": "да" if login_ in ctx.settings.exclude else "—",
        }
        for login_ in [login, *logins]
    ]
    context = (
        f"accounts_discover: {login}, метод {method}, кабинетов: {len(rows)}."
    )
    extra = [f"Ошибки методов: {e}" for e in errors]
    if ctx.settings.legacy_sections:
        extra.append(
            "старый формат конфига: секции "
            + ", ".join(f"[accounts.{a}]" for a in ctx.settings.legacy_sections)
            + " трактуются как aliases; исправление: переименуйте "
            "[accounts.X] в [aliases.X] (содержимое секций не менять)"
        )
    return finalize(
        ctx, context, "accounts_discover",
        ["Логин", "Алиас/роль", "Exclude"], rows,
        params.limit, params.save_as, extra,
        output=params.output, format=params.format, account=login,
    )


@action(
    "accounts_check",
    "read",
    "Доступ и число кампаний по кабинетам, кандидаты в exclude",
    ("кабинеты", "аккаунты", "accounts", "проверка", "доступ", "check",
     "активные", "active", "пустые", "exclude", "баллы", "units",
     "активные кабинеты", "все кабинеты", "список логинов"),
    AccountsCheckParams,
)
async def _check(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AccountsCheckParams)
    for note in await ensure_cache(ctx):
        ctx.notes.append(f"accounts_check: {note}")
    logins = [e.login for e in ctx.accounts(params.account)]
    rows, checks = await _check_many(ctx, logins, params.include_archived)
    cache = read_cache(ctx)
    merged = cache.get("checks") if isinstance(cache.get("checks"), dict) else {}
    merged.update(checks)
    _save_cache(ctx, {**cache, "checks": merged})
    table: list[dict] = []
    empty: list[str] = []
    for row in rows:
        if "error" in row:
            table.append(
                {
                    "Логин": row["login"],
                    "Доступ": f"ошибка: {row['error']}",
                    "ON": "—", "SUSP": "—", "OFF": "—",
                    "ENDED": "—", "Архивных": "—", "Units": "—",
                }
            )
            continue
        counts = row["counts"]
        active = counts["on"] + counts["suspended"] + counts["off"] + counts["ended"]
        if active == 0:
            empty.append(row["login"])
        table.append(
            {
                "Логин": row["login"],
                "Доступ": "ок",
                "ON": counts["on"], "SUSP": counts["suspended"],
                "OFF": counts["off"], "ENDED": counts["ended"],
                "Архивных": counts["archived"] if params.include_archived else "—",
                "Units": row["units"],
            }
        )
    columns = ["Логин", "Доступ", "ON", "SUSP", "OFF", "ENDED", "Архивных", "Units"]
    out = finalize(
        ctx, f"accounts_check: {len(table)} кабинетов.", "accounts_check",
        columns, table, params.limit, params.save_as, [],
        output=params.output, format=params.format, account=params.account,
    )
    if empty:
        out += "\n\nБез активных кампаний (кандидаты в exclude): " + ", ".join(empty) + "."
    return out


__all__ = ["ensure_cache", "read_cache"]


# v1.1.15: баланс общего счёта через Live v4 AccountManagement/Get
# (единственный API с балансом; финансовый токен для Get не нужен —
# проверено живьём 26.09.2026). Строго чтение: операции с движением
# денег здесь не вызываются (транспорт api/live.py умеет только Get).
BALANCE_SPEND_DAYS = 7


class AccountsBalanceParams(GetActionParams):
    account: str = ""


async def _live_balances(
    ctx: Ctx, logins: list[str]
) -> tuple[dict[str, dict], list[str]]:
    """По одному Live-вызову на логин (живём 26.09.2026: этот принципал —
    клиент, мульти-Логины/IDS отвергаются 71 «только один AccountID»;
    лимит 50 из доков — для агентств). Возвращает (счета, ошибки)."""
    by_login: dict[str, dict] = {}
    errors: list[str] = []
    client = LiveClient(token=ctx.token)
    try:
        for login in logins:
            try:
                data = await client.account_management_get([login])
            except LiveError as e:
                errors.append(f"⚠ {login}: {e.human_message()}")
                continue
            problems: dict[str, str] = {}
            actions_result = data.get("ActionsResult")
            if isinstance(actions_result, list):
                for entry in actions_result:
                    if not isinstance(entry, dict):
                        continue
                    errs = entry.get("Errors") or []
                    if errs and isinstance(errs, list):
                        first = errs[0] if isinstance(errs[0], dict) else {}
                        problems[str(entry.get("Login") or "")] = (
                            f"{first.get('FaultString') or 'ошибка'} "
                            f"({first.get('FaultCode')})"
                        )
            accounts = data.get("Accounts")
            seen: set[str] = set()
            if isinstance(accounts, list):
                for acc in accounts:
                    if not isinstance(acc, dict):
                        continue
                    acc_login = str(acc.get("Login") or "")
                    if acc_login:
                        by_login[acc_login] = acc
                        seen.add(acc_login)
            if login in problems:
                errors.append(f"⚠ {login}: {problems[login]}")
            elif login not in seen:
                errors.append(f"⚠ {login}: общий счёт не найден")
    finally:
        await client.aclose()
    ctx.net.merge(client.stats)
    return by_login, errors


async def _spend_7d(ctx: Ctx, login: str) -> tuple[Decimal, int] | DirectError:
    """Расход за 7 дней по дням (Reports, баллов не тратит)."""
    definition = {
        "SelectionCriteria": {},
        "FieldNames": ["Date", "Cost"],
        "ReportType": "ACCOUNT_PERFORMANCE_REPORT",
        "DateRangeType": "LAST_7_DAYS",
        "Format": "TSV",
        "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
    }
    client = ctx.reports()
    try:
        _, rows = await client.fetch(login, definition)
    except DirectError as e:
        return e
    active: set[str] = set()
    total = Decimal(0)
    for row in rows:
        cost = to_decimal(row.get("Cost")) or Decimal(0)
        total += cost
        if cost > 0 and row.get("Date"):
            active.add(str(row["Date"]))
    return total, len(active)


@action(
    "accounts_balance",
    "read",
    "Баланс общего счёта (Live v4): баланс, доступно к переносу, расход 7 дн., запас дней",
    ("баланс", "balance", "счёт", "счет", "общий счёт", "общий счет",
     "баланса", "хватит", "запас", "остаток денег", "amount",
     "accounts_balance", "сколько денег", "сколько осталось"),
    AccountsBalanceParams,
)
async def _balance(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AccountsBalanceParams)
    if ctx.sandbox:
        return "accounts_balance: Live v4 недоступен в песочнице."
    entries = ctx.accounts(params.account or "all")
    logins = [e.login for e in entries]
    by_login, errors = await _live_balances(ctx, logins)

    async def _one(login: str):
        return login, await _spend_7d(ctx, login)

    sem = asyncio.Semaphore(3)

    async def _guarded(login: str):
        async with sem:
            return await _one(login)

    spends = dict(
        await asyncio.gather(*(_guarded(login) for login in by_login))
    ) if by_login else {}

    vat = "с НДС" if ctx.settings.include_vat else "без НДС"
    rows: list[dict] = []
    for login in logins:
        acc = by_login.get(login)
        if acc is None:
            continue
        balance = to_decimal(acc.get("Amount"))
        avail = to_decimal(acc.get("AmountAvailableForTransfer"))
        spend = spends.get(login)
        if isinstance(spend, DirectError) or spend is None:
            if isinstance(spend, DirectError):
                errors.append(f"⚠ {login}: расход: {spend.human_message()}")
            total, active = None, None
        else:
            total, active = spend
        if total is None or active is None:
            avg_cal = avg_act = days = "—"
        else:
            avg_cal = money(total / BALANCE_SPEND_DAYS)
            avg_act = money(total / active) if active else "—"
            if active and balance is not None and (total / active) > 0:
                left = balance / (total / active)
                days = f"хватит на ~{left:.1f} дней (по активным дням)"
            else:
                days = "—"
        rows.append(
            {
                "Логин": login,
                "AccountID": acc.get("AccountID") or "—",
                "Баланс": money(balance) if balance is not None else "—",
                "Доступно к переносу": money(avail) if avail is not None else "—",
                "Валюта": acc.get("Currency") or "—",
                "Расход 7 дн": money(total) if total is not None else "—",
                "Ср/кал. день": avg_cal,
                "Ср/акт. день": avg_act,
                "Активных дней": active if active is not None else "—",
                "Запас": days,
            }
        )
    columns = ["Логин", "AccountID", "Баланс", "Доступно к переносу",
               "Валюта", "Расход 7 дн", "Ср/кал. день", "Ср/акт. день",
               "Активных дней", "Запас"]
    context = (
        f"accounts_balance: {', '.join(logins)}, "
        f"расход за {BALANCE_SPEND_DAYS} дн. {vat}. "
        "Баланс — фактические деньги счёта (НДС к балансу неприменим)."
    )
    return finalize(
        ctx, context, "accounts_balance", columns, rows,
        params.limit, params.save_as, errors,
        money_cols=(),
        output=params.output, format=params.format, account=params.account,
    )
