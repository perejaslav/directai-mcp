"""v1.1.27: Slot — allowlist, подписи, итог из агрегата без измерений."""

import httpx
import pytest

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"

DETAIL = (
    "CampaignId\tSlot\tImpressions\tClicks\tCost\tConversions\n"
    "7\tPREMIUMBLOCK\t100\t10\t100.00\t2\n"
    "7\tSUGGEST\t50\t5\t50.00\t1\n"
    "7\tWEIRD_NEW\t10\t1\t10.00\t0\n"
)
AGG = "Impressions\tClicks\tCost\tConversions\n160\t16\t160.00\t3\n"
RECONC = "CampaignId\tImpressions\tClicks\tCost\n7\t160\t16\t160.00\n"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


DETAIL_GOALS = (
    "CampaignId\tSlot\tImpressions\tClicks\tCost\tConversions_9_AUTO\n"
    "7\tPREMIUMBLOCK\t100\t10\t100.00\t2\n"
    "7\tSUGGEST\t50\t5\t50.00\t1\n"
    "7\tWEIRD_NEW\t10\t1\t10.00\t0\n"
)
AGG_NODIMS = "Impressions\tClicks\tCost\tConversions\n160\t16\t159.00\t3\n"


async def test_stats_group_by_slot_labels_and_total(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(RURL).mock(
        side_effect=[_tsv(DETAIL_GOALS), _tsv(AGG_NODIMS)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_campaigns"].run(
        ctx, ACTIONS["stats_campaigns"].params(
            account="agency-login", campaign_ids=[7], goals=["9"],
            attribution=["AUTO"], group_by="slot"))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "Slot" in sent["FieldNames"]
    # Агрегат для итога — без измерений (v1.1.27).
    agg_sent = _json.loads(route.calls[1].request.content)["params"]
    assert "Slot" not in agg_sent["FieldNames"]
    # Подписи в inline, неизвестное — как есть.
    assert "Спецразмещение" in out
    assert "Подсказки" in out
    assert "WEIRD_NEW" in out
    assert "PREMIUMBLOCK" not in out
    # Итог из агрегата (159.00), а не сумма строк (160.00).
    assert "Расход: 159.00 ₽" in out


async def test_custom_slot_allowlist_and_raw_csv(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(RURL).mock(
        side_effect=[_tsv(DETAIL), _tsv(AGG), _tsv(DETAIL), _tsv(AGG)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_custom"].run(
        ctx, ACTIONS["stats_custom"].params(
            account="agency-login", report_type="CUSTOM_REPORT",
            field_names=["Impressions", "Clicks", "Cost"],
            group_by=["Slot"], with_conversions=False))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "Slot" in sent["FieldNames"]
    assert "Спецразмещение" in out
    ctx2 = _ctx(tmp_path)
    out2 = await ACTIONS["stats_custom"].run(
        ctx2, ACTIONS["stats_custom"].params(
            account="agency-login", report_type="CUSTOM_REPORT",
            field_names=["Impressions", "Clicks", "Cost"],
            group_by=["Slot"], with_conversions=False,
            output="file", format="csv"))
    path = out2.split("Полный результат: ")[1].split(" (")[0]
    from pathlib import Path as _Path

    body = _Path(path).read_text(encoding="utf-8-sig")
    assert "PREMIUMBLOCK" in body  # в CSV — сырые значения
    assert "Спецразмещение" not in body


async def test_custom_bad_dim_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="unknown dimensions"):
        await ACTIONS["stats_custom"].run(
            ctx, ACTIONS["stats_custom"].params(
                account="agency-login", report_type="CUSTOM_REPORT",
                field_names=["Clicks"], group_by=["Hour"]))


async def test_slot_cr_sum_suffix(respx_mock, tmp_path):
    detail = (
        "CampaignId\tSlot\tImpressions\tClicks\tCost\t"
        "Conversions_9_AUTO\tConversions_10_AUTO\n"
        "7\tPREMIUMBLOCK\t100\t10\t100.00\t1\t1\n")
    agg = "Impressions\tClicks\tCost\n100\t10\t100.00\n"
    reconc = "CampaignId\tImpressions\tClicks\tCost\n7\t100\t10\t100.00\n"
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(detail), _tsv(agg), _tsv(reconc)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_campaigns"].run(
        ctx, ACTIONS["stats_campaigns"].params(
            account="agency-login", campaign_ids=[7], goals=["9", "10"],
            attribution=["AUTO"], group_by="slot"))
    # п.5: CR с суффиксом суммы + пометка про дубли.
    assert "| CR (сумма по целям) |" in out
    assert "дубли визитов" in out
