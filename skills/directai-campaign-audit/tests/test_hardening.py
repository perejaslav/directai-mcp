#!/usr/bin/env python3
"""Регрессии по итогам приёмки: блокировки, действия, снимки, аккаунт, полнота."""
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
from auditlib import build, validate  # noqa: E402
from auditlib.monitor import compatibility  # noqa: E402

TODAY = datetime.date(2026, 10, 23)


class HardeningCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-hard-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def session(self, name="s", **kwargs):
        kwargs.setdefault("modes", ["deep"])
        return factory.make_session(self.tmp / name, **kwargs)

    def audit(self, name="s", **kwargs):
        return build.build(self.session(name, **kwargs), today=TODAY)

    def rewrite_session(self, root: Path, mutate):
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
        mutate(session)
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")


class TestValidatorBlocking(HardeningCase):
    def test_blocked_status_without_checks_is_fail(self):
        audit = self.audit("v1")
        audit["data_quality"]["status"] = "blocked"
        result = validate.validate_doc(audit)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("DQ-05", {issue["code"] for issue in result["issues"]})

    def test_critical_check_is_fail(self):
        audit = self.audit("v2")
        for check in audit["data_quality"]["checks"]:
            if check["id"] == "DQ-ACC-01":
                check["status"] = "blocked"
                check["blocks"] = ["all"]
        audit["data_quality"]["status"] = "blocked"
        result = validate.validate_doc(audit)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("DQ-07", {issue["code"] for issue in result["issues"]})

    def test_section_check_is_warn(self):
        audit = self.audit("v3")
        for check in audit["data_quality"]["checks"]:
            if check["id"] == "DQ-API-01":
                check["status"] = "blocked"
                check["blocks"] = ["segments", "rsy"]
        audit["data_quality"]["status"] = "blocked"
        result = validate.validate_doc(audit)
        self.assertEqual(result["status"], "WARN")
        self.assertIn("DQ-03", {issue["code"] for issue in result["issues"]})

    def test_status_passed_with_warnings_is_fail(self):
        audit = self.audit("v4")
        for check in audit["data_quality"]["checks"]:
            if check["id"] == "DQ-API-01":
                check["status"] = "warning"
        audit["data_quality"]["status"] = "passed"
        result = validate.validate_doc(audit)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("DQ-06", {issue["code"] for issue in result["issues"]})


class TestSourceScope(HardeningCase):
    def test_foreign_campaign_source_blocks(self):
        root = self.session("c1")
        self.rewrite_session(root, lambda s: s["sources"].append(
            {"file": "stats_compare.json", "action": "stats_compare",
             "params": {"campaign_ids": [999999999]}, "status": "ok"}))
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-CMP-02")
        self.assertEqual(check["status"], "blocked")
        self.assertEqual(audit["validation"]["status"], "FAIL")
        source = next(s for s in audit["data_sources"] if s["params"].get("campaign_ids") == [999999999])
        self.assertEqual(source["status"], "blocked")

    def test_stats_source_without_campaign_scope_is_warning(self):
        root = self.session("c2")
        self.rewrite_session(root, lambda s: [src.pop("params", None) for src in s["sources"]
                                              if src["action"] == "stats_placements"])
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-CMP-02")
        self.assertEqual(check["status"], "warning")
        self.assertIn("stats_placements", check["detail"])


class TestUnknownActions(HardeningCase):
    def test_unknown_action_is_blocked_and_fails_validation(self):
        root = self.session("u1")
        self.rewrite_session(root, lambda s: s["sources"].append(
            {"file": "stats_compare.json", "action": "mystery_stats", "status": "ok"}))
        audit = build.build(root, today=TODAY)
        source = next(s for s in audit["data_sources"] if s["action"] == "mystery_stats")
        self.assertEqual(source["status"], "blocked")
        self.assertFalse(source["known_action"])
        self.assertEqual(audit["validation"]["status"], "FAIL")
        self.assertIn("SAFE-03", {i["code"] for i in audit["validation"]["issues"]})

    def test_unknown_action_does_not_feed_analysis(self):
        root = self.session("u2", queries=[
            ("окна даром", dict(impressions=2000, clicks=60, cost=2400, conversions=0))])
        # Подменяем действие у файла запросов на незарегистрированное.
        path = root / "raw" / "stats_search_queries.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["meta"]["action"] = "stats_queries_v99"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.rewrite_session(root, lambda s: [src.update({"action": "stats_queries_v99"})
                                              for src in s["sources"]
                                              if src["action"] == "stats_search_queries"])
        audit = build.build(root, today=TODAY)
        self.assertEqual(audit["segments"]["search_queries"]["available"], False)
        self.assertNotIn("SQ-01", {f["rule_id"] for f in audit["findings"]})


