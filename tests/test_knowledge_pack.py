"""Пакет «Знания» A1–A11 + B2: вымышленные данные (9...)."""

import asyncio

import directai_mcp.catalog.campaigns as camp_mod
import directai_mcp.catalog.keywords as kw_mod
from directai_mcp.catalog.common import goal_label
from directai_mcp.catalog.notices import (
    is_read_only,
    notices_for_settings,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.tracking import (
    calc_effective_multiplier,
    validate_tracking_macros,
)
from directai_mcp.config import AccountEntry, Settings


def _ctx(tmp_path) -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _entry() -> AccountEntry:
    return AccountEntry(alias="m", login="agency-login")


def test_b2_notices_registry():
    notes = notices_for_settings(
        [{"Option": "ENABLE_AREA_OF_INTEREST_TARGETING", "Value": "YES"}]
    )
    assert len(notes) == 1 and notes[0]["status"] == "read_only"
    assert is_read_only("ENABLE_AREA_OF_INTEREST_TARGETING")
    assert not is_read_only("SOME_OTHER")


def test_a1_campaigns_update_rejects(tmp_path):
    ctx, entry = _ctx(tmp_path), _entry()
    params = camp_mod.CampaignsUpdateParams(
        campaign_ids=[900000031],
        settings=[{"Option": "ENABLE_AREA_OF_INTEREST_TARGETING", "Value": "NO"}],
    )
    try:
        asyncio.run(camp_mod._prepare_campaigns_update(ctx, entry, params))
    except ValueError as e:
        assert "только читается" in str(e)
    else:
        raise AssertionError("A1: запись не отклонена")


def test_a3_minus_operators():
    # Полное пересечение (в т.ч. в кавычках) отменяет минус.
    assert kw_mod._kw_blocked_by_minus({"купить", "шкаф"}, '"купить шкаф"') is None
    assert kw_mod._kw_blocked_by_minus({"купить", "шкаф"}, "купить шкаф") is None
    assert (
        kw_mod._kw_blocked_by_minus({"купить", "шкаф", "недорого"}, '"купить шкаф"')
        is None
    )
    # Полное пересечение отменяет минус.
    assert kw_mod._kw_blocked_by_minus({"купить", "шкаф"}, "купить шкаф") is None
    # Подмножество блокирует.
    assert kw_mod._kw_blocked_by_minus({"купить", "шкаф", "недорого"}, "шкаф") == "шкаф"
    # Порядок: [] при подмножестве блокирует (порядок — по тексту в Excel).
    assert (
        kw_mod._kw_blocked_by_minus({"шкаф", "купить", "недорого"}, "[купить шкаф]")
        == "[купить шкаф]"
    )
    # ! и + снимаются для сравнения.
    assert kw_mod._kw_blocked_by_minus({"купить", "шкаф"}, "!шкаф") == "!шкаф"
    assert kw_mod._kw_blocked_by_minus({"купить", "шкаф", "белый"}, "+шкаф") == "+шкаф"


def test_a4_multiplier():
    mult, _note = calc_effective_multiplier(
        [
            {"type": "MOBILE_ADJUSTMENT", "raw": 130},
            {"type": "TABLET_ADJUSTMENT", "raw": 120},
            {"type": "REGIONAL_ADJUSTMENT", "raw": 110},
        ]
    )
    assert mult == round(1.3 * 1.1, 4)  # внутри devices побеждает max
    mult0, _ = calc_effective_multiplier([{"type": "MOBILE_ADJUSTMENT", "raw": 0}])
    assert mult0 == 0.0
    mult1, note1 = calc_effective_multiplier(
        [
            {"type": "MOBILE_ADJUSTMENT", "raw": 0},
            {"type": "MOBILE_ADJUSTMENT", "raw": 130},
        ],
        strategy_type="AVERAGE_CPA",
    )
    assert mult1 == 1.3 and "CPA" in note1


def test_a5_service_goals():
    assert "служебное" in goal_label(13, {})
    assert "служебное" in goal_label(12, {})
    assert goal_label(900000007, {"900000007": "Покупка"}) == "Покупка (900000007)"


def test_a9_macros():
    rec, warns = validate_tracking_macros(
        "utm_campaign={campaign_name}&x={campaign_name_lat}"
    )
    assert "{campaign_name}" in rec and not warns
    _rec, warns2 = validate_tracking_macros("a={unknown_macro}")
    assert warns2 and "Поддерживаются" in warns2[0]
    try:
        validate_tracking_macros("a={broken")
    except ValueError:
        pass
    else:
        raise AssertionError("A9: битый синтаксис не заблокирован")


def test_a6_attribution_block(tmp_path):
    from directai_mcp.catalog.stats import (
        StatsParams,
        attribution_info,
        attribution_line,
    )

    p = StatsParams()
    info = attribution_info(_ctx(tmp_path), p)
    assert info["effective"] is None and info["source"] == "not_reported"
    assert "effective=null" in attribution_line(_ctx(tmp_path), p)


def test_campaigns_get_notices_offline(monkeypatch, tmp_path):
    items = [
        {
            "Id": 900000031,
            "Name": "Тест",
            "Type": "UNIFIED_CAMPAIGN",
            "State": "ON",
            "Status": "ACCEPTED",
            "TimeZone": "Europe/Moscow",
            "UnifiedCampaign": {
                "Settings": [
                    {"Option": "ENABLE_AREA_OF_INTEREST_TARGETING", "Value": "YES"}
                ],
                "BiddingStrategy": {},
            },
        }
    ]

    async def fake(ctx, account_value, fn):
        return [(_entry(), items)]

    monkeypatch.setattr(camp_mod, "map_accounts", fake)
    out = asyncio.run(
        ACTIONS["campaigns_get"].run(
            _ctx(tmp_path), camp_mod.CampaignsGetParams(campaign_ids=[900000031])
        )
    )
    assert "campaign_setting_notices" in out
    assert "только читается" in out or "read_only" in out
