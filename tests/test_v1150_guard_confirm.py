"""v1.15.0: guard mode=confirm — политические запреты становятся
«опасной операцией»: план создаётся, apply только с owner_confirmed=true.
Жёсткими остаются: объект не найден, read_only-поля, переключатели
Аудиторий/ретаргетинга, неизвестные действия."""

import httpx
import pytest

import directai_mcp.catalog.ads as _ad  # noqa: F401 (реестр)
import directai_mcp.catalog.campaigns as _c  # noqa: F401 (реестр)
import directai_mcp.catalog.retargeting as _rt  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, ConfigError, Settings, load_settings
from directai_mcp.safety.guard import BUDGET_BLOCK, DANGER_NOTICE
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

V5 = "https://api.direct.yandex.com/json/v5"
V501 = "https://api.direct.yandex.com/json/v501"
COMBAT_ID = 900000003
COMBAT_NAME = "ПОИСК - Эталон"


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _ctx(tmp_path, mode="confirm"):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        guard_mode=mode,
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _campaign(**extra):
    return {"Id": COMBAT_ID, "Name": COMBAT_NAME, "Type": "TEXT_CAMPAIGN",
            "State": "ON", **extra}


def _campaigns_resp(**extra):
    return httpx.Response(200, json={"result": {"Campaigns": [_campaign(**extra)]}})


def _pid(out: str) -> str:
    assert out.startswith("План "), out
    return out.split()[1].rstrip(":")


async def test_budget_is_danger_and_needs_owner_confirmed(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{V5}/campaigns").mock(side_effect=[
        _campaigns_resp(DailyBudget={"Amount": 300_000_000, "Mode": "STANDARD"}),
        httpx.Response(200, json={"result": {"UpdateResults": [{"Id": COMBAT_ID}]}}),
        _campaigns_resp(DailyBudget={"Amount": 500_000_000, "Mode": "STANDARD"}),
    ])
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_campaigns_resp(
        DailyBudget={"Amount": 300_000_000, "Mode": "STANDARD"}))
    out = await do_plan_write(ctx, "campaigns_update", {
        "account": "m", "campaign_ids": [COMBAT_ID], "daily_budget": 500.0})
    pid = _pid(out)
    assert DANGER_NOTICE in out
    assert BUDGET_BLOCK in out
    assert "дневной бюджет: 300 ₽ → 500 ₽" in out
    body = PLANS.peek(pid).requests[0][2]["Campaigns"][0]
    assert body["DailyBudget"] == {"Amount": 500_000_000, "Mode": "STANDARD"}
    refused = await do_apply_write(ctx, pid)
    assert "ОПАСНАЯ ОПЕРАЦИЯ" in refused
    assert "owner_confirmed=true" in refused
    # warnings тоже нужны отдельно: owner_confirmed их не заменяет.
    assert "порог" in out  # ratio-warning бюджета (rules.max_budget_ratio)
    refused = await do_apply_write(ctx, pid, owner_confirmed=True)
    assert "acknowledge_warnings=true" in refused
    applied = await do_apply_write(
        ctx, pid, acknowledge_warnings=True, owner_confirmed=True)
    assert "статус applied" in applied, applied


async def test_campaign_suspend_combat_is_danger(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_campaigns_resp())
    out = await do_plan_write(_ctx(tmp_path), "campaigns_state", {
        "account": "m", "campaign_ids": [COMBAT_ID], "operation": "suspend"})
    _pid(out)
    assert "вне тестового префикса" in out
    assert DANGER_NOTICE in out


async def test_rename_is_danger_and_body_has_name(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_campaigns_resp())
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_campaigns_resp())
    out = await do_plan_write(_ctx(tmp_path), "campaigns_update", {
        "account": "m", "campaign_ids": [COMBAT_ID], "name": "Новое имя"})
    pid = _pid(out)
    assert "переименование" in out
    assert PLANS.peek(pid).requests[0][2]["Campaigns"][0]["Name"] == "Новое имя"


async def test_moderate_is_danger(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/ads").mock(return_value=httpx.Response(200, json={
        "result": {"Ads": [{"Id": 7, "AdGroupId": 5, "State": "OFF",
                            "Status": "DRAFT"}]}}))
    respx_mock.post(f"{V5}/adgroups").mock(return_value=httpx.Response(
        200, json={"result": {"AdGroups": [{"Id": 5, "CampaignId": COMBAT_ID}]}}))
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_campaigns_resp())
    out = await do_plan_write(_ctx(tmp_path), "ads_state", {
        "account": "m", "ad_ids": [7], "operation": "moderate"})
    _pid(out)
    assert "модерация" in out
    assert DANGER_NOTICE in out


async def test_safe_plan_has_no_danger(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(
        return_value=_campaigns_resp(ExcludedSites={"Items": []}))
    out = await do_plan_write(_ctx(tmp_path), "campaigns_update", {
        "account": "m", "campaign_ids": [COMBAT_ID],
        "excluded_sites": ["example.com"]})
    pid = _pid(out)
    assert DANGER_NOTICE not in out
    assert PLANS.peek(pid).danger == []


async def test_hard_blocks_remain_in_confirm(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    # read_only-поле (A1)
    out = await do_plan_write(ctx, "campaigns_update", {
        "account": "m", "campaign_ids": [COMBAT_ID],
        "settings": [{"Option": "ENABLE_AREA_OF_INTEREST_TARGETING",
                      "Value": "YES"}]})
    assert out.startswith("Заблокировано защитой"), out
    # переключатель ретаргетинга
    out = await do_plan_write(ctx, "retargeting_list_delete", {
        "account": "m", "list_id": 1})
    assert out.startswith("Заблокировано защитой"), out
    # неизвестное действие
    out = await do_plan_write(ctx, "campaigns_delete", {"account": "m"})
    assert out.startswith("Заблокировано защитой"), out
    # кампания не найдена
    respx_mock.post(f"{V5}/campaigns").mock(return_value=httpx.Response(
        200, json={"result": {"Campaigns": []}}))
    respx_mock.post(f"{V5}/reports").mock(return_value=httpx.Response(200, text=""))
    out = await do_plan_write(ctx, "campaigns_state", {
        "account": "m", "campaign_ids": [COMBAT_ID], "operation": "suspend"})
    assert out.startswith("Заблокировано защитой"), out


async def test_block_mode_unchanged(tmp_path):
    out = await do_plan_write(_ctx(tmp_path, mode="block"), "campaigns_update", {
        "account": "m", "campaign_ids": [COMBAT_ID], "daily_budget": 500.0})
    assert out.startswith(f"Заблокировано защитой: {BUDGET_BLOCK}"), out


def test_config_mode(tmp_path):
    base = '[auth]\nlogin = "agency-login"\n[aliases.msk]\nlogin = "agency-login"\n'
    cfg = tmp_path / "accounts.toml"
    cfg.write_text(base, encoding="utf-8")
    assert load_settings(cfg).guard_mode == "block"
    cfg.write_text(base + '[guard]\nmode = "confirm"\n', encoding="utf-8")
    assert load_settings(cfg).guard_mode == "confirm"
    cfg.write_text(base + '[guard]\nmode = "off"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(cfg)
