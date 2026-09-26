"""v1.1.22: stats_compare — A/B/Δ/Δ% от неокруглённых, п.п., агрегат для долей."""

import httpx
import pytest

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"

COLS = ("CampaignId\tCampaignName\tImpressions\tClicks\tCost\t"
        "BounceRate\tAvgImpressionPosition\tAvgClickPosition\tConversions\n")
ROW_A = "7\tCamp\t200000\t10000\t110000.00\t29.41\t3.61\t2.85\t170\n"
ROW_B = "7\tCamp\t200000\t11947\t158260.00\t35.15\t3.41\t2.70\t322\n"

DCOLS = ("CampaignId\tCampaignName\tDevice\tImpressions\tClicks\tCost\t"
         "BounceRate\tAvgImpressionPosition\tAvgClickPosition\tConversions\n")
DET_A = ("7\tCamp\tdesktop\t150000\t8000\t80000.00\t30.00\t3.50\t2.80\t100\n"
         "7\tCamp\tmobile\t50000\t2000\t20000.00\t25.00\t4.00\t3.00\t70\n")
DET_B = ("7\tCamp\tdesktop\t150000\t9000\t100000.00\t32.00\t3.40\t2.70\t150\n"
         "7\tCamp\tmobile\t50000\t2947\t46260.00\t40.00\t3.90\t2.90\t172\n")


