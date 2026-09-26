"""v1.1.9: вычисляемые колонки, топ-N, дубли только при ≥2 целях, флаг CTR."""

import httpx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import PlacementsParams
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"

CHUNK = (
    "Placement\tImpressions\tClicks\tCost\tConversions_9_AUTO\t"
    "CostPerConversion_9_AUTO\tRevenue_9_AUTO\n"
    "site-a\t393\t85\t5500.00\t3\t1833.33\t0.00\n"
    "site-b\t100\t19\t3000.00\t0\t--\t0.00\n"
    "site-c\t1000\t25\t1100.00\t1\t1100.00\t0.00\n"
    "site-d\t1000\t1\t2.675\t0\t--\t0.00\n"
)
AGG = ("Placement\tImpressions\tClicks\tCost\tConversions\n"
       "all\t2493\t130\t9602.68\t4\n")
RECONC = ("CampaignId\tImpressions\tClicks\tCost\n"
          "7\t2493\t130\t9602.68\n")


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def test_placements_params_registered():
    assert ACTIONS["stats_placements"].params is PlacementsParams
    assert PlacementsParams().anomaly_min_clicks == 20
    assert PlacementsParams().anomaly_min_ctr == 5.0


async def test_derived_columns_order_and_values(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(CHUNK), _tsv(AGG), _tsv(RECONC)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_placements"]
    out = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                       goals=["9"], attribution=["AUTO"]))
    # v1.1.26: нулевая Revenue_9_AUTO выведена из колонок.
    assert "| # | Placement | Impressions | Clicks | CTR | Cost | CostShare | " \
        "CPC | Conversions | CPA | CR | Conversions_9_AUTO | " \
        "CostPerConversion_9_AUTO |" in out
    assert "| 1 | site-a | 393 | 85 | 21.63% | 5 500.00 | 57.28% | 64.71 | " \
        "3 | 1833.33 | 3.53% |" in out
    assert "| 4 | site-d | 1000 | 1 | 0.10% | 2.68 | 0.03% | 2.68 | " \
        "0 | — | — |" in out
    # Итоги из агрегата, без двойного счёта вычисленной колонки.
    assert "Конверсии: 4;" in out
    assert "Расход: 9 602.68 ₽" in out
    assert "CR: 3.08%" in out
    assert "Сверка с итогом кампании: сходится (2 493 / 130 / 9 602.68 ₽)." in out
    assert "Сверка с итогом кампании: сходится (2 493 / 130 / 9 602.68 ₽)." in out


