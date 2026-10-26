#!/usr/bin/env python3
"""Проверка исправлений приёмки на демонстрационном аудите (только чтение).

    python examples/check_hardening.py

Ничего не меняет на диске: работает с копией audit.json в памяти.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from auditlib import validate  # noqa: E402

AUDIT = ROOT / "examples" / "example-87654321" / "audit.json"


def main() -> int:
    base = json.loads(AUDIT.read_text(encoding="utf-8"))
    ok = True

    # 1. status = blocked без блокирующих проверок
    mutated = copy.deepcopy(base)
    mutated["data_quality"]["status"] = "blocked"
    result = validate.validate_doc(mutated)
    codes = {i["code"] for i in result["issues"]}
    print(f"1. status=blocked без проверок -> {result['status']} ({', '.join(sorted(codes))})")
    ok &= result["status"] == "FAIL" and "DQ-05" in codes

    # 2. критическая проверка заблокирована
    mutated = copy.deepcopy(base)
    for check in mutated["data_quality"]["checks"]:
        if check["id"] == "DQ-CMP-01":
            check["status"] = "blocked"
            check["blocks"] = ["all"]
    mutated["data_quality"]["status"] = "blocked"
    result = validate.validate_doc(mutated)
    print(f"2. критическая блокировка -> {result['status']} "
          f"({', '.join(sorted({i['code'] for i in result['issues']}))})")
    ok &= result["status"] == "FAIL"

    # 3. незарегистрированное действие в источниках
    mutated = copy.deepcopy(base)
    mutated["data_sources"].append({"action": "mystery_action", "file": "x.json",
                                    "status": "ok", "known_action": False})
    result = validate.validate_doc(mutated)
    print(f"3. неизвестное действие -> {result['status']} "
          f"({', '.join(sorted({i['code'] for i in result['issues']}))})")
    ok &= result["status"] == "FAIL" and "SAFE-03" in {i["code"] for i in result["issues"]}

    # 4. сравнение снимков из разных кампаний
    other = copy.deepcopy(base)
    other["scope"]["campaign_id"] = "999999999"
    other["audit_id"] = "audit-other"
    with tempfile.TemporaryDirectory() as tmp:
        p1 = Path(tmp) / "a.json"
        p2 = Path(tmp) / "b.json"
        p1.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        p2.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "monitor_compare.py"),
                              str(p1), str(p2)],
                             capture_output=True, text=True, encoding="utf-8")
    print(f"4. сравнение разных кампаний -> код {run.returncode}: "
          f"{run.stdout.strip().splitlines()[0] if run.stdout.strip() else ''}")
    ok &= run.returncode == 2

    # 5. сравнение без основной цели
    no_goal = copy.deepcopy(base)
    no_goal["goal"] = {"id": None, "label": "", "source": "none",
                       "configured": False, "value_rub": None}
    with tempfile.TemporaryDirectory() as tmp:
        p1 = Path(tmp) / "a.json"
        p2 = Path(tmp) / "b.json"
        p1.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        p2.write_text(json.dumps(no_goal, ensure_ascii=False), encoding="utf-8")
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "monitor_compare.py"),
                              str(p1), str(p2)],
                             capture_output=True, text=True, encoding="utf-8")
    print(f"5. снимок без основной цели -> код {run.returncode}: "
          f"{run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ''}")
    ok &= run.returncode == 2

    print("ИТОГ:", "все проверки пройдены" if ok else "ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
