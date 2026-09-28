"""FastMCP server: 4 read + 3 write tools (SPEC 6.2, steps 2-4)."""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from directai_mcp.api.direct import LAST_SEEN_UNITS
from directai_mcp.catalog import accounts as accounts_mod
from directai_mcp.catalog import adgroups as adgroups_mod
from directai_mcp.catalog import ads as ads_mod
from directai_mcp.catalog import audiences as audiences_mod
from directai_mcp.catalog import bids as bids_mod
from directai_mcp.catalog import campaigns as campaigns_mod
from directai_mcp.catalog import changes as changes_mod
from directai_mcp.catalog import counters as counters_mod
from directai_mcp.catalog import dictionaries as dictionaries_mod
from directai_mcp.catalog import extensions as extensions_mod
from directai_mcp.catalog import keywords as keywords_mod
from directai_mcp.catalog import limits as limits_mod
from directai_mcp.catalog import moderation as moderation_mod
from directai_mcp.catalog import negatives as negatives_mod
from directai_mcp.catalog import stats as stats_mod
from directai_mcp.catalog import webmaster as webmaster_mod

# Referenced so ruff --fix never drops these registration imports.
_ACTION_MODULES = (
    accounts_mod,
    adgroups_mod,
    ads_mod,
    audiences_mod,
    bids_mod,
    campaigns_mod,
    changes_mod,
    counters_mod,
    dictionaries_mod,
    extensions_mod,
    keywords_mod,
    moderation_mod,
    negatives_mod,
    stats_mod,
    webmaster_mod,
)
from directai_mcp import __version__
from directai_mcp.catalog.registry import ACTIONS, Ctx, search
from directai_mcp.config import (
    ConfigError,
    TokenMissingError,
    data_dir,
    get_token,
    load_settings,
)
from directai_mcp.log import setup_logging
from directai_mcp.safety import journal as journal_mod
from directai_mcp.safety.guard import GuardBlocked, check_write, guard_active
from directai_mcp.safety.plans import Plan, PlanStore

log = logging.getLogger(__name__)

INSTRUCTIONS = (
    "DirectAI: Яндекс Директ. Порядок работы: "
    "1) search_actions — найти действие по запросу; "
    "2) describe_action — параметры и пример; "
    "3) run_read — выполнить чтение, либо plan_write — подготовить запись; "
    "4) покажи пользователю предпросмотр плана и получи согласие; "
    "5) apply_write — только после согласия на это превью: план действует "
    "15 минут, истёкший план пересобирается, новое превью требует нового "
    "согласия, apply по старому подтверждению запрещён. "
    "Для аналитики по умолчанию используйте account=active "
    "(кабинеты с активными кампаниями). "
    "Полный результат за один вызов: output=file, format=json. "
    "Блокировка guard — это защита, а не ошибка: остановитесь и сообщите "
    "пользователю; не ищите и не меняйте конфиг guard, не предлагайте обход, "
    "не переносите операцию на другую кампанию без его явного указания. "
    "Сравнение периодов — только через stats_compare, не двумя вызовами "
    "с ручной арифметикой. Если пользователь указал конкретный MCP-сервер "
    "или инструмент — используйте только его, не подменяйте другим. "
    "Метрики из вывода инструмента (CTR, CPC, CPA, CR, Δ, доли) не "
    "пересчитывать вручную — брать как есть. Сверку не утверждать "
    "(«сходится», «расхождений нет») без фактического сравнения чисел. "
    "Режим целей и атрибуцию цитировать из шапки инструмента, не "
    "формулировать своими словами. Почасового среза в Reports API нет — "
    "не пытаться через Hour/DayOfWeek; дни недели — только через "
    "group_by=weekday. Журнал содержит только операции DirectAI — "
    "не предлагать его для выяснения причин ручных изменений в кабинете. "
    "Пробные и исследовательские записи сверх задачи пользователя запрещены; "
    "лимиты — из документации или из ошибки самой запрошенной операции. "
    "Политика записи: бюджеты и смена стратегии запрещены везде "
    "(«Изменение бюджета запрещено политикой»); в боевых кампаниях разрешены "
    "фразы add/пауза, объявления create/update, ссылки/уточнения, регионы "
    "групп, минусы add, ExcludedSites add, ставки/корректировки; replace, "
    "пауза кампаний/объявлений и удаления — только [TEST DirectAI]."
)

