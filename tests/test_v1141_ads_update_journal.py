"""v1.14.1: ads_update — привязка к кампании в журнале, перемодерация
только при реальном изменении текста/ссылки."""

import httpx
import pytest

from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety import journal as journal_mod
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"
V501 = "https://api.direct.yandex.com/json/v501"

SET_ID = 9000000003
EXT_IDS = [90000007, 90000008]
CAMPAIGN_ID = 2


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=True,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _mocks(respx_mock):
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=httpx.Response(200, json={"result": {"AdGroups": [
            {"Id": 5, "CampaignId": CAMPAIGN_ID}]}}))
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=httpx.Response(200, json={"result": {"Campaigns": [
            {"Id": CAMPAIGN_ID, "Name": "[TEST DirectAI] v1.14.1"}]}}))
    respx_mock.post(f"{BASE}/sitelinks").mock(
        return_value=httpx.Response(200, json={"result": {
            "SitelinksSets": [{"Id": SET_ID}]}}))
    respx_mock.post(f"{BASE}/adextensions").mock(
        return_value=httpx.Response(200, json={"result": {
            "AdExtensions": [{"Id": i} for i in EXT_IDS]}}))


def _responsive(bound=False, titles=("Эмаль тест", "Второй")):
    sub = {
        "Titles": [{"Title": t} for t in titles],
        "Texts": [{"Text": "t"}],
        "Href": "https://example.com",
        "DisplayUrlPath": "test",
    }
    if bound:
        sub["SitelinkSetId"] = SET_ID
        sub["AdExtensions"] = [{"AdExtensionId": i} for i in EXT_IDS]
    return {"Id": 11, "Type": "RESPONSIVE_AD", "AdGroupId": 5,
            "CampaignId": CAMPAIGN_ID, "ResponsiveAd": sub}


def _text(text="t"):
    return {"Id": 12, "Type": "TEXT_AD", "AdGroupId": 5,
            "CampaignId": CAMPAIGN_ID,
            "TextAd": {"Title": "Эмаль тест", "Text": text,
                       "Href": "https://example.com",
                       "DisplayUrlPath": "test"}}


def _ads(items):
    return httpx.Response(200, json={"result": {"Ads": items}})


async def test_bind_only_no_remod_warning_and_journal_campaign(
    tmp_path, respx_mock
):
    ctx = _ctx(tmp_path)
    _mocks(respx_mock)
    respx_mock.post(f"{V501}/ads").mock(return_value=httpx.Response(
        200, json={"result": {"UpdateResults": [{"Id": 11}]}}))
    respx_mock.post(f"{BASE}/ads").mock(side_effect=[
        _ads([_responsive()]), _ads([_responsive(bound=True)])])
    out = await do_plan_write(ctx, "ads_update", {
        "account": "t", "ad_ids": [11], "display_url_path": "test",
        "sitelink_set_id": SET_ID, "ad_extension_ids": EXT_IDS,
    })
    assert out.startswith("План ")
    assert "перемодерацию" not in out
    assert "acknowledge_warnings" not in out
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(ctx, pid)
    assert "статус applied" in applied
    conn = journal_mod.connect(tmp_path)
    try:
        ops = journal_mod.recent(conn, campaign_id=CAMPAIGN_ID)
    finally:
        conn.close()
    assert [o["action"] for o in ops] == ["ads_update"]


async def test_same_titles_no_remod_warning(tmp_path, respx_mock):
    _mocks(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ads([_responsive()]))
    out = await do_plan_write(_ctx(tmp_path), "ads_update", {
        "account": "t", "ad_ids": [11], "display_url_path": "test",
        "titles": ["Эмаль тест", "Второй"],
    })
    assert out.startswith("План ")
    assert "перемодерацию" not in out


async def test_changed_titles_remod_warning(tmp_path, respx_mock):
    _mocks(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ads([_responsive()]))
    out = await do_plan_write(_ctx(tmp_path), "ads_update", {
        "account": "t", "ad_ids": [11], "display_url_path": "test",
        "titles": ["Эмаль тест", "Новый"],
    })
    assert "перемодерацию" in out


async def test_changed_display_remod_warning(tmp_path, respx_mock):
    _mocks(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ads([_text()]))
    out = await do_plan_write(_ctx(tmp_path), "ads_update", {
        "account": "t", "ad_ids": [12], "display_url_path": "test-2",
    })
    assert "перемодерацию" in out


async def test_text_ad_same_text_no_remod_warning(tmp_path, respx_mock):
    _mocks(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ads([_text()]))
    out = await do_plan_write(_ctx(tmp_path), "ads_update", {
        "account": "t", "ad_ids": [12], "display_url_path": "test",
        "text": "t",
    })
    assert out.startswith("План ")
    assert "перемодерацию" not in out
