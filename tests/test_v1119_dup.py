"""v1.1.19: дубли визитов в строках (sum/primary), CR в разрезах."""

import httpx
import pytest

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import (
    conv_mode,
    derived_names,
    row_goal_conversions,
)
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"

CHUNK = (
    "Placement\tImpressions\tClicks\tCost\t"
    "Conversions_1_AUTO\tConversions_2_AUTO\n"
    "site-a\t100\t10\t1100.00\t3\t2\n"
    "site-b\t50\t5\t550.00\t1\t0\n"
)
AGG = "Placement\tImpressions\tClicks\tCost\tConversions\nall\t150\t15\t1650.00\t5\n"
RECONC = "CampaignId\tImpressions\tClicks\tCost\n7\t150\t15\t1650.00\n"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def test_conv_mode_and_names():
    assert conv_mode(1, None) == "single"
    assert conv_mode(0, None) == "single"
    assert conv_mode(2, None) == "sum"
    assert conv_mode(3, "9") == "primary"
    assert conv_mode(1, "9") == "single"
    assert derived_names("single") == ("Conversions", "CPA", "CR")
    assert derived_names("primary") == ("Conversions", "CPA", "CR")
    assert derived_names("sum") == (
        "Conversions (сумма по целям)",
        "CPA (сумма по целям)",
        "CR (сумма по целям)",
    )


def test_row_goal_conversions():
    row = {"Conversions_1_AUTO": "3", "Conversions_2_AUTO": "2"}
    assert row_goal_conversions(row, "1") == 3
    assert row_goal_conversions(row, "2") == 2
    assert row_goal_conversions({}, "1") is None
    localized = {"Conversions_Имя (1)_AUTO": "4", "Conversions_Др (2)_AUTO": "1"}
    assert row_goal_conversions(localized, "1") == 4
    assert row_goal_conversions(localized, "2") == 1
    # CostPerConversion той же цели не суммируется (живой баг 26.09.2026).
    mixed = {"Conversions_Имя (1)_AUTO": "3",
             "CostPerConversion_Имя (1)_AUTO": "1940.81"}
    assert row_goal_conversions(mixed, "1") == 3


async def test_sum_mode_labels_and_dup_note(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(CHUNK), _tsv(AGG), _tsv(RECONC)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_placements"]
    out = await act.run(ctx, act.params(
        account="agency-login", campaign_ids=[7], goals=["1", "2"],
        attribution=["AUTO"]))
    assert "Conversions (сумма по целям)" in out
    assert "CPA (сумма по целям)" in out
    assert "CR (сумма по целям)" in out
    assert "В строках возможны дубли визитов" in out
    assert "уникальные конверсии — только в итоге" in out
    # site-a: сумма 5, CPA 200.00, CR 50.00%.
    assert "| 1 | site-a | 100 | 10 | 10.00% | 1 100.00 | 66.67% | 110.00 | " \
        "5 | 220.00 | 50.00% |" in out
    # Сумма строк 6 при итоге 5 — честно подписано, итог из агрегата.
    assert "Конверсии: 5;" in out


async def test_primary_goal_no_dup_sum_within_total(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(CHUNK), _tsv(AGG), _tsv(RECONC)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_placements"]
    out = await act.run(ctx, act.params(
        account="agency-login", campaign_ids=[7], goals=["1", "2"],
        attribution=["AUTO"], primary_goal="1"))
    assert "Строки — по цели 1." in out
    assert "сумма по целям" not in out
    # site-a: только цель 1 → 3, CPA 333.33, CR 30.00%.
    assert "| 1 | site-a | 100 | 10 | 10.00% | 1 100.00 | 66.67% | 110.00 | " \
        "3 | 366.67 | 30.00% |" in out
    # Сумма строк 4 не превышает итог 5.
    assert "Конверсии: 5;" in out


async def test_primary_goal_must_be_in_goals(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_placements"]
    with pytest.raises(ValueError, match="primary_goal 9 нет среди целей"):
        await act.run(ctx, act.params(
            account="agency-login", campaign_ids=[7], goals=["1", "2"],
            attribution=["AUTO"], primary_goal="9"))


async def test_custom_group_by_has_share_and_cr(respx_mock, tmp_path):
    chunk = ("Gender\tClicks\tCost\tConversions_1_AUTO\n"
             "GENDER_MALE\t10\t1100.00\t2\n")
    agg = "Clicks\tCost\tConversions\n10\t1100.00\t2\n"
    respx_mock.post(RURL).mock(side_effect=[_tsv(chunk), _tsv(agg)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_custom"].run(
        ctx, ACTIONS["stats_custom"].params(
            account="agency-login", report_type="CUSTOM_REPORT",
            field_names=["Clicks", "Cost", "Conversions"],
            group_by=["Gender"], goals=["1"], attribution=["AUTO"]))
    assert "CostShare" in out
    assert "| CR |" in out
    assert "20.00%" in out


async def test_single_goal_keeps_plain_names(respx_mock, tmp_path):
    chunk = ("Placement\tImpressions\tClicks\tCost\tConversions_9_AUTO\n"
             "site-a\t100\t10\t1100.00\t4\n")
    agg = "Placement\tImpressions\tClicks\tCost\tConversions\nall\t100\t10\t1100.00\t4\n"
    reconc = "CampaignId\tImpressions\tClicks\tCost\n7\t100\t10\t1100.00\n"
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(chunk), _tsv(agg), _tsv(reconc)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_placements"].run(
        ctx, ACTIONS["stats_placements"].params(
            account="agency-login", campaign_ids=[7], goals=["9"],
            attribution=["AUTO"]))
    assert "сумма по целям" not in out
    assert "дубли визитов" not in out
    assert "| 1 | site-a | 100 | 10 | 10.00% | 1 100.00 | 100.00% | 110.00 | " \
        "4 | 275.00 | 40.00% |" in out