def _points(login: str) -> str:
    """Шаг 1.1-4 (Q4): остаток + время обновления («—» без данных)."""
    seen = LAST_SEEN_UNITS.get(login)
    if seen is None:
        return "—"
    units, moment = seen
    return f"{units.rest}/{units.limit} ({moment})"


PLANS = PlanStore()


def accounts_table(settings) -> str:
    """Шаг 1.1-4: кабинеты из кеша discover (без API-вызовов)."""
    from directai_mcp.config import _home_of, _managed_cache

    lines = ["Кабинеты Яндекс Директа:"]
    home = _home_of(settings)
    cache = _managed_cache(home) if home is not None else {}
    logins = cache.get("logins") if isinstance(cache.get("logins"), list) else []
    if not logins:
        lines.append("кеш пуст — выполните accounts_discover.")
        for entry in settings.accounts.values():
            role = f" ({entry.role})" if entry.role else ""
            lines.append(
                f"- {entry.alias}: {entry.login}{role}, "
                f"баллы {_points(entry.login)}"
            )
        return "\n".join(lines)
    known = {e.login: e for e in settings.accounts.values()}
    checks = cache.get("checks") if isinstance(cache.get("checks"), dict) else {}
    lines.append("| Логин | Алиас | Активных ON | Проверка | Баллы |")
    lines.append("| --- | --- | --- | --- | --- |")
    ordered = [settings.auth_login, *[l for l in logins if l != settings.auth_login]]
    for login in ordered:
        alias = known[login].alias if login in known else "—"
        info = checks.get(login)
        if isinstance(info, dict) and "error" not in info:
            on = info.get("on", "—")
            checked = info.get("checked_at", "—")
        else:
            on, checked = "—", "—"
        lines.append(f"| {login} | {alias} | {on} | {checked} | {_points(login)} |")
    return "\n".join(lines)


def build_server(sandbox: bool = False) -> FastMCP:
    mcp = FastMCP("directai-mcp", instructions=INSTRUCTIONS)
    # v1.2.3: FastMCP не передаёт версию во внутренний lowlevel-сервер —
    # SDK подставляет версию пакета mcp (1.30.0) в serverInfo. Проставляем
    # явно, чтобы initialize отдавал версию CLI (__version__).
    mcp._mcp_server.version = __version__

    def _ctx() -> Ctx:
        settings = load_settings()
        token = get_token(settings.auth_login)
        return Ctx(settings=settings, token=token, sandbox=sandbox, data_dir=data_dir())

    @mcp.tool()
    def list_accounts() -> str:
        """Кабинеты из кеша discover, активные кампании, остаток баллов."""
        try:
            ctx = _ctx()
        except (ConfigError, TokenMissingError) as e:
            return f"Ошибка конфигурации: {e}"
        out = accounts_table(ctx.settings)
        if ctx.sandbox:
            out = out.replace(
                "Кабинеты Яндекс Директа:",
                "Кабинеты Яндекс Директа:\n[ПЕСОЧНИЦА]",
                1,
            )
        return out

    @mcp.tool()
    def search_actions(query: str, mode: str = "any") -> str:
        """Поиск действий каталога. mode: read, write или any."""
        from directai_mcp.catalog.registry import categories, match_hint

        if mode not in ("read", "write", "any"):
            return "Ошибка: mode должен быть read, write или any."
        notice = limits_mod.match_limit(query)
        if notice is not None:
            return notice + " (доступно только в веб-интерфейсе)."
        hint = match_hint(query)
        try:
            found = search(query, mode)
        except ValueError as e:
            return f"Ошибка: {e}"
        parts = []
        if hint is not None:
            parts.append(f"Подсказка: {hint}")
        if not found:
            parts.append(categories())
        else:
            parts.append("Найденные действия:")
            for act in found:
                parts.append(f"- {act.name} [{act.mode}]: {act.summary}")
        return "\n".join(parts)

    @mcp.tool()
    def describe_action(name: str) -> str:
        """Описание действия: параметры (JSON Schema), пример, ограничения."""
        act = ACTIONS.get(name)
        if act is None:
            valid = ", ".join(sorted(ACTIONS))
            return f"Ошибка: неизвестное действие '{name}'. Доступны: {valid}."
        schema = act.params.model_json_schema()
        example: dict[str, Any] = {"account": "all", "period": "LAST_7_DAYS"}
        lines = [
            f"Действие {act.name} [{act.mode}]: {act.summary}",
            "",
            "Параметры (JSON Schema):",
            str(schema),
            "",
            f"Пример: run_read({{'name': '{act.name}', 'params': {example}}})",
            (
                "Ограничения: чтение идёт через Reports API c НДС; "
                "запись подключается на шаге 4."
            ),
        ]
        return "\n".join(lines)

    @mcp.tool()
    async def run_read(name: str, params: dict[str, Any] | None = None) -> str:
        """Выполнить действие чтения. Возвращает Markdown-текст."""
        act = ACTIONS.get(name)
        if act is None:
            valid = ", ".join(sorted(ACTIONS))
            return f"Ошибка: неизвестное действие '{name}'. Доступны: {valid}."
        if act.mode != "read" or act.run is None:
            return f"Ошибка: действие '{name}' недоступно для чтения."
        try:
            ctx = _ctx()
        except (ConfigError, TokenMissingError) as e:
            return f"Ошибка конфигурации: {e}"
        try:
            validated = act.params.model_validate(params or {})
        except ValidationError as e:
            return f"Ошибка параметров: {e}"
        try:
            return await act.run(ctx, validated)
        except Exception as e:
            log.exception("run_read %s failed", name)
            return f"Ошибка выполнения '{name}': {e}"

    @mcp.tool()
    async def plan_write(name: str, params: dict[str, Any]) -> str:
        """Подготовить запись: предпросмотр, предупреждения, plan_id."""
        try:
            ctx = _ctx()
        except (ConfigError, TokenMissingError) as e:
            return f"Ошибка конфигурации: {e}"
        return await do_plan_write(ctx, name, params or {})

    @mcp.tool()
    async def apply_write(plan_id: str, acknowledge_warnings: bool = False) -> str:
        """Применить план. Только после согласия пользователя."""
        try:
            ctx = _ctx()
        except (ConfigError, TokenMissingError) as e:
            return f"Ошибка конфигурации: {e}"
        return await do_apply_write(ctx, plan_id, acknowledge_warnings)

    @mcp.tool()
    def get_operation_log(limit: int = 20, account: str | None = None) -> str:
        """Последние операции записи из журнала."""
        try:
            ctx = _ctx()
        except (ConfigError, TokenMissingError) as e:
            return f"Ошибка конфигурации: {e}"
        return do_get_log(ctx, limit, account)

    return mcp


