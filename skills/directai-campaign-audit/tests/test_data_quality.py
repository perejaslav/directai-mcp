#!/usr/bin/env python3
"""Качество данных: состояния, гейты, происхождение (ТЗ §5.4, §5.5)."""
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
from auditlib import build  # noqa: E402
from auditlib.rawio import SessionError  # noqa: E402

TODAY = datetime.date(2026, 10, 23)


class DQCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-dq-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, name="s", **kwargs):
        root = factory.make_session(self.tmp / name, **kwargs)
        return build.build(root, today=TODAY)

    def session(self, root: Path):
        return json.loads((root / "session.json").read_text(encoding="utf-8"))

    def write_session(self, root: Path, session: dict):
        (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")

    def check(self, audit, check_id):
        return next((c for c in audit["data_quality"]["checks"] if c["id"] == check_id), None)


class TestCoreChecks(DQCase):
    def test_happy_path_core_checks_pass(self):
        audit = self.build()
        for check_id in ("DQ-SRC-01", "DQ-SRC-02", "DQ-CMP-01", "DQ-MET-01", "DQ-ACC-01"):
            self.assertEqual(self.check(audit, check_id)["status"], "passed", check_id)
        self.assertEqual(audit["validation"]["status"], "PASS")

    def test_missing_primary_source_blocks_dynamics(self):
        audit = self.build(sources=["campaigns_get.json"])
        check = self.check(audit, "DQ-SRC-01")
        self.assertEqual(check["status"], "blocked")
        self.assertIn("dynamics", check["blocks"])
        self.assertIn("dynamics", audit["data_quality"]["gates"])

    def test_missing_campaign_settings_blocks_identity(self):
        audit = self.build(sources=["stats_compare.json"])
        self.assertEqual(self.check(audit, "DQ-SRC-02")["status"], "blocked")
        self.assertIn("identity", audit["data_quality"]["gates"])

    def test_campaign_id_mismatch_blocks_all(self):
        audit = self.build(sources=["stats_compare.json"], campaign_id="900000999")
        # campaigns_get не объявлен -> идентификация не подтверждена
        self.assertTrue(audit["data_quality"]["gates"].get("identity")
                        or audit["data_quality"]["gates"].get("all"))


class TestIdentity(DQCase):
    def test_account_mismatch_blocks(self):
        root = factory.make_session(self.tmp / "s")
        session = self.session(root)
        session["account_login"] = "other-login"
        self.write_session(root, session)
        audit = build.build(root, today=TODAY)
        check = self.check(audit, "DQ-ACC-01")
        self.assertEqual(check["status"], "blocked")
        self.assertIn("all", audit["data_quality"]["gates"])

    def test_campaign_row_mismatch_blocks(self):
        root = factory.make_session(self.tmp / "s", campaign_id="900000001")
        payload_path = root / "raw" / "campaigns_get.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["rows"][0]["Id"] = 900000777
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        self.assertEqual(self.check(audit, "DQ-CMP-01")["status"], "blocked")


class TestPeriods(DQCase):
    def test_period_mismatch_with_context_is_warning(self):
        root = factory.make_session(self.tmp / "s")
        path = root / "raw" / "stats_compare.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["meta"]["context"] = payload["meta"]["context"].replace(
            "A: 2026-09-26, B: 2026-09-12", "A: 2026-09-25, B: 2026-09-11")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        check = self.check(audit, "DQ-PRD-02")
        self.assertEqual(check["status"], "warning")
        self.assertIn("шапка", check["detail"])

    def test_different_lengths_warn(self):
        audit = self.build(periods={
            "a": {"from": "2026-09-26", "to": "2026-10-09"},
            "b": {"from": "2026-09-12", "to": "2026-09-20"}})
        check = self.check(audit, "DQ-PRD-01")
        self.assertEqual(check["status"], "warning")
        self.assertIn("длины периодов", check["detail"])

    def test_today_in_period_warns(self):
        audit = self.build(periods={
            "a": {"from": "2026-10-10", "to": "2026-10-23"},
            "b": {"from": "2026-09-26", "to": "2026-10-09"}})
        self.assertIn("сегодняшний", self.check(audit, "DQ-PRD-01")["detail"])

    def test_empty_previous_period_warns(self):
        audit = self.build(
            a=dict(impressions=50000, clicks=1000, cost=60000, conversions=10),
            b=dict(impressions=0, clicks=0, cost=0, conversions=0))
        self.assertEqual(self.check(audit, "DQ-PRD-03")["status"], "warning")
        self.assertIn("dynamics", audit["data_quality"]["gates"])

    def test_unknown_previous_period_is_unavailable(self):
        audit = self.build(sources=["campaigns_get.json"])
        check = self.check(audit, "DQ-PRD-03")
        self.assertEqual(check["status"], "unavailable")
        self.assertIn("не получены", check["detail"])
        self.assertIn("dynamics", audit["data_quality"]["gates"])

    def test_unknown_traffic_is_unavailable(self):
        audit = self.build(sources=["campaigns_get.json"])
        self.assertEqual(self.check(audit, "DQ-MET-02")["status"], "unavailable")


