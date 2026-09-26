"""v1.1.17: привязка расширений к объявлению (SitelinkSetId/AdExtensionIds)."""

import httpx
import pytest

from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"
V501 = "https://api.direct.yandex.com/json/v501"

SET_ID = 9000000003
EXT_IDS = [90000007, 90000008]


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=True,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _campaigns(items):
    return httpx.Response(200, json={"result": {"Campaigns": items}})


def _guard(respx_mock, adgroup_id=5):
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=httpx.Response(
            200, json={"result": {"AdGroups": [{"Id": adgroup_id, "CampaignId": 2}]}}
        )
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns([{"Id": 2, "Name": "[TEST DirectAI] Шаг 4"}])
    )


def _refs(respx_mock, sets=(SET_ID,), extensions=EXT_IDS):
    respx_mock.post(f"{BASE}/sitelinks").mock(
        return_value=httpx.Response(
            200,
            json={"result": {"SitelinksSets": [{"Id": i} for i in sets]}},
        )
    )
    respx_mock.post(f"{BASE}/adextensions").mock(
        return_value=httpx.Response(
            200,
            json={"result": {"AdExtensions": [{"Id": i} for i in extensions]}},
        )
    )


def _text_ad(ad_id=12, **extra):
    ad = {
        "Id": ad_id,
        "Type": "TEXT_AD",
        "AdGroupId": 5,
        "TextAd": {
            "Title": "Эмаль тест",
            "Text": "t",
            "Href": "https://example.com",
            "DisplayUrlPath": "test",
        },
    }
    ad["TextAd"].update(extra)
    return ad


def _responsive_ad(ad_id=11, **extra):
    ad = {
        "Id": ad_id,
        "Type": "RESPONSIVE_AD",
        "AdGroupId": 5,
        "ResponsiveAd": {
            "Titles": [{"Title": "Эмаль тест"}],
            "Texts": [{"Text": "t"}],
            "Href": "https://example.com",
            "DisplayUrlPath": "test",
        },
    }
    ad["ResponsiveAd"].update(extra)
    return ad


def _ads(items):
    return httpx.Response(200, json={"result": {"Ads": items}})


def _guard_ad(ad_id):
    """Ответ Ads.get для guard: id + группа объявления."""
    return httpx.Response(200, json={"result": {"Ads": [{"Id": ad_id, "AdGroupId": 5}]}})


def _added(ad_id):
    return httpx.Response(200, json={"result": {"AddResults": [{"Id": ad_id}]}})


def _updated(ad_id):
    return httpx.Response(200, json={"result": {"UpdateResults": [{"Id": ad_id}]}})


def _bound(ad_id, type_="TEXT_AD", set_id=SET_ID, ext_ids=EXT_IDS):
    """Объявление после привязки расширений."""
    extra = {
        "SitelinkSetId": set_id,
        "AdExtensions": [{"AdExtensionId": i} for i in ext_ids],
    }
    maker = _text_ad if type_ == "TEXT_AD" else _responsive_ad
    return maker(ad_id, **extra)


async def test_ads_create_preview_shows_all_fields(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [
                {
                    "title": "Эмаль тест",
                    "title2": "Второй заголовок",
                    "text": "Текст объявления",
                    "href": "https://example.com/price",
                    "display_url_path": "test-text",
                }
            ],
        },
    )
    assert out.startswith("План ")
    assert "TEXT_AD «Эмаль тест»" in out
    assert "заголовок 2 «Второй заголовок»" in out
    assert "текст «Текст объявления»" in out
    assert "Href https://example.com/price" in out
    assert "DisplayUrlPath test-text" in out


async def test_ads_create_responsive_preview_shows_display_url(
    tmp_path, respx_mock
):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "RESPONSIVE_AD",
            "responsive_ads": [
                {
                    "titles": ["Эмаль тест", "Заголовок два"],
                    "texts": ["Первый текст", "Второй текст"],
                    "href": "https://example.com",
                    "display_url_path": "test-2",
                }
            ],
        },
    )
    assert out.startswith("План ")
    assert "текст «Первый текст» (+1)" in out
    assert "Href https://example.com" in out
    assert "DisplayUrlPath test-2" in out


async def test_ads_create_binds_extensions(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock)
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [
                {
                    "title": "Эмаль тест",
                    "text": "t",
                    "href": "https://example.com",
                    "display_url_path": "test",
                    "sitelink_set_id": SET_ID,
                    "ad_extension_ids": EXT_IDS,
                }
            ],
        },
    )
    assert out.startswith("План ")
    assert f"набор ссылок {SET_ID}" in out
    assert "уточнений 2" in out
    body = PLANS.peek(out.split()[1].rstrip(":")).requests[0][2]["Ads"][0]["TextAd"]
    assert body["SitelinkSetId"] == SET_ID
    assert body["AdExtensionIds"] == EXT_IDS


