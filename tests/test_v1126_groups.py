"""v1.1.26: группы (статус/тип/счёт), нулевой Revenue, сверка, «отказы»."""

import httpx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"
V501G = "https://api.direct.yandex.com/json/v501/adgroups"
V501C = "https://api.direct.yandex.com/json/v501/campaigns"

CHUNK = (
    "CampaignId\tCampaignName\tAdGroupId\tAdGroupName\tImpressions\t"
    "Clicks\tCost\tConversions\n"
    "7\tК\t11\tГроза\t2645\t189\t19610.02\t5\n"
    "7\tК\t22\tГрад\t3406\t93\t7827.66\t2\n"
)
RECONC = "CampaignId\tImpressions\tClicks\tCost\n7\t6051\t282\t27437.68\n"
GROUPS = {"result": {"AdGroups": [
    {"Id": 11, "CampaignId": 7, "Name": "Гроза", "Status": "ACCEPTED",
     "ServingStatus": "ELIGIBLE", "Type": "TEXT_AD_GROUP"},
    {"Id": 22, "CampaignId": 7, "Name": "Град", "Status": "ACCEPTED",
     "ServingStatus": "RARELY_SERVED", "Type": "TEXT_AD_GROUP"},
    {"Id": 33, "CampaignId": 7, "Name": "Тишь", "Status": "ACCEPTED",
     "ServingStatus": "ELIGIBLE", "Type": "TEXT_AD_GROUP"},
]}}


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def _mock(respx_mock, chunk=CHUNK, reconc=RECONC, groups=GROUPS):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(chunk), _tsv(reconc)])
    respx_mock.post(V501G).mock(
        return_value=httpx.Response(200, json=groups))


async def test_groups_status_type_counts(respx_mock, tmp_path):
    _mock(respx_mock)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_adgroups"].run(
        ctx, ACTIONS["stats_adgroups"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    assert "| AdGroupName | Status | ServingStatus | Type |" in out
    assert "RARELY_SERVED" in out
    assert "TEXT_AD_GROUP" in out
    assert "Групп в кампании: 3, со статистикой: 2." in out
    assert "Тишь" not in out  # без статистики, флаг выключен


async def test_include_empty_shows_missing_group(respx_mock, tmp_path):
    _mock(respx_mock)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_adgroups"].run(
        ctx, ACTIONS["stats_adgroups"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False,
            include_empty=True))
    assert "Тишь" in out
    assert "Групп в кампании: 3, со статистикой: 2." in out


async def test_zero_revenue_dropped(respx_mock, tmp_path):
    chunk = (CHUNK.split("Conversions\n")[0] + "Conversions\tRevenue\n"
             + "7\tК\t11\tГроза\t2645\t189\t19610.02\t5\t0.00\n"
             + "7\tК\t22\tГрад\t3406\t93\t7827.66\t2\t0.00\n")
    _mock(respx_mock, chunk=chunk)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_adgroups"].run(
        ctx, ACTIONS["stats_adgroups"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    assert "Revenue" not in out
    assert "0.00" not in out.split("Итого:")[0]
    assert "Ценность цели" not in out


async def test_nonzero_revenue_renamed_inline_kept_in_csv(respx_mock, tmp_path):
    chunk = (
        "CampaignId\tCampaignName\tAdGroupId\tAdGroupName\tImpressions\t"
        "Clicks\tCost\tConversions_9_AUTO\tRevenue_9_AUTO\n"
        "7\tК\t11\tГроза\t100\t10\t1100.00\t2\t750.00\n")
    reconc = "CampaignId\tImpressions\tClicks\tCost\n7\t100\t10\t1100.00\n"
    agg = ("CampaignId\tCampaignName\tAdGroupId\tAdGroupName\tImpressions\t"
           "Clicks\tCost\n7\tК\t11\tГроза\t100\t10\t1100.00\n")
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(chunk), _tsv(agg), _tsv(reconc),
                     _tsv(chunk), _tsv(agg), _tsv(reconc)])
    respx_mock.post(V501G).mock(
        return_value=httpx.Response(200, json=GROUPS))
    # v1.1.29: типы ценности — CounterIds кампаний (пусто → fallback goals.toml).
    respx_mock.post(V501C).mock(
        return_value=httpx.Response(200, json={"result": {"Campaigns": []}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_adgroups"].run(
        ctx, ACTIONS["stats_adgroups"].params(
            account="agency-login", campaign_ids=[7], goals=["9"],
            attribution=["AUTO"]))
    assert "Ценность целей (условная)_" in out
    assert "| Revenue_9_AUTO |" not in out
    # Одна цель: в итоге ценность скрыта штатно, строка цели остаётся.
    assert "Ценность цели 9:" in out
    ctx2 = _ctx(tmp_path)
    out2 = await ACTIONS["stats_adgroups"].run(
        ctx2, ACTIONS["stats_adgroups"].params(
            account="agency-login", campaign_ids=[7], goals=["9"],
            attribution=["AUTO"], output="file", format="csv"))
    path = out2.split("Полный результат: ")[1].split(" (")[0]
    from pathlib import Path as _Path

    body = _Path(path).read_text(encoding="utf-8-sig")
    assert "Revenue_9_AUTO" in body  # в CSV — имена полей API


async def test_reconc_mismatch_shows_numbers(respx_mock, tmp_path):
    reconc = "CampaignId\tImpressions\tClicks\tCost\n7\t6000\t280\t27000.00\n"
    _mock(respx_mock, reconc=reconc)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_adgroups"].run(
        ctx, ACTIONS["stats_adgroups"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    assert "Сверка с итогом кампании: расходится:" in out
    assert "6 051" in out and "6 000" in out


async def test_totals_cr_from_aggregate(respx_mock, tmp_path):
    _mock(respx_mock)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_adgroups"].run(
        ctx, ACTIONS["stats_adgroups"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    # 7/282 = 2.482…% — только из сумм, не среднее по строкам.
    assert "CR: 2.48%" in out


async def test_no_otkat_wording(respx_mock, tmp_path):
    """п.4: «откат» отсутствует в выводах (термин — «отказы»)."""
    respx_mock.post(RURL).mock(return_value=_tsv(CHUNK))
    respx_mock.post(V501G).mock(
        return_value=httpx.Response(200, json=GROUPS))
    ctx = _ctx(tmp_path)
    for name, extra in (
            ("stats_adgroups", {"campaign_ids": [7]}),
            ("stats_campaigns", {"campaign_ids": [7]}),
            ("stats_devices", {})):
        act = ACTIONS[name]
        out = await act.run(
            ctx, act.params(account="agency-login", with_conversions=False,
                            **extra))
        assert "откат" not in out.lower()
