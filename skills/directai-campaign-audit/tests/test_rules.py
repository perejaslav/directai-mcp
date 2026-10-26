#!/usr/bin/env python3
"""Правила: регрессии ТЗ §4.2 и §4.5, приоритеты, деньги, сегменты."""
from __future__ import annotations

import datetime
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import factory  # noqa: E402
from auditlib import build  # noqa: E402
from auditlib.negatives import (classify_intent, conflicts_with_keywords,  # noqa: E402
                                fully_overlaps_keyword, negative_blocks,
                                suggest_negatives)

TODAY = datetime.date(2026, 10, 23)
MODES = ("deep",)


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def audit(self, name="s", **kwargs):
        kwargs.setdefault("modes", MODES)
        root = factory.make_session(self.tmp / name, **kwargs)
        return build.build(root, today=TODAY)

    @staticmethod
    def rules(audit):
        return [finding["rule_id"] for finding in audit["findings"]]

    def finding(self, audit, rule):
        return next((f for f in audit["findings"] if f["rule_id"] == rule), None)


class TestPlacementThreshold(AuditCase):
    """Регрессия §4.2: CPA 1460, порог 2920, расход 2700 — НЕ кандидат на исключение."""

    def test_below_threshold_is_watch_only(self):
        audit = self.audit(
            a=dict(impressions=400000, clicks=4000, cost=146000, conversions=100),
            b=dict(impressions=390000, clicks=3900, cost=142000, conversions=98),
            placements=[("video-fun.example", dict(impressions=60000, clicks=300, cost=2700, conversions=0))],
        )
        cpa = audit["metrics"]["a"]["cpa"]
        self.assertAlmostEqual(cpa, 1460.0, places=2)
        threshold = audit["thresholds"]["attention_threshold_rub"]
        self.assertAlmostEqual(threshold, 2920.0, places=2)
        self.assertIn("RSY-02", self.rules(audit))
        self.assertNotIn("RSY-01", self.rules(audit))
        for rec in audit["recommendations"]:
            self.assertNotEqual(rec["object"].get("id"), "video-fun.example",
                                "площадка ниже порога не должна попадать в рекомендации")
        at_risk = audit["spend_accounting"]["at_risk_spend"]
        self.assertNotIn("video-fun.example", [o["path"] for o in at_risk["objects"]])
        self.assertTrue(any("ниже порога" in f["why"] for f in audit["findings"]))

    def test_above_threshold_is_exclusion_candidate(self):
        audit = self.audit(
            a=dict(impressions=400000, clicks=4000, cost=146000, conversions=100),
            b=dict(impressions=390000, clicks=3900, cost=142000, conversions=98),
            placements=[("bad-site.example", dict(impressions=80000, clicks=500, cost=3200, conversions=0))],
        )
        finding = self.finding(audit, "RSY-01")
        self.assertIsNotNone(finding)
        rec = next(r for r in audit["recommendations"] if r["object"].get("id") == "bad-site.example")
        self.assertEqual(rec["expected_effect"]["type"], "estimate")
        self.assertTrue(rec["expected_effect"]["conditions"])
        self.assertEqual(rec["apply"]["method"], "manual")

    def test_threshold_is_named_as_attention(self):
        audit = self.audit(placements=[("p.example", dict(impressions=5000, clicks=80, cost=1600, conversions=0))])
        finding = self.finding(audit, "RSY-01") or self.finding(audit, "RSY-02")
        self.assertIn("порог внимания", finding["threshold"]["meaning"])