async def do_plan_write(ctx: Ctx, name: str, params: dict) -> str:
    """Shared plan_write body (also used by tests)."""
    from directai_mcp.api.errors import DirectError
    from directai_mcp.safety.guard import precheck

    if guard_active(ctx):
        hit = precheck(name, params)
        if hit is not None:
            return f"Заблокировано защитой: {GuardBlocked(hit)}"
    act = ACTIONS.get(name)
    if act is None:
        if guard_active(ctx):
            blocked = GuardBlocked(
                f"действие '{name}' недоступно в режиме защиты."
            )
            return f"Заблокировано защитой: {blocked}"
        valid = ", ".join(sorted(ACTIONS))
        return f"Ошибка: неизвестное действие '{name}'. Доступны: {valid}."
    if act.mode != "write" or act.prepare is None:
        return f"Ошибка: действие '{name}' не является записью."
    try:
        validated = act.params.model_validate(params)
    except ValidationError as e:
        return f"Ошибка параметров: {e}"
    try:
        entries = ctx.accounts(validated.account)
    except ConfigError as e:
        return f"Ошибка: {e}"
    if len(entries) != 1:
        return "Ошибка: запись требует ровно один аккаунт, не 'all'."
    entry = entries[0]
    client = ctx.direct()
    try:
        try:
            await check_write(ctx, client, entry.login, name, validated.model_dump())
        except GuardBlocked as e:
            return f"Заблокировано защитой: {e}"
        try:
            prep = await act.prepare(ctx, entry, validated)
        except GuardBlocked as e:
            # v1.1.16: guard внутри prepare (корректировки ставок) — тот же
            # формат ответа, что и на check_write, без создания плана.
            return f"Заблокировано защитой: {e}"
        except (DirectError, ValueError) as e:
            return f"Ошибка подготовки: {e}"
    finally:
        await client.aclose()
    plan = Plan(
        plan_id="",
        action=name,
        account_login=entry.login,
        params=validated.model_dump(),
        before=prep["before"],
        requests=prep["requests"],
        preview=prep["preview"],
        warnings=prep.get("warnings", []),
    )
    plan_id = PLANS.put(plan)
    from directai_mcp.catalog.common import net_summary

    lines = [
        f"План {plan_id}: {name} @ {entry.login}.",
        "",
        plan.preview,
    ]
    prep_cost = net_summary(ctx)
    if prep_cost:
        lines += ["", f"Стоимость подготовки: {prep_cost}"]
    if plan.warnings:
        lines += ["", "Предупреждения (нужен acknowledge_warnings=true):"]
        lines += [f"- {w}" for w in plan.warnings]
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    if mark:
        lines.insert(0, mark)
    lines += ["", "Действует 15 минут. Покажи пользователю и получи согласие."]
    return "\n".join(lines)