async def test_top_line_with_limit_only(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[_tsv(CHUNK), _tsv(AGG), _tsv(RECONC),
                                            _tsv(CHUNK), _tsv(AGG), _tsv(RECONC)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_placements"]
    limited = await act.run(ctx, act.params(
        account="agency-login", campaign_ids=[7], goals=["9"],
        attribution=["AUTO"], limit=2))
    assert "Итого топ-2: Показы: 493; Клики: 104; Расход: 8 500.00 ₽; " \
        "Доля: 88.52%; Конверсии: 3." in limited
    ctx2 = _ctx(tmp_path)
    full = await act.run(ctx2, act.params(
        account="agency-login", campaign_ids=[7], goals=["9"],
        attribution=["AUTO"]))
    assert "Итого топ-" not in full


async def test_summary_without_share_and_top(respx_mock, tmp_path):
    body = "Impressions\tClicks\tCost\n100\t5\t1100.00\n60\t3\t500.00\n"
    respx_mock.post(RURL).mock(return_value=_tsv(body))
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_summary"]
    out = await act.run(ctx, act.params(account="agency-login",
                                       with_conversions=False, limit=1))
    assert "CostShare" not in out
    assert "Итого топ-" not in out
    assert "| 5.00% |" in out  # CTR есть


async def test_duplicates_note_only_with_two_goals(respx_mock, tmp_path):
    one = "Placement\tImpressions\tClicks\tCost\tConversions_9_AUTO\n" \
        "site-a\t10\t1\t100.00\t1\n"
    two = "Placement\tImpressions\tClicks\tCost\tConversions_9_AUTO\t" \
        "Conversions_10_AUTO\nsite-a\t10\t1\t100.00\t1\t2\n"
    agg = "Placement\tImpressions\tClicks\tCost\nsite-a\t10\t1\t100.00\n"
    respx_mock.post(RURL).mock(side_effect=[_tsv(one), _tsv(agg),
                                            _tsv(two), _tsv(agg)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_placements"]
    single = await act.run(ctx, act.params(
        account="agency-login", goals=["9"], attribution=["AUTO"]))
    assert "дубли визитов" not in single
    # v1.1.27: без данных Revenue строка ценности не выдумывается.
    assert "Ценность цели" not in single
    ctx2 = _ctx(tmp_path)
    multi = await act.run(ctx2, act.params(
        account="agency-login", goals=["9", "10"], attribution=["AUTO"]))
    assert "Суммы по целям: возможны дубли визитов." in multi


async def test_duplicates_note_needs_two_goals_with_data(respx_mock, tmp_path):
    """11 целей в запросе, данные в одной — пометки нет (кейс теста 7)."""
    chunk = "Placement\tImpressions\tClicks\tCost\tConversions_9_AUTO\t" \
        "Conversions_10_AUTO\tConversions_11_AUTO\n" \
        "site-a\t10\t1\t100.00\t1\t0\t0\n"
    agg = "Placement\tImpressions\tClicks\tCost\nsite-a\t10\t1\t100.00\n"
    respx_mock.post(RURL).mock(side_effect=[_tsv(chunk), _tsv(agg)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_placements"].run(
        ctx, ACTIONS["stats_placements"].params(
            account="agency-login", goals=["9", "10", "11"],
            attribution=["AUTO"]))
    # v1.1.27: без данных Revenue строк ценности нет (ноль не выдумываем).
    assert "Ценность цели" not in out
    # v1.1.9: пометка у строк ценности — только при данных ≥2 целей.
    assert "Суммы по целям: возможны дубли визитов." not in out
    # v1.1.19: шапка и имена колонок — по числу целей в запросе (3 ≥ 2).
    assert "В строках возможны дубли визитов" in out
    assert "Conversions (сумма по целям)" in out


async def test_anomaly_flag_default_thresholds(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(CHUNK), _tsv(AGG), _tsv(RECONC)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_placements"].run(
        ctx, ACTIONS["stats_placements"].params(
            account="agency-login", campaign_ids=[7], goals=["9"],
            attribution=["AUTO"]))
    assert out.count("⚠ аномальный CTR") == 1
    assert "| site-a | 393 | 85 | 21.63% | 5 500.00 | 57.28% | 64.71 | 3 | " \
        "1833.33 | 3.53% |" in out.split("⚠ аномальный CTR")[0].splitlines()[-1]
    assert " | Flag |" in out


async def test_anomaly_flag_custom_thresholds_hide_column(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(CHUNK), _tsv(AGG), _tsv(RECONC)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_placements"].run(
        ctx, ACTIONS["stats_placements"].params(
            account="agency-login", campaign_ids=[7], goals=["9"],
            attribution=["AUTO"], anomaly_min_clicks=100,
            anomaly_min_ctr=50.0))
    assert "аномальный CTR" not in out
    assert "Flag" not in out


async def test_custom_partial_metrics_prune(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[
        _tsv("Device\tClicks\tCost\nDESKTOP\t10\t100.00\nMOBILE\t5\t50.00\n"),
        _tsv("Clicks\tCost\n15\t150.00\n"),
    ])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_custom"].run(
        ctx, ACTIONS["stats_custom"].params(
            account="agency-login", report_type="CUSTOM_REPORT",
            field_names=["Clicks", "Cost"], group_by=["Device"],
            with_conversions=False))
    assert "CPC" in out  # есть входы клики+расход
    assert "| CTR |" not in out  # нет показов — столбец скрыт (в итоге CTR: — штатно)
    assert "CostShare" in out  # разрез — доля есть


async def test_csv_plain_numbers_without_percent(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(CHUNK), _tsv(AGG), _tsv(RECONC)])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_placements"].run(
        ctx, ACTIONS["stats_placements"].params(
            account="agency-login", campaign_ids=[7], goals=["9"],
            attribution=["AUTO"], output="file", format="csv"))
    path = out.split("Полный результат: ")[1].split(" (")[0]
    from pathlib import Path as _P

    lines = _P(path).read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split(";")[4] == "CTR"
    assert lines[1].split(";")[4] == "21,63"  # число, без % для Excel
    assert "21.63%" in out  # в inline/MD сводке знак % есть
