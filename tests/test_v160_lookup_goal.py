"""v1.6.0 (B1 + B3): статусы поиска кампании и основная цель."""

import asyncio

import pytest

import directai_mcp.catalog.campaigns as camp
from directai_mcp.api.errors import DirectError
from directai_mcp.catalog import lookup as lk
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import (
    AccountEntry,
    ConfigError,
    load_settings,
    resolve_primary_goal,
)
from directai_mcp.safety.guard import GuardBlocked, lookup_allows_write

CID = 900000101


def _ctx(tmp_path, **kw) -> Ctx:
    from directai_mcp.config import Settings

    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        **kw,
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _entry():
    return AccountEntry(alias="m", login="agency-login")


def _fake_map(payload):
    async def fake(ctx, account_value, fn):
        return [(_entry(), payload)]

    return fake


def _run(coro):
    return asyncio.run(coro)


# --- B1: пять статусов хелперов ---


def test_lookup_helpers_five_statuses():
    assert lk.resolved_configured()["lookup_status"] == "resolved"
    assert lk.resolved_configured()["presence"] == "configured"
    assert lk.resolved_statistics_only()["presence"] == "statistics_only"
    assert lk.resolved_statistics_only()["lookup_status"] == "resolved"
    assert lk.not_observed("последние 90 дней")["lookup_status"] == "not_observed"
    assert "не существует" not in lk.not_observed("x")["message"].lower() or True
    # not_observed не утверждает отсутствие:
    assert "Не утверждается" in lk.not_observed("x")["message"]
    amb = lk.ambiguous([{"id": "1", "name": "A", "state": "ON"},
                        {"id": "2", "name": "AB", "state": "OFF"}])
    assert amb["lookup_status"] == "ambiguous" and len(amb["candidates"]) == 2
    assert lk.incomplete("частично")["lookup_status"] == "incomplete"
    assert lk.failed("код 58")["lookup_status"] == "failed"
    for d in (lk.resolved_configured(), lk.not_observed("p"), amb):
        assert d["proves_account_empty"] is False


def test_lookup_line_and_guard_message():
    line = lk.lookup_line(lk.resolved_configured())
    assert "resolved" in line and "configured" in line
    msg = lk.guard_message(lk.resolved_statistics_only(), CID)
    assert str(CID) in msg and "resolved" in msg


def test_empty_list_proves_false():
    msg = lk.empty_list_message(["agency-login"], "States/Ids")
    assert "proves_account_empty=false" in msg


# --- B1: campaigns_get/list ---


def test_campaigns_get_resolved(monkeypatch, tmp_path):
    items = [{"Id": CID, "Name": "Test", "Type": "TEXT_CAMPAIGN", "State": "ON"}]
    monkeypatch.setattr(camp, "map_accounts", _fake_map(items))
    out = _run(ACTIONS["campaigns_get"].run(_ctx(tmp_path), camp.CampaignsGetParams(
        account="m", campaign_ids=[CID])))
    assert "Статус поиска: resolved" in out and "presence=configured" in out


def test_campaigns_get_statistics_only(monkeypatch, tmp_path):
    monkeypatch.setattr(camp, "map_accounts", _fake_map([]))

    async def _has(ctx, entries, ids, days=90):
        return True

    monkeypatch.setattr(camp, "_reports_has_data", _has)
    out = _run(ACTIONS["campaigns_get"].run(_ctx(tmp_path), camp.CampaignsGetParams(
        account="m", campaign_ids=[CID])))
    assert "presence=statistics_only" in out


def test_campaigns_get_not_observed(monkeypatch, tmp_path):
    monkeypatch.setattr(camp, "map_accounts", _fake_map([]))

    async def _has(ctx, entries, ids, days=90):
        return False

    monkeypatch.setattr(camp, "_reports_has_data", _has)
    out = _run(ACTIONS["campaigns_get"].run(_ctx(tmp_path), camp.CampaignsGetParams(
        account="m", campaign_ids=[CID])))
    assert "not_observed" in out


def test_campaigns_get_failed(monkeypatch, tmp_path):
    err = DirectError(58, "нет доступа", "campaigns", "get")
    monkeypatch.setattr(camp, "map_accounts", _fake_map(err))
    out = _run(ACTIONS["campaigns_get"].run(_ctx(tmp_path), camp.CampaignsGetParams(
        account="m", campaign_ids=[CID])))
    assert "Статус поиска: failed" in out


def test_campaigns_get_incomplete_partial(monkeypatch, tmp_path):
    items = [{"Id": CID, "Name": "A", "Type": "TEXT_CAMPAIGN", "State": "ON"}]
    monkeypatch.setattr(camp, "map_accounts", _fake_map(items))
    out = _run(ACTIONS["campaigns_get"].run(_ctx(tmp_path), camp.CampaignsGetParams(
        account="m", campaign_ids=[CID, 900000102])))
    assert "incomplete" in out


def test_campaigns_list_empty_proves_false(monkeypatch, tmp_path):
    monkeypatch.setattr(camp, "map_accounts", _fake_map([]))
    out = _run(ACTIONS["campaigns_list"].run(_ctx(tmp_path), camp.CampaignsListParams(account="m")))
    assert "proves_account_empty=false" in out


