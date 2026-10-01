"""v1.4.0: файловый конверт dump (ТЗ §2.1–2.2, решения 1–6)."""

import json

import directai_mcp.catalog.dump as dump_mod
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

BIG_ID = 9000000000000000007


def _ctx(tmp_path) -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _entry() -> AccountEntry:
    return AccountEntry(alias="m", login="agency-login")


def _fake_map(items):
    async def fake(ctx, account_value, fn):
        return [(_entry(), items)]

    return fake


def _read_manifest(dump_dir):
    with open(dump_dir / "manifest.json", encoding="utf-8") as f:
        return json.load(f)


def test_envelope_keywords_raw_ids_money(monkeypatch, tmp_path):
    import asyncio

    import directai_mcp.catalog.keywords as kw

    items = [{
        "Id": BIG_ID, "AdGroupId": 900000010,
        "Keyword": "эмаль -купить -бесплатно",
        "State": "ON", "Status": "ACCEPTED", "ServingStatus": "ELIGIBLE",
        "Bid": 120000000, "ContextBid": 300000,
    }]
    monkeypatch.setattr(kw, "map_accounts", _fake_map(items))
    dump_dir = tmp_path / "dump"
    out = asyncio.run(ACTIONS["keywords_list"].run(
        _ctx(tmp_path), kw.KeywordsListParams(
            account="m", campaign_ids=[900000031],
            dump_dir=str(dump_dir))))
    assert "Dump-конверт" in out
    manifest = _read_manifest(dump_dir)
    assert len(manifest) == 1 and manifest[0]["action"] == "keywords_list"
    env = json.loads((dump_dir / manifest[0]["file"]).read_text(
        encoding="utf-8"))
    assert env["envelope_version"] == 1
    assert env["account_login"] == "agency-login"
    assert env["requested_field_names"]["FieldNames"]
    raw = env["sections"]["keywords_list"]["raw_items"][0]
    assert raw["Id"] == str(BIG_ID)  # ID строкой (ошибка 7)
    assert raw["Bid"] == 120000000  # micros без преобразований (ошибка 2)
    assert raw["Keyword"] == "эмаль -купить -бесплатно"  # без чистки (ошибка 13)
    assert raw["linked_to_campaign"] is True
    disp = env["sections"]["keywords_list"]["display_rows"][0]
    assert disp["Id"] == str(BIG_ID)
    # sha256 манифеста сходится с файлом.
    import hashlib

    assert manifest[0]["sha256"] == hashlib.sha256(
        (dump_dir / manifest[0]["file"]).read_bytes()).hexdigest()
    # describe один раз.
    assert (dump_dir / "describe_keywords_list.json").exists()


def test_envelope_two_calls_two_files(monkeypatch, tmp_path):
    import asyncio

    import directai_mcp.catalog.keywords as kw

    items = [{"Id": 1, "AdGroupId": 5, "Keyword": "фраза"}]
    monkeypatch.setattr(kw, "map_accounts", _fake_map(items))
    dump_dir = tmp_path / "dump"
    for tag in (None, "archived"):
        asyncio.run(ACTIONS["keywords_list"].run(
            _ctx(tmp_path), kw.KeywordsListParams(
                account="m", campaign_ids=[7], dump_dir=str(dump_dir),
                dump_tag=tag)))
    manifest = _read_manifest(dump_dir)
    assert [m["seq"] for m in manifest] == [1, 2]
    assert manifest[0]["file"] == "01_keywords_list.json"
    assert manifest[1]["file"] == "02_keywords_list_archived.json"


