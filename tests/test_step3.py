"""Step 3 unit tests: helpers, extraction, cap (SPEC 9)."""

from directai_mcp.catalog.ads import NO_DISPLAY_URL, _extract, display_url
from directai_mcp.catalog.bids import BidModifiersGetParams, _detail, _pct
from directai_mcp.catalog.campaigns import ACTIVE_STATES
from directai_mcp.catalog.changes import ChangesCheckParams, _normalize_timestamp
from directai_mcp.catalog.common import chunk, finalize
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings


def test_chunk_campaign_limit():
    assert list(chunk([1, 2, 3], 10)) == [[1, 2, 3]]
    assert list(chunk(list(range(25)), 10)) == [
        list(range(10)),
        list(range(10, 20)),
        list(range(20, 25)),
    ]


def test_archive_excluded_by_default():
    assert "ARCHIVED" not in ACTIVE_STATES
    assert "CONVERTED" not in ACTIVE_STATES


def test_display_url_mark():
    assert NO_DISPLAY_URL == "НЕТ"
    assert display_url("") == "НЕТ"
    assert display_url(None) == "НЕТ"
    assert display_url("price-ot-100") == "price-ot-100"


def test_extract_text_ad_display_url():
    with_url = _extract(
        {"TextAd": {"Title": "T", "Text": "x", "DisplayUrlPath": "price-ot-100"}}
    )
    assert display_url(with_url["display"]) == "price-ot-100"
    without_url = _extract({"TextAd": {"Title": "T", "Text": "x"}})
    assert display_url(without_url["display"]) == "НЕТ"


def test_extract_responsive_ad():
    ad = {
        "ResponsiveAd": {
            "Titles": [{"Title": "A"}, {"Title": "B"}],
            "Texts": [{"Text": "t"}],
        }
    }
    info = _extract(ad)
    assert info["title"] == "A (2 вариантов)"
    assert info["text"] == "t (1 вариантов)"
    assert info["titles_all"] == "A | B"
    assert info["texts_all"] == "t"
    assert display_url(info["display"]) == "НЕТ"


def test_modifier_detail():
    item = {
        "Type": "REGIONAL_ADJUSTMENT",
        "RegionalAdjustment": {"RegionId": 213, "BidModifier": 120, "Enabled": "YES"},
    }
    assert (
        _detail(item)
        == "RegionalAdjustment: RegionId=? (213), BidModifier=+20%, "
        "Enabled=YES"
    )
    assert (
        _detail(item, {213: "Москва"})
        == "RegionalAdjustment: RegionId=Москва (213), BidModifier=+20%, "
        "Enabled=YES"
    )
    assert _detail({"Type": "X"}) == "—"


def test_pct_format():
    assert _pct(130) == "+30%"
    assert _pct(75) == "-25%"
    assert _pct(100) == "+0%"
    assert _pct(0) == "-100% (показы отключены)"
    assert _pct(None) == "—"


def test_changes_timestamp():
    assert _normalize_timestamp("2026-09-20") == "2026-09-20T00:00:00Z"
    assert _normalize_timestamp("2026-09-20 10:30") == "2026-09-20T10:30:00Z"
    assert _normalize_timestamp("2026-09-20T10:30:05Z") == "2026-09-20T10:30:05Z"
    try:
        _normalize_timestamp("20.09.2026")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_changes_params_validation():
    try:
        ChangesCheckParams(since="2026-09-20", fields=["Nope"])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_bid_modifiers_default_levels():
    assert BidModifiersGetParams().levels == ["CAMPAIGN", "AD_GROUP"]


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x", accounts={"a": AccountEntry(alias="a", login="l")}
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def test_finalize_cap_and_autosave(tmp_path):
    ctx = Ctx(
        settings=Settings(
            auth_login="x",
            accounts={"a": AccountEntry(alias="a", login="l")},
            reports_dir=tmp_path / "reports",
        ),
        token="t",
        data_dir=tmp_path,
    )
    rows = [{"Id": i, "Name": f"n{i}"} for i in range(250)]
    out = finalize(ctx, "ctx", "test_action", ["Id", "Name"], rows, 1000, None, [])
    assert "Скрыто строк: 50 из 250." in out
    assert "Показано 200 из 250 строк, полный отчёт:" in out
    saved = list((tmp_path / "reports").glob("test_action_all_*.csv"))
    assert len(saved) == 1
    assert len(saved[0].read_text(encoding="utf-8-sig").splitlines()) == 251


def test_finalize_no_file_when_fits(tmp_path):
    ctx = _ctx(tmp_path)
    rows = [{"Id": 1}]
    out = finalize(ctx, "ctx", "t", ["Id"], rows, None, None, [])
    assert "полный отчёт" not in out
