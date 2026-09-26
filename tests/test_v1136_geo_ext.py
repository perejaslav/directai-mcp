"""v1.1.36: ссылки, уточнения (дедуп, add/remove), регионы групп."""

from types import SimpleNamespace

import httpx
import pytest

import directai_mcp.catalog.adgroups as _ag  # noqa: F401 (реестр)
import directai_mcp.catalog.ads as _ad  # noqa: F401 (реестр)
import directai_mcp.catalog.extensions as _e  # noqa: F401 (реестр)
from directai_mcp.catalog.adgroups import _verify_adgroups_updated
from directai_mcp.catalog.common import _GEO_CACHE
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"
V501 = "https://api.direct.yandex.com/json/v501"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=False,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _entry():
    return AccountEntry(alias="t", login="test-login")


def _ok(result):
    return httpx.Response(200, json={"result": result})


@pytest.fixture(autouse=True)
def _clean():
    PLANS.clear()
    _GEO_CACHE.clear()
    _GEO_CACHE["GeoRegions"] = [
        {"GeoRegionId": 213, "GeoRegionName": "Москва"},
        {"GeoRegionId": 216, "GeoRegionName": "Санкт-Петербург"},
        {"GeoRegionId": 219, "GeoRegionName": "Черноголовка"},
    ]
    yield
    PLANS.clear()
    _GEO_CACHE.clear()


def _link(i):
    return {"title": f"Ссылка {i}", "href": "https://example.com"}


async def test_sitelink_set_count_over_blocked(tmp_path):
    out = await do_plan_write(
        _ctx(tmp_path), "extensions_create",
        {"account": "t", "sitelinks": [_link(i) for i in range(9)]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "1–8" in out
    assert len(PLANS) == 0


async def test_sitelink_titles_sum_warns(tmp_path):
    out = await do_plan_write(
        _ctx(tmp_path), "extensions_create",
        {"account": "t", "sitelinks": [
            {"title": "Длинный заголовок ссылки номер",
             "href": "https://example.com"} for _ in range(3)]},
    )
    assert out.startswith("План ")
    assert "сумма заголовков" in out
    assert "acknowledge_warnings=true" in out


async def test_callout_dedup_reuses_id(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/adextensions").mock(side_effect=[
        _ok({"AdExtensions": [
            {"Id": 5, "Type": "CALLOUT",
             "Callout": {"CalloutText": "Прямой API"}}]}),
        _ok({"AddResults": [{"Id": 6}]}),
        _ok({"AdExtensions": [{"Id": 6}]}),
        _ok({"AdExtensions": [{"Id": 5}]}),
    ])
    out = await do_plan_write(
        _ctx(tmp_path), "extensions_create",
        {"account": "t", "callouts": ["  ПРЯМОЙ API ", "Новое уточнение"]},
    )
    assert out.startswith("План ")
    assert "переиспользованы" in out and "Id 5" in out
    body = PLANS.peek(out.split()[1].rstrip(":")).requests[0][2]["AdExtensions"]
    assert [e["Callout"]["CalloutText"] for e in body] == ["Новое уточнение"]
    applied = await do_apply_write(
        _ctx(tmp_path), out.split()[1].rstrip(":"))
    assert "статус applied" in applied
    assert "переиспользовано уточнений: 1" in applied


def _resp_ad(aid=11, ext=()):
    return {"Id": aid, "Type": "RESPONSIVE_AD",
            "ResponsiveAd": {
                "Titles": [{"Title": "T"}], "Texts": [{"Text": "t"}],
                "Href": "https://example.com", "DisplayUrlPath": "test",
                "AdExtensions": [{"AdExtensionId": i} for i in ext]}}


async def test_ads_ext_add_merges(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/ads").mock(side_effect=[
        _ok({"Ads": [_resp_ad(ext=(1,))]}),
        _ok({"Ads": [_resp_ad(ext=(1, 2))]}),
    ])
    respx_mock.post(f"{BASE}/adextensions").mock(
        return_value=_ok({"AdExtensions": [{"Id": 2}]}))
    respx_mock.post(f"{V501}/ads").mock(
        return_value=_ok({"UpdateResults": [{"Id": 11}]}))
    out = await do_plan_write(
        _ctx(tmp_path), "ads_update",
        {"account": "t", "ad_ids": [11], "ad_extension_ids": [2],
         "extension_mode": "add", "display_url_path": "test"},
    )
    assert out.startswith("План ")
    assert "[1] → [1, 2]" in out
    pid = out.split()[1].rstrip(":")
    body = PLANS.peek(pid).requests[0][2]["Ads"][0]["ResponsiveAd"]
    assert body["CalloutSetting"]["AdExtensions"] == [
        {"AdExtensionId": 1, "Operation": "SET"},
        {"AdExtensionId": 2, "Operation": "SET"}]
    applied = await do_apply_write(_ctx(tmp_path), pid,
                                      acknowledge_warnings=True)
    assert "статус applied" in applied