class TestNegativePhrases(unittest.TestCase):
    def test_job_query_is_not_blocked_by_vacancy_word(self):
        result = negative_blocks("вакансии", "работа монтажником окон")
        self.assertFalse(result["blocks"])
        self.assertEqual(result["missing"], ["вакансии"])
        self.assertIn("нет слов", result["reason"])

    def test_all_words_present_blocks(self):
        result = negative_blocks("окна картинки", "красивые окна картинки скачать")
        self.assertTrue(result["blocks"])
        self.assertFalse(result["missing"])

    def test_partial_match_does_not_block(self):
        self.assertFalse(negative_blocks("окна даром", "окна недорого")["blocks"])

    def test_full_overlap_with_keyword_is_ignored(self):
        self.assertTrue(fully_overlaps_keyword("купить слона", "купить розового слона"))
        self.assertFalse(fully_overlaps_keyword("купить недорого", "купить розового слона"))

    def test_conflict_detection(self):
        conflicts = conflicts_with_keywords("даром", ["окна даром", "окна пвх"])
        kinds = {c["keyword"]: c["kind"] for c in conflicts}
        # Все слова минуса входят в фразу -> Яндекс игнорирует минус.
        self.assertEqual(kinds["окна даром"], "ignored_full_overlap")
        self.assertNotIn("окна пвх", kinds)

    def test_does_not_invent_words_outside_query(self):
        suggestion = suggest_negatives(
            "работа монтажником окон", keywords=["окна пвх"],
            protected_words=["цена"], exclude_terms=["вакансии", "работа"])
        candidates = {c["candidate"]: c for c in suggestion["candidates"]}
        self.assertNotIn("вакансии", candidates)
        for candidate in suggestion["usable"]:
            self.assertIn(candidate["candidate"], suggestion["query_words"])

    def test_overlapping_negative_is_useless_not_dangerous(self):
        suggestion = suggest_negatives(
            "окна даром", keywords=["окна даром"], protected_words=[],
            exclude_terms=["даром"])
        verdicts = {c["candidate"]: c["verdict"] for c in suggestion["candidates"]}
        self.assertEqual(verdicts.get("даром"), "ignored_by_keyword")

    def test_converting_query_protects_word(self):
        suggestion = suggest_negatives(
            "окна даром", keywords=["окна пвх"], protected_words=[],
            exclude_terms=["даром"], converting_queries=["окна даром купить"])
        verdicts = {c["candidate"]: c["verdict"] for c in suggestion["candidates"]}
        self.assertEqual(verdicts.get("даром"), "blocks_converting_query")

    def test_protected_word_is_not_suggested(self):
        suggestion = suggest_negatives(
            "окна цена", keywords=["окна пвх"], protected_words=["цена"], exclude_terms=["цена"])
        verdicts = {c["candidate"]: c["verdict"] for c in suggestion["candidates"]}
        self.assertEqual(verdicts.get("цена"), "protected_word")

    def test_informational_intent_is_not_irrelevant(self):
        result = classify_intent("как установить окна своими руками")
        self.assertEqual(result["intent"], "informational")
        self.assertNotEqual(result["intent"], "irrelevant")

    def test_vacancy_intent_requires_context(self):
        with_context = classify_intent("вакансии монтажник окон",
                                       product_terms={"exclude_terms": ["вакансии"]})
        self.assertEqual(with_context["intent"], "irrelevant")


class TestSearchRules(AuditCase):
    def test_junk_query_without_context_is_not_minused(self):
        audit = self.audit(
            product={},
            queries=[("работа монтажником окон",
                      dict(impressions=2000, clicks=60, cost=2400, conversions=0))])
        finding = self.finding(audit, "SQ-01")
        self.assertIsNotNone(finding)
        self.assertEqual(finding["classification"], "fact")
        rec = next(r for r in audit["recommendations"] if r["finding_id"] == finding["id"])
        self.assertEqual(rec["apply"]["method"], "manual")
        self.assertNotIn("negatives_set", str(rec["apply"]))

    def test_irrelevant_query_with_context_gets_verified_negative(self):
        audit = self.audit(
            product={"what": "окна ПВХ", "exclude_terms": ["вакансии", "работа"],
                     "protected_terms": ["цена", "отзывы"]},
            queries=[("вакансии окна", dict(impressions=1500, clicks=70, cost=2100, conversions=0))],
        )
        rec = next((r for r in audit["recommendations"]
                    if r["object"].get("type") == "search_query"), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["apply"]["tool"], "negatives_set")
        self.assertIn("минус-фраз", rec["proposed_change"])

    def test_informational_query_is_not_minused(self):
        audit = self.audit(queries=[
            ("как мыть окна", dict(impressions=3000, clicks=90, cost=2700, conversions=0)),
        ])
        recs = [r for r in audit["recommendations"] if r["object"].get("type") == "search_query"]
        self.assertFalse(any(r["apply"].get("tool") == "negatives_set" for r in recs))
        self.assertTrue(any(h["title"].startswith("Информационный") for h in audit["hypotheses"]))

    def test_hot_query_with_good_cpa_becomes_growth(self):
        audit = self.audit(
            queries=[("купить окна пвх", dict(impressions=5000, clicks=150, cost=9000, conversions=9))],
        )
        self.assertTrue(any(g["type"] == "search_intent" for g in audit["growth"]))