class TestMonitorCompatibility(HardeningCase):
    def write(self, audit: dict, name: str) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
        return path

    def test_compatibility_detects_other_campaign(self):
        first = self.audit("m1", campaign_id="900000001")
        second = self.audit("m2", campaign_id="900000002")
        result = compatibility(first, second)
        self.assertFalse(result["compatible"])
        self.assertTrue(any("Кампания" in reason for reason in result["reasons"]))

    def test_compatibility_detects_other_account(self):
        first = self.audit("m3", login="agency-one")
        second = self.audit("m4", login="agency-two")
        result = compatibility(first, second)
        self.assertFalse(result["compatible"])
        self.assertTrue(any("Кабинет" in reason for reason in result["reasons"]))

    def test_compatibility_detects_goal_and_attribution(self):
        first = self.audit("m5", goal=("11111111", "Заявка"))
        second = self.audit("m6", goal=("22222222", "Покупка"))
        self.assertFalse(compatibility(first, second)["compatible"])
        third = self.audit("m7", attribution="FIRST_CLICK")
        self.assertFalse(compatibility(first, third)["compatible"])

    def test_missing_goal_in_one_snapshot_blocks(self):
        first = self.audit("g1")
        broken = self.audit("g2")
        broken["goal"] = {"id": None, "label": "", "source": "none",
                          "configured": False, "value_rub": None}
        result = compatibility(first, broken)
        self.assertFalse(result["compatible"])
        self.assertFalse(result["conversion_comparable"])
        self.assertTrue(any("Основная цель" in reason for reason in result["reasons"]))

    def test_missing_attribution_in_both_blocks(self):
        first = self.audit("g3")
        second = self.audit("g4")
        for audit in (first, second):
            audit["attribution"] = {"requested": None, "effective": None, "source": None}
        result = compatibility(first, second)
        self.assertFalse(result["compatible"])
        self.assertFalse(result["conversion_comparable"])
        self.assertTrue(any("атрибуци" in reason.lower() for reason in result["reasons"]))

    def test_both_goals_and_attribution_match(self):
        first = self.audit("g5")
        second = self.audit("g6")
        result = compatibility(first, second)
        self.assertTrue(result["compatible"])
        self.assertTrue(result["conversion_comparable"])

    def test_build_blocks_monitor_when_previous_has_no_goal(self):
        first = self.audit("g7", goal=None)
        p = self.write(first, "prev-no-goal.json")
        root = self.session("g8", previous=p)
        second = build.build(root, today=TODAY)
        self.assertFalse(second["mode_status"]["monitor"]["available"])
        self.assertIn("Основная цель", second["mode_status"]["monitor"]["reason"])
        self.assertNotIn("MON-01", {f["rule_id"] for f in second["findings"]})
        self.assertFalse(second["monitor_compatibility"]["conversion_comparable"])

    def test_cli_rejects_snapshot_without_goal(self):
        first = self.audit("g9")
        broken = self.audit("g10", goal=None)
        p1, p2 = self.write(first, "goal-a.json"), self.write(broken, "goal-b.json")
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "monitor_compare.py"),
                                 str(p1), str(p2), "--json"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertFalse(payload["comparable"])
        self.assertFalse(payload["compatibility"]["conversion_comparable"])
        self.assertEqual(payload["metric_deltas"], {})

    def test_cli_rejects_incompatible_snapshots(self):
        first = self.audit("m8", campaign_id="900000001")
        second = self.audit("m9", campaign_id="900000002")
        p1, p2 = self.write(first, "a1.json"), self.write(second, "a2.json")
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "monitor_compare.py"),
                                 str(p1), str(p2), "--json"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertIn("несопоставимы", result.stdout)
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertFalse(payload["comparable"])
        self.assertEqual(payload["metric_deltas"], {})

    def test_build_ignores_incompatible_previous(self):
        first = self.audit("m10", campaign_id="900000001")
        p = self.write(first, "prev.json")
        root = self.session("m11", campaign_id="900000777", previous=p)
        second = build.build(root, today=TODAY)
        self.assertFalse(second["mode_status"]["monitor"]["available"])
        self.assertIn("несовместим", second["mode_status"]["monitor"]["reason"])
        self.assertNotIn("MON-01", {f["rule_id"] for f in second["findings"]})
        self.assertTrue(any("несовместим" in l["text"] for l in second["limitations"]))

    def test_compatible_previous_is_used(self):
        first = self.audit("m12")
        p = self.write(first, "prev2.json")
        root = self.session("m13", previous=p)
        second = build.build(root, today=TODAY)
        self.assertTrue(second["mode_status"]["monitor"]["available"])
        self.assertTrue(second["monitor_compatibility"]["compatible"])


