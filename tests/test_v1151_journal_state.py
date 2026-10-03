"""v1.15.1: ads_state / keywords_state попадают в журнал своей кампании."""

import httpx
import pytest

import directai_mcp.catalog.ads as _ad  # noqa: F401 (реестр)
import directai_mcp.catalog.keywords as _kw  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety import journal as journal_mod
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

V5 = "https://api.direct.yandex.com/json/v5"
CID = 715
TEST_NAME = "[TEST DirectAI] журнал"


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ok(key, items):
    return httpx.Response(200, json={"result": {key: items}})


def _guard(respx_mock):
    respx_mock.post(f"{V5}/adgroups").mock(
        return_value=_ok("AdGroups", [{"Id": 5, "CampaignId": CID}]))
    respx_mock.post(f"{V5}/campaigns").mock(
        return_value=_ok("Campaigns", [{"Id": CID, "Name": TEST_NAME}]))


def _journal_actions(tmp_path):
    conn = journal_mod.connect(tmp_path)
    try:
        return [o["action"] for o in journal_mod.recent(conn, campaign_id=CID)]
    finally:
        conn.close()


async def test_ads_state_bound_to_campaign(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    respx_mock.post(f"{V5}/ads").mock(side_effect=[
        _ok("Ads", [{"Id": 7, "AdGroupId": 5}]),  # guard: группа объявления
        _ok("Ads", [{"Id": 7, "State": "ON", "Status": "ACCEPTED",
                     "CampaignId": CID}]),  # prepare
        _ok("SuspendResults", [{"Id": 7}]),  # apply
        _ok("Ads", [{"Id": 7, "State": "SUSPENDED"}]),  # verify
    ])
    out = await do_plan_write(ctx, "ads_state", {
        "account": "t", "ad_ids": [7], "operation": "suspend"})
    assert out.startswith("План "), out
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(ctx, pid)
    assert "статус applied" in applied, applied
    assert _journal_actions(tmp_path) == ["ads_state"]


async def test_keywords_state_bound_to_campaign(tmp_path, respx_mock):
    # пауза фраз разрешена в боевой — guard не читает кампанию.
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{V5}/keywords").mock(side_effect=[
        _ok("Keywords", [{"Id": 4, "Keyword": "к", "State": "ON",
                          "CampaignId": CID}]),  # prepare
        _ok("SuspendResults", [{"Id": 4}]),  # apply
        _ok("Keywords", [{"Id": 4, "State": "SUSPENDED"}]),  # verify
    ])
    out = await do_plan_write(ctx, "keywords_state", {
        "account": "t", "keyword_ids": [4], "operation": "suspend"})
    assert out.startswith("План "), out
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(ctx, pid)
    assert "статус applied" in applied, applied
    assert _journal_actions(tmp_path) == ["keywords_state"]
