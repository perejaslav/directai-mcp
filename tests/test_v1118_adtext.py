"""v1.1.18: валидация текстов (п.1), предупреждение о конвертации (п.2),
сырой коэффициент только в файлах (п.3), правило истёкших планов."""

import httpx
import pytest

import directai_mcp.catalog.ads as _a  # noqa: F401 (реестр)
import directai_mcp.catalog.extensions as _e  # noqa: F401 (реестр)
from directai_mcp.catalog.bids import _pct, _raw_modifier
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety import adtext
from directai_mcp.server import INSTRUCTIONS, PLANS, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"


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


def _guard(respx_mock, campaign=None):
    campaign = campaign or {"Id": 2, "Name": "[TEST DirectAI] Шаг 4"}
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=httpx.Response(
            200, json={"result": {"AdGroups": [{"Id": 5, "CampaignId": 2}]}}
        )
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=httpx.Response(200, json={"result": {"Campaigns": [campaign]}})
    )


# --- п.1: символы и длины ---

def test_middle_dot_blocked_with_field_and_code():
    err = adtext.check_title2("title2", "Шаг 4 · Тест")
    assert err is not None
    assert "title2" in err and "·" in err and "U+00B7" in err


def test_em_dash_passes():
    assert adtext.check_title("title", "Эмаль и грунт — тест") is None


def test_lengths():
    ok56 = ("ab " * 19).strip()  # 56 знаков, слова короткие
    assert len(ok56) == 56
    assert adtext.check_title("title", ok56) is None
    assert "> 56" in (adtext.check_title("title", ok56 + "c") or "")
    assert adtext.check_title2("title2", "x y" * 10) is None
    assert "> 30" in (adtext.check_title2("title2", "x y" * 10 + "zz") or "")
    assert adtext.check_text("text", "x y" * 27) is None
    assert "> 81" in (adtext.check_text("text", "x y" * 27 + "zz") or "")
    assert "22" in (adtext.check_title("title", "x" * 23) or "")
    assert "23" in (adtext.check_text("text", "x" * 24) or "")


def test_narrow_allowance():
    base = ("ab " * 10).strip() + "c"  # 30 не-«узких», слова короткие
    assert adtext.check_title2("title2", base + "!" * 15) is None
    assert "> 15" in (adtext.check_title2("title2", base + "!" * 16) or "")


def test_display_rules():
    assert adtext.check_display("display_url_path", "test-text/20%") is None
    for bad, frag in (
        ("a b", "U+0020"),
        ("a_b", "U+005F"),
        ("a--b", "--"),
        ("a//b", "//"),
        ("x" * 21, "> 20"),
    ):
        err = adtext.check_display("display_url_path", bad)
        assert err is not None and frag in err, bad


def test_sitelink_callout_limits():
    assert adtext.check_sitelink("t" * 30, "https://example.com", "d" * 60) == []
    assert any("> 30" in e for e in adtext.check_sitelink("t" * 31, None, None))
    assert any("> 60" in e for e in adtext.check_sitelink("t", None, "d" * 61))
    assert any("протокол" in e for e in adtext.check_sitelink("t", "example.com", None))
    assert adtext.check_callout("c" * 25) is None
    assert "> 25" in (adtext.check_callout("c" * 26) or "")


def test_foreign_script_blocked():
    err = adtext.check_text("text", "test ελληνικά")
    assert err is not None and "U+03B5" in err


async def test_create_middle_dot_prepare_error_no_add(respx_mock, tmp_path):
    _guard(respx_mock)
    ads_route = respx_mock.post(f"{BASE}/ads").mock(
        return_value=httpx.Response(200, json={"result": {"AddResults": []}})
    )
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "TEXT_AD",
         "text_ads": [{"title": "Эмаль тест", "title2": "Шаг 4 · Тест",
                       "text": "Проверка", "href": "https://example.com",
                       "display_url_path": "test"}]},
    )
    assert out.startswith("Ошибка подготовки:")
    assert "title2" in out and "U+00B7" in out
    assert ads_route.call_count == 0


async def test_extensions_prepare_error(respx_mock, tmp_path):
    _guard(respx_mock)
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "extensions_create",
        {"account": "t", "callouts": ["норм", "x" * 26]},
    )
    assert out.startswith("Ошибка подготовки:")
    assert "> 25" in out


# --- п.2: предупреждение о конвертации ---

async def test_unified_preview_warns_and_glues(respx_mock, tmp_path):
    _guard(respx_mock, {"Id": 2, "Name": "[TEST DirectAI] ЕПК",
                        "Type": "UNIFIED_CAMPAIGN"})
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "TEXT_AD",
         "text_ads": [{"title": "Эмаль тест", "title2": "Второй",
                       "text": "Проверка", "href": "https://example.com",
                       "display_url_path": "test"}]},
    )
    assert "будет сконвертировано в RESPONSIVE_AD" in out
    assert "«Эмаль тест. Второй»" in out


async def test_text_campaign_warns_live_proven(respx_mock, tmp_path):
    _guard(respx_mock, {"Id": 2, "Name": "[TEST DirectAI] Текст",
                        "Type": "TEXT_CAMPAIGN"})
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "ads_create",
        {"account": "t", "adgroup_id": 5, "ad_type": "TEXT_AD",
         "text_ads": [{"title": "Эмаль тест",
                       "text": "Проверка", "href": "https://example.com",
                       "display_url_path": "test"}]},
    )
    assert "План " in out
    assert "API конвертирует TEXT_AD в RESPONSIVE_AD" in out
    assert "(подтверждено живьём, 10251)" in out
    assert "итоговый заголовок «Эмаль тест»" in out


# --- п.3: сырой коэффициент ---

def test_pct_without_raw():
    assert _pct(130) == "+30%"
    assert _pct(80) == "-20%"
    assert _pct(0) == "-100% (показы отключены)"
    assert _pct(None) == "—"


def test_raw_modifier_from_any_block():
    assert _raw_modifier({"SmartTvAdjustment": {"BidModifier": 0}}) == 0
    assert _raw_modifier(
        {"DemographicsAdjustment": {"BidModifier": 130}}) == 130
    assert _raw_modifier({}) is None


async def test_file_output_has_raw_column(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/bidmodifiers").mock(
        return_value=httpx.Response(200, json={"result": {"BidModifiers": [
            {"Id": 1, "CampaignId": 7, "AdGroupId": None, "Level": "CAMPAIGN",
             "Type": "MOBILE_ADJUSTMENT",
             "MobileAdjustment": {"BidModifier": 80}},
        ]}}))
    respx_mock.post(f"{BASE}/dictionaries").mock(
        return_value=httpx.Response(200, json={"result": {"GeoRegions": []}}))
    ctx = _ctx(tmp_path)
    inline = await ACTIONS["bid_modifiers_get"].run(
        ctx, ACTIONS["bid_modifiers_get"].params(account="t", campaign_ids=[7]))
    assert "BidModifier=-20%" in inline
    assert "BidModifier|" not in inline and "| 80 |" not in inline
    filed = await ACTIONS["bid_modifiers_get"].run(
        ctx, ACTIONS["bid_modifiers_get"].params(
            account="t", campaign_ids=[7], output="file", format="csv"))
    assert "BidModifier" in filed


# --- правило истёкших планов ---

def test_instructions_require_fresh_consent():
    assert "новое превью" in INSTRUCTIONS
    assert "по старому подтверждению запрещён" in INSTRUCTIONS