async def test_ads_ext_remove_merges(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/ads").mock(
        return_value=_ok({"Ads": [_resp_ad(ext=(1, 2))]}))
    respx_mock.post(f"{BASE}/adextensions").mock(
        return_value=_ok({"AdExtensions": [{"Id": 1}]}))
    out = await do_plan_write(
        _ctx(tmp_path), "ads_update",
        {"account": "t", "ad_ids": [11], "ad_extension_ids": [1],
         "extension_mode": "remove", "display_url_path": "test"},
    )
    assert out.startswith("План ")
    assert "[1, 2] → [2]" in out


async def test_ads_ext_refs_checked(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/ads").mock(
        return_value=_ok({"Ads": [_resp_ad()]}))
    respx_mock.post(f"{BASE}/adextensions").mock(
        return_value=_ok({"AdExtensions": []}))
    out = await do_plan_write(
        _ctx(tmp_path), "ads_update",
        {"account": "t", "ad_ids": [11], "ad_extension_ids": [999],
         "extension_mode": "add", "display_url_path": "test"},
    )
    assert out.startswith("Ошибка подготовки")
    assert "уточнения не найдены" in out


def _group(respx_mock, regions=(213,), restricted=(), campaign=2):
    respx_mock.post(f"{BASE}/adgroups").mock(return_value=_ok({"AdGroups": [{
        "Id": 7, "Name": "G", "CampaignId": campaign,
        "RegionIds": list(regions), "RestrictedRegionIds": list(restricted)}]}))


async def test_regions_add_by_name(respx_mock, tmp_path):
    _group(respx_mock)
    out = await do_plan_write(
        _ctx(tmp_path), "adgroups_update",
        {"account": "t",
         "groups": [{"id": 7, "regions": ["Санкт-Петербург"],
                     "regions_mode": "add"}]},
    )
    assert out.startswith("План ")
    assert "Санкт-Петербург" in out
    body = PLANS.peek(out.split()[1].rstrip(":")).requests[0][2]["AdGroups"]
    assert body[0]["RegionIds"] == [213, 216]


async def test_regions_add_preview_counts(respx_mock, tmp_path):
    _group(respx_mock, regions=(213,))
    out = await do_plan_write(
        _ctx(tmp_path), "adgroups_update",
        {"account": "t",
         "groups": [{"id": 7, "regions": ["Москва", "Санкт-Петербург"],
                     "regions_mode": "add"}]},
    )
    assert out.startswith("План ")
    assert "было 1, добавляется 2, дублей 1, станет 2" in out


async def test_regions_unknown_name_blocked(respx_mock, tmp_path):
    _group(respx_mock)
    out = await do_plan_write(
        _ctx(tmp_path), "adgroups_update",
        {"account": "t",
         "groups": [{"id": 7, "regions": ["Атлантида"],
                     "regions_mode": "add"}]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "не найден" in out


async def test_regions_only_minus_blocked(respx_mock, tmp_path):
    _group(respx_mock)
    out = await do_plan_write(
        _ctx(tmp_path), "adgroups_update",
        {"account": "t",
         "groups": [{"id": 7, "regions": ["-Черноголовка"],
                     "regions_mode": "replace"}]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "только минус-регионы" in out


async def test_regions_remove_warns_adjustment(respx_mock, tmp_path):
    _group(respx_mock, regions=(213, 216))
    respx_mock.post(f"{BASE}/bidmodifiers").mock(return_value=_ok({
        "BidModifiers": [{"Id": 5, "CampaignId": 2,
                          "RegionalAdjustment": {"RegionId": 216,
                                                 "BidModifier": 120}}]}))
    out = await do_plan_write(
        _ctx(tmp_path), "adgroups_update",
        {"account": "t",
         "groups": [{"id": 7, "regions": ["Санкт-Петербург"],
                     "regions_mode": "remove"}]},
    )
    assert out.startswith("План ")
    assert "корректировки 5:216" in out
    assert "Санкт-Петербург" in out
    assert "acknowledge_warnings=true" in out


async def test_regions_verify_split_sets(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/adgroups").mock(return_value=_ok({"AdGroups": [{
        "Id": 7, "RegionIds": [213, -219], "RestrictedRegionIds": []}]}))
    plan = SimpleNamespace(
        params={"groups": [{"id": 7, "regions": ["-Черноголовка"],
                            "regions_mode": "add"}]},
        requests=[("adgroups", "update",
                   {"AdGroups": [{"Id": 7, "RegionIds": [213, -219]}]}, "v5")],
    )
    result = await _verify_adgroups_updated(_ctx(tmp_path), _entry(), plan)
    assert result["ok"] is True


async def test_regions_verify_signed_echo(respx_mock, tmp_path):
    """Живой кейс: API отдаёт знаковый массив в RegionIds как есть."""
    respx_mock.post(f"{BASE}/adgroups").mock(return_value=_ok({"AdGroups": [{
        "Id": 7, "RegionIds": [-98546, 2, 213]}]}))
    plan = SimpleNamespace(
        params={"groups": [{"id": 7,
                            "regions": ["Санкт-Петербург", "-Петергоф"],
                            "regions_mode": "add"}]},
        requests=[("adgroups", "update",
                   {"AdGroups": [{"Id": 7,
                                  "RegionIds": [-98546, 2, 213]}]}, "v5")],
    )
    result = await _verify_adgroups_updated(_ctx(tmp_path), _entry(), plan)
    assert result["ok"] is True
