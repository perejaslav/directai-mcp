"""v1.1.30 (тест 24.П.3): counter_check — диагностика счётчиков кампании."""

import json

import httpx
import pytest

import directai_mcp.catalog.counters as _c  # noqa: F401 (реестр)
from directai_mcp.catalog import metrika_goals as _mg
from directai_mcp.catalog.counters import _norm_domain, _strategy_goal_id
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, ConfigError, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"
BASE = "https://api.direct.yandex.com/json/v5"
V501C = "https://api.direct.yandex.com/json/v501/campaigns"
V5A = "https://api.direct.yandex.com/json/v5/ads"


def _ctx(tmp_path, **kw):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        **kw,
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def _ok(result):
    return httpx.Response(200, json={"result": result})


CAMP = {"result": {"Campaigns": [{
    "Id": 7, "Name": "K",
    "UnifiedCampaign": {
        "CounterIds": {"Items": [11]},
        "PriorityGoals": {"Items": [{"GoalId": 1}, {"GoalId": 2}]},
        "BiddingStrategy": {"Network": {"GoalId": 2}},
    }}]}}
ADS = {"result": {"Ads": [
    {"Id": 1, "TextAd": {"Href": "https://other.ru/page"}},
    {"Id": 2, "ResponsiveAd": {"Href": "http://www.site11.ru/"}},
]}}


def _metrika(monkeypatch):
    def _fake(token, path, timeout=30.0):
        if path == "/management/v1/counter/11":
            body = {"counter": {"id": 11, "name": "C11", "site": "site11.ru",
                                "status": "Active", "code_status": "CS_OK",
                                "activity_status": "high"}}
        elif path == "/management/v1/counter/11/goals":
            body = {"goals": [{"id": 1, "name": "G1", "type": "action"},
                              {"id": 2, "name": "G2", "type": "action"}]}
        elif path == "/management/v1/counter/99":
            body = {"counter": {"id": 99, "name": "C99", "site": "site99.ru",
                                "status": "Active", "code_status": "CS_OK",
                                "activity_status": "high"}}
        elif path == "/management/v1/counter/99/goals":
            body = {"goals": [{"id": 3, "name": "G3", "type": "action"}]}
        elif path.startswith("/stat/v1/data"):
            if "lastDirectClickOrder" in path:
                body = {"data": [], "totals": [0.0]}
            elif "trafficSource" in path:
                body = {"data": [{"metrics": [12.0]}], "totals": [12.0]}
            else:
                body = {"data": [{"metrics": [141.0]}], "totals": [141.0]}
        else:
            raise AssertionError(path)
        return "HTTP/1.1 200 OK", json.dumps(body)
    monkeypatch.setattr(_mg, "_mget", _fake)
    for cache in (_mg.COUNTER_GOALS_CACHE, _mg.COUNTER_INFO_CACHE,
                  _mg.COUNTER_GOAL_NAMES_CACHE):
        cache.clear()


def test_helpers():
    assert _norm_domain("https://WWW.Example.ru/a?b=1") == "example.ru"
    assert _norm_domain("www.example.ru") == "example.ru"
    assert _norm_domain("not a url") == ""
    assert _norm_domain(None) == ""
    assert _strategy_goal_id({"Network": {"GoalId": 5}}) == 5
    assert _strategy_goal_id({"A": [{"B": {}}]}) is None
    assert _strategy_goal_id(None) is None


def test_threshold_default_and_bad(tmp_path):
    assert _ctx(tmp_path).settings.counter_visits_warn_pct == 50
    path = tmp_path / "accounts.toml"
    path.write_text('[defaults]\ncounter_visits_warn_pct = 120\n'
                    '[aliases.m]\nlogin = "agency-login"\n', encoding="utf-8")
    from directai_mcp.config import load_settings
    with pytest.raises(ConfigError, match="counter_visits_warn_pct"):
        load_settings(path)


async def test_counter_info_and_stat(monkeypatch):
    _metrika(monkeypatch)
    info = await _mg.counter_info("t", 11)
    assert (info["name"], info["site"], info["status"]) == (
        "C11", "site11.ru", "Active")
    stat = await _mg.stat_visits("t", 11, "2026-09-11", "2026-09-24", 7)
    assert stat == {"matched": 0, "matched_empty": True,
                    "ad_total": 12, "total": 141}


