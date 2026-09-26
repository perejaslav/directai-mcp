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


def cmd_set_token(login: str | None = None) -> int:
    """Masked token input, save to Windows Credential Manager."""
    target = data_dir()
    target.mkdir(parents=True, exist_ok=True)
    setup_logging(target)

    resolved_login = login
    if not resolved_login:
        try:
            resolved_login = load_settings().auth_login
        except ConfigError:
            from directai_mcp.config import DEFAULT_AUTH_LOGIN

            resolved_login = DEFAULT_AUTH_LOGIN
            print(f"no accounts.toml, using login '{resolved_login}'")
            print(f"hint: run `directai-mcp init` first (data dir: {target})")

    token = getpass.getpass(f"token for {resolved_login}: ").strip()
    if not token:
        print("empty token, not saved", file=sys.stderr)
        return 1
    import keyring

    keyring.set_password(KEYRING_SERVICE, resolved_login, token)
    print(f"saved to Credential Manager: {KEYRING_SERVICE}/{resolved_login}")
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
    if sandbox:
        print("[SANDBOX]")
    return 0 if ok else 1


def cmd_check(sandbox: bool = False) -> int:
    setup_logging(data_dir())
    return asyncio.run(_check_all(sandbox))


def cmd_serve(sandbox: bool = False) -> int:
    from directai_mcp.server import run_server

    run_server(sandbox=sandbox)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="directai-mcp", description="DirectAI MCP")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--sandbox", action="store_true", help="use Direct sandbox")
    p.add_argument("--home", type=Path, default=None, help="override data dir")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init", help="create data dir and example configs")

    st = sub.add_parser("set-token", help="save token to Credential Manager")
    st.add_argument("--login", default=None)

    sub.add_parser("check", help="Clients.get + campaign count per account")
    sub.add_parser("serve", help="run MCP server over STDIO (step 2)")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.home is not None:
        os.environ["DIRECTAI_HOME"] = str(args.home)
    if args.command == "init":
        raise SystemExit(cmd_init())
    if args.command == "set-token":
        raise SystemExit(cmd_set_token(args.login))
    if args.command == "check":
        raise SystemExit(cmd_check(sandbox=args.sandbox))
    raise SystemExit(cmd_serve(sandbox=args.sandbox))
