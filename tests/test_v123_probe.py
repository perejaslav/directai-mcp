"""v1.2.3: warning старого формата — с инструкцией; probe — живьём."""

import logging

from directai_mcp.cli import PROBE_EXPECTED, PROBE_QUERY, cmd_probe
from directai_mcp.config import load_settings


def test_legacy_warning_has_fix(tmp_path, caplog):
    (tmp_path / "accounts.toml").write_text(
        '[auth]\nlogin = "agency-login"\n[accounts.msk]\nlogin = "agency-login"\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="directai_mcp.config"):
        settings = load_settings(tmp_path / "accounts.toml")
    assert settings.legacy_sections == ("msk",)
    assert settings.aliases["msk"].login == "agency-login"
    assert any(
        "переименуйте [accounts.X] в [aliases.X]" in r.message
        for r in caplog.records
    )


def test_legacy_fix_targets_only_accounts(tmp_path, caplog):
    (tmp_path / "accounts.toml").write_text(
        '[auth]\nlogin = "agency-login"\n'
        "[accounts]\nexclude = []\n"
        '[aliases.msk]\nlogin = "agency-login"\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="directai_mcp.config"):
        settings = load_settings(tmp_path / "accounts.toml")
    assert settings.legacy_sections == ()
    assert not any(
        "старый формат" in r.message for r in caplog.records
    )


def test_probe_query_finds_expected():
    from directai_mcp.catalog import stats as _stats  # noqa: F401 (реестр)
    from directai_mcp.catalog.registry import search

    names = [a.name for a in search(PROBE_QUERY, "any")]
    assert PROBE_EXPECTED in names


def test_probe_live_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    assert cmd_probe(timeout=60.0) == 0
    out = capsys.readouterr().out
    assert out.startswith("OK server=directai-mcp version=")
    assert PROBE_EXPECTED in out