class TestDynamics(AuditCase):
    def test_decline_is_fact_plus_alternatives(self):
        audit = self.audit(
            a=dict(impressions=100000, clicks=2000, cost=120000, conversions=26),
            b=dict(impressions=99000, clicks=1990, cost=118000, conversions=40),
        )
        finding = self.finding(audit, "DYN-01")
        self.assertIsNotNone(finding)
        self.assertEqual(finding["classification"], "likely_cause")
        self.assertTrue(finding["alternatives"])
        text = str(finding["why"]) + " " + " ".join(finding["alternatives"])
        self.assertNotIn("проблема в сайте", text.lower())

    def test_small_sample_is_not_called_a_problem(self):
        audit = self.audit(
            a=dict(impressions=900, clicks=20, cost=2000, conversions=1),
            b=dict(impressions=850, clicks=18, cost=1800, conversions=3),
        )
        self.assertIn("STAT-01", self.rules(audit))
        self.assertNotIn("DYN-01", self.rules(audit))

    def test_zero_base_does_not_produce_infinity(self):
        audit = self.audit(
            a=dict(impressions=50000, clicks=1000, cost=60000, conversions=12),
            b=dict(impressions=48000, clicks=960, cost=58000, conversions=0),
        )
        comparison = audit["metrics"]["comparison"]["conversions"]
        self.assertTrue(comparison["base_zero"])
        self.assertIsNone(comparison["pct"])
        self.assertIn("DYN-00", self.rules(audit))


class TestSegments(AuditCase):
    def test_incompatible_segment_sum_blocks_comparison(self):
        audit = self.audit(devices=[
            ("MOBILE", dict(impressions=40000, clicks=900, cost=60000, conversions=8)),
        ])
        self.assertIn("SEG-00", self.rules(audit))
        self.assertNotIn("SEG-01", self.rules(audit))
        checked = audit["segments_checked"]
        self.assertTrue(any(not item["comparable"] for item in checked))

    def test_compatible_segment_with_high_cpa_is_flagged(self):
        audit = self.audit(devices=[
            ("MOBILE", dict(impressions=50000, clicks=1500, cost=95000, conversions=10)),
            ("DESKTOP", dict(impressions=50000, clicks=500, cost=25000, conversions=30)),
        ])
        self.assertIn("SEG-01", self.rules(audit))
        self.assertTrue(any(item.get("comparable") for item in audit["segments_checked"]))


class TestMoneyAndPriority(AuditCase):
    def test_overlapping_objects_are_not_summed(self):
        audit = self.audit(
            queries=[("окна даром", dict(impressions=2000, clicks=60, cost=2400, conversions=0))],
            keywords=[("окна даром", dict(impressions=2000, clicks=60, cost=2400, conversions=0))],
        )
        at_risk = audit["spend_accounting"]["at_risk_spend"]
        self.assertTrue(at_risk["overlap_free"])
        types = {o["type"] for o in at_risk["objects"]}
        if len(types) > 1:
            self.assertIsNone(at_risk["sum"])
            self.assertTrue(at_risk["mixed_levels"])
            self.assertEqual(sorted(at_risk["sum_by_type"].values()), [2400.0, 2400.0])
        else:
            self.assertEqual([o["value"] for o in at_risk["objects"]], [2400.0])

    def test_saving_is_conditional(self):
        audit = self.audit(
            a=dict(impressions=400000, clicks=4000, cost=146000, conversions=100),
            b=dict(impressions=390000, clicks=3900, cost=142000, conversions=98),
            placements=[("bad.example", dict(impressions=80000, clicks=500, cost=3200, conversions=0))],
        )
        saving = audit["spend_accounting"]["potential_saving"]
        self.assertTrue(saving["conditional"])
        self.assertIn("не гарантия", saving["method"])
        self.assertIn("оценка", audit["spend_accounting"]["note"].lower())

    def test_low_confidence_is_not_high_priority(self):
        audit = self.audit(queries=[
            ("странный запрос", dict(impressions=100, clicks=1, cost=1500, conversions=0)),
        ])
        findings = [f for f in audit["findings"] if f["rule_id"] == "SQ-01"]
        self.assertTrue(all(f["priority"] in ("P2", "P3") for f in findings))


