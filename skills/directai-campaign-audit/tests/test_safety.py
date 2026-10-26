#!/usr/bin/env python3
"""Безопасность: запрет записи, секреты, .gitignore (ТЗ §15, §4.7)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import factory  # noqa: E402
from auditlib import build, render_html, render_md, render_xlsx  # noqa: E402
from auditlib.rawio import FORBIDDEN_ACTIONS, READ_ONLY_ACTIONS  # noqa: E402

NETWORK_IMPORTS = ("import requests", "import httpx", "import urllib", "import socket",
                   "import aiohttp", "http.client", "from urllib")
CALL_PATTERNS = (
    r"plan_write\s*\(", r"apply_write\s*\(", r"run_read\s*\(", r"tools\[",
    r"api\.direct\.yandex", r"lidfly",
)


def script_files() -> list[Path]:
    return sorted(SCRIPTS.rglob("*.py"))


class TestNoWriteAccess(unittest.TestCase):
    def test_allowlist_has_no_write_actions(self):
        self.assertEqual(READ_ONLY_ACTIONS & FORBIDDEN_ACTIONS, set())
        for action in ("negatives_set", "ads_update", "campaigns_update", "bid_modifiers_set"):
            self.assertNotIn(action, READ_ONLY_ACTIONS)

    def test_scripts_do_not_call_mcp(self):
        offenders = []
        for path in script_files():
            text = path.read_text(encoding="utf-8")
            for pattern in CALL_PATTERNS:
                if re.search(pattern, text) and not path.name.endswith(".md"):
                    # упоминания в реестре запрещённых действий — это данные, а не вызовы
                    for line in text.splitlines():
                        if re.search(pattern, line) and not line.strip().startswith(("'", '"', "#")):
                            offenders.append(f"{path.name}: {line.strip()[:80]}")
        self.assertEqual(offenders, [])

    def test_scripts_do_not_use_network(self):
        offenders = []
        for path in script_files():
            text = path.read_text(encoding="utf-8")
            for needle in NETWORK_IMPORTS:
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")
        self.assertEqual(offenders, [])

    def test_build_rejects_forbidden_source(self):
        tmp = Path(tempfile.mkdtemp(prefix="audit-safety-"))
        try:
            root = factory.make_session(tmp / "s", modes=["deep"])
            session = json.loads((root / "session.json").read_text(encoding="utf-8"))
            session["sources"].append({"file": "x.json", "action": "campaigns_update", "status": "ok"})
            (root / "session.json").write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
            audit = build.build(root, today=__import__("datetime").date(2026, 10, 23))
            source = next(s for s in audit["data_sources"] if s["action"] == "campaigns_update")
            self.assertEqual(source["status"], "blocked")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_recommendations_never_execute(self):
        tmp = Path(tempfile.mkdtemp(prefix="audit-safety-"))
        try:
            root = factory.make_session(tmp / "s", modes=["deep"], placements=[
                ("bad.example", dict(impressions=90000, clicks=600, cost=7000, conversions=0))])
            audit = build.build(root, today=__import__("datetime").date(2026, 10, 23))
            for rec in audit["recommendations"]:
                self.assertEqual(rec["status"], "proposed")
                if rec["apply"]["method"] == "tool":
                    self.assertIn(rec["apply"]["tool"], FORBIDDEN_ACTIONS | READ_ONLY_ACTIONS
                                  | {"negatives_set", "ads_update", "extensions_create", "bid_modifiers_set",
                                     "campaigns_update"})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-journal-"))
        root = factory.make_session(self.tmp / "s", modes=["deep"],
                                    moderation=[("1923568665500247917", "REJECTED",
                                                 "превышение лимита заглавных")])
        self.audit = build.build(root, today=__import__("datetime").date(2026, 10, 23))
        self.path = self.tmp / "audit.json"
        self.path.write_text(json.dumps(self.audit, ensure_ascii=False), encoding="utf-8")
        self.journal = self.tmp / "journal.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "journal_append.py"), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_agent_cannot_record_decision(self):
        rec_id = (self.audit["recommendations"] or [{"id": "R-000"}])[0]["id"]
        result = self.run_cli(str(self.journal), "--audit", str(self.path),
                              "--recommendation", rec_id, "--decision", "accepted", "--by", "agent")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.journal.exists())

    def test_user_decision_is_recorded_and_scrubbed(self):
        rec_id = self.audit["recommendations"][0]["id"]
        result = self.run_cli(str(self.journal), "--audit", str(self.path),
                              "--recommendation", rec_id, "--decision", "accepted",
                              "--by", "Пользователь", "--comment", "api_key=SECRET123 принято")
        self.assertEqual(result.returncode, 0)
        line = json.loads(self.journal.read_text(encoding="utf-8").strip())
        self.assertEqual(line["decision"], "accepted")
        self.assertFalse(line["executed_by_agent"])
        self.assertNotIn("SECRET123", line["comment"])
        self.assertEqual(len(line["audit_sha256"]), 64)

    def test_unknown_recommendation_rejected(self):
        result = self.run_cli(str(self.journal), "--audit", str(self.path),
                              "--recommendation", "R-999", "--decision", "accepted", "--by", "Пользователь")
        self.assertEqual(result.returncode, 2)


class TestGitignore(unittest.TestCase):
    def test_patterns_cover_generated_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="audit-git-"))
        try:
            subprocess.run(["git", "init", "-q", str(tmp)], check=True, capture_output=True)
            shutil.copy2(ROOT / ".gitignore", tmp / ".gitignore")
            expected_ignored = ["reports/audit_demo.md", "raw/stats_compare.json",
                                "exports/campaigns_list.csv", "tests/.tmp/session.json",
                                "scripts/__pycache__/x.pyc"]
            for name in expected_ignored:
                path = tmp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
            (tmp / "SKILL.md").write_text("x", encoding="utf-8")

            for name in expected_ignored:
                result = subprocess.run(["git", "-C", str(tmp), "check-ignore", "-q", name])
                self.assertEqual(result.returncode, 0, f"{name} должен быть в .gitignore")

            for name in ("SKILL.md", "scripts/build_audit.py", "references/checklist.md"):
                result = subprocess.run(["git", "-C", str(tmp), "check-ignore", "-q", name])
                self.assertEqual(result.returncode, 1, f"{name} не должен игнорироваться")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNoSecrets(unittest.TestCase):
    def test_reports_contain_no_tokens(self):
        tmp = Path(tempfile.mkdtemp(prefix="audit-secret-"))
        try:
            root = factory.make_session(tmp / "s", modes=["deep"], metrika=dict(visits=1500))
            audit = build.build(root, today=__import__("datetime").date(2026, 10, 23))
            blobs = [json.dumps(audit, ensure_ascii=False), render_md.render(audit),
                     render_html.render(audit)]
            for blob in blobs:
                for pattern in (r"Bearer\s+\w+", r"y0_[A-Za-z0-9_-]{10,}",
                                r"(?i)(token|api[_-]?key|secret)\s*[:=]\s*\S+"):
                    self.assertIsNone(re.search(pattern, blob), pattern)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_xlsx_has_no_secrets(self):
        tmp = Path(tempfile.mkdtemp(prefix="audit-secret-"))
        try:
            root = factory.make_session(tmp / "s", modes=["deep"])
            audit = build.build(root, today=__import__("datetime").date(2026, 10, 23))
            data = render_xlsx.render(audit)
            self.assertNotIn(b"Bearer", data)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