def _ctx(tmp_path, extra_accounts=False):
    accounts = {"m": AccountEntry(alias="m", login="agency-login")}
    if extra_accounts:
        accounts["m2"] = AccountEntry(alias="m2", login="second-login")
    settings = Settings(
        auth_login="agency-login",
        accounts=accounts,
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def _base(account="agency-login", **kw):
    args = {
        "account": account, "campaign_ids": [7],
        "period_a_from": "2026-09-11", "period_a_to": "2026-09-17",
        "period_b_from": "2026-09-18", "period_b_to": "2026-09-24",
    }
    args.update(kw)
    return ACTIONS["stats_compare"].params(**args)


async def test_compare_totals_delta(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[
        _tsv(COLS + ROW_A), _tsv(COLS + ROW_A),
        _tsv(COLS + ROW_B), _tsv(COLS + ROW_B),
    ])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_compare"].run(ctx, _base(goals=["5"]))
    assert ("stats_compare: agency-login, A: 11.09.2026–17.09.2026, "
            "B: 18.09.2026–24.09.2026") in out
    assert "атрибуция: AUTO, целей: 1 (указаны явно)" in out
    assert "| Показы | 200 000 | 200 000 | 0 | 0.00% |" in out
    assert "| Клики | 10 000 | 11 947 | +1 947 | +19.47% |" in out
    assert "| CTR, % | 5.00 | 5.97 | +0.97 п.п. | +19.47% |" in out
    assert "| Расход, ₽ | 110 000.00 | 158 260.00 | +48 260.00 | +43.87% |" in out
    assert "| CPC, ₽ | 11.00 | 13.25 | +2.25 | +20.43% |" in out
    assert "| Конверсии (достижения) | 170 | 322 | +152 | +89.41% |" in out
    assert "| CR, % | 1.70 | 2.70 | +1.00 п.п. | +58.54% |" in out
    assert "| CPA, ₽ | 647.06 | 491.49 | -155.57 | -24.04% |" in out
    assert "| Отказы, % | 29.41 | 35.15 | +5.74 п.п. | +19.52% |" in out
    assert "| Ср. позиция показов | 3.61 | 3.41 | -0.20 | — |" in out
    assert "| Ср. позиция кликов | 2.85 | 2.70 | -0.15 | — |" in out
    # п.4: конверсии — число достижений, ценность — отдельно.
    assert "конверсии по условной ценности" not in out
    assert "условная (из настроек цели, не выручка)" in out


async def test_compare_request_shape(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(RURL).mock(side_effect=[
        _tsv(COLS + ROW_A), _tsv(COLS + ROW_A),
        _tsv(COLS + ROW_B), _tsv(COLS + ROW_B),
    ])
    ctx = _ctx(tmp_path)
    await ACTIONS["stats_compare"].run(ctx, _base(goals=["5"]))
    assert len(route.calls) == 4
    bodies = [_json.loads(c.request.content)["params"] for c in route.calls]
    # Единые фильтры/цели/атрибуция для обоих периодов.
    assert bodies[0]["Goals"] == bodies[2]["Goals"] == ["5"]
    assert (bodies[0]["AttributionModels"]
            == bodies[2]["AttributionModels"] == ["AUTO"])
    assert bodies[0]["SelectionCriteria"] == {
        "DateFrom": "2026-09-11", "DateTo": "2026-09-17",
        "Filter": [{"Field": "CampaignId", "Operator": "IN", "Values": ["7"]}],
    }
    assert bodies[2]["SelectionCriteria"]["DateFrom"] == "2026-09-18"
    assert bodies[3].get("Goals") is None  # агрегат B — без целей
    for body in bodies:
        for field in ("BounceRate", "AvgImpressionPosition", "AvgClickPosition"):
            assert field in body["FieldNames"]
        assert body["DateRangeType"] == "CUSTOM_DATE"


async def test_compare_lc_header_without_goals(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[
        _tsv(COLS + ROW_A), _tsv(COLS + ROW_A),
        _tsv(COLS + ROW_B), _tsv(COLS + ROW_B),
    ])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_compare"].run(ctx, _base(with_conversions=False))
    assert "атрибуция: LC (AUTO неприменима без целей" in out


async def test_compare_device_itogo_from_aggregate(respx_mock, tmp_path):
    """Итого долей — только из агрегата, не среднее по группам."""
    respx_mock.post(RURL).mock(side_effect=[
        _tsv(DCOLS + DET_A), _tsv(COLS + ROW_A),
        _tsv(DCOLS + DET_B), _tsv(COLS + ROW_B),
    ])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_compare"].run(
        ctx, _base(with_conversions=False, group_by="device"))
    assert "| Группа | Метрика | A | B | Δ | Δ% |" in out
    # Среднее (30+25)/2=27.50 запрещено; только агрегат 29.41.
    assert "| Итого | Отказы, % | 29.41 | 35.15 | +5.74 п.п. | +19.52% |" in out
    assert "| desktop | Отказы, % | 30.00 | 32.00 | +2.00 п.п. | +6.67% |" in out
    # Сортировка групп по расходу B: desktop (100000) выше mobile.
    assert out.index("| desktop | Показы |") < out.index("| mobile | Показы |")
    assert out.index("| Итого | Показы |") < out.index("| desktop | Показы |")


async def test_compare_single_login_only(respx_mock, tmp_path):
    base = "https://api.direct.yandex.com/json/v5"
    respx_mock.post(f"{base}/clients").mock(
        return_value=httpx.Response(200, json={"result": {"Clients": []}}))
    respx_mock.post(f"{base}/agencyclients").mock(
        return_value=httpx.Response(200, json={"result": {"AgencyClients": []}}))
    ctx = _ctx(tmp_path, extra_accounts=True)
    with pytest.raises(ValueError, match="ровно один кабинет"):
        await ACTIONS["stats_compare"].run(ctx, _base(account="all"))


def test_grouped_keeps_minus_between_minus1_and_0():
    """v1.1.22: регрессия _grouped — минус у -0.20 не теряется."""
    from decimal import Decimal

    from directai_mcp.fmt import money, num

    assert money(Decimal("-0.20")) == "-0.20"
    assert num(Decimal("-0.20")) == "-0.20"
    assert money(Decimal("-134.01")) == "-134.01"
    assert money(Decimal(0)) == "0.00"


def test_instructions_compare_and_tool_rules():
    """v1.1.22 п.5: сравнение — только stats_compare; инструмент не подменять."""
    from directai_mcp.server import INSTRUCTIONS

    text = " ".join(INSTRUCTIONS.split())
    assert "только через stats_compare" in text
    assert "не двумя вызовами с ручной арифметикой" in text
    assert "не подменяйте другим" in text


def test_instructions_no_recalc_no_claims():
    """Правила §6: метрики как есть, сверка по числам, шапка цитатой."""
    from directai_mcp.server import INSTRUCTIONS

    text = " ".join(INSTRUCTIONS.split())
    assert "не пересчитывать вручную — брать как есть" in text
    assert "без фактического сравнения чисел" in text
    assert "цитировать из шапки инструмента" in text