async def do_apply_write(
    ctx: Ctx, plan_id: str, acknowledge_warnings: bool = False
) -> str:
    """Shared apply_write body (also used by tests)."""
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    plan = PLANS.peek(plan_id)
    if plan is None:
        return f"Ошибка: plan_id {plan_id} неизвестен, просрочен или уже применён."
    if plan.warnings and not acknowledge_warnings:
        return (
            f"Ошибка: план {plan_id} содержит предупреждения; "
            f"повторите с acknowledge_warnings=true."
        )
    plan = PLANS.take(plan_id)
    assert plan is not None
    act = ACTIONS.get(plan.action)
    assert act is not None and act.apply is not None and act.verify is not None
    entry_login = plan.account_login
    from directai_mcp.config import AccountEntry as _Entry

    entry = _Entry(alias=entry_login, login=entry_login)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    try:
        apply_result = await act.apply(ctx, entry, plan)
    except DirectUnverifiedError as e:
        apply_result = {
            "status": "unverified",
            "lines": [f"Результат неизвестен: {e.human_message()}"],
            "response": {"error": e.human_message()},
        }
    except DirectError as e:
        apply_result = {
            "status": "failed",
            "lines": [f"Ошибка API: {e.human_message()}"],
            "response": {"error": e.human_message()},
        }
    status = apply_result.get("status", "failed")
    plan.last_response = apply_result  # type: ignore[attr-defined]
    try:
        verify_result = await act.verify(ctx, entry, plan)
    except DirectError as e:
        verify_result = {
            "after": None,
            "ok": False,
            "note": f"read-back не удался: {e.human_message()}",
        }
    after = verify_result.get("after")
    note = str(verify_result.get("note", ""))
    if status == "applied" and not verify_result.get("ok"):
        status = "unverified"
    if verify_result.get("display_empty") and status == "applied":
        status = "partial"
    summary = "; ".join(apply_result.get("lines", [])) + " | " + note
    conn = journal_mod.connect(ctx.data_dir or data_dir())
    journal_id = journal_mod.insert(
        conn,
        plan_id=plan.plan_id,
        action=plan.action,
        account_login=entry_login,
        params=plan.params,
        before=plan.before,
        requests=[list(r) for r in plan.requests],
        response=apply_result.get("response"),
        after=after,
        status=status,
        summary=summary[:2000],
    )
    conn.close()
    lines = [f"{mark}Применение {plan.plan_id} ({plan.action}): статус {status}."]
    lines += apply_result.get("lines", [])
    lines.append(f"Read-back: {note}")
    from directai_mcp.catalog.common import net_summary

    fact_cost = net_summary(ctx)
    if fact_cost:
        lines.append(fact_cost)
    lines.append(f"Запись журнала #{journal_id}.")
    return "\n".join(lines)


def do_get_log(ctx: Ctx, limit: int = 20, account: str | None = None) -> str:
    """Shared get_operation_log body (also used by tests)."""
    logins: set[str] | None = None
    if account and account != "all":
        try:
            logins = {e.login for e in ctx.accounts(account)}
        except ConfigError as e:
            return f"Ошибка: {e}"
    journal_path = (ctx.data_dir or data_dir()) / "journal.sqlite"
    if not journal_path.exists():
        return "Журнал пуст."
    conn = journal_mod.connect(ctx.data_dir or data_dir())
    try:
        rows = journal_mod.recent(conn, limit)
    finally:
        conn.close()
    if logins is not None:
        rows = [r for r in rows if r["account"] in logins]
    if not rows:
        return "Журнал пуст."
    lines = ["Журнал операций:"]
    for row in rows:
        lines.append(
            f"#{row['id']} {row['created_at']} {row['action']} "
            f"@{row['account']} [{row['status']}]: {row['summary']}"
        )
    return "\n".join(lines)


def run_server(sandbox: bool = False) -> None:
    setup_logging(data_dir())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if os.environ.get("DIRECTAI_SANDBOX") == "1":
        sandbox = True
    build_server(sandbox).run(transport="stdio")
