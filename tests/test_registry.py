"""Registry completeness: every action name registered exactly once (SPEC 6.3)."""

import directai_mcp.server as server_mod
from directai_mcp.catalog.registry import ACTIONS

EXPECTED = frozenset(
    {
        "stats_summary",
        "stats_campaigns",
        "stats_adgroups",
        "stats_ads",
        "stats_keywords",
        "stats_audiences",
        "stats_search_queries",
        "stats_regions",
        "stats_placements",
        "stats_devices",
        "stats_compare",
        "stats_custom",
        "campaigns_list",
        "campaigns_get",
        "campaigns_create",
        "campaigns_update",
        "campaigns_state",
        "adgroups_list",
        "adgroups_create",
        "adgroups_update",
        "ads_list",
        "ads_create",
        "ads_update",
        "ads_state",
        "keywords_list",
        "keywords_add",
        "keywords_update",
        "keywords_state",
        "negatives_audit",
        "negatives_set",
        "extensions_list",
        "extensions_create",
        "moderation_check",
        "audiences_list",
        "bids_get",
        "bids_set",
        "bid_modifiers_get",
        "bid_modifiers_set",
        "dictionaries_get",
        "changes_check",
        "counter_check",
        "metrika_goals_list",
        "accounts_discover",
        "accounts_check",
        "accounts_balance",
        "webmaster_hosts",
        "webmaster_summary",
        "webmaster_query",
        "audience_segments_list",
        "audience_segment_get",
        "audience_segment_from_file",
        "audience_segment_delete",
        "strategies_get",
        "feeds_get",
        "dynamic_targets_get",
        "dynamic_feed_targets_get",
        "smart_targets_get",
        "businesses_get",
        "turbopages_get",
    }
)


def test_registry_has_all_actions():
    assert set(ACTIONS) == EXPECTED


def test_registration_imports_referenced():
    assert len(server_mod._ACTION_MODULES) == 18
