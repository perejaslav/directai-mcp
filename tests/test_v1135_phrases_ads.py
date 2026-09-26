"""v1.1.35: фразы (лимиты, дедуп, минусы, ставка) и объявления (счётчики,
DisplayUrlPath, перемодерация, read-back)."""

from types import SimpleNamespace

import httpx
import pytest

import directai_mcp.catalog.ads as _ad  # noqa: F401 (реестр)
import directai_mcp.catalog.keywords as _kw  # noqa: F401 (реестр)
from directai_mcp.catalog.ads import _verify_ads_created, _verify_ads_update
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"

MANUAL = {"TextCampaign": {"BiddingStrategy": {
    "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
    "Network": {"BiddingStrategyType": "SERVING_OFF"}}}}
AUTO = {"TextCampaign": {"BiddingStrategy": {
    "Search": {"BiddingStrategyType": "MAXIMUM_CONVERSIONS"},
    "Network": {"BiddingStrategyType": "SERVING_OFF"}}}}


def _ctx(tmp_path, guard=False):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=guard,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _entry():
    return AccountEntry(alias="t", login="test-login")


def _ok(result):
    return httpx.Response(200, json={"result": result})


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _kw_mocks(respx_mock, strategy=None, existing=None, negatives=None):
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_ok({"AdGroups": [{"Id": 10, "CampaignId": 2}]})
    )
    camp = {"Id": 2, **(strategy or MANUAL)}
    if negatives is not None:
        camp["NegativeKeywords"] = {"Items": negatives}
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_ok({"Campaigns": [camp]})
    )
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=_ok({"Keywords": existing or []})
    )


def _kw_plan(ctx, keywords):
    return do_plan_write(
        ctx, "keywords_add",
        {"account": "t", "adgroup_id": 10, "keywords": keywords},
    )


async def test_kw_too_many_words_blocked_no_api(tmp_path):
    out = await _kw_plan(_ctx(tmp_path), [{"text": "а б в г д е ж з"}])
    assert out.startswith("Ошибка подготовки")
    assert "лимит 7" in out
    assert len(PLANS) == 0


async def test_kw_long_word_blocked(tmp_path):
    out = await _kw_plan(_ctx(tmp_path), [{"text": "к" * 36}])
    assert out.startswith("Ошибка подготовки")
    assert "длиннее 35" in out


async def test_kw_dedup_warn_and_single_request(respx_mock, tmp_path):
    _kw_mocks(respx_mock, existing=[{"Id": 1, "Keyword": "Грунт"}])
    out = await _kw_plan(_ctx(tmp_path), [{"text": "  грунт "}, {"text": "краска"}])
    assert out.startswith("План ")
    assert "дубли пропущены" in out
    pid = out.split()[1].rstrip(":")
    body = PLANS.peek(pid).requests[0][2]["Keywords"]
    assert [k["Keyword"] for k in body] == ["краска"]


async def test_kw_all_dupes_error(respx_mock, tmp_path):
    _kw_mocks(respx_mock, existing=[{"Id": 1, "Keyword": "грунт"}])
    out = await _kw_plan(_ctx(tmp_path), [{"text": "ГРУНТ"}])
    assert out.startswith("Ошибка подготовки")
    assert "все фразы — дубли" in out


async def test_kw_minus_intersection_warns(respx_mock, tmp_path):
    _kw_mocks(respx_mock, negatives=["грунт"])
    out = await _kw_plan(_ctx(tmp_path), [{"text": "грунт эмаль"}])
    assert out.startswith("План ")
    assert "заблокирует показ" in out


async def test_kw_bid_below_min_blocked(respx_mock, tmp_path):
    _kw_mocks(respx_mock)
    out = await _kw_plan(_ctx(tmp_path), [{"text": "грунт", "bid": 0.2}])
    assert out.startswith("Ошибка подготовки")
    assert "ниже минимума 0.30" in out


async def test_kw_bid_auto_strategy_blocked(respx_mock, tmp_path):
    _kw_mocks(respx_mock, strategy=AUTO)
    out = await _kw_plan(_ctx(tmp_path), [{"text": "грунт", "bid": 5.0}])
    assert out.startswith("Ошибка подготовки")
    assert "только для ручной стратегии" in out


