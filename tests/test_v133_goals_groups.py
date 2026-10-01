"""v1.3.3: adgroups-колонки, counter goals_only, metrika_goals_list."""

import httpx

import directai_mcp.catalog.adgroups as _ad  # noqa: F401 (реестр)
import directai_mcp.catalog.counters as _c  # noqa: F401 (реестр)
from directai_mcp.catalog import metrika_goals as _mg
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

V501G = "https://api.direct.yandex.com/json/v501/adgroups"


def _ctx(tmp_path) -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _metrika(monkeypatch):
    import json

    def _fake(token, path, timeout=30.0):
        if path == "/management/v1/counter/11":
            body = {"counter": {"id": 11, "name": "C11",
                                "site": "site11.ru", "status": "Active"}}
        elif path == "/management/v1/counter/11/goals":
            body = {"goals": [{"id": 900000142, "name": "G-retarget",
                               "type": "action"}]}
        else:
            raise AssertionError(path)
        return "HTTP/1.1 200 OK", json.dumps(body)

    monkeypatch.setattr(_mg, "_mget", _fake)
    for cache in (_mg.COUNTER_GOALS_CACHE, _mg.COUNTER_INFO_CACHE,
                  _mg.COUNTER_GOAL_NAMES_CACHE):
        cache.clear()


def test_adgroups_negatives_tracking_columns(respx_mock, tmp_path):
    respx_mock.post(V501G).mock(
        return_value=httpx.Response(200, json={"result": {"AdGroups": [
            {"Id": 5, "CampaignId": 7, "Name": "G",
             "Type": "UNIFIED_AD_GROUP", "Status": "ACCEPTED",
             "ServingStatus": "ELIGIBLE", "RegionIds": [1],
             "NegativeKeywords": {"Items": ["a", "b"]},
             "NegativeKeywordSharedSetIds": {"Items": [900000051]},
             "TrackingParams": "from=direct"},
        ]}}))
    out = _run_adgroups(respx_mock, tmp_path)
    assert ("| Negatives | SharedSets | Tracking | Autotargeting |"
            in out)
    assert "2 фраз" in out and "900000051" in out and "from=direct" in out


def _run_adgroups(respx_mock, tmp_path):
    import asyncio


    respx_mock.post("https://api.direct.yandex.com/json/v5/keywords").mock(
        return_value=httpx.Response(200, json={"result": {"Keywords": []}}))
    return asyncio.run(ACTIONS["adgroups_list"].run(
        _ctx(tmp_path),
        ACTIONS["adgroups_list"].params(account="m", campaign_ids=[7])))


async def test_counter_goals_only_no_stats(respx_mock, tmp_path, monkeypatch):
    _metrika(monkeypatch)
    respx_mock.post(
        "https://api.direct.yandex.com/json/v501/campaigns").mock(
        return_value=httpx.Response(200, json={"result": {"Campaigns": [{
            "Id": 7, "Name": "K",
            "UnifiedCampaign": {
                "CounterIds": {"Items": [11]},
                "PriorityGoals": {"Items": [{"GoalId": 900000142}]},
                "BiddingStrategy": {}}}]}}))
    # Reports/Stat не мокаем: любой stats-запрос уронит тест.
    out = await ACTIONS["counter_check"].run(
        _ctx(tmp_path), ACTIONS["counter_check"].params(
            account="m", campaign_ids=[7], goals_only=True))
    assert "только цели, без статистики" in out
    assert "G-retarget" in out or "900000142" in out
    assert "Конверсии" not in out and "Визиты" not in out


async def test_metrika_goals_list_names(respx_mock, tmp_path, monkeypatch):
    _metrika(monkeypatch)
    respx_mock.post(
        "https://api.direct.yandex.com/json/v501/campaigns").mock(
        return_value=httpx.Response(200, json={"result": {"Campaigns": [{
            "Id": 7, "Name": "K",
            "UnifiedCampaign": {
                "CounterIds": {"Items": [11]},
                "PriorityGoals": {"Items": []},
                "BiddingStrategy": {}}}]}}))
    out = await ACTIONS["metrika_goals_list"].run(
        _ctx(tmp_path), ACTIONS["metrika_goals_list"].params(
            account="m", campaign_ids=[7]))
    assert "metrika_goals_list" in out
    assert "G-retarget" in out and "900000142" in out and "action" in out


def test_metrika_goals_list_requires_scope(tmp_path):
    import asyncio

    out = asyncio.run(ACTIONS["metrika_goals_list"].run(
        _ctx(tmp_path), ACTIONS["metrika_goals_list"].params()))
    assert "campaign_ids" in out


def test_goals_only_param_schema():
    assert not ACTIONS["counter_check"].params.model_fields[
        "goals_only"].is_required()
    assert ACTIONS["metrika_goals_list"].mode == "read"


async def test_keywords_full_phrase_by_default(respx_mock, tmp_path):
    import httpx as _httpx

    respx_mock.post(
        "https://api.direct.yandex.com/json/v5/keywords").mock(
        return_value=_httpx.Response(200, json={"result": {"Keywords": [
            {"Id": 900000011, "AdGroupId": 900000010,
             "Keyword": "эмаль -купить -бесплатно",
             "State": "ON", "Status": "ACCEPTED",
             "ServingStatus": "ELIGIBLE", "Bid": 90000001,
             "ContextBid": 500000},
        ]}}))
    out = await ACTIONS["keywords_list"].run(
        _ctx(tmp_path), ACTIONS["keywords_list"].params(
            account="m", adgroup_ids=[900000010]))
    assert "эмаль -купить -бесплатно" in out
    out = await ACTIONS["keywords_list"].run(
        _ctx(tmp_path), ACTIONS["keywords_list"].params(
            account="m", adgroup_ids=[900000010], short_phrases=True))
    assert "| эмаль |" in out