class TestTrackingAndModeration(AuditCase):
    def test_missing_counter_is_p0(self):
        audit = self.audit(counters=())
        finding = self.finding(audit, "TRK-01")
        self.assertEqual(finding["priority"], "P0")
        self.assertEqual(finding["confidence"], "high")

    def test_missing_goal_blocks_cpa(self):
        audit = self.audit(goal=None)
        self.assertIn("TRK-02", self.rules(audit))
        self.assertIn("cpa", audit["data_quality"]["gates"])
        self.assertFalse(any("CPA" in rec["proposed_change"] and rec["apply"].get("tool") == "bid_modifiers_set"
                             for rec in audit["recommendations"]))

    def test_deprecated_vcard_is_not_required(self):
        audit = self.audit(ads=[("123456789", ["VCardId"])])
        self.assertNotIn("EXT-01", {f["rule_id"] for f in audit["findings"]})

    def test_extension_text_matches_missing_field(self):
        audit = self.audit(
            strategy="поиск: WB_MAXIMUM_CLICKS; сети: WB_MAXIMUM_CONVERSION_RATE",
            ads=[("123456789", ["Images"])])
        finding = next(f for f in audit["findings"] if f["rule_id"] == "EXT-01")
        self.assertIn("изображения", finding["why"])
        rec = next(r for r in audit["recommendations"] if r["finding_id"] == finding["id"])
        self.assertIn("изображения", rec["proposed_change"])
        self.assertNotIn("визитк", rec["proposed_change"].lower())

    def test_truncated_strategy_does_not_require_images(self):
        audit = self.audit(
            strategy="поиск: HIGHEST_POSITION; PlacementTypes: {SearchResults=YES} / сети: SERVIN…",
            ads=[("123456789", ["Images"])])
        self.assertNotIn("EXT-01", {f["rule_id"] for f in audit["findings"]})
        self.assertTrue(any("обрезан" in l["text"] for l in audit["limitations"]))

    def test_images_not_required_for_search_only(self):
        audit = self.audit(ads=[("123456789", ["Images"])])
        self.assertNotIn("EXT-01", {f["rule_id"] for f in audit["findings"]})
        self.assertTrue(any("Изображения не проверялись" in l["text"] for l in audit["limitations"]))

    def test_rejected_ad_is_p0(self):
        audit = self.audit(moderation=[("1923568665500247917", "REJECTED", "превышение лимита заглавных")])
        finding = self.finding(audit, "MOD-01")
        self.assertEqual(finding["priority"], "P0")

    def test_archived_campaign_without_stats_says_unavailable(self):
        audit = self.audit(state="ARCHIVED", sources=["campaigns_get.json"])
        finding = self.finding(audit, "CMP-01")
        self.assertIn("статистика за период недоступна", finding["title"])
        self.assertIn("не получены", finding["why"])
        self.assertNotIn("показов нет", finding["title"])

    def test_archived_campaign_with_zero_stats_says_no_impressions(self):
        audit = self.audit(
            state="ARCHIVED",
            a=dict(impressions=0, clicks=0, cost=0, conversions=0),
            b=dict(impressions=0, clicks=0, cost=0, conversions=0))
        finding = self.finding(audit, "CMP-01")
        self.assertIn("показов нет", finding["title"])

    def test_archived_campaign_is_reported(self):
        audit = self.audit(state="ARCHIVED")
        finding = self.finding(audit, "CMP-01")
        self.assertIsNotNone(finding)
        self.assertEqual(finding["priority"], "P1")
        self.assertTrue(any(r["finding_id"] == finding["id"] for r in audit["recommendations"]))

    def test_disabled_campaign_is_p0(self):
        audit = self.audit(state="SUSPENDED")
        self.assertEqual(self.finding(audit, "CMP-01")["priority"], "P0")

    def test_click_visit_gap_is_likely_cause(self):
        audit = self.audit(metrika=dict(visits=800))
        finding = self.finding(audit, "TRK-03")
        self.assertIsNotNone(finding)
        self.assertEqual(finding["classification"], "likely_cause")
        self.assertTrue(finding["alternatives"])


if __name__ == "__main__":
    unittest.main()
