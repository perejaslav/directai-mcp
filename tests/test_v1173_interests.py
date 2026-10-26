"""dictionaries_get: AudienceInterests — путь по дереву и фильтр."""

import httpx

import directai_mcp.catalog.dictionaries as _d  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import Settings

BASE = "https://api.direct.yandex.com/json/v5"


async def test_interests_path_and_filter(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/dictionaries").mock(return_value=httpx.Response(200, json={
        "result": {"AudienceInterests": [
            {"InterestKey": 1, "ParentId": 0, "Name": "Дом и сад",
             "InterestType": "ANY", "IsTargetable": "NO"},
            {"InterestKey": 2, "ParentId": 1, "Name": "Дача",
             "InterestType": "SHORT_TERM", "IsTargetable": "YES"},
            {"InterestKey": 3, "ParentId": 0, "Name": "Авто",
             "InterestType": "ANY", "IsTargetable": "YES"},
        ]}}))
    ctx = Ctx(settings=Settings(auth_login="x", guard=False), token="fake", data_dir=tmp_path)
    act = ACTIONS["dictionaries_get"]
    out = await act.run(ctx, act.params(name="дача", dictionary="AudienceInterests"))
    assert "Дом и сад → Дача" in out and "Авто" not in out
    out = await act.run(ctx, act.params(name="*", dictionary="AudienceInterests"))
    assert "совпадений 3" in out
