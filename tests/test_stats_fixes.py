"""Step 2 fixes: cap, criterion, dates, CSV decimals, MD save."""

from datetime import datetime, timedelta

from directai_mcp.catalog.registry import Ctx
from directai_mcp.catalog.stats import (
    StatsParams,
    _clean_criterion,
    _context,
    _period_label,
    chunk_goals,
    effective_attribution,
    extract_campaign_goals,
)
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.fmt import MAX_TOOL_ROWS, render_table, save_csv, save_md


def _test_ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def test_header_attribution_default_lc(tmp_path):
    ctx = _test_ctx(tmp_path)
    entries = list(ctx.settings.accounts.values())
    header = _context(ctx, "stats_summary", entries, StatsParams(period="YESTERDAY"))
    assert "атрибуция: LC (AUTO неприменима без целей" in header
    assert "у кампании нет ключевых целей" in header
    assert "YESTERDAY" not in header


def test_header_attribution_with_goals(tmp_path):
    ctx = _test_ctx(tmp_path)
    entries = list(ctx.settings.accounts.values())
    params = StatsParams(period="YESTERDAY", goals=["123"], attribution=["FCCD"])
    header = _context(ctx, "stats_summary", entries, params)
    assert "атрибуция: FCCD" in header
    assert effective_attribution(ctx, StatsParams()) == ["AUTO"]


def test_hard_cap_200():
    assert MAX_TOOL_ROWS == 200
    rows = [{"Cost": str(i), "Clicks": "1"} for i in range(250)]
    out = render_table("ctx", ["Cost", "Clicks"], rows, min(1000, MAX_TOOL_ROWS))
    assert "Скрыто строк: 50 из 250." in out
    assert out.count("\n| ") == 200 + 2  # header + separator + 200 rows


def test_criterion_cut_on_space_hyphen_only():
    assert (
        _clean_criterion("грунт-эмаль по металлу -hammer -армокот", False)
        == "грунт-эмаль по металлу"
    )
    assert (
        _clean_criterion("эмаль 3-в-1 по ржавчине", False) == "эмаль 3-в-1 по ржавчине"
    )
    assert _clean_criterion("краска -крыша -опт", False) == "краска"
    assert _clean_criterion("краска -крыша -опт", True) == "краска -крыша -опт"
    assert _clean_criterion("---autotargeting", False) == "Автотаргетинг"
    assert _clean_criterion("---autotargeting", True) == "Автотаргетинг"
    assert _clean_criterion(None, False) is None


def test_period_label_concrete_dates():
    today = datetime.now().astimezone().date()
    week_ago = today - timedelta(days=7)
    yesterday = today - timedelta(days=1)
    fmt = lambda d: d.strftime("%d.%m.%Y")
    assert _period_label(StatsParams(period="LAST_7_DAYS")) == (
        f"{fmt(week_ago)}–{fmt(yesterday)}"
    )
    assert _period_label(StatsParams(period="YESTERDAY")) == fmt(yesterday)
    custom = StatsParams(date_from="2026-09-01", date_to="2026-09-10")
    assert _period_label(custom) == "01.09.2026–10.09.2026"


def test_csv_comma_decimals(tmp_path):
    path = save_csv(
        tmp_path,
        "t",
        ["Cost", "Clicks", "X"],
        [{"Cost": "1116.05", "Clicks": "2", "X": None}],
    )
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[1] == "1116,05;2;"


def test_save_md_full_table(tmp_path):
    rows = [{"A": str(i), "B": "1"} for i in range(5)]
    path = save_md(tmp_path, "rep", "ctx-line", ["A", "B"], rows)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# rep\n")
    assert "ctx-line" in text
    assert "Итого" in text


def test_extract_campaign_goals_priority_and_strategy():
    item = {
        "TextCampaign": {
            "PriorityGoals": {"Items": [{"GoalId": 11}, {"GoalId": 12}, {"GoalId": 0}]},
            "BiddingStrategy": {"Search": {"AverageCpa": {"GoalId": 22}}},
            "RelevantKeywords": {"OptimizeGoalId": 33},
        }
    }
    assert extract_campaign_goals(item) == {11, 22, 33}


def test_extract_campaign_goals_ignores_placeholders():
    item = {
        "UnifiedCampaign": {
            "PriorityGoals": {"Items": [{"GoalId": 13}]},
            "BiddingStrategy": {"Search": {"WbMaximumConversionRate": {"GoalId": 13}}},
        }
    }
    assert extract_campaign_goals(item) == set()


def test_context_auto_goals_header(tmp_path):
    ctx = _test_ctx(tmp_path)
    entries = list(ctx.settings.accounts.values())
    header = _context(
        ctx,
        "stats_campaigns",
        entries,
        StatsParams(period="YESTERDAY"),
        (["1", "2"], "auto-key"),
    )
    assert "атрибуция: AUTO" in header
    assert "целей: 2 (авто key: PriorityGoals+стратегия)" in header
    assert "режим: key" in header


def test_chunk_goals_max_10():
    goals = [str(i) for i in range(12)]
    chunks = chunk_goals(goals)
    assert chunks == [goals[:10], goals[10:]]
    assert chunk_goals([]) == []


def test_localize_goal_columns_named_and_bare():
    from directai_mcp.catalog.stats import localize_goal_columns

    cols = ["CampaignName", "Conversions_1_AUTO", "Revenue_2_AUTO", "Clicks"]
    rows = [{
        "CampaignName": "A",
        "Conversions_1_AUTO": "3",
        "Revenue_2_AUTO": "100.00",
        "Clicks": "5",
    }]
    new_cols, new_rows = localize_goal_columns(cols, rows, {"1": "Заказ"})
    assert new_cols == [
        "CampaignName",
        "Conversions_Заказ (1)_AUTO",
        "Revenue_2_AUTO",
        "Clicks",
    ]
    assert new_rows == [{
        "CampaignName": "A",
        "Conversions_Заказ (1)_AUTO": "3",
        "Revenue_2_AUTO": "100.00",
        "Clicks": "5",
    }]
