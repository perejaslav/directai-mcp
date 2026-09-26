"""Search and limits tests (SPEC 6.3)."""

from directai_mcp.catalog import stats as _stats  # noqa: F401 (registers actions)
from directai_mcp.catalog.limits import match_limit
from directai_mcp.catalog.registry import search


def test_limit_carousel_declension():
    assert match_limit("состав карусели старых объявлений") is not None


def test_limit_cpm_video_and_no_false_positive():
    assert match_limit("создание cpm видеокреативов") is not None
    assert match_limit("статистика cpm кампаний") is None


def test_limit_landing():
    assert match_limit("контент лендинга") is not None
    assert match_limit("расходы по кампаниям") is None


def test_search_top_is_stats_summary():
    found = search("расходы по аккаунтам за неделю", "read")
    assert found and found[0].name == "stats_summary"


def test_search_exact_name_bonus():
    found = search("stats_summary")
    assert found and found[0].name == "stats_summary"


def test_search_write_finds_state_actions():
    names = {a.name for a in search("остановить кампанию", "write")}
    assert "campaigns_state" in names