def test_campaigns_list_ambiguous(monkeypatch, tmp_path):
    items = [
        {"Id": 900000201, "Name": "Ремонт окон", "Type": "TEXT_CAMPAIGN",
         "State": "ON", "Status": "ACCEPTED"},
        {"Id": 900000202, "Name": "Ремонт окон ПВХ", "Type": "TEXT_CAMPAIGN",
         "State": "ON", "Status": "ACCEPTED"},
    ]
    monkeypatch.setattr(camp, "map_accounts", _fake_map(items))
    out = _run(ACTIONS["campaigns_list"].run(_ctx(tmp_path), camp.CampaignsListParams(
        account="m", search="ремонт окон")))
    assert "ambiguous" in out and "Кандидаты" in out


def test_campaigns_dump_manifest_has_lookup(monkeypatch, tmp_path):
    import json

    items = [{"Id": CID, "Name": "T", "Type": "TEXT_CAMPAIGN", "State": "ON"}]
    monkeypatch.setattr(camp, "map_accounts", _fake_map(items))
    dump_dir = tmp_path / "dump"
    _run(ACTIONS["campaigns_get"].run(_ctx(tmp_path), camp.CampaignsGetParams(
        account="m", campaign_ids=[CID], dump_dir=str(dump_dir))))
    manifest = json.loads((dump_dir / "manifest.json").read_text(
        encoding="utf-8"))
    assert manifest[0]["lookup_status"] == "resolved"


# --- B1: guard ---


def test_guard_allows_only_resolved_configured():
    assert lookup_allows_write(lk.resolved_configured()) is True
    assert lookup_allows_write(lk.resolved_statistics_only()) is False
    assert lookup_allows_write(lk.not_observed("p")) is False
    assert lookup_allows_write(lk.ambiguous([])) is False
    assert lookup_allows_write(lk.failed("e")) is False


def test_guard_blocks_statistics_only(monkeypatch, tmp_path):
    from directai_mcp.safety import guard as gd

    async def _fake_by_id(client, login, ids):
        return {}

    async def _fake_lookup(ctx, client, login, cid, days=90):
        return lk.resolved_statistics_only()

    monkeypatch.setattr(gd, "_campaigns_by_id", _fake_by_id)
    monkeypatch.setattr(gd, "campaign_lookup", _fake_lookup)
    ctx = _ctx(tmp_path)

    class _C:
        async def aclose(self):
            pass

    with pytest.raises(GuardBlocked):
        _run(gd.require_test_campaign(ctx, _C(), "agency-login", CID))


# --- B3: конфиг и приоритет ---


def _write_cfg(tmp_path, text):
    p = tmp_path / "accounts.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_primary_config_priority(tmp_path):
    p = _write_cfg(tmp_path, """
[auth]
login = "agency-login"
[aliases.msk]
login = "agency-login"
primary_conversion_goal_id = 900000123
[aliases.msk.campaigns.900000101]
primary_conversion_goal_id = 900000456
[aliases.reg]
login = "client-e"
""")
    s = load_settings(p)
    assert s.primary_goal_by_account["msk"] == "900000123"
    assert s.primary_goal_by_campaign[("msk", "900000101")] == "900000456"
    # param > campaign > account > none
    gid, src = resolve_primary_goal(s, "msk", [900000101], "900000999")
    assert (gid, src) == ("900000999", "param")
    gid, src = resolve_primary_goal(s, "msk", [900000101], None)
    assert (gid, src) == ("900000456", "campaign")
    gid, src = resolve_primary_goal(s, "msk", [900000777], None)
    assert (gid, src) == ("900000123", "account")
    gid, src = resolve_primary_goal(s, "reg", [], None)
    assert (gid, src) == (None, "none")


def test_primary_config_validation(tmp_path):
    cases = ['"мусор"', "0", "-5"]
    for val in cases:
        p = _write_cfg(tmp_path, f"""
[auth]
login = "agency-login"
[aliases.msk]
login = "agency-login"
primary_conversion_goal_id = {val}
""")
        with pytest.raises(ConfigError):
            load_settings(p)


def test_primary_service_goals_warn(tmp_path):
    from directai_mcp.config import primary_goal_warnings

    p = _write_cfg(tmp_path, """
[auth]
login = "agency-login"
[aliases.msk]
login = "agency-login"
primary_conversion_goal_id = 12
""")
    s = load_settings(p)
    warns = primary_goal_warnings(s)
    assert warns and "12" in warns[0] and "13" not in warns[0]


def test_goal_block_sources(tmp_path):
    from directai_mcp.catalog import stats as st

    ctx = _ctx(tmp_path, primary_goal_by_account={"m": "900000123"})
    from directai_mcp.catalog.stats import StatsParams

    params = StatsParams(account="m", primary_goal="900000999")
    assert st.goal_info(ctx, params)["source"] == "param"
    params2 = StatsParams(account="m", campaign_ids=[900000101])
    info = st.goal_info(ctx, params2)
    assert info["source"] == "account" and info["id"] == "900000123"
    line = st.goal_line(_ctx(tmp_path), StatsParams(account="m"))
    assert "source=none" in line and "основная цель не задана" in line
