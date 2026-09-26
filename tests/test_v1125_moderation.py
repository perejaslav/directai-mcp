"""v1.1.25: moderation_check — REJECTED, partial, RARELY_SERVED, suspended."""

import httpx

import directai_mcp.catalog.ads as _a  # noqa: F401 (реестр)
import directai_mcp.catalog.moderation as _m  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

V5 = "https://api.direct.yandex.com/json/v5"
V501 = "https://api.direct.yandex.com/json/v501"

EXT_ID = 991


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ok(payload):
    return httpx.Response(200, json={"result": payload})


def _ads():
    return [
        {"Id": 1, "CampaignId": 7, "AdGroupId": 1, "Type": "TEXT_AD",
         "State": "ON", "Status": "REJECTED",
         "StatusClarification": "запрещённая лексика",
         "TextAd": {"Title": "T", "Text": "x"}},
        {"Id": 2, "CampaignId": 7, "AdGroupId": 1, "Type": "RESPONSIVE_AD",
         "State": "ON", "Status": "ACCEPTED",
         "ResponsiveAd": {
             "Titles": [{"Title": "A"},
                        {"Title": "B", "Status": "REJECTED",
                         "StatusClarification": "капс"}],
             "Texts": [{"Title": "t", "Text": "t"}],
             "SitelinksModeration": {"Status": "REJECTED",
                                     "StatusClarification": "битая ссылка"}}},
        {"Id": 3, "CampaignId": 7, "AdGroupId": 1, "Type": "TEXT_AD",
         "State": "ON", "Status": "ACCEPTED",
         "TextAd": {"Title": "T", "Text": "x",
                    "AdExtensions": [{"AdExtensionId": EXT_ID}]}},
        {"Id": 4, "CampaignId": 7, "AdGroupId": 1, "Type": "TEXT_AD",
         "State": "ON", "Status": "ACCEPTED",
         "TextAd": {"Title": "T", "Text": "x"}},
        {"Id": 5, "CampaignId": 7, "AdGroupId": 1, "Type": "TEXT_AD",
         "State": "OFF", "Status": "ACCEPTED",
         "TextAd": {"Title": "T", "Text": "x"}},
    ]


def _mock_all(respx_mock):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_ok(
        {"Campaigns": [{"Id": 7, "Name": "К", "State": "ON",
                        "Status": "ACCEPTED", "Type": "TEXT_CAMPAIGN"}]}))
    respx_mock.post(f"{V501}/adgroups").mock(return_value=_ok(
        {"AdGroups": [{"Id": 1, "CampaignId": 7, "Name": "Г",
                       "Status": "ACCEPTED", "ServingStatus": "RARELY_SERVED"}]}))
    respx_mock.post(f"{V5}/ads").mock(return_value=_ok({"Ads": _ads()}))
    respx_mock.post(f"{V5}/keywords").mock(return_value=_ok(
        {"Keywords": [
            {"Id": 11, "CampaignId": 7, "AdGroupId": 1, "Keyword": "фраза",
             "State": "ON", "Status": "REJECTED", "ServingStatus": "ELIGIBLE"},
            {"Id": 12, "CampaignId": 7, "AdGroupId": 1, "Keyword": "редкая",
             "State": "ON", "Status": "ACCEPTED",
             "ServingStatus": "RARELY_SERVED"},
            {"Id": 13, "CampaignId": 7, "AdGroupId": 1, "Keyword": "ок",
             "State": "ON", "Status": "ACCEPTED", "ServingStatus": "ELIGIBLE"},
            {"Id": 14, "CampaignId": 7, "AdGroupId": 1, "Keyword": "стоп",
             "State": "SUSPENDED", "Status": "ACCEPTED",
             "ServingStatus": "ELIGIBLE"},
            {"Id": 15, "CampaignId": 7, "AdGroupId": 1, "Keyword": "выкл",
             "State": "OFF", "Status": "ACCEPTED", "ServingStatus": "ELIGIBLE"},
        ]}))
    respx_mock.post(f"{V5}/adextensions").mock(return_value=_ok(
        {"AdExtensions": [{"Id": EXT_ID, "Type": "CALLOUT",
                           "Callout": {"CalloutText": "Скидки"},
                           "Status": "REJECTED",
                           "StatusClarification": "враньё"}]}))


