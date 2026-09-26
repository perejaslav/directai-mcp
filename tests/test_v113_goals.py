"""v1.1.3: all-union с goals.toml, агрегатные итоги, чанки, нули в all."""

import httpx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import resolve_auto_goals
from directai_mcp.config import AccountEntry, Settings

V501 = "https://api.direct.yandex.com/json/v501/campaigns"
RURL = "https://api.direct.yandex.com/json/v5/reports"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _write_toml(tmp_path):
    (tmp_path / "goals.toml").write_text(
        "[goals]\n1 = \"Один\"\n2 = \"Два\"\n", encoding="utf-8"
    )


def _camp(*goal_ids):
    return httpx.Response(200, json={"result": {"Campaigns": [{
        "Id": 7, "Type": "UNIFIED_CAMPAIGN",
        "UnifiedCampaign": {
            "PriorityGoals": {"Items": [{"GoalId": g} for g in goal_ids]},
            "BiddingStrategy": {},
        },
    }]}})


async def test_all_union_adds_toml_ids(respx_mock, tmp_path):
    _write_toml(tmp_path)
    respx_mock.post(V501).mock(return_value=_camp(9))
    ctx = _ctx(tmp_path)
    entries = [AccountEntry(alias="m", login="agency-login")]
    key = await resolve_auto_goals(ctx, entries, [7], "key")
    allm = await resolve_auto_goals(ctx, entries, [7], "all")
    assert key == {"agency-login": ["9"]}
    assert allm == {"agency-login": ["1", "2", "9"]}


def _tsv(header, row):
    return httpx.Response(200, text=header + "\n" + row + "\n")


async def test_merge_12_goals_two_chunks(respx_mock, tmp_path):
    goals = [str(i) for i in range(1, 13)]
    h1 = "CampaignId\t" + "\t".join(f"Conversions_{g}_AUTO" for g in goals[:10])
    h2 = "CampaignId\t" + "\t".join(f"Conversions_{g}_AUTO" for g in goals[10:])
    r1 = "7\t" + "\t".join(["1"] * 10)
    r2 = "7\t" + "\t".join(["2"] * 2)
    route = respx_mock.post(RURL).mock(side_effect=[_tsv(h1, r1), _tsv(h2, r2),
                                                    _tsv("CampaignId\tConversions", "7\t12")])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_campaigns"]
    out = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                        goals=goals, attribution=["AUTO"],
                                        with_conversions=True))
    assert route.call_count == 3  # 2 чанка + агрегат
    assert "Конверсии: 14" in out  # v1.2.1: итог = Σ строк (10×1 + 2×2)
    assert "дубли визитов" in out


async def test_row_sums_win_over_aggregate(respx_mock, tmp_path):
    route = respx_mock.post(RURL).mock(side_effect=[
        _tsv("CampaignId\tConversions_1_AUTO", "7\t10"),
        _tsv("CampaignId\tConversions", "7\t9"),
    ])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_campaigns"]
    out = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                        goals=["1"], attribution=["AUTO"],
                                        with_conversions=True))
    assert route.call_count == 2
    assert "Конверсии: 10" in out  # v1.2.1: итог = Σ строк
    assert "Конверсии (все цели, LC): 9 (другая популяция, не итог)." in out
    assert "Доход:" not in out
    assert "ДРР" not in out


async def test_all_keeps_zero_goal_columns(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[
        _tsv("CampaignId\tConversions_1_AUTO", "7\t--"),
        _tsv("CampaignId\tConversions", "7\t0"),
    ])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_campaigns"]
    out = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                        goals=["1"], goals_mode="all",
                                        with_conversions=True,
                                        include_empty=True))
    assert "Conversions_1_AUTO" in out
