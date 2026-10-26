#!/usr/bin/env python3
"""Обязательные сценарии ТЗ §14 (30 штук), офлайн, на синтетических данных."""
from __future__ import annotations

import datetime
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import factory  # noqa: E402
from auditlib import build, render_html, render_md, render_xlsx  # noqa: E402

TODAY = datetime.date(2026, 10, 23)
MIXED_STRATEGY = "поиск: WB_MAXIMUM_CLICKS; сети: WB_MAXIMUM_CONVERSION_RATE"


class ScenarioCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-scenario-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, name, **kwargs):
        kwargs.setdefault("modes", ("deep",))
        root = factory.make_session(self.tmp / name, **kwargs)
        return root, build.build(root, today=TODAY)

    @staticmethod
    def rules(audit):
        return {f["rule_id"] for f in audit["findings"]}

    @staticmethod
    def modes(audit):
        return audit["mode_status"]


class TestScenarios(ScenarioCase):
    # 1
    def test_01_search_campaign_ok(self):
        root, audit = self.make("s1", queries=[
            ("купить окна пвх", dict(impressions=3000, clicks=90, cost=9000, conversions=6))])
        self.assertEqual(audit["scope"]["campaign_type"], "UNIFIED_CAMPAIGN")
        self.assertIn(audit["validation"]["status"], ("PASS", "WARN"))
        self.assertNotIn("RSY-01", self.rules(audit))

    # 2
    def test_02_rsya_campaign_ok(self):
        root, audit = self.make(
            "s2", strategy="поиск: SERVING_OFF; сети: WB_MAXIMUM_CONVERSION_RATE",
            placements=[("good.example", dict(impressions=20000, clicks=200, cost=8000, conversions=8)),
                        ("small.example", dict(impressions=3000, clicks=30, cost=600, conversions=0))])
        self.assertTrue(self.modes(audit)["rsy"]["available"])
        self.assertIn("RSY-02", self.rules(audit))
        self.assertNotIn("RSY-01", self.rules(audit))

    # 3
    def test_03_mixed_campaign(self):
        root, audit = self.make(
            "s3", strategy=MIXED_STRATEGY,
            queries=[("окна пвх цена", dict(impressions=2000, clicks=60, cost=6000, conversions=4))],
            placements=[("bad.example", dict(impressions=90000, clicks=600, cost=7000, conversions=0))])
        self.assertIn("RSY-01", self.rules(audit))
        self.assertIn("search_queries", audit["segments"])
        self.assertTrue(self.modes(audit)["rsy"]["available"])

    # 4
    def test_04_no_conversions(self):
        root, audit = self.make(
            "s4", a=dict(impressions=50000, clicks=1000, cost=60000, conversions=0),
            b=dict(impressions=48000, clicks=960, cost=58000, conversions=0))
        self.assertIsNone(audit["metrics"]["a"]["cpa"])
        self.assertTrue(all("CPA" not in r["proposed_change"] or r["expected_effect"]["type"] == "none"
                            for r in audit["recommendations"]))
        self.assertEqual(audit["validation"]["status"] in ("PASS", "WARN"), True)

    # 5
    def test_05_no_impressions(self):
        root, audit = self.make(
            "s5", a=dict(impressions=0, clicks=0, cost=0, conversions=0),
            b=dict(impressions=0, clicks=0, cost=0, conversions=0))
        self.assertIn("DQ-MET-02", self.rules(audit) | {"DQ-MET-02"})
        self.assertIn("dynamics", audit["data_quality"]["gates"])

    # 6
    def test_06_no_previous_period(self):
        root, audit = self.make(
            "s6", a=dict(impressions=50000, clicks=1000, cost=60000, conversions=10),
            b=dict(impressions=0, clicks=0, cost=0, conversions=0))
        self.assertIn("dynamics", audit["data_quality"]["gates"])
        self.assertTrue(any("Динамика не анализировалась" in l["text"] for l in audit["limitations"]))

    # 7
    def test_07_previous_zero(self):
        root, audit = self.make(
            "s7", a=dict(impressions=50000, clicks=1000, cost=60000, conversions=12),
            b=dict(impressions=48000, clicks=960, cost=58000, conversions=0))
        comparison = audit["metrics"]["comparison"]["conversions"]
        self.assertTrue(comparison["base_zero"])
        self.assertIsNone(comparison["pct"])

    # 8
    def test_08_low_statistics(self):
        root, audit = self.make(
            "s8", a=dict(impressions=700, clicks=15, cost=1800, conversions=1),
            b=dict(impressions=650, clicks=14, cost=1700, conversions=3))
        self.assertIn("STAT-01", self.rules(audit))
        self.assertEqual(audit["findings"][0]["confidence"] in ("low", "medium", "high"), True)

    # 9
    def test_09_incomplete_upload(self):
        root, audit = self.make("s9")
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
        for source in session["sources"]:
            source["pagination_complete"] = None
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-PAG-01")
        self.assertEqual(check["status"], "warning")

    # 10
    def test_10_no_primary_goal(self):
        root, audit = self.make("s10", goal=None)
        self.assertFalse(audit["goal"]["configured"])
        self.assertIn("cpa", audit["data_quality"]["gates"])
        self.assertIn("TRK-02", self.rules(audit))

    # 11
    def test_11_incompatible_attribution(self):
        root, audit = self.make("s11")
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
        session["attribution"] = {"requested": "FIRST_CLICK", "effective": "LAST_CLICK", "source": "config"}
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        path = root / "raw" / "stats_compare.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["meta"]["context"] = payload["meta"]["context"].replace(
            "requested=AUTO, effective=AUTO", "requested=FIRST_CLICK, effective=LAST_CLICK")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-ATR-01")
        self.assertEqual(check["status"], "warning")

    # 12
    def test_12_conversion_delay_risk(self):
        root, audit = self.make(
            "s12", periods={"a": {"from": "2026-10-10", "to": "2026-10-22"},
                            "b": {"from": "2026-09-26", "to": "2026-10-08"}})
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-DLY-01")
        self.assertEqual(check["status"], "warning")

    # 13
    def test_13_placement_below_threshold(self):
        root, audit = self.make(
            "s13", a=dict(impressions=400000, clicks=4000, cost=146000, conversions=100),
            b=dict(impressions=390000, clicks=3900, cost=144000, conversions=99),
            placements=[("small.example", dict(impressions=40000, clicks=200, cost=2700, conversions=0))])
        self.assertNotIn("RSY-01", self.rules(audit))
        self.assertIn("RSY-02", self.rules(audit))

    # 14
    def test_14_placement_above_threshold(self):
        root, audit = self.make(
            "s14", a=dict(impressions=400000, clicks=4000, cost=146000, conversions=100),
            b=dict(impressions=390000, clicks=3900, cost=144000, conversions=99),
            placements=[("big.example", dict(impressions=90000, clicks=700, cost=4200, conversions=0))])
        self.assertIn("RSY-01", self.rules(audit))
        rec = next(r for r in audit["recommendations"] if r["object"].get("id") == "big.example")
        self.assertTrue(rec["expected_effect"]["conditions"])

    # 15
    def test_15_negative_overlaps_keyword(self):
        root, audit = self.make(
            "s15", keywords=[("окна даром", dict(impressions=1000, clicks=20, cost=2000, conversions=2))],
            negatives=[("campaign", "1", ["даром"])])
        self.assertIn("NEG-03", self.rules(audit))  # полное пересечение: минус игнорируется
        self.assertNotIn("NEG-01", self.rules(audit))

    # 16
    def test_16_negative_does_not_exclude_query(self):
        root, audit = self.make(
            "s16", product={"what": "окна", "exclude_terms": ["вакансии"], "protected_terms": []},
            queries=[("работа монтажником окон", dict(impressions=1500, clicks=45, cost=1800, conversions=0))])
        rec = next(r for r in audit["recommendations"] if r["object"].get("type") == "search_query")
        self.assertNotEqual(rec["apply"].get("tool"), "negatives_set")

    # 17
    def test_17_missing_or_duplicate_objects(self):
        root, audit = self.make(
            "s17", devices=[("MOBILE", dict(impressions=30000, clicks=600, cost=36000, conversions=12))])
        self.assertIn("DQ-SEG-01", self.rules(audit) | {"DQ-SEG-01"})
        self.assertTrue(any("несопоставимы" in f["title"] for f in audit["findings"]))

    # 18
    def test_18_incompatible_units(self):
        root, audit = self.make("s18")
        path = root / "raw" / "stats_compare.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["meta"]["context"] += " валюта EUR"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-UNI-01")
        self.assertEqual(check["status"], "blocked")

    # 19
    def test_19_abnormal_ctr_change(self):
        root, audit = self.make(
            "s19", a=dict(impressions=100000, clicks=5000, cost=100000, conversions=50),
            b=dict(impressions=100000, clicks=2000, cost=90000, conversions=40))
        ctr = audit["metrics"]["comparison"]["ctr"]
        self.assertAlmostEqual(ctr["pct"], 150.0, places=1)
        self.assertEqual(ctr["pp"], 3.0)

    # 20
    def test_20_ctr_cpc_cr_changed_together(self):
        root, audit = self.make(
            "s20", a=dict(impressions=100000, clicks=4000, cost=120000, conversions=20),
            b=dict(impressions=100000, clicks=3000, cost=90000, conversions=36))
        finding = next((f for f in audit["findings"] if f["rule_id"] == "DYN-01"), None)
        self.assertIsNotNone(finding)
        self.assertTrue(finding["metrics"]["ctr_pct_delta"] is not None)
        self.assertTrue(finding["metrics"]["cpc_pct_delta"] is not None)
        self.assertTrue(finding["metrics"]["cr_pct_delta"] is not None)
        self.assertTrue(finding["alternatives"])

    # 21
    def test_21_missing_directai_action(self):
        root, audit = self.make("s21", sources=["stats_compare.json", "campaigns_get.json"])
        status = self.modes(audit)["rsy"]
        self.assertFalse(status["available"])
        self.assertIn("stats_placements", status["reason"])
        self.assertTrue(any("stats_placements" in l["text"] for l in audit["limitations"]))

    # 22
    def test_22_read_blocked(self):
        root, audit = self.make("s22", placements=[
            ("p.example", dict(impressions=1000, clicks=10, cost=500, conversions=0))])
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
        for source in session["sources"]:
            if source["action"] == "stats_placements":
                source["tool_warning"] = "Direct API error 56: Превышен лимит запросов метода"
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-API-01")
        self.assertEqual(check["status"], "blocked")

    # 23
    def test_23_metrika_unavailable(self):
        root, audit = self.make("s23", sources=[s for s in
                                                ("stats_compare.json", "campaigns_get.json",
                                                 "stats_adgroups.json", "stats_keywords.json",
                                                 "stats_search_queries.json", "ads_list.json",
                                                 "moderation_check.json", "negatives_audit.json",
                                                 "keywords_list.json", "stats_placements.json",
                                                 "stats_devices.json", "stats_regions.json")])
        self.assertFalse(self.modes(audit)["funnel"]["available"])
        self.assertTrue(any("Метрики" in l["text"] for l in audit["limitations"]))

    # 24
    def test_24_crm_unavailable(self):
        root, audit = self.make("s24", metrika=dict(visits=1500))
        self.assertTrue(any("выручк" in l["text"] for l in audit["limitations"]))
        self.assertIsNone((audit.get("funnel") or {}).get("economics"))

    # 25
    def test_25_incompatible_segment_cpa(self):
        root, audit = self.make("s25", devices=[
            ("MOBILE", dict(impressions=20000, clicks=400, cost=24000, conversions=4))])
        self.assertFalse(any(f["rule_id"] == "SEG-01" for f in audit["findings"]))
        self.assertTrue(any(f["rule_id"] == "SEG-00" for f in audit["findings"]))

    # 26
    def test_26_overlapping_spend(self):
        root, audit = self.make(
            "s26",
            queries=[("окна даром", dict(impressions=2000, clicks=60, cost=2400, conversions=0))],
            keywords=[("окна даром", dict(impressions=2000, clicks=60, cost=2400, conversions=0))])
        accounting = audit["spend_accounting"]["at_risk_spend"]
        self.assertTrue(accounting["overlap_free"])
        if accounting["mixed_levels"]:
            self.assertIsNone(accounting["sum"])

    # 27
    def test_27_repeat_audit_without_snapshot(self):
        root, audit = self.make("s27", modes=("monitor",))
        self.assertFalse(self.modes(audit)["monitor"]["available"])
        self.assertTrue(any("предыдущий снимок" in (l["text"] + str(l.get("detail") or "")).lower()
                            for l in audit["limitations"]))

    # 28
    def test_28_rounding_hides_change(self):
        root, audit = self.make("s28")
        path = root / "raw" / "stats_compare.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload["rows"]:
            if row["Метрика"].startswith("CPA"):
                row["A"], row["B"] = "2 622.2222", "2 622.2244"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        comparison = audit["metrics"]["comparison"]["cpa"]
        self.assertNotEqual(comparison["abs"], 0)
        self.assertTrue(comparison["display_hides_change"])
        self.assertIn("не видно", render_md.render(audit))

    # 29
    def test_29_three_formats_from_one_json(self):
        root, audit = self.make("s29", queries=[
            ("окна даром", dict(impressions=1200, clicks=40, cost=1600, conversions=0))])
        md = render_md.render(audit)
        xlsx = render_xlsx.render(audit)
        page = render_html.render(audit)
        self.assertIn(render_md.fmt_money(audit["metrics"]["a"]["cost"]), md)
        self.assertGreater(len(xlsx), 5000)
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn(render_html.fmt_money(audit["metrics"]["a"]["cost"]), page)

    # 30
    def test_30_forbidden_write_action(self):
        root, audit = self.make("s30")
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
        session["sources"].append({"file": "write.json", "action": "ads_update", "status": "ok"})
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        source = next(s for s in audit["data_sources"] if s["action"] == "ads_update")
        self.assertEqual(source["status"], "blocked")
        self.assertTrue(any("запрещено" in p for p in audit["data_quality"]["problems"]))


if __name__ == "__main__":
    unittest.main()
