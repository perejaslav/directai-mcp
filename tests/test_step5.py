"""Step 5 tests: create/update validation, batch lines (SPEC 9)."""

import httpx
import pytest

from directai_mcp.catalog.common import summarize
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"
BASE_V501 = "https://api.direct.yandex.com/json/v501"


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


def _adgroups(items):
    return httpx.Response(200, json={"result": {"AdGroups": items}})


def _campaigns(items):
    return httpx.Response(200, json={"result": {"Campaigns": items}})


def test_summarize_partial_lines():
    lines, ok = summarize(
        ["a", "b", "c"],
        [{"Id": 1}, {"Errors": [{"Code": 5000, "Message": "bad"}]}, {"Id": 3}],
    )
    assert ok == 2
    assert lines[0] == "1: OK"
    assert lines[1].startswith("b: ОШИБКА 5000")


async def test_ads_create_without_display_no_plan(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_adgroups([{"Id": 5, "CampaignId": 2}])
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns([{"Id": 2, "Name": "[TEST DirectAI] Шаг 4"}])
    )
    # v1.8.0: типы группы/кампании — через v501.
    respx_mock.post(f"{BASE_V501}/adgroups").mock(
        return_value=_adgroups(
            [{"Id": 5, "CampaignId": 2, "Type": "TEXT_AD_GROUP"}])
    )
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_campaigns(
            [{"Id": 2, "Name": "[TEST DirectAI] Шаг 4", "Type": "TEXT_CAMPAIGN"}])
    )
    out = await do_plan_write(
        ctx,
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [
                {"title": "Эмаль тест", "text": "t", "href": "https://example.com"}
            ],
        },
    )
    assert out.startswith("Ошибка подготовки")
    assert "DisplayUrlPath" in out
    assert len(PLANS) == 0


async def test_responsive_create_title_warning(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    (tmp_path / "rules.toml").write_text(
        '[titles]\nproduct_words = ["эмаль", "краска"]\nmode = "warn"\n',
        encoding="utf-8",
    )
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_adgroups([{"Id": 5, "CampaignId": 2}])
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns([{"Id": 2, "Name": "[TEST DirectAI] Шаг 4"}])
    )
    # v1.8.0: типы группы/кампании — через v501.
    respx_mock.post(f"{BASE_V501}/adgroups").mock(
        return_value=_adgroups(
            [{"Id": 5, "CampaignId": 2, "Type": "TEXT_AD_GROUP"}])
    )
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_campaigns(
            [{"Id": 2, "Name": "[TEST DirectAI] Шаг 4", "Type": "TEXT_CAMPAIGN"}])
    )
    respx_mock.post("https://api.direct.yandex.com/json/v501/ads").mock(
        return_value=httpx.Response(200, json={"result": {"AddResults": [{"Id": 77}]}})
    )
    respx_mock.post(f"{BASE}/ads").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "Ads": [
                        {
                            "Id": 77,
                            "Type": "RESPONSIVE_AD",
                            "ResponsiveAd": {"DisplayUrlPath": "test"},
                        }
                    ]
                }
            },
        )
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
                    "titles": ["Тестовый заголовок"],
                    "texts": ["t"],
                    "href": "https://example.com",
                    "display_url_path": "test",
                }
            ],
        },
    )
    assert out.startswith("План ")
    assert "без слов" in out
    pid = out.split()[1].rstrip(":")
    denied = await do_apply_write(ctx, pid)
    assert "acknowledge_warnings=true" in denied
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус unverified" in applied or "статус applied" in applied


async def test_keywords_add_batch_partial(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_adgroups([{"Id": 5, "CampaignId": 2}])
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns([{"Id": 2, "Name": "[TEST DirectAI] Шаг 4"}])
    )
    respx_mock.post(f"{BASE}/keywords").mock(
        side_effect=[
            httpx.Response(
                200, json={"result": {"Keywords": []}}
            ),
            httpx.Response(
                200,
                json={
                    "result": {
                        "AddResults": [
                            {"Id": 101},
                            {"Errors": [{"Code": 5002, "Message": "bad"}]},
                        ]
                    }
                },
            ),
            httpx.Response(
                200, json={"result": {"Keywords": [{"Id": 101, "Keyword": "a"}]}}
            ),
        ]
    )
    out = await do_plan_write(
        ctx,
        "keywords_add",
        {"account": "t", "adgroup_id": 5, "keywords": [{"text": "a"}, {"text": "b"}]},
    )
    assert out.startswith("План ")
    pid = out.split()[1].rstrip(":")
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус partial" in applied
    assert "101: OK" in applied
    assert "ОШИБКА 5002" in applied