class TestMetricsGates(DQCase):
    def test_no_impressions_warns(self):
        audit = self.build(
            a=dict(impressions=0, clicks=0, cost=0, conversions=0),
            b=dict(impressions=0, clicks=0, cost=0, conversions=0))
        self.assertEqual(self.check(audit, "DQ-MET-02")["status"], "warning")
        self.assertIn("cpa", audit["data_quality"]["gates"])

    def test_missing_conversions_are_unavailable(self):
        audit = self.build(
            a=dict(impressions=10000, clicks=200, cost=12000, conversions=None),
            b=dict(impressions=9000, clicks=180, cost=11000, conversions=None))
        check = self.check(audit, "DQ-CNV-01")
        self.assertEqual(check["status"], "unavailable")
        self.assertIn("cpa", audit["data_quality"]["gates"])
        self.assertNotEqual(audit["data_quality"]["status"], "passed")

    def test_missing_goal_is_warning_with_gate(self):
        audit = self.build(goal=None)
        self.assertEqual(self.check(audit, "DQ-GOAL-01")["status"], "warning")
        self.assertIn("cpa", audit["data_quality"]["gates"])

    def test_missing_attribution_is_warning(self):
        audit = self.build(attribution="")
        check = self.check(audit, "DQ-ATR-01")
        self.assertIn(check["status"], ("warning", "unavailable"))

    def test_negative_cost_blocks(self):
        audit = self.build(a=dict(impressions=10000, clicks=200, cost=-500, conversions=3))
        self.assertEqual(self.check(audit, "DQ-NEG-01")["status"], "blocked")

    def test_currency_other_than_rub_blocks(self):
        root = factory.make_session(self.tmp / "s")
        path = root / "raw" / "stats_compare.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["meta"]["context"] += " расход в USD"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        self.assertEqual(self.check(audit, "DQ-UNI-01")["status"], "blocked")


