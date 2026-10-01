"""CLI: init | set-token | check | serve (SPEC 9 step 1)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import shutil
import sys
from pathlib import Path

from directai_mcp import __version__
from directai_mcp.api.direct import DirectClient
from directai_mcp.api.errors import DirectError
from directai_mcp.config import (
    KEYRING_SERVICE,
    KEYRING_SERVICE_AUDIENCE,
    KEYRING_SERVICE_WEBMASTER,
    ConfigError,
    TokenMissingError,
    data_dir,
    get_token,
    load_settings,
)
from directai_mcp.log import setup_logging

log = logging.getLogger(__name__)

# Campaigns.get states that are NOT archive (SPEC 7.7).
ACTIVE_STATES = ["ON", "OFF", "SUSPENDED", "ENDED"]

REPO_EXAMPLES_CANDIDATES = Path(__file__).resolve().parent.parent.parent / "examples"


def _examples_dir() -> Path | None:
    if REPO_EXAMPLES_CANDIDATES.is_dir():
        return REPO_EXAMPLES_CANDIDATES
    try:
        from importlib.resources import files

        res = files("directai_mcp") / "examples"
        if res.is_dir():
            return Path(str(res))
    except Exception as e:  # noqa: BLE001
        log.debug("examples resource lookup failed: %s", e)
    return None


def cmd_init(home: Path | None = None) -> int:
    """Create data dir and copy example configs without overwriting."""
    target = home or data_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / "exports").mkdir(exist_ok=True)
    (target / "logs").mkdir(exist_ok=True)
    setup_logging(target)

    src = _examples_dir()
    copied, kept = [], []
    if src is not None:
        for name in ("accounts.toml", "rules.toml", "goals.toml"):
            dst = target / name
            if dst.exists():
                kept.append(name)
                continue
            origin = src / name
            if origin.exists():
                shutil.copy(origin, dst)
                copied.append(name)
    print(f"data dir: {target}")
    if copied:
        print(f"copied: {', '.join(copied)}")
    if kept:
        print(f"kept existing: {', '.join(kept)}")
    if src is None:
        print("examples not found in package; create accounts.toml manually")
    return 0


def cmd_set_token(
    login: str | None = None,
    webmaster: bool = False,
    audience: bool = False,
) -> int:
    """Masked token input, save to Windows Credential Manager."""
    target = data_dir()
    target.mkdir(parents=True, exist_ok=True)
    setup_logging(target)

    if audience:
        service = KEYRING_SERVICE_AUDIENCE
        label = "Аудитории"
    elif webmaster:
        service = KEYRING_SERVICE_WEBMASTER
        label = "Вебмастер"
    else:
        service = KEYRING_SERVICE
        label = "основной"

    resolved_login = login
    if not resolved_login:
        try:
            resolved_login = load_settings().auth_login
        except ConfigError:
            from directai_mcp.config import DEFAULT_AUTH_LOGIN

            resolved_login = DEFAULT_AUTH_LOGIN
            print(f"no accounts.toml, using login '{resolved_login}'")
            print(f"hint: run `directai-mcp init` first (data dir: {target})")

    token = getpass.getpass(f"token for {resolved_login} ({label}): ").strip()
    if not token:
        print("empty token, not saved", file=sys.stderr)
        return 1
    import keyring

    keyring.set_password(service, resolved_login, token)
    print(f"saved to Credential Manager: {service}/{resolved_login}")
    return 0


async def _check_one(login: str, role: str, token: str, sandbox: bool) -> str:
    client = DirectClient(token=token, sandbox=sandbox)
    try:
        await client.call(
            "clients", "get", {"FieldNames": ["Login", "ClientId"]}, login
        )
        campaigns = await client.get_all(
            "campaigns",
            {
                "SelectionCriteria": {"States": ACTIVE_STATES},
                "FieldNames": ["Id"],
            },
            login,
            "Campaigns",
            "v501",
        )
        units = client.last_units.get(login)
        points = f"{units.rest}/{units.limit}" if units else "?"
        label = f" ({role})" if role else ""
        return f"OK {login}{label}: {len(campaigns)} campaigns, points {points}"
    except DirectError as e:
        return f"FAIL {login}: {e.human_message()}"
    finally:
        await client.aclose()


async def _check_all(sandbox: bool) -> int:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    try:
        token = get_token(settings.auth_login)
    except TokenMissingError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    sem = asyncio.Semaphore(3)

    async def one(alias_login: tuple[str, str, str]) -> str:
        _, login, role = alias_login
        async with sem:
            return await _check_one(login, role, token, sandbox)

    entries = [(e.alias, e.login, e.role) for e in settings.accounts.values()]

    results = await asyncio.gather(*(one(e) for e in entries))
    ok = True
    for line in results:
        print(line)
        if line.startswith("FAIL"):
            ok = False
    print(await _check_audience(settings.auth_login, token))
    for warn in _check_primary_goals(settings, token):
        print(warn)
    if sandbox:
        print("[SANDBOX]")
    return 0 if ok else 1


def _check_primary_goals(settings, token: str) -> list[str]:
    """B3: валидация основной цели в check (предупреждения, не ошибки)."""
    from directai_mcp.config import primary_goal_warnings

    out: list[str] = []
    for warn in primary_goal_warnings(settings):
        out.append(f"ВНИМАНИЕ: {warn}")
    ids: set[str] = set(settings.primary_goal_by_account.values()) | {
        g for g in settings.primary_goal_by_campaign.values()
    }
    ids.discard("12")
    ids.discard("13")
    if not ids or not settings.counter_id:
        if ids and not settings.counter_id:
            out.append(
                "ВНИМАНИЕ: primary_conversion_goal_id задан, но счётчик "
                "Метрики не настроен — сверка с Метрикой пропущена (не ошибка)."
            )
        return out
    try:
        import json as _json

        from directai_mcp.catalog.metrika_goals import _check_status, _mget

        status, body = _mget(
            token, f"/management/v1/counter/{settings.counter_id}/goals"
        )
        _check_status("metrika goals", status)
        payload = _json.loads(body)
        have = {
            str(g.get("id")) for g in payload.get("goals", []) if isinstance(g, dict)
        }
        missing = sorted(i for i in ids if i not in have)
        if missing:
            out.append(
                "ВНИМАНИЕ: целей нет у счётчика "
                f"{settings.counter_id}: {', '.join(missing)} (не ошибка)."
            )
    except Exception as e:  # noqa: BLE001
        out.append(f"ВНИМАНИЕ: сверка целей с Метрикой пропущена: {e} (не ошибка).")
    return out


async def _check_audience(auth_login: str, main_token: str) -> str:
    """Строка check по Аудиториям: только отдельный токен, в API без него не ходим."""
    from directai_mcp.api.audience import _get
    from directai_mcp.api.errors import AudienceError
    from directai_mcp.config import get_audience_token

    token = get_audience_token(auth_login)
    if not token:
        return (
            "Аудитории: не настроены (необязательно: directai-mcp set-token --audience)"
        )
    try:
        payload = await _get(token, "segments")
    except AudienceError as e:
        return f"FAIL Аудитории: {e}"
    items = payload.get("segments") if isinstance(payload, dict) else None
    if items is None:
        return "FAIL Аудитории: нет поля `segments` в ответе"
    return f"OK Аудитории: {len(items)} сегментов"


def cmd_check(sandbox: bool = False) -> int:
    setup_logging(data_dir())
    return asyncio.run(_check_all(sandbox))


def cmd_serve(sandbox: bool = False) -> int:
    from directai_mcp.server import run_server

    run_server(sandbox=sandbox)
    return 0


# v1.2.3: самопроверка MCP через настоящий клиент (initialize → tools/list →
# search_actions). Только локальные вызовы, Reports API не трогает (баллы
# не тратятся). Сырые stdio-пробы вручную не делать — только эта команда.
PROBE_QUERY = "итоги по аккаунтам"
PROBE_EXPECTED = "stats_summary"


def _probe_text(result) -> str:
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


async def _probe_async(timeout: float) -> int:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "directai_mcp.cli"]
    )
    try:
        async with (
            asyncio.timeout(timeout),
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            listed = await session.list_tools()
            names = [t.name for t in listed.tools]
            if "search_actions" not in names:
                print("FAIL tools/list: нет search_actions")
                return 1
            res = await session.call_tool(
                "search_actions",
                {"query": PROBE_QUERY, "mode": "any"},
            )
    except Exception as e:  # noqa: BLE001
        print(f"FAIL probe: {e}")
        return 1
    info = init.serverInfo
    found = PROBE_EXPECTED in _probe_text(res)
    print(f"OK server={info.name} version={info.version} tools={len(names)}")
    print(
        f"{'OK' if found else 'FAIL'} "
        f"search_actions({PROBE_QUERY!r}): {PROBE_EXPECTED} "
        f"{'найден' if found else 'НЕ найден'}"
    )
    return 0 if found else 1


def cmd_probe(timeout: float = 30.0) -> int:
    return asyncio.run(_probe_async(timeout))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="directai-mcp", description="DirectAI MCP")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--sandbox", action="store_true", help="use Direct sandbox")
    p.add_argument("--home", type=Path, default=None, help="override data dir")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init", help="create data dir and example configs")

    st = sub.add_parser("set-token", help="save token to Credential Manager")
    st.add_argument("--login", default=None)
    st.add_argument(
        "--webmaster",
        action="store_true",
        help="сохранить отдельный токен Вебмастера (из приложения «для доступа к API»)",
    )
    st.add_argument(
        "--audience",
        action="store_true",
        help="сохранить отдельный токен Аудиторий (экспериментально)",
    )

    sub.add_parser("check", help="Clients.get + campaign count per account")
    sub.add_parser("serve", help="run MCP server over STDIO (step 2)")
    pr = sub.add_parser(
        "probe",
        help="self-check over MCP: initialize, tools/list, search_actions",
    )
    pr.add_argument("--timeout", type=float, default=30.0)
    doc = sub.add_parser(
        "doctor",
        help="diagnose connection: version, exe, processes, config, tokens, API, Hermes",
    )
    doc.add_argument("--json", action="store_true", help="machine output for the skill")
    doc.add_argument("--skip-api", action="store_true", help="skip network calls")
    doc.add_argument(
        "--preinstall",
        action="store_true",
        help="strict pre-reinstall check: busy exe is FAIL, not INFO",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.home is not None:
        os.environ["DIRECTAI_HOME"] = str(args.home)
    if args.command == "init":
        raise SystemExit(cmd_init())
    if args.command == "set-token":
        raise SystemExit(
            cmd_set_token(args.login, webmaster=args.webmaster, audience=args.audience)
        )
    if args.command == "check":
        raise SystemExit(cmd_check(sandbox=args.sandbox))
    if args.command == "probe":
        raise SystemExit(cmd_probe(timeout=args.timeout))
    if args.command == "doctor":
        from directai_mcp.doctor import cmd_doctor

        raise SystemExit(
            cmd_doctor(
                json_output=args.json,
                skip_api=args.skip_api,
                preinstall=args.preinstall,
            )
        )
    raise SystemExit(cmd_serve(sandbox=args.sandbox))


if __name__ == "__main__":
    main()
