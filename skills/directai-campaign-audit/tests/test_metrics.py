#!/usr/bin/env python3
"""Формулы, единицы и статистическая надёжность (ТЗ §4.4, §6.4)."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from auditlib import metrics as m  # noqa: E402
from auditlib.model import fmt_money, fmt_number, parse_number, period_days, unit_of


class TestCompare(unittest.TestCase):
    def test_delta_uses_b_as_base(self):
        result = m.compare(5.0, 4.0, kind="rate")
        self.assertAlmostEqual(result["abs"], 1.0)
        self.assertAlmostEqual(result["pp"], 1.0)
        self.assertAlmostEqual(result["pct"], 25.0)

    def test_rates_expose_points_and_percent_separately(self):
        result = m.compare(5.0, 4.0, kind="rate")
        self.assertNotAlmostEqual(result["pp"], result["pct"])
        self.assertEqual(result["pp"], 1.0)
        self.assertEqual(result["pct"], 25.0)

    def test_zero_base_has_no_percent(self):
        result = m.compare(3.0, 0.0)
        self.assertTrue(result["base_zero"])
        self.assertIsNone(result["pct"])
        self.assertEqual(result["note"], "нет базы для сравнения: B = 0")

    def test_missing_is_not_zero(self):
        result = m.compare(None, 0.0)
        self.assertFalse(result["available"])
        self.assertIsNone(result["abs"])
        self.assertIsNone(result["pct"])

    def test_falling_values_are_negative(self):
        result = m.compare(80, 100)
        self.assertEqual(result["abs"], -20)
        self.assertEqual(result["pct"], -20.0)

    def test_rounding_hides_small_change(self):
        result = m.compare(4.04, 4.03, kind="rate", decimals=1)
        self.assertAlmostEqual(result["abs"], 0.01, places=6)
        self.assertTrue(result["display_hides_change"])
        self.assertIn("не видно", result["note"])

    def test_tool_style_wrong_delta_is_not_used(self):
        # stats_compare отдаёт Δ% от базы A и меняет знак — проверяем, что мы считаем сами.
        a, b = 326064, 257550
        result = m.compare(a, b)
        self.assertEqual(result["pct"], (a - b) / b * 100)
        self.assertGreater(result["pct"], 0)
        self.assertEqual(result["abs"], a - b)

    def test_derived_metrics(self):
        self.assertAlmostEqual(m.ctr(50, 1000), 5.0)
        self.assertAlmostEqual(m.cpc(1000, 50), 20.0)
        self.assertAlmostEqual(m.cr(5, 50), 10.0)
        self.assertAlmostEqual(m.cpa(1000, 5), 200.0)

    def test_no_conversions_means_no_cpa(self):
        self.assertIsNone(m.cpa(1000, 0))
        self.assertIsNone(m.safe_div(10, 0))


class TestNumbers(unittest.TestCase):
    def test_parses_report_strings(self):
        self.assertEqual(parse_number("326 064"), 326064)
        self.assertEqual(parse_number("1 234.56"), 1234.56)
        self.assertEqual(parse_number("1 234,56"), 1234.56)
        self.assertEqual(parse_number("+0.07 п.п."), 0.07)
        self.assertEqual(parse_number("75.6%"), 75.6)
        self.assertEqual(parse_number("−68 514"), -68514)

    def test_missing_tokens_return_none(self):
        for value in ("—", "-", "", "нет данных", None, "н/д", "NaN"):
            self.assertIsNone(parse_number(value), msg=repr(value))

    def test_zero_is_zero_not_missing(self):
        self.assertEqual(parse_number("0"), 0.0)
        self.assertEqual(parse_number(0), 0.0)

    def test_unit_detection(self):
        self.assertEqual(unit_of("75.6%"), "percent")
        self.assertEqual(unit_of("+0.07 п.п."), "percentage_point")
        self.assertEqual(unit_of("1 200 ₽"), "RUB")
        self.assertIsNone(unit_of("1 200"))

    def test_formatting_is_only_presentation(self):
        self.assertEqual(fmt_number(1234.567, 2), "1 234,57")
        self.assertEqual(fmt_money(0), "0,00 ₽")
        self.assertEqual(fmt_number(None), "нет данных")

    def test_period_length(self):
        self.assertEqual(period_days({"from": "2026-09-26", "to": "2026-10-09"}), 14)
        self.assertIsNone(period_days({"from": "2026-09-26", "to": "2026-09-20"}))


class TestSignificance(unittest.TestCase):
    def test_two_proportions_significant(self):
        result = m.two_proportion_z(400, 10000, 342, 10000)
        self.assertTrue(result["valid"])
        self.assertLess(result["p"], 0.05)

    def test_two_proportions_not_significant(self):
        result = m.two_proportion_z(20, 1000, 19, 1000)
        self.assertTrue(result["valid"])
        self.assertGreater(result["p"], 0.05)

    def test_small_sample_is_not_tested(self):
        result = m.two_proportion_z(2, 20, 1, 20)
        self.assertFalse(result["valid"])
        self.assertIsNone(result["p"])
        self.assertIn("мало наблюдений", result["reason"])

    def test_sufficiency_reports_gaps(self):
        result = m.sufficiency(500, 20, 1, {"min_impressions": 1000, "min_clicks": 100,
                                           "min_conversions": 10})
        self.assertFalse(result["sufficient"])
        self.assertEqual(len(result["gaps"]), 3)

    def test_sufficiency_pass(self):
        result = m.sufficiency(5000, 200, 20, {"min_impressions": 1000, "min_clicks": 100,
                                              "min_conversions": 10})
        self.assertTrue(result["sufficient"])

    def test_bonferroni_increases_threshold(self):
        adjustment = m.adjust_alpha(0.05, 20)
        self.assertAlmostEqual(adjustment["alpha_adjusted"], 0.0025)
        self.assertEqual(adjustment["method"], "Bonferroni")

    def test_poisson_ci_brackets_ratio(self):
        result = m.poisson_ratio_ci(40, 20)
        self.assertTrue(result["valid"])
        self.assertLess(result["ci_low"], 2.0)
        self.assertGreater(result["ci_high"], 2.0)

    def test_directional_word(self):
        self.assertEqual(m.trend_word(-25), "падение")
        self.assertEqual(m.trend_word(5), "стабильно")


if __name__ == "__main__":
    unittest.main()
