"""v1.1.1: Decimal-деньги, без ДРР по ценностям, единый формат ценности."""

from decimal import Decimal

from directai_mcp.catalog.stats import drop_roi, goal_value_lines
from directai_mcp.fmt import money, num, totals, totals_line


def test_cpa_rounding_half_up():
    assert money(Decimal("15176.23") / 2) == "7 588.12"
    assert money("7 588,115") == "7 588.12"
    assert money("2.675") == "2.68"
    assert money("2.665") == "2.67"
    assert money("0.125") == "0.13"
    assert money("2000000") == "2 000 000.00"


def test_num_rounding():
    assert num("2.675") == "2.68"
    assert num(5, 0) == "5"
    assert num(None) == "—"
    assert money(None) == "—"


def test_totals_cpa_decimal():
    rows = [{"Cost": "15176.23", "Conversions_900000010_AUTO": "2",
             "Revenue_900000010_AUTO": "400"}]
    t = totals(rows)
    assert t["Cost"] == Decimal("15176.23")
    line = totals_line(t, single_goal=True)
    assert "CPA: 7 588.12 ₽" in line
    assert "ДРР" not in line
    assert "Доход:" not in line
    assert "Суммарная ценность целей" not in line


def test_totals_multi_goal_keeps_aggregate():
    rows = [{"Cost": "100", "Conversions_1_X": "1", "Revenue_1_X": "200",
             "Conversions_2_X": "3", "Revenue_2_X": "300"}]
    line = totals_line(totals(rows))
    assert "Ценность целей (условная): 500.00 ₽" in line
    assert "Доход:" not in line
    assert "ДРР" not in line


def test_totals_aggregate_no_goals_conditional():
    rows = [{"Cost": "100", "Conversions": "1", "Revenue": "5500"}]
    line = totals_line(totals(rows))
    assert "Ценность целей (условная): 5 500.00 ₽" in line
    assert "Доход:" not in line


def test_drop_roi():
    cols = ["CampaignId", "GoalsRoi_1_AUTO", "Revenue_1_AUTO"]
    rows = [{"CampaignId": "1", "GoalsRoi_1_AUTO": "3544",
             "Revenue_1_AUTO": "400"}]
    kept_cols, kept_rows = drop_roi(cols, rows)
    assert kept_cols == ["CampaignId", "Revenue_1_AUTO"]
    assert kept_rows == [{"CampaignId": "1", "Revenue_1_AUTO": "400"}]
    assert drop_roi(["A"], [{"A": 1}]) == (["A"], [{"A": 1}])


def test_goal_value_format():
    cols = ["Conversions_900000010_AUTO", "Revenue_900000010_AUTO"]
    rows = [{"Conversions_900000010_AUTO": "2", "Revenue_900000010_AUTO": "400"}]
    lines = goal_value_lines(cols, rows, {"900000010": "Все формы"})
    assert lines == [
        ("Ценность цели Все формы (900000010): 200.00 ₽ · "
         "Суммарная: 400.00 ₽ (2 конв.)")
    ]


def test_goal_value_no_name_no_conversions():
    cols = ["Revenue_9_X"]
    rows = [{"Revenue_9_X": "100"}]
    assert goal_value_lines(cols, rows, {}) == [
        "Ценность цели 9: — · Суммарная: 100.00 ₽ (0 конв.)"
    ]
    assert goal_value_lines(["Cost"], [{"Cost": "1"}], {}) == []