def _reports(respx_mock, conv_cols=""):
    detail = ("CampaignId\tClicks\tCost" + conv_cols + "\n"
              "7\t4965\t40549.11" + ("\t2" if conv_cols else "") + "\n")
    lc = "CampaignId\tClicks\tCost\tConversions\n7\t4965\t40549.11\t2\n"
    respx_mock.post(RURL).mock(side_effect=[_tsv(detail), _tsv(lc)])


async def test_full_flags(respx_mock, tmp_path, monkeypatch):
    _metrika(monkeypatch)
    respx_mock.post(V501C).mock(return_value=httpx.Response(200, json=CAMP))
    respx_mock.post(V5A).mock(return_value=httpx.Response(200, json=ADS))
    conv = "\tConversions_1_AUTO\tConversions_2_AUTO"
    _reports(respx_mock, conv)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["counter_check"].run(
        ctx, ACTIONS["counter_check"].params(
            account="agency-login", campaign_ids=[7], counter_ids=[99],
            date_from="2026-09-11", date_to="2026-09-24"))
    assert "counter_check: 2026-09-11–2026-09-24" in out
    assert "site11.ru" in out and "site99.ru" in out
    assert "дополнительно" in out
    # Визиты 12 против 4965 (0.24%) — флаг; размерность пустая — сигнал.
    assert "не связан с Директом" in out
    assert "ниже порога 50%" in out
    # Посадочная other.ru без счётчика — флаг.
    assert "посадочная без счётчика кампании: other.ru" in out
    assert "флагов нет" not in out.lower()


async def test_unexplained_conversions_flag(respx_mock, tmp_path, monkeypatch):
    _metrika(monkeypatch)
    respx_mock.post(V501C).mock(return_value=httpx.Response(200, json=CAMP))
    respx_mock.post(V5A).mock(return_value=httpx.Response(200, json=ADS))
    _reports(respx_mock)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["counter_check"].run(
        ctx, ACTIONS["counter_check"].params(
            account="agency-login", campaign_ids=[7],
            date_from="2026-09-11", date_to="2026-09-24"))
    assert "итог LC — 2 конв." in out
    assert "цель чужого счётчика" in out


async def test_foreign_goal_flag(respx_mock, tmp_path, monkeypatch):
    _metrika(monkeypatch)
    camp = {"result": {"Campaigns": [{
        "Id": 7, "Name": "K",
        "UnifiedCampaign": {
            "CounterIds": {"Items": [11]},
            "PriorityGoals": {"Items": [{"GoalId": 3}]},
            "BiddingStrategy": {},
        }}]}}
    respx_mock.post(V501C).mock(return_value=httpx.Response(200, json=camp))
    respx_mock.post(V5A).mock(return_value=httpx.Response(200, json=ADS))
    detail = ("CampaignId\tClicks\tCost\tConversions_3_AUTO\n"
              "7\t100\t100.00\t1\n")
    lc = "CampaignId\tClicks\tCost\tConversions\n7\t100\t100.00\t1\n"
    respx_mock.post(RURL).mock(side_effect=[_tsv(detail), _tsv(lc)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["counter_check"].run(
        ctx, ACTIONS["counter_check"].params(
            account="agency-login", campaign_ids=[7], counter_ids=[99],
            date_from="2026-09-11", date_to="2026-09-24"))
    assert "цель 3 — счётчик 99 вне CounterIds" in out
    assert "конверсии (1) по цели 3" in out


async def test_validation(tmp_path):
    ctx = _ctx(tmp_path)
    out = await ACTIONS["counter_check"].run(
        ctx, ACTIONS["counter_check"].params(
            account="agency-login", date_from="2026-09-11",
            date_to="2026-09-24"))
    assert out.startswith("Ошибка: укажите campaign_ids")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ACTIONS["counter_check"].params(
            account="agency-login", campaign_ids=[7],
            date_from="11.09.2026", date_to="2026-09-24")