async def test_ad_title_over_by_one_blocked_no_api(tmp_path, respx_mock):
    route = respx_mock.post(f"{BASE}/ads").mock(
        return_value=_ok({"AddResults": []}))
    out = await do_plan_write(
        _ctx(tmp_path), "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "TEXT_AD",
         "text_ads": [{"title": "Э" * 57, "text": "t",
                       "href": "https://example.com",
                       "display_url_path": "test"}]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "> 56" in out
    assert route.call_count == 0


async def test_ad_create_preview_counters(tmp_path):
    out = await do_plan_write(
        _ctx(tmp_path), "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "RESPONSIVE_AD",
         "responsive_ads": [{"titles": ["Тестовый заголовок"], "texts": ["t"],
                             "href": "https://example.com",
                             "display_url_path": "test"}]},
    )
    assert out.startswith("План ")
    assert "[titles 18/56, texts 1/81]" in out
    assert "4/20" in out


def _test_ad(respx_mock, display="test"):
    text_ad = {"Title": "Эмаль тест", "Text": "t",
               "Href": "https://example.com"}
    if display is not None:
        text_ad["DisplayUrlPath"] = display
    respx_mock.post(f"{BASE}/ads").mock(
        return_value=_ok({"Ads": [{"Id": 12, "Type": "TEXT_AD",
                                   "TextAd": text_ad}]})
    )


async def test_ad_update_remod_warning(respx_mock, tmp_path):
    _test_ad(respx_mock)
    out = await do_plan_write(
        _ctx(tmp_path, guard=True), "ads_update",
        {"account": "t", "ad_ids": [12], "text": "Новый текст",
         "display_url_path": "test"},
    )
    assert out.startswith("План ")
    assert "перемодерацию" in out
    assert "acknowledge_warnings=true" in out


async def test_ad_update_display_required_each_op(respx_mock, tmp_path):
    """v1.1.37: поле не передано при существующей ссылке → отказ до API."""
    _test_ad(respx_mock)
    out = await do_plan_write(
        _ctx(tmp_path, guard=True), "ads_update",
        {"account": "t", "ad_ids": [12], "text": "Новый текст"},
    )
    assert out.startswith("Ошибка подготовки")
    assert "DisplayUrlPath обязателен" in out
    assert "в той же операции" in out


async def test_ad_update_no_display_same_op_block(respx_mock, tmp_path):
    _test_ad(respx_mock, display=None)
    out = await do_plan_write(
        _ctx(tmp_path, guard=True), "ads_update",
        {"account": "t", "ad_ids": [12], "text": "Новый текст"},
    )
    assert out.startswith("Ошибка подготовки")
    assert "укажите display_url_path в той же операции" in out


async def test_verify_create_display_mismatch(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ok({"Ads": [
        {"Id": 99, "Type": "RESPONSIVE_AD",
         "ResponsiveAd": {"DisplayUrlPath": "other"}}]}))
    plan = SimpleNamespace(
        params={"account": "t", "adgroup_id": 5, "ad_type": "RESPONSIVE_AD",
                "responsive_ads": [{"titles": ["T"], "texts": ["t"],
                                    "href": "https://example.com",
                                    "display_url_path": "test"}]},
        requests=[("ads", "add", {"Ads": [{"ResponsiveAd": {}}]}, "v501")],
        last_response={"response": {"AddResults": [{"Id": 99}]}},
    )
    result = await _verify_ads_created(_ctx(tmp_path), _entry(), plan)
    assert result["ok"] is False
    assert "DisplayUrlPath" in result["note"]


async def test_verify_update_empty_display(respx_mock, tmp_path):
    _test_ad(respx_mock, display=None)
    plan = SimpleNamespace(
        params={"ad_ids": [12], "display_url_path": "test"},
        requests=[("ads", "update", {"Ads": [{"Id": 12}]}, "v5")],
    )
    result = await _verify_ads_update(_ctx(tmp_path), _entry(), plan)
    assert result["ok"] is False
    assert "пустая отображаемая ссылка" in result["note"]


# --- v1.1.35.1: сумма пары заголовков, полный read-back, варнинги ---

