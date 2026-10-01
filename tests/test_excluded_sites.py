"""ExcludedSites в campaigns_update: add-мерж, превью, read-back."""

from types import SimpleNamespace

import httpx
import pytest

import directai_mcp.catalog.campaigns as _cm  # noqa: F401 (реестр)
from directai_mcp.catalog.campaigns import (
    _prepare_campaigns_update,
    _verify_campaigns_updated,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

V5C = "https://api.direct.yandex.com/json/v5/campaigns"
V501C = "https://api.direct.yandex.com/json/v501/campaigns"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _entry():
    return AccountEntry(alias="m", login="agency-login")


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _params(**kw):
    return ACTIONS["campaigns_update"].params(account="m", campaign_ids=[7], **kw)


async def test_excluded_add_preview(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "Name": "K", "Type": "TEXT_CAMPAIGN",
                       "ExcludedSites": {"Items": ["ok.ru"]}}]}))
    ctx = _ctx(tmp_path)
    prep = await _prepare_campaigns_update(
        ctx, _entry(), _params(excluded_sites=["OK.RU", "vk.com"]))
    assert "было 1, добавляется 2, дублей 1, станет 2" in prep["preview"]
    body = prep["requests"][0][2]
    assert body["Campaigns"][0]["ExcludedSites"] == {"Items": ["ok.ru", "vk.com"]}


async def test_excluded_validation(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "Name": "K", "Type": "TEXT_CAMPAIGN"}]}))
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="пустой элемент"):
        await _prepare_campaigns_update(
            ctx, _entry(), _params(excluded_sites=["  "]))
    with pytest.raises(ValueError, match="255"):
        await _prepare_campaigns_update(
            ctx, _entry(), _params(excluded_sites=["x" * 256]))


async def test_excluded_verify_subset_ok(respx_mock, tmp_path):
    respx_mock.post(V5C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "ExcludedSites": {"Items": ["ok.ru", "VK.COM"]}}]}))
    ctx = _ctx(tmp_path)
    plan = SimpleNamespace(
        params={"campaign_ids": [7], "excluded_sites": ["ok.ru"]},
        requests=[("campaigns", "update", {"Campaigns": [{"Id": 7}]}, "v5")])
    result = await _verify_campaigns_updated(ctx, _entry(), plan)
    assert result["ok"] is True
    assert result["note"] == "подтверждено read-back."


async def test_excluded_verify_missing(respx_mock, tmp_path):
    respx_mock.post(V5C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "ExcludedSites": {"Items": ["ok.ru"]}}]}))
    ctx = _ctx(tmp_path)
    plan = SimpleNamespace(
        params={"campaign_ids": [7], "excluded_sites": ["ok.ru", "vk.com"]},
        requests=[("campaigns", "update", {"Campaigns": [{"Id": 7}]}, "v5")])
    result = await _verify_campaigns_updated(ctx, _entry(), plan)
    assert result["ok"] is False
    assert "7.excluded" in result["note"]