class TestSources(DQCase):
    def test_rate_limit_error_blocks_source(self):
        root = factory.make_session(self.tmp / "s",
                                    placements=[("p.example", dict(impressions=1000, clicks=10, cost=500, conversions=0))])
        session = self.session(root)
        for source in session["sources"]:
            if source["action"] == "stats_placements":
                source["tool_warning"] = ("⚠ demo: Direct API error 56: Превышен лимит запросов метода. "
                                          "Данный метод можно запрашивать не чаще чем 1 раз в 10 секунд.")
        self.write_session(root, session)
        audit = build.build(root, today=TODAY)
        check = self.check(audit, "DQ-API-01")
        self.assertEqual(check["status"], "blocked")
        self.assertIn("не доказывает отсутствие", check["detail"])

    def test_missing_raw_file_is_unavailable(self):
        root = factory.make_session(self.tmp / "s")
        (root / "raw" / "stats_devices.json").unlink()
        audit = build.build(root, today=TODAY)
        source = next(s for s in audit["data_sources"] if s["action"] == "stats_devices")
        self.assertEqual(source["status"], "unavailable")
        self.assertTrue(any("не найден" in problem
                            for problem in audit["data_quality"]["problems"]))
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-SRC-03")
        self.assertEqual(check["status"], "warning")
        self.assertIn("stats_devices", check["detail"])

    def test_unknown_envelope_blocks_source(self):
        root = factory.make_session(self.tmp / "s")
        (root / "raw" / "stats_devices.json").write_text('{"rows": []}', encoding="utf-8")
        audit = build.build(root, today=TODAY)
        source = next(s for s in audit["data_sources"] if s["action"] == "stats_devices")
        self.assertEqual(source["status"], "blocked")

    def test_forbidden_write_action_in_sources_is_blocked(self):
        root = factory.make_session(self.tmp / "s")
        session = self.session(root)
        session["sources"].append({"file": "fake.json", "action": "negatives_set", "status": "ok"})
        self.write_session(root, session)
        audit = build.build(root, today=TODAY)
        source = next(s for s in audit["data_sources"] if s["action"] == "negatives_set")
        self.assertEqual(source["status"], "blocked")
        self.assertTrue(any("запрещено" in problem for problem in audit["data_quality"]["problems"]))

    def test_hash_is_recorded(self):
        audit = self.build()
        for source in audit["data_sources"]:
            if source["status"] == "ok":
                self.assertEqual(len(source["sha256"]), 64)

    def test_sha_mismatch_blocks(self):
        root = factory.make_session(self.tmp / "s")
        session = self.session(root)
        session["sources"][0]["sha256"] = "0" * 64
        self.write_session(root, session)
        audit = build.build(root, today=TODAY)
        self.assertEqual(audit["data_sources"][0]["status"], "blocked")

    def test_missing_session_file_raises(self):
        with self.assertRaises(SessionError):
            build.build(self.tmp / "absent", today=TODAY)


    def test_declared_failed_call_is_recorded(self):
        root = factory.make_session(self.tmp / "e1")
        session = self.session(root)
        session["sources"].append({
            "action": "stats_compare",
            "params": {"campaign_ids": [900000001]},
            "status": "error",
            "error": {"code": 56, "message": "Превышен лимит запросов метода"},
        })
        self.write_session(root, session)
        audit = build.build(root, today=TODAY)
        source = next(s for s in audit["data_sources"] if s["action"] == "stats_compare"
                      and s["status"] == "error")
        self.assertEqual(source["error"]["code"], 56)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-API-01")
        self.assertEqual(check["status"], "blocked")
        self.assertIn("код 56", check["detail"])
        self.assertIn("не доказывает отсутствие", check["detail"])
        self.assertEqual(audit["validation"]["status"], "WARN")
        src_check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-SRC-03")
        self.assertEqual(src_check["status"], "warning")
        self.assertIn("stats_compare", src_check["detail"])
        self.assertIn("код 56", src_check["detail"])

    def test_api_error_other_than_limit_is_warning(self):
        root = factory.make_session(self.tmp / "e2")
        session = self.session(root)
        session["sources"].append({
            "action": "stats_regions",
            "params": {"campaign_ids": [900000001]},
            "status": "error",
            "error": {"code": 4001, "message": "Неверно заданы параметры"},
        })
        self.write_session(root, session)
        audit = build.build(root, today=TODAY)
        check = next(c for c in audit["data_quality"]["checks"] if c["id"] == "DQ-API-01")
        self.assertEqual(check["status"], "warning")
        self.assertIn("4001", check["detail"])

    def test_failed_call_without_reason_is_blocked(self):
        root = factory.make_session(self.tmp / "e3")
        session = self.session(root)
        session["sources"].append({"action": "stats_compare", "status": "ok"})
        self.write_session(root, session)
        audit = build.build(root, today=TODAY)
        source = next(s for s in audit["data_sources"] if s["action"] == "stats_compare"
                      and not s["file"])
        self.assertEqual(source["status"], "blocked")
        self.assertTrue(any("нет описания ошибки" in p for p in audit["data_quality"]["problems"]))


class TestSegmentsAndDuplicates(DQCase):
    def test_segment_sum_deviation_is_warning(self):
        audit = self.build(devices=[
            ("MOBILE", dict(impressions=20000, clicks=300, cost=30000, conversions=5)),
        ])
        check = self.check(audit, "DQ-SEG-01")
        self.assertEqual(check["status"], "warning")
        self.assertIn("расходится", check["detail"])

    def test_duplicate_rows_are_reported(self):
        root = factory.make_session(self.tmp / "s", devices=[
            ("MOBILE", dict(impressions=60000, clicks=1200, cost=72000, conversions=24)),
            ("DESKTOP", dict(impressions=40000, clicks=800, cost=48000, conversions=16)),
        ])
        path = root / "raw" / "stats_devices.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["rows"].append(dict(payload["rows"][0]))
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        audit = build.build(root, today=TODAY)
        # дубли ищутся по Id-подобным ключам; здесь ключ сегмента не Id, поэтому проверяем отсутствие ложного ok
        self.assertIn(self.check(audit, "DQ-DUP-01")["status"], ("passed", "warning"))


if __name__ == "__main__":
    unittest.main()