async def test_create_title_sum_over_blocked_no_api(tmp_path, respx_mock):
    route = respx_mock.post(f"{BASE}/ads").mock(
        return_value=_ok({"AddResults": []}))
    out = await do_plan_write(
        _ctx(tmp_path), "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "TEXT_AD",
         "text_ads": [{"title": "Э" * 56, "title2": "Ю" * 30, "text": "t",
                       "href": "https://example.com",
                       "display_url_path": "test"}]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "З1 56 + З2 30 + 2 = 88 / 56, лишних 32" in out
    assert route.call_count == 0


async def test_create_responsive_pair_sum_over_blocked(tmp_path):
    out = await do_plan_write(
        _ctx(tmp_path), "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "RESPONSIVE_AD",
         "responsive_ads": [{"titles": ["Э" * 40, "Ю" * 20], "texts": ["t"],
                             "href": "https://example.com",
                             "display_url_path": "test"}]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "З1 40 + З2 20 + 2 = 62 / 56, лишних 6" in out


async def test_update_responsive_pair_sum_over_blocked(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ok({"Ads": [{
        "Id": 11, "Type": "RESPONSIVE_AD",
        "ResponsiveAd": {"Titles": [{"Title": "Э" * 40}],
                         "Texts": [{"Text": "t"}],
                         "Href": "https://example.com",
                         "DisplayUrlPath": "test"}}]}))
    out = await do_plan_write(
        _ctx(tmp_path), "ads_update",
        {"account": "t", "ad_ids": [11], "titles": ["Э" * 40, "Ю" * 20],
         "display_url_path": "test"},
    )
    assert out.startswith("Ошибка подготовки")
    assert "лишних 6" in out


def _etalon_create_plan(display="test-limit-20-chars1"):
    return SimpleNamespace(
        params={"account": "t", "adgroup_id": 5, "ad_type": "TEXT_AD",
                "text_ads": [{"title": "Э" * 56, "title2": "Ю" * 30,
                              "text": "t", "href": "https://example.com",
                              "display_url_path": display}]},
        requests=[("ads", "add", {"Ads": [{}]}, "v5")],
        last_response={"response": {"AddResults": [{"Id": 99}]}},
    )


async def test_verify_created_dropped_title2_fails(respx_mock, tmp_path):
    """Эталон 9000000000000000004: Title2 отброшен → ok=False."""
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ok({"Ads": [{
        "Id": 99, "Type": "RESPONSIVE_AD",
        "ResponsiveAd": {"Titles": [{"Title": "Э" * 56}],
                         "Texts": [{"Text": "t"}],
                         "Href": "https://example.com",
                         "DisplayUrlPath": "test-limit-20-chars1"}}]}))
    result = await _verify_ads_created(
        _ctx(tmp_path), _entry(), _etalon_create_plan())
    assert result["ok"] is False
    assert "Title2" in result["note"]


async def test_verify_created_glued_title2_ok(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ok({"Ads": [{
        "Id": 99, "Type": "RESPONSIVE_AD",
        "ResponsiveAd": {
            "Titles": [{"Title": "Э" * 20 + ". " + "Ю" * 30}],
            "Texts": [{"Text": "t"}],
            "Href": "https://example.com",
            "DisplayUrlPath": "test-limit-20-chars1"}}]}))
    plan = SimpleNamespace(
        params={"account": "t", "adgroup_id": 5, "ad_type": "TEXT_AD",
                "text_ads": [{"title": "Э" * 20, "title2": "Ю" * 30,
                              "text": "t", "href": "https://example.com",
                              "display_url_path": "test-limit-20-chars1"}]},
        requests=[("ads", "add", {"Ads": [{}]}, "v5")],
        last_response={"response": {"AddResults": [{"Id": 99}]}},
    )
    result = await _verify_ads_created(_ctx(tmp_path), _entry(), plan)
    assert result["ok"] is True


async def test_apply_shows_warning_codes(respx_mock, tmp_path):
    respx_mock.post(
        "https://api.direct.yandex.com/json/v501/ads").mock(
        return_value=_ok({"AddResults": [{
            "Id": 91,
            "Warnings": [{"Code": 10251, "Message": "Создание текстовых "
                                                   "баннеров закрыто"}]}]}))
    respx_mock.post(f"{BASE}/ads").mock(
        return_value=_ok({"Ads": [{
            "Id": 91, "Type": "RESPONSIVE_AD",
            "ResponsiveAd": {"Titles": [{"Title": "Эмаль тест"}],
                             "Texts": [{"Text": "t"}],
                             "Href": "https://example.com",
                             "DisplayUrlPath": "test"}}]}))
    out = await do_plan_write(
        _ctx(tmp_path), "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "RESPONSIVE_AD",
         "responsive_ads": [{"titles": ["Эмаль тест"], "texts": ["t"],
                             "href": "https://example.com",
                             "display_url_path": "test"}]},
    )
    assert out.startswith("План ")
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(_ctx(tmp_path), pid)
    assert "10251:" in applied