async def test_ads_create_responsive_binds_extensions(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock)
    respx_mock.post(f"{V501}/ads").mock(return_value=_added(91))
    respx_mock.post(f"{BASE}/ads").mock(
        return_value=_ads([_bound(91, "RESPONSIVE_AD", ext_ids=[])])
    )
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "RESPONSIVE_AD",
            "responsive_ads": [
                {
                    "titles": ["Эмаль тест"],
                    "texts": ["t"],
                    "href": "https://example.com",
                    "display_url_path": "test",
                    "sitelink_set_id": SET_ID,
                }
            ],
        },
    )
    assert out.startswith("План ")
    pid = out.split()[1].rstrip(":")
    body = PLANS.peek(pid).requests[0][2]["Ads"][0]["ResponsiveAd"]
    assert body["SitelinkSetId"] == SET_ID
    assert PLANS.peek(pid).requests[0][3] == "v501"
    applied = await do_apply_write(ctx, pid)
    assert "статус applied" in applied


async def test_ads_create_unknown_sitelink_set_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock, sets=())
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [
                {
                    "title": "Эмаль тест",
                    "text": "t",
                    "href": "https://example.com",
                    "display_url_path": "test",
                    "sitelink_set_id": SET_ID,
                }
            ],
        },
    )
    assert out.startswith("Ошибка подготовки")
    assert "быстрых ссылок не найдены" in out
    assert str(SET_ID) in out
    assert len(PLANS) == 0


async def test_ads_create_unknown_callout_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock, extensions=[EXT_IDS[0]])
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [
                {
                    "title": "Эмаль тест",
                    "text": "t",
                    "href": "https://example.com",
                    "display_url_path": "test",
                    "ad_extension_ids": EXT_IDS,
                }
            ],
        },
    )
    assert out.startswith("Ошибка подготовки")
    assert "уточнения не найдены" in out
    assert str(EXT_IDS[1]) in out
    assert len(PLANS) == 0


async def test_ads_create_unverified_when_binding_ignored(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(
        side_effect=[_added(77), _ads([_text_ad(77)])]
    )
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [
                {
                    "title": "Эмаль тест",
                    "text": "t",
                    "href": "https://example.com",
                    "display_url_path": "test",
                    "sitelink_set_id": SET_ID,
                }
            ],
        },
    )
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(ctx, pid)
    assert "статус unverified" in applied
    assert "НЕ подтвердил привязку" in applied


async def test_ads_create_partial_response_skips_binding_check(tmp_path, respx_mock):
    """v1.1.17: создано меньше, чем запрошено — привязка не сверяется по индексу."""
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock)
    failed = httpx.Response(
        200,
        json={
            "result": {
                "AddResults": [
                    {
                        "Errors": [
                            {"Code": 5002, "Message": "Используются недопустимые символы"}
                        ]
                    },
                    {"Id": 78},
                ]
            }
        },
    )
    respx_mock.post(f"{BASE}/ads").mock(
        side_effect=[failed, _ads([_text_ad(78)])]
    )
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [
                {
                    "title": "Эмаль тест",
                    "text": "t",
                    "href": "https://example.com",
                    "display_url_path": "test",
                    "sitelink_set_id": SET_ID,
                },
                {
                    "title": "Эмаль тест 2",
                    "text": "t",
                    "href": "https://example.com",
                    "display_url_path": "test-2",
                },
            ],
        },
    )
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(ctx, pid)
    assert "статус partial" in applied
    assert "НЕ подтвердил привязку" not in applied
    assert "Привязка не сверялась: запрошено 2, создано 1" in applied


