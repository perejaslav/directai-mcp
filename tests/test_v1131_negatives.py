"""v1.1.31 (тест 25): negatives_set — add/replace, валидация, read-back."""

from types import SimpleNamespace

import httpx
import pytest

import directai_mcp.catalog.negatives as _n  # noqa: F401 (реестр)
from directai_mcp.catalog.negatives import (
    _norm_negative,
    _prepare_negatives_set,
    _validate_negatives,
    _verify_negatives_set,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

BASE = "https://api.direct.yandex.com/json/v5"
V501C = "https://api.direct.yandex.com/json/v501/campaigns"
V5C = "https://api.direct.yandex.com/json/v5/campaigns"
V5G = "https://api.direct.yandex.com/json/v5/adgroups"
V5S = "https://api.direct.yandex.com/json/v5/negativekeywordsharedsets"


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
    act = ACTIONS["negatives_set"]
    return act.params(account="m", **kw)


def test_norm():
    assert _norm_negative("  СЕРТИФИКАТ  ") == "сертификат"
    assert _norm_negative("паспорт   рф") == "паспорт рф"
    assert _norm_negative("!Паспорт") == "!паспорт"
    assert _norm_negative(None) == ""


def test_validation_limits():
    _validate_negatives(["сертификат", "паспорт рф"], 20000, "кампания 1")
    with pytest.raises(ValueError, match="8 слов"):
        _validate_negatives(["с1 с2 с3 с4 с5 с6 с7 с8"], 20000, "кампания 1")
    with pytest.raises(ValueError, match="36 символов"):
        _validate_negatives(["x" * 36], 20000, "кампания 1")
    with pytest.raises(ValueError, match="суммарная длина"):
        _validate_negatives(["y" * 35] * 120, 4096, "группа 1")
    with pytest.raises(ValueError, match="пустая"):
        _validate_negatives(["  "], 20000, "кампания 1")


async def test_add_merges_dedup(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "NegativeKeywords": {"Items": ["Сертификат"]}}]}))
    ctx = _ctx(tmp_path)
    prep = await _prepare_negatives_set(
        ctx, _entry(), _params(campaign_ids=[7],
                               negatives=["сертификат ", "паспорт"]))
    assert "было 1, добавляется 2, дублей 1, станет 2" in prep["preview"]
    body = prep["requests"][0][2]
    assert body["Campaigns"][0]["NegativeKeywords"]["Items"] == [
        "Сертификат", "паспорт"]


async def test_replace_warns(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "NegativeKeywords": {"Items": ["a"]}}]}))
    ctx = _ctx(tmp_path)
    prep = await _prepare_negatives_set(
        ctx, _entry(), _params(campaign_ids=[7], negatives=["b", "c"],
                               mode="replace"))
    assert "было 1 → станет 2. ВНИМАНИЕ" in prep["preview"]
    body = prep["requests"][0][2]
    assert body["Campaigns"][0]["NegativeKeywords"]["Items"] == ["b", "c"]


async def test_add_validation_no_write(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "NegativeKeywords": {"Items": []}}]}))
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="8 слов"):
        await _prepare_negatives_set(
            ctx, _entry(), _params(campaign_ids=[7],
                                   negatives=["с1 с2 с3 с4 с5 с6 с7 с8"]))


def _plan_pair():
    return SimpleNamespace(
        params={"campaign_ids": [7], "negatives": ["Сертификат", "паспорт"]},
        requests=[("campaigns", "update",
                   {"Campaigns": [{"Id": 7, "NegativeKeywords": {
                       "Items": ["Сертификат", "паспорт"]}}]}, "v501")],
    )


async def test_verify_case_order_insensitive(respx_mock, tmp_path):
    respx_mock.post(V5C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "NegativeKeywords": {
            "Items": ["паспорт", "СЕРТИФИКАТ"]}}]}))
    ctx = _ctx(tmp_path)
    result = await _verify_negatives_set(ctx, _entry(), _plan_pair())
    assert result["ok"] is True
    assert result["note"] == "подтверждено read-back."


async def test_verify_mismatch(respx_mock, tmp_path):
    respx_mock.post(V5C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "NegativeKeywords": {
            "Items": ["паспорт"]}}]}))
    ctx = _ctx(tmp_path)
    result = await _verify_negatives_set(ctx, _entry(), _plan_pair())
    assert result["ok"] is False
    assert "кампания 7" in result["note"]


async def test_shared_set_replace_preview(respx_mock, tmp_path):
    respx_mock.post(V5S).mock(return_value=_ok({
        "NegativeKeywordSharedSets": [{"Id": 5, "NegativeKeywords": {
            "Items": ["a", "b", "c"]}}]}))
    ctx = _ctx(tmp_path)
    prep = await _prepare_negatives_set(
        ctx, _entry(), _params(update_shared_set={"id": 5, "negatives": ["d"]}))
    assert "набор 5: было 3 → станет 1. ВНИМАНИЕ" in prep["preview"]


async def test_group_add(respx_mock, tmp_path):
    respx_mock.post(V5G).mock(return_value=_ok({
        "AdGroups": [{"Id": 11, "NegativeKeywords": {"Items": ["тест минус"]}}]}))
    ctx = _ctx(tmp_path)
    prep = await _prepare_negatives_set(
        ctx, _entry(), _params(adgroup_ids=[11], negatives=["ТЕСТ минус", "x"]))
    assert "было 1, добавляется 2, дублей 1, станет 2" in prep["preview"]
