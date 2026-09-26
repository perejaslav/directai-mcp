"""Шаг 1.1-4 step 4: поиск по предметным словам, подсказки, list_accounts."""

import json

import directai_mcp.catalog.accounts as _acc  # noqa: F401
import directai_mcp.catalog.adgroups as _g  # noqa: F401
import directai_mcp.catalog.ads as _d  # noqa: F401
import directai_mcp.catalog.audiences as _u  # noqa: F401
import directai_mcp.catalog.bids as _b  # noqa: F401
import directai_mcp.catalog.campaigns as _c  # noqa: F401
import directai_mcp.catalog.changes as _ch  # noqa: F401
import directai_mcp.catalog.dictionaries as _dc  # noqa: F401
import directai_mcp.catalog.extensions as _e  # noqa: F401
import directai_mcp.catalog.keywords as _k  # noqa: F401
import directai_mcp.catalog.negatives as _n  # noqa: F401
import directai_mcp.catalog.stats as _s  # noqa: F401
from directai_mcp.api.direct import LAST_SEEN_UNITS, UnitsInfo, _touch_seen
from directai_mcp.catalog.common import GetActionParams
from directai_mcp.catalog.registry import categories, match_hint, search
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import accounts_table

TABLE = {
    "цели приоритетные": "campaigns_get",
    "атрибуция": "campaigns_get",
    "стратегия ставок": "campaigns_get",
    "utm метки": "campaigns_get",
    "корректировки регион": "bid_modifiers_get",
    "минус-фразы набор": "negatives_audit",
    "баллы units": "accounts_check",
    "список кабинетов": "accounts_discover",
    "статистика конверсии": "stats_campaigns",
    "быстрые ссылки": "extensions_list",
    "активные кабинеты": "accounts_check",
    "ссылки объявлений": "ads_list",
}


def test_acceptance_table_top3():
    for query, expected in TABLE.items():
        top3 = [a.name for a in search(query)[:3]]
        assert expected in top3, f"{query!r} -> {top3}"


def test_keywords_outweigh_summary():
    top = [a.name for a in search("приоритетные цели")]
    assert top[0] == "campaigns_get"


def test_empty_gives_categories():
    assert search("абракадабра несуществующая") == []
    cats = categories()
    assert "- stats:" in cats and "- campaigns:" in cats


def test_file_hint():
    hint = match_hint("выгрузить в файл")
    assert hint is not None and "output=file" in hint
    assert match_hint("расходы по кампаниям") is None


def test_account_help_text():
    desc = GetActionParams.model_json_schema()["properties"]["account"]
    assert "active" in desc.get("description", "")
    assert "30" not in desc.get("description", "")


def _settings(tmp_path):
    return Settings(
        auth_login="agency-login",
        accounts={"msk": AccountEntry(alias="msk", login="agency-login", role="r")},
        accounts_path=tmp_path / "accounts.toml",
    )


def test_list_accounts_cache(tmp_path):
    cache = {
        "updated_at": "2026-09-26T10:00:00+03:00",
        "logins": ["a-login"],
        "checks": {"agency-login": {"on": 12, "checked_at": "2026-09-26T11:00:00+03:00"}},
    }
    (tmp_path / "accounts_cache.json").write_text(json.dumps(cache), encoding="utf-8")
    _touch_seen("agency-login", UnitsInfo(used=1, rest=100, limit=1000))
    try:
        out = accounts_table(_settings(tmp_path))
    finally:
        LAST_SEEN_UNITS.pop("agency-login", None)
    assert "| agency-login | msk | 12 | 2026-09-26T11:00:00+03:00 | 100/1000 (" in out
    assert "| a-login | — | — | — | — |" in out


def test_list_accounts_no_cache(tmp_path):
    out = accounts_table(_settings(tmp_path))
    assert "кеш пуст — выполните accounts_discover." in out
    assert "msk" in out