def test_envelope_truncated_honest(monkeypatch, tmp_path):
    # v1.4.1: фейк без tally — полнота неизвестна: честный null,
    # а не default true; display-обрезка отдельно.
    import asyncio

    import directai_mcp.catalog.keywords as kw

    items = [{"Id": i, "Keyword": f"ф{i}"} for i in range(25)]
    monkeypatch.setattr(kw, "map_accounts", _fake_map(items))
    dump_dir = tmp_path / "dump"
    asyncio.run(ACTIONS["keywords_list"].run(
        _ctx(tmp_path), kw.KeywordsListParams(
            account="m", campaign_ids=[7], dump_dir=str(dump_dir))))
    env = json.loads((dump_dir / "01_keywords_list.json").read_text(
        encoding="utf-8"))
    assert env["pagination_complete"] is None
    assert env["truncated"] is None
    assert env["display_truncated"] is True
    assert len(env["sections"]["keywords_list"]["raw_items"]) == 25


def test_envelope_display_cut_raw_full(tmp_path):
    # v1.4.1: display обрезан (25 строк), raw полный и пагинация
    # завершена → truncated=false, display_truncated=true.
    from directai_mcp.catalog.common import write_dump_sections

    dump_dir = tmp_path / "dump"
    rows = [{"Id": str(i)} for i in range(25)]
    write_dump_sections(
        _ctx(tmp_path), str(dump_dir), "keywords_list", "m",
        {"account": "m"}, {
            "keywords_list": {
                "columns": ["Id"], "display_rows": rows,
                "raw_items": [dict(r, linked_to_campaign=True)
                              for r in rows]},
        },
        {"FieldNames": ["Id"]},
        {"pages": 2, "versions": ["v5"], "complete": True},
        ["agency-login"], "campaign", [], True)
    env = json.loads((dump_dir / "01_keywords_list.json").read_text(
        encoding="utf-8"))
    assert env["pagination_complete"] is True
    assert env["truncated"] is False
    assert env["display_truncated"] is True
    manifest = _read_manifest(dump_dir)
    assert manifest[0]["truncated"] is False
    assert manifest[0]["pagination_complete"] is True


def test_envelope_raw_incomplete(tmp_path):
    # v1.4.1: пагинация не завершена → truncated=true (неполнота raw).
    from directai_mcp.catalog.common import write_dump_sections

    dump_dir = tmp_path / "dump"
    rows = [{"Id": str(i)} for i in range(3)]
    write_dump_sections(
        _ctx(tmp_path), str(dump_dir), "keywords_list", "m",
        {"account": "m"}, {
            "keywords_list": {
                "columns": ["Id"], "display_rows": rows,
                "raw_items": [dict(r, linked_to_campaign=True)
                              for r in rows]},
        },
        {"FieldNames": ["Id"]},
        {"pages": 1, "versions": ["v5"], "complete": False},
        ["agency-login"], "campaign", [], False)
    env = json.loads((dump_dir / "01_keywords_list.json").read_text(
        encoding="utf-8"))
    assert env["pagination_complete"] is False
    assert env["truncated"] is True
    assert env["display_truncated"] is False


def test_envelope_metrika_complete(monkeypatch, tmp_path):
    # v1.4.1: полный ответ Метрики — complete=true без tally Direct.
    import asyncio

    import directai_mcp.catalog.counters as cc

    async def fake_info(token, counter_id):
        return {"name": "C", "site": "s"}

    async def fake_types(token, counter_id):
        return {"1": "action"}

    async def fake_names(token, counter_id):
        return {"1": "G"}

    monkeypatch.setattr(cc, "counter_info", fake_info)
    monkeypatch.setattr(cc, "counter_goal_types", fake_types)
    monkeypatch.setattr(cc, "counter_goal_names", fake_names)
    dump_dir = tmp_path / "dump"
    asyncio.run(ACTIONS["metrika_goals_list"].run(
        _ctx(tmp_path), cc.MetrikaGoalsListParams(
            account="m", counter_ids=[7], dump_dir=str(dump_dir))))
    env = json.loads((dump_dir / "01_metrika_goals_list.json").read_text(
        encoding="utf-8"))
    assert env["pagination_complete"] is True
    assert env["truncated"] is False
    assert env["display_truncated"] is False
    assert len(env["sections"]["metrika_goals_list"]["raw_items"]) == 1