class TestAccountProof(HardeningCase):
    def test_meta_account_mismatch_blocks(self):
        root = self.session("a1")
        path = root / "raw" / "stats_compare.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["meta"]["account"] = "foreign-cabinet"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-ACC-02")
        self.assertEqual(check["status"], "blocked")
        self.assertEqual(audit["validation"]["status"], "FAIL")

    def test_missing_account_evidence_blocks(self):
        root = self.session("a2")
        for name in ("stats_compare.json", "campaigns_get.json", "stats_adgroups.json"):
            path = root / "raw" / name
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["meta"].pop("account", None)
            for row in payload["rows"]:
                row.pop("_account", None)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.rewrite_session(root, lambda s: s.update({"account_login": None}))
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-ACC-02")
        self.assertEqual(check["status"], "blocked")
        self.assertEqual(audit["validation"]["status"], "FAIL")

    def test_meta_account_recorded_in_sources(self):
        root = self.session("a3")
        audit = build.build(root, today=TODAY)
        login = audit["scope"]["account_login"]
        for source in audit["data_sources"]:
            if source["status"] == "ok":
                self.assertEqual(source["account"], login)


class TestIncompleteUploads(HardeningCase):
    def test_truncated_placements_block_exclusion(self):
        root = self.session(
            "t1",
            a=dict(impressions=400000, clicks=4000, cost=146000, conversions=100),
            b=dict(impressions=390000, clicks=3900, cost=144000, conversions=99),
            placements=[("bad.example", dict(impressions=90000, clicks=700, cost=4200, conversions=0))])

        def mark_truncated(session):
            for source in session["sources"]:
                if source["action"] == "stats_placements":
                    source["truncated"] = True

        self.rewrite_session(root, mark_truncated)
        audit = build.build(root, today=TODAY)
        self.assertNotIn("RSY-01", {f["rule_id"] for f in audit["findings"]})
        self.assertFalse(any(r["object"].get("id") == "bad.example"
                             for r in audit["recommendations"]))
        self.assertTrue(any("неполная" in l["text"] for l in audit["limitations"]))

    def test_incomplete_pagination_blocks_segments(self):
        root = self.session(
            "t2",
            devices=[("MOBILE", dict(impressions=50000, clicks=1500, cost=95000, conversions=10)),
                     ("DESKTOP", dict(impressions=50000, clicks=500, cost=25000, conversions=30))])
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
        for source in session["sources"]:
            if source["action"] == "stats_devices":
                source["pagination_complete"] = False
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")

        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-PAG-01")
        self.assertEqual(check["status"], "warning")
        self.assertIn("segments", check["blocks"])
        self.assertNotIn("SEG-01", {f["rule_id"] for f in audit["findings"]})

    def test_truncated_ads_list_blocks_extension_findings(self):
        root = self.session("t3", ads=[("123456789", ["DisplayUrlPath"])])
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
        for source in session["sources"]:
            if source["action"] == "ads_list":
                source["truncated"] = True
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        self.assertNotIn("EXT-01", {f["rule_id"] for f in audit["findings"]})
        self.assertTrue(any("ads_list" in l["text"] and "неполная" in l["text"]
                            for l in audit["limitations"]))


if __name__ == "__main__":
    unittest.main()
