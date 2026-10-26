#!/usr/bin/env python3
"""Регрессия на примере аудита (ТЗ §14).

Проверяет случай CPA=1460, порог=2920, расход площадки=2700:
правило исключения по порогу срабатывать не должно.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

EXAMPLE = ROOT / "examples" / "example-87654321"
DOC = ROOT / "references" / "example-audit.md"


class TestExampleRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = json.loads((EXAMPLE / "audit.json").read_text(encoding="utf-8"))
        cls.doc = DOC.read_text(encoding="utf-8")

    def finding(self, rule, object_id):
        return next((f for f in self.audit["findings"]
                     if f["rule_id"] == rule and f["object"].get("id") == object_id), None)

    def test_cpa_and_threshold(self):
        self.assertAlmostEqual(self.audit["metrics"]["a"]["cpa"], 1460.0, places=2)
        self.assertAlmostEqual(self.audit["thresholds"]["attention_threshold_rub"], 2920.0, places=2)

    def test_placement_below_threshold_is_watch(self):
        below = self.finding("RSY-02", "video-fun.example")
        self.assertIsNotNone(below, "площадка 2 700 ₽ должна попасть в наблюдение")
        self.assertIsNone(self.finding("RSY-01", "video-fun.example"))
        self.assertEqual(below["spend"]["at_risk"], None)

    def test_no_exclusion_recommendation_for_below_threshold(self):
        for rec in self.audit["recommendations"]:
            self.assertNotEqual(rec["object"].get("id"), "video-fun.example")

    def test_placement_above_threshold_is_candidate(self):
        above = self.finding("RSY-01", "shop-fun.example")
        self.assertIsNotNone(above)
        rec = next(r for r in self.audit["recommendations"]
                   if r["object"].get("id") == "shop-fun.example")
        self.assertEqual(rec["expected_effect"]["type"], "estimate")
        self.assertEqual(rec["apply"]["method"], "manual")

    def test_example_doc_shows_both_numbers(self):
        self.assertIn("2 700,00 ₽", self.doc)
        self.assertIn("2 920,00 ₽", self.doc)
        self.assertIn("RSY-02", self.doc)

    def test_example_doc_matches_json(self):
        metrics = self.audit["metrics"]
        self.assertIn("1 460,00 ₽", self.doc)
        self.assertIn("950,00 ₽", self.doc)
        self.assertIn(str(metrics["a"]["conversions"]), self.doc)
        self.assertIn("53,68%", self.doc)

    def test_example_doc_has_no_categorical_blame(self):
        lowered = self.doc.lower()
        self.assertNotIn("проблема в сайте", lowered)
        self.assertNotIn("виноват сайт", lowered)
        self.assertIn("альтернативные объяснения", lowered)

    def test_example_is_validated(self):
        self.assertEqual(self.audit["validation"]["status"], "PASS")
        self.assertEqual(self.audit["data_quality"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
