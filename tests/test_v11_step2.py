"""Шаг 1.1-2 step 2: discover/check кабинетов, кеш, all/active (SPEC-v1.1).

Даты кеша — относительно «сейчас» (issue #2): зашитая 2026-09-26 перестала
быть свежей через неделю, и 4 теста начали делать незваный Clients.get.
"""
import json
from datetime import datetime, timedelta

import httpx

from directai_mcp.catalog.accounts import (
    AccountsCheckParams,
    AccountsDiscoverParams,
    ensure_cache,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx, search
from directai_mcp.config import (
    CACHE_STALE_DAYS,
    AccountEntry,
    Settings,
    load_settings,
    resolve_account,
)

V5C = "https://api.direct.yandex.com/json/v5/clients"
V5A = "https://api.direct.yandex.com/json/v5/agencyclients"
V501G = "https://api.direct.yandex.com/json/v501/campaigns"


def _ago(**delta) -> str:
    """Метка времени относительно «сейчас» — не хардкод."""
    return (datetime.now().astimezone() - timedelta(**delta)).isoformat()


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _err(code, string="boom"):
    return httpx.Response(
        200,
        json={
            "error": {
                "error_code": code,
                "error_string": string,
                "error_detail": "d",
                "request_id": "r1",
            }
        },
    )


def _ctx(tmp_path, **kw):
    settings = Settings(
        auth_login="agency-login",
        accounts=dict(kw.get("accounts", {})),
        aliases=dict(kw.get("aliases", {})),
        exclude=tuple(kw.get("exclude", ())),
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _cache(tmp_path, **kw):
    cache = {
        # Свежий кеш — час назад: тест не должен зависеть от сегодняшней даты.
        "updated_at": kw.get("updated_at", _ago(hours=1)),
        "method": "Clients.get → ManagedLogins",
        "manager": "agency-login",
        "logins": kw.get("logins", ["a-login", "b-login"]),
        "checks": kw.get("checks", {}),
    }
    (tmp_path / "accounts_cache.json").write_text(
        json.dumps(cache), encoding="utf-8"
    )
    return cache


async def test_discover_writes_cache(respx_mock, tmp_path):
    respx_mock.post(V5C).mock(
        return_value=_ok(
            {"Clients": [{"Login": "agency-login", "ManagedLogins": ["a", "b"]}]}
        )
    )
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_discover"].run(ctx, AccountsDiscoverParams())
    assert "кабинетов: 3" in out
    assert "Clients.get → ManagedLogins" in out
    saved = json.loads((tmp_path / "accounts_cache.json").read_text(encoding="utf-8"))
    assert saved["logins"] == ["a", "b"]
    assert saved["manager"] == "agency-login"


async def test_discover_fallback_agency(respx_mock, tmp_path):
    respx_mock.post(V5C).mock(return_value=_err(54, "auth"))
    respx_mock.post(V5A).mock(
        return_value=_ok({"AgencyClients": [{"Login": "x"}]})
    )
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_discover"].run(ctx, AccountsDiscoverParams())
    assert "AgencyClients.get" in out
    assert "Clients.get:" in out


async def test_discover_blocker(respx_mock, tmp_path):
    respx_mock.post(V5C).mock(return_value=_err(54, "auth"))
    respx_mock.post(V5A).mock(return_value=_err(513, "no direct"))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_discover"].run(ctx, AccountsDiscoverParams())
    assert "список не получен" in out
    assert not (tmp_path / "accounts_cache.json").exists()


async def test_stale_cache_refreshes(respx_mock, tmp_path):
    # Намеренно устаревший кеш: старше порога CACHE_STALE_DAYS.
    _cache(tmp_path, updated_at=_ago(days=CACHE_STALE_DAYS + 2))
    route = respx_mock.post(V5C).mock(
        return_value=_ok(
            {"Clients": [{"Login": "agency-login", "ManagedLogins": ["a-login"]}]}
        )
    )
    ctx = _ctx(tmp_path)
    notes = await ensure_cache(ctx)
    assert route.call_count == 1
    assert any("обновлён" in n for n in notes)


async def test_fresh_cache_no_requests(respx_mock, tmp_path):
    _cache(tmp_path)
    ctx = _ctx(tmp_path)
    notes = await ensure_cache(ctx)
    assert notes == []


def test_resolve_all_active_exclude(tmp_path):
    _cache(
        tmp_path,
        checks={
            "agency-login": {"on": 2},
            "a-login": {"on": 1},
            "b-login": {"on": 0},
        },
    )
    ctx = _ctx(
        tmp_path,
        aliases={"msk": AccountEntry(alias="msk", login="agency-login", role="r")},
        exclude=("b-login",),
    )
    assert [e.login for e in resolve_account(ctx.settings, "all")] == [
        "agency-login",
        "a-login",
    ]
    assert [e.login for e in resolve_account(ctx.settings, "active")] == [
        "agency-login",
        "a-login",
    ]
    assert [e.login for e in resolve_account(ctx.settings, "msk")] == ["agency-login"]


def test_legacy_sections_become_aliases(tmp_path):
    (tmp_path / "accounts.toml").write_text(
        '[auth]\nlogin = "agency-login"\n[accounts.msk]\nlogin = "agency-login"\n',
        encoding="utf-8",
    )
    settings = load_settings(tmp_path / "accounts.toml")
    assert settings.aliases["msk"].login == "agency-login"
    assert settings.legacy_sections == ("msk",)


async def test_check_aggregates_and_saves(respx_mock, tmp_path):
    _cache(tmp_path)
    route = respx_mock.post(V501G).mock(
        side_effect=[
            _ok({"Campaigns": [
                {"Id": 1, "State": "ON"},
                {"Id": 2, "State": "ON"},
                {"Id": 3, "State": "ARCHIVED"},
            ]}),
            _err(513, "no direct"),
            _ok({"Campaigns": []}),
        ]
    )
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_check"].run(
        ctx, AccountsCheckParams(account="all")
    )
    sent = json.loads(route.calls[0].request.content)["params"]
    assert sent["SelectionCriteria"] == {"States": ["ON", "SUSPENDED", "OFF", "ENDED"]}
    assert "Архивных" in out
    assert "b-login" in out.split("кандидаты в exclude")[1]
    saved = json.loads((tmp_path / "accounts_cache.json").read_text(encoding="utf-8"))
    assert saved["checks"]["agency-login"]["on"] == 2
    assert "error" in saved["checks"]["a-login"]


async def test_check_default_hides_archived(respx_mock, tmp_path):
    _cache(tmp_path, logins=["solo"])
    respx_mock.post(V501G).mock(
        return_value=_ok({"Campaigns": [{"Id": 1, "State": "ON"}]})
    )
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_check"].run(
        ctx, AccountsCheckParams(account="solo")
    )
    assert "| solo | ок | 1 |" in out
    assert "—" in out  # колонка «Архивных» без запроса архива


async def test_check_include_archived(respx_mock, tmp_path):
    _cache(tmp_path, logins=["solo"])
    route = respx_mock.post(V501G).mock(
        return_value=_ok({"Campaigns": [
            {"Id": 1, "State": "ON"},
            {"Id": 2, "State": "ARCHIVED"},
        ]})
    )
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_check"].run(
        ctx, AccountsCheckParams(account="solo", include_archived=True)
    )
    sent = json.loads(route.calls[0].request.content)["params"]
    assert sent["SelectionCriteria"] == {}
    assert "| solo | ок | 1 | 0 | 0 | 0 | 1 |" in out


def test_search_finds_account_actions():
    top3 = [a.name for a in search("список кабинетов")[:3]]
    assert "accounts_discover" in top3
    assert "accounts_check" in top3
    names = [a.name for a in search("проверка кабинетов доступ")]
    assert "accounts_check" in names