async def test_check_table_rows(respx_mock, tmp_path):
    _mock_all(respx_mock)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["moderation_check"].run(
        ctx, ACTIONS["moderation_check"].params(account="t"))
    assert "| Кампания | Группа | ID | Объект | Статус | Причина |" in out
    # Отклонённое объявление с причиной.
    assert "запрещённая лексика" in out
    # Частично отклонённые: заголовок + быстрые ссылки; уточнение.
    assert "частично отклонено" in out
    assert "Заголовок 2: капс" in out
    assert "Быстрые ссылки: битая ссылка" in out
    assert "Уточнение «Скидки»: враньё" in out
    # Отклонённая фраза, редкие показы (фраза и группа).
    assert "Фраза «фраза»" in out and "REJECTED" in out
    assert "RARELY_SERVED" in out
    # Чистое объявление 4 — нигде.
    assert "| 4 |" not in out
    # Приостановленные отдельно.
    assert "Приостановлено вручную:" in out
    assert "OFF" in out.split("Приостановлено вручную:")[1]
    # Фразы: SUSPENDED в блоке, OFF — нет.
    held_block = out.split("Приостановлено вручную:")[1]
    assert "Фраза «стоп»" in held_block
    assert "Фраза «выкл»" not in held_block


async def test_check_empty_ok(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_ok(
        {"Campaigns": [{"Id": 7, "Name": "К", "State": "ON",
                        "Status": "ACCEPTED", "Type": "TEXT_CAMPAIGN"}]}))
    respx_mock.post(f"{V501}/adgroups").mock(return_value=_ok(
        {"AdGroups": [{"Id": 1, "CampaignId": 7, "Name": "Г",
                       "Status": "ACCEPTED", "ServingStatus": "ELIGIBLE"}]}))
    respx_mock.post(f"{V5}/ads").mock(return_value=_ok({"Ads": [_ads()[3]]}))
    respx_mock.post(f"{V5}/keywords").mock(return_value=_ok({"Keywords": []}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["moderation_check"].run(
        ctx, ACTIONS["moderation_check"].params(account="t"))
    assert "Отклонений нет" in out
    assert "Приостановлено вручную: нет." in out


async def test_include_archived_flag(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(f"{V5}/campaigns").mock(
        return_value=_ok({"Campaigns": []}))
    respx_mock.post(f"{V501}/adgroups").mock(
        return_value=_ok({"AdGroups": []}))
    respx_mock.post(f"{V5}/ads").mock(return_value=_ok({"Ads": []}))
    respx_mock.post(f"{V5}/keywords").mock(return_value=_ok({"Keywords": []}))
    ctx = _ctx(tmp_path)
    await ACTIONS["moderation_check"].run(
        ctx, ACTIONS["moderation_check"].params(account="t"))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "ARCHIVED" not in sent["SelectionCriteria"]["States"]
    ctx2 = _ctx(tmp_path)
    await ACTIONS["moderation_check"].run(
        ctx2, ACTIONS["moderation_check"].params(
            account="t", include_archived=True))
    sent2 = _json.loads(route.calls[1].request.content)["params"]
    assert "ARCHIVED" in sent2["SelectionCriteria"]["States"]


async def test_ads_list_clarification_columns(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/ads").mock(return_value=_ok({"Ads": _ads()[:2]}))
    respx_mock.post(f"{V5}/adextensions").mock(
        return_value=_ok({"AdExtensions": []}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["ads_list"].run(
        ctx, ACTIONS["ads_list"].params(account="t", ad_ids=[1, 2]))
    assert "StatusClarification" in out
    assert "PartialReject" in out
    assert "запрещённая лексика" in out
    assert "Заголовок 2" in out


def test_journal_rule_in_instructions_and_overview():
    from pathlib import Path

    from directai_mcp.server import INSTRUCTIONS

    text = " ".join(INSTRUCTIONS.split())
    assert "только операции DirectAI" in text
    assert "ручных изменений в кабинете" in text
    overview = (Path(__file__).resolve().parent.parent
                / "docs" / "PUBLIC-OVERVIEW.md").read_text(encoding="utf-8")
    assert "только операции DirectAI" in overview