async def test_ads_update_binds_extensions_only(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(
        side_effect=[
            _ads([_text_ad()]),
            _updated(12),
            _ads([_bound(12)]),
        ]
    )
    out = await do_plan_write(
        ctx,
        "ads_update",
        {
            "account": "t",
            "ad_ids": [12],
            "display_url_path": "test",
            "sitelink_set_id": SET_ID,
            "ad_extension_ids": EXT_IDS,
        },
    )
    assert out.startswith("План ")
    assert "расширения: sitelink_set_id: None" in out
    # v1.1.37: display передаётся в каждой операции — виден как изменение.
    assert "display_url_path (4/20): test → test" in out
    pid = out.split()[1].rstrip(":")
    body = PLANS.peek(pid).requests[0][2]["Ads"][0]["TextAd"]
    assert body == {
        "DisplayUrlPath": "test",
        "SitelinkSetId": SET_ID,
        "CalloutSetting": {
            "AdExtensions": [
                {"AdExtensionId": EXT_IDS[0], "Operation": "SET"},
                {"AdExtensionId": EXT_IDS[1], "Operation": "SET"},
            ]
        },
    }
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус applied" in applied


async def test_ads_update_responsive_binds_extensions(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock)
    respx_mock.post(f"{V501}/ads").mock(return_value=_updated(11))
    respx_mock.post(f"{BASE}/ads").mock(
        side_effect=[
            _ads([_responsive_ad()]),
            _ads([_bound(11, "RESPONSIVE_AD", ext_ids=[EXT_IDS[0]])]),
        ]
    )
    out = await do_plan_write(
        ctx,
        "ads_update",
        {
            "account": "t",
            "ad_ids": [11],
            "display_url_path": "test",
            "sitelink_set_id": SET_ID,
            "ad_extension_ids": [EXT_IDS[0]],
        },
    )
    assert out.startswith("План ")
    pid = out.split()[1].rstrip(":")
    assert PLANS.peek(pid).requests[0][3] == "v501"
    body = PLANS.peek(pid).requests[0][2]["Ads"][0]["ResponsiveAd"]
    assert body["SitelinkSetId"] == SET_ID
    assert body["CalloutSetting"]["AdExtensions"] == [
        {"AdExtensionId": EXT_IDS[0], "Operation": "SET"}
    ]
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус applied" in applied


async def test_ads_update_unverified_when_binding_ignored(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(
        side_effect=[
            _ads([_text_ad()]),
            _updated(12),
            _ads([_text_ad()]),
        ]
    )
    out = await do_plan_write(
        ctx,
        "ads_update",
        {"account": "t", "ad_ids": [12], "display_url_path": "test",
         "sitelink_set_id": SET_ID},
    )
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус unverified" in applied
    assert "НЕ подтвердил" in applied
    assert f"{SET_ID}" in applied


async def test_ads_update_unknown_extension_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    _refs(respx_mock, extensions=())
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ads([_text_ad()]))
    out = await do_plan_write(
        ctx,
        "ads_update",
        {"account": "t", "ad_ids": [12], "display_url_path": "test",
         "ad_extension_ids": EXT_IDS},
    )
    assert out.startswith("Ошибка подготовки")
    assert "уточнения не найдены" in out
    assert len(PLANS) == 0


async def test_ads_update_without_fields_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    _guard(respx_mock)
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ads([_text_ad()]))
    out = await do_plan_write(ctx, "ads_update", {"account": "t", "ad_ids": [12]})
    assert out.startswith("Ошибка подготовки")
    assert "укажите хотя бы одно поле" in out
    assert "sitelink_set_id" in out
    assert len(PLANS) == 0


async def test_ads_list_shows_bindings(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    ad = _bound(11, "RESPONSIVE_AD", ext_ids=[EXT_IDS[0]])
    ad["Status"] = "DRAFT"
    respx_mock.post(f"{BASE}/ads").mock(return_value=_ads([ad]))
    respx_mock.post(f"{BASE}/sitelinks").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "SitelinksSets": [
                        {
                            "Id": SET_ID,
                            "Sitelinks": [
                                {"Title": "О нас", "Href": "https://example.com/about"}
                            ],
                        }
                    ]
                }
            },
        )
    )
    respx_mock.post(f"{BASE}/adextensions").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "AdExtensions": [
                        {
                            "Id": EXT_IDS[0],
                            "Type": "CALLOUT",
                            "Callout": {"CalloutText": "Прямой API"},
                        }
                    ]
                }
            },
        )
    )
    out = await ACTIONS["ads_list"].run(
        ctx, ACTIONS["ads_list"].params(account="t", adgroup_ids=[5])
    )
    assert "SitelinkSetId" in out
    assert str(SET_ID) in out
    assert "О нас (https://example.com/about)" in out
    assert "AdExtensionIds" in out
    assert str(EXT_IDS[0]) in out
    assert "Прямой API" in out
    assert "Moderation" in out
    assert "DRAFT" in out
    # v1.1.17: в ячейке не может быть "|" — иначе таблица разъезжается
    rows = [ln for ln in out.splitlines() if str(11) in ln]
    assert rows and rows[0].count("|") == 15  # 14 колонок ads_list (v1.1.25)
