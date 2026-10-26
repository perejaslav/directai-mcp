#!/usr/bin/env python3
"""Режимы: доступность, комбинации, монитор и эксперимент (ТЗ §9)."""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import factory  # noqa: E402
from auditlib import build  # noqa: E402

TODAY = datetime.date(2026, 10, 23)


class ModeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-mode-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, name, modes, **kwargs):
        kwargs["modes"] = modes
        root = factory.make_session(self.tmp / name, **kwargs)
        return root, build.build(root, today=TODAY)


class TestModeScopes(ModeCase):
    def test_quick_runs_only_core(self):
        root, audit = self.make("q", ["quick"], queries=[
            ("окна даром", dict(impressions=2000, clicks=60, cost=2400, conversions=0))],
            placements=[("p.example", dict(impressions=9000, clicks=90, cost=2500, conversions=0))])
        rules = {f["rule_id"] for f in audit["findings"]}
        self.assertNotIn("SQ-01", rules)
        self.assertNotIn("RSY-01", rules)
        self.assertIn(audit["validation"]["status"], ("PASS", "WARN"))

    def test_deep_plus_semantic_combines(self):
        root, audit = self.make("ds", ["deep", "semantic"], queries=[
            ("окна даром", dict(impressions=2000, clicks=60, cost=2400, conversions=0))])
        self.assertIn("SQ-01", {f["rule_id"] for f in audit["findings"]})
        self.assertEqual(audit["mode"], ["deep", "semantic"])

    def test_rsy_unavailable_for_search_only(self):
        root, audit = self.make("r", ["rsy"], strategy="поиск: WB_MAXIMUM_CLICKS; сети: SERVING_OFF")
        status = audit["mode_status"]["rsy"]
        self.assertFalse(status["available"])
        self.assertIn("сетях", status["reason"])

    def test_semantic_degraded_without_product(self):
        root, audit = self.make("sp", ["semantic"], product={})
        self.assertTrue(audit["mode_status"]["semantic"].get("degraded"))
        self.assertIn("продукт", audit["mode_status"]["semantic"]["reason"])

    def test_funnel_requires_metrika(self):
        root, audit = self.make("f", ["funnel"])
        self.assertFalse(audit["mode_status"]["funnel"]["available"])

    def test_every_mode_reports_reason(self):
        root, audit = self.make("m", ["deep"])
        for name, status in audit["mode_status"].items():
            if not status["available"]:
                self.assertTrue(status.get("reason"), f"режим {name} без причины")


class TestExperimentMode(ModeCase):
    def test_experiment_available_with_hypothesis(self):
        root, audit = self.make(
            "e", ["experiment"],
            a=dict(impressions=700, clicks=15, cost=1800, conversions=1),
            b=dict(impressions=650, clicks=14, cost=1700, conversions=3))
        self.assertTrue(audit["mode_status"]["experiment"]["available"])
        self.assertTrue(audit["experiments"])
        experiment = audit["experiments"][0]
        for field in ("metric", "baseline", "start_condition", "stop_condition",
                      "min_duration_days", "success", "failure"):
            self.assertIn(field, experiment)

    def test_experiment_without_hypothesis_is_unavailable(self):
        root, audit = self.make("e2", ["experiment"])
        self.assertFalse(audit["mode_status"]["experiment"]["available"])


class TestMonitorMode(ModeCase):
    def test_monitor_uses_previous_snapshot(self):
        root1, first = self.make("m1", ["deep"])
        first_path = self.tmp / "m1" / "audit.json"
        first_path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")

        root2 = factory.make_session(
            self.tmp / "m2", modes=["monitor"],
            a=dict(impressions=90000, clicks=1800, cost=110000, conversions=30),
            b=dict(impressions=100000, clicks=2000, cost=120000, conversions=40),
            previous=first_path)
        second = build.build(root2, today=TODAY)

        status = second["mode_status"]["monitor"]
        self.assertTrue(status["available"])
        self.assertIn("MON-01", {f["rule_id"] for f in second["findings"]})
        self.assertEqual(second["monitor"]["previous_audit_id"], first["audit_id"])
        self.assertIn("cost", second["monitor"]["metric_deltas"])

    def test_monitor_without_snapshot_is_unavailable(self):
        root, audit = self.make("m3", ["monitor"])
        self.assertFalse(audit["mode_status"]["monitor"]["available"])

    def test_monitor_compare_cli(self):
        root1, first = self.make("m4", ["deep"])
        p1 = self.tmp / "a1.json"
        p1.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
        root2 = factory.make_session(
            self.tmp / "m5", modes=["monitor"],
            a=dict(impressions=90000, clicks=1800, cost=110000, conversions=30),
            previous=p1)
        second = build.build(root2, today=TODAY)
        p2 = self.tmp / "a2.json"
        p2.write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")

        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "monitor_compare.py"),
                                 str(p1), str(p2), "--json"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertEqual(report["current_audit_id"], second["audit_id"])
        self.assertIn("metric_deltas", report)


if __name__ == "__main__":
    unittest.main()
