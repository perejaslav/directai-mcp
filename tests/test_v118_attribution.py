"""v1.1.8: шапка атрибуции — LC без целей, AUTO с целями."""

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import Ctx
from directai_mcp.catalog.stats import StatsParams, _context
from directai_mcp.config import AccountEntry, Settings


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def test_header_lc_without_goals(tmp_path):
    ctx = _ctx(tmp_path)
    entries = list(ctx.settings.accounts.values())
    header = _context(ctx, "stats_regions", entries,
                      StatsParams(period="YESTERDAY"), ([], "none"))
    assert "атрибуция: LC (AUTO неприменима без целей; " \
        "у кампании нет ключевых целей)" in header
    assert "атрибуция: AUTO" not in header


def test_header_auto_with_key_goals(tmp_path):
    ctx = _ctx(tmp_path)
    entries = list(ctx.settings.accounts.values())
    header = _context(ctx, "stats_regions", entries,
                      StatsParams(period="YESTERDAY"),
                      (["900000010", "900000019"], "auto-key"))
    assert "атрибуция: AUTO" in header
    assert "неприменима без целей" not in header
    assert "целей: 2 (авто key: PriorityGoals+стратегия)" in header
    assert "режим: key" in header