def test_envelope_audiences_linked(monkeypatch, tmp_path):
    import asyncio

    import directai_mcp.catalog.audiences as au

    targets = [{"Id": 10, "CampaignId": 7, "AdGroupId": 5,
                "RetargetingListId": 900000021, "State": "ON"}]
    lists = [{"Id": 900000021, "Type": "RETARGETING", "Name": "L1",
              "IsAvailable": "YES", "Scope": "X", "Rules": [],
              "AvailableForTargetsInAdGroupTypes": {"Items": []}},
             {"Id": 900000022, "Type": "AUDIENCE", "Name": "L2",
              "IsAvailable": "YES", "Scope": "X", "Rules": [],
              "AvailableForTargetsInAdGroupTypes": {"Items": []}}]
    monkeypatch.setattr(au, "map_accounts", _fake_map(
        (targets, lists, {"targets": [], "lists": []})))
    dump_dir = tmp_path / "dump"
    asyncio.run(ACTIONS["audiences_list"].run(
        _ctx(tmp_path), au.AudiencesListParams(
            account="m", campaign_ids=[7], dump_dir=str(dump_dir))))
    env = json.loads((dump_dir / "01_audiences_list.json").read_text(
        encoding="utf-8"))
    assert set(env["sections"]) == {"audiences_targets", "audiences_lists"}
    flags = {i["Id"]: i["linked_to_campaign"]
             for i in env["sections"]["audiences_lists"]["raw_items"]}
    assert flags == {"900000021": True, "900000022": False}


def test_envelope_feeds_scope_cabinet(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map(
        [{"Id": 1, "Name": "F"}]))
    dump_dir = tmp_path / "dump"
    asyncio.run(ACTIONS["feeds_get"].run(
        _ctx(tmp_path), dump_mod.FeedsGetParams(
            account="m", dump_dir=str(dump_dir))))
    manifest = _read_manifest(dump_dir)
    assert manifest[0]["scope"] == "cabinet"
    env = json.loads((dump_dir / manifest[0]["file"]).read_text(
        encoding="utf-8"))
    assert env["sections"]["feeds_get"]["raw_items"][0][
        "linked_to_campaign"] is False
    # tally пустой (фейк без API) — честный null, не default true.
    assert env["pagination_complete"] is None
    assert any("пагинации" in w for w in env["warnings"])


def test_envelope_params_dump_dir_schema():
    fields = ACTIONS["keywords_list"].params.model_fields
    assert "dump_dir" in fields and "dump_tag" in fields
    assert not fields["dump_dir"].is_required()


def test_envelope_negatives_sections_raw(monkeypatch, tmp_path):
    import asyncio

    import directai_mcp.catalog.negatives as neg

    payload = (
        [{"Id": 7, "Name": "C", "NegativeKeywords": {"Items": ["a"]}}],
        [{"Id": 5, "CampaignId": 7, "Name": "G",
          "NegativeKeywords": {"Items": []}}],
        [{"Id": 900000051, "Name": "S",
          "NegativeKeywords": {"Items": ["b"]}}],
        {"Campaigns": {}, "AdGroups": {}, "NegativeKeywordSharedSets": {}},
    )
    monkeypatch.setattr(neg, "map_accounts", _fake_map(payload))
    dump_dir = tmp_path / "dump"
    asyncio.run(ACTIONS["negatives_audit"].run(
        _ctx(tmp_path), neg.NegativesAuditParams(
            account="m", campaign_ids=[7], dump_dir=str(dump_dir))))
    env = json.loads((dump_dir / "01_negatives_audit.json").read_text(
        encoding="utf-8"))
    assert set(env["sections"]) == {
        "negatives_audit", "campaigns", "adgroups",
        "negative_keyword_shared_sets"}
    for sec in ("campaigns", "adgroups", "negative_keyword_shared_sets"):
        assert len(env["sections"][sec]["raw_items"]) == 1
        assert env["sections"][sec]["raw_items"][0][
            "linked_to_campaign"] is True
