"""Шаг 1.1-3 step 3: truncated-флаг, output=file, reports/ (SPEC-v1.1)."""

import json
import re

from directai_mcp.catalog.common import finalize
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings, load_settings
from directai_mcp.fmt import report_filename, truncated_line


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"a": AccountEntry(alias="a", login="l")},
        reports_dir=tmp_path / "reports",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def test_truncated_flag_both_states(tmp_path):
    ctx = _ctx(tmp_path)
    out = finalize(ctx, "c", "act", ["Id"], [{"Id": 1}], None, None, [])
    assert "truncated: false, показано 1 из 1 строк." in out
    assert "output=file" not in out
    rows = [{"Id": i} for i in range(250)]
    out2 = finalize(ctx, "c", "act", ["Id"], rows, 1000, None, [])
    assert "truncated: true, показано 200 из 250 строк." in out2
    assert "повторите с output=file" in out2


def test_truncated_line_unit():
    assert truncated_line(5, 5) == "truncated: false, показано 5 из 5 строк."
    assert "true" in truncated_line(10, 3)


def test_file_json_full_rows_and_summary(tmp_path):
    ctx = _ctx(tmp_path)
    rows = [{"Id": i, "Secret": f"s{i}"} for i in range(30)]
    out = finalize(
        ctx, "c", "act", ["Id"], rows, None, None, [],
        output="file", format="json", account="all",
    )
    m = re.search(r"Полный результат: (\S+) \(30 строк, формат json\)", out)
    assert m, out
    with open(m.group(1), encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["meta"]["total_rows"] == 30
    assert payload["meta"]["account"] == "all"
    assert len(payload["rows"]) == 30
    assert payload["rows"][0]["Secret"] == "s0"  # все поля, не только колонки
    assert re.search(r"act_all_\d{8}-\d{6}\.json$", m.group(1))
    assert "| 19 |" in out  # сводка: первые 20 строк
    assert "| 29 |" not in out


def test_file_md_and_csv(tmp_path):
    ctx = _ctx(tmp_path)
    rows = [{"Id": 1}]
    out_md = finalize(
        ctx, "c", "act", ["Id"], rows, None, None, [],
        output="file", format="md", account="l",
    )
    assert "формат md" in out_md and out_md.count("act_l_") >= 1
    out_csv = finalize(
        ctx, "c", "act", ["Id"], rows, None, None, [],
        output="file", format="csv", account="l",
    )
    assert "формат csv" in out_csv


def test_save_as_is_file_alias(tmp_path):
    ctx = _ctx(tmp_path)
    rows = [{"Id": 1}]
    out = finalize(ctx, "c", "act", ["Id"], rows, None, "csv", [])
    assert "Полный результат:" in out and "формат csv" in out


def test_report_filename_pattern():
    name = report_filename("campaigns_list", "all", "json")
    assert re.fullmatch(r"campaigns_list_all_\d{8}-\d{6}\.json", name)
    assert report_filename("a", "weird login!", "md").startswith("a_weird_login_")


def test_reports_dir_from_config(tmp_path):
    (tmp_path / "accounts.toml").write_text(
        '[auth]\nlogin = "x"\n[aliases.a]\nlogin = "l"\n'
        '[paths]\nreports_dir = "rep"\n',
        encoding="utf-8",
    )
    settings = load_settings(tmp_path / "accounts.toml")
    assert str(settings.reports_dir) == "rep"
