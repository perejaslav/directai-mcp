"""Wordstat Cloud API integration: transport, catalog, config and setup CLI."""

import json

import httpx
import keyring
import pytest

import directai_mcp.catalog.wordstat as ws
import directai_mcp.config as cfg
import directai_mcp.server as server_mod
from directai_mcp.api.wordstat import _scrub, post
from directai_mcp.catalog.registry import ACTIONS, Ctx, search
from directai_mcp.config import AccountEntry, Settings

BASE = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
SECRET = "wordstat-secret-test"


async def test_wordstat_success_scrubs_nested_secret_without_truncation(respx_mock):
    prefix, suffix = "a" * 600, "z" * 600
    payload = {"nested": [{"text": prefix + SECRET + suffix}]}
    respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(200, json=payload)
    )
    result = await post(SECRET, "topRequests")
    assert result == {"nested": [{"text": prefix + "[REDACTED]" + suffix}]}
    assert SECRET not in json.dumps(result)


async def test_wordstat_success_scrubs_dict_keys(respx_mock):
    payload = {"nested": [{f"before-{SECRET}-after": "value"}]}
    respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(200, json=payload)
    )
    result = await post(SECRET, "topRequests")
    assert result == {"nested": [{"before-[REDACTED]-after": "value"}]}


async def test_wordstat_success_preserves_payload_without_secret(respx_mock):
    payload = {"nested": [{"text": "x" * 1200, "count": 42}], "flag": True, "empty": None}
    respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(200, json=payload)
    )
    assert await post(SECRET, "topRequests") == payload


def test_wordstat_scrub_preserves_tuple_and_scalar_types():
    result = _scrub((SECRET, [1, 2.5, True, None]), SECRET)
    assert isinstance(result, tuple)
    assert result == ("[REDACTED]", [1, 2.5, True, None])
    assert [type(item) for item in result[1]] == [int, float, bool, type(None)]


def test_wordstat_scrub_empty_secret_returns_original():
    payload = {SECRET: [SECRET, (SECRET,)]}
    assert _scrub(payload, "") is payload


async def test_wordstat_top_dump_scrubs_echoed_secret(respx_mock, tmp_path):
    payload = {
        "results": [{"phrase": "paint", "count": "12"}],
        "nested": [{f"field-{SECRET}": "a" * 600 + SECRET + "z" * 600}],
    }
    respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(200, json=payload)
    )
    dump_dir = tmp_path / "dump"
    await ACTIONS["wordstat_top"].run(
        _ctx(tmp_path),
        ws.WordstatTopParams(phrase="paint", output="file", dump_dir=str(dump_dir)),
    )
    files = list(dump_dir.glob("[0-9]*_wordstat_top.json"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert SECRET not in content
    raw = json.loads(content)["sections"]["wordstat_top"]["raw_items"]
    assert raw == [{
        "results": payload["results"],
        "nested": [{"field-[REDACTED]": "a" * 600 + "[REDACTED]" + "z" * 600}],
    }]


def _ctx(tmp_path) -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        wordstat_folder_id="folder-test-123",
    )
    return Ctx(
        settings=settings,
        token="",
        data_dir=tmp_path,
        wordstat_api_key=SECRET,
        wordstat_folder_id="folder-test-123",
    )


def test_ctx_repr_hides_direct_and_wordstat_credentials(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.token = "direct-oauth-test-secret"
    assert ctx.token not in repr(ctx)
    assert ctx.wordstat_api_key not in repr(ctx)


def test_wordstat_actions_are_discoverable_and_provider_scoped():
    names = {action.name for action in search("wordstat частотность", "read")}
    assert "wordstat_top" in names
    for name in (
        "wordstat_top",
        "wordstat_dynamics",
        "wordstat_regions",
        "wordstat_regions_tree",
    ):
        assert ACTIONS[name].mode == "read"
        assert ACTIONS[name].provider == "wordstat"
        assert ACTIONS[name].prepare is None


def test_wordstat_validation():
    with pytest.raises(ValueError):
        ws.WordstatTopParams(phrase="x", num_phrases=2001)
    with pytest.raises(ValueError):
        ws.WordstatTopParams(phrase="x", devices=["DEVICE_UNKNOWN"])
    with pytest.raises(ValueError):
        ws.WordstatTopParams(phrase="x", regions=[str(i) for i in range(101)])
    with pytest.raises(ValueError):
        ws.WordstatTopParams(phrase="x", devices=["DEVICE_ALL"] * 4)
    with pytest.raises(ValueError):
        ws.WordstatTopParams(phrase="x" * 401)
    with pytest.raises(ValueError):
        ws.WordstatDynamicsParams(
            phrase="x",
            from_date="2026-01-01T00:00:00Z",
            to_date="bad",
        )
    assert ws._dates(
        ws.WordstatDynamicsParams(
            phrase="x",
            period="PERIOD_WEEKLY",
            from_date="2025-12-29T00:00:00Z",
            to_date="2026-01-25T00:00:00Z",
        )
    ) == ("2025-12-29T00:00:00Z", "2026-01-25T00:00:00Z")
    assert isinstance(
        ws._dates(
            ws.WordstatDynamicsParams(
                phrase="x",
                period="PERIOD_WEEKLY",
                from_date="2026-01-01T00:00:00Z",
                to_date="2026-01-31T00:00:00Z",
            )
        ),
        str,
    )
    assert ws._dates(
        ws.WordstatDynamicsParams(
            phrase="x",
            period="PERIOD_DAILY",
            from_date="2025-12-01T00:00:00Z",
        )
    ) == ("2025-12-01T00:00:00Z", "")
    assert "будущем" in ws._dates(
        ws.WordstatDynamicsParams(
            phrase="x",
            period="PERIOD_DAILY",
            from_date="2099-12-01T00:00:00Z",
        )
    )


async def test_wordstat_top_auth_payload_and_rendering(respx_mock, tmp_path):
    route = respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(
            200,
            json={
                "totalCount": "33276",
                "results": [{"phrase": "краска для бетона", "count": "33276"}],
                "associations": [{"phrase": "краска по бетону", "count": "6661"}],
            },
        )
    )
    out = await ACTIONS["wordstat_top"].run(
        _ctx(tmp_path),
        ws.WordstatTopParams(
            phrase="краска для бетона",
            num_phrases=20,
            regions=["213"],
        ),
    )
    assert route.called
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Api-Key " + SECRET
    assert json.loads(request.content) == {
        "phrase": "краска для бетона",
        "numPhrases": 20,
        "regions": ["213"],
        "devices": ["DEVICE_ALL"],
        "folderId": "folder-test-123",
    }
    assert "33276" in out
    assert "краска по бетону" in out


async def test_wordstat_top_dump_keeps_complete_api_payload(respx_mock, tmp_path):
    payload = {
        "totalCount": "12",
        "results": [{"phrase": "краска", "count": "12"}],
        "associations": [{"phrase": "краска фасадная", "count": "3"}],
    }
    respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(200, json=payload)
    )
    dump_dir = tmp_path / "dump"
    await ACTIONS["wordstat_top"].run(
        _ctx(tmp_path),
        ws.WordstatTopParams(
            phrase="краска", output="file", dump_dir=str(dump_dir)
        ),
    )
    envelopes = list(dump_dir.glob("*_wordstat_top.json"))
    assert envelopes
    envelope = json.loads(envelopes[0].read_text(encoding="utf-8"))
    assert envelope["sections"]["wordstat_top"]["raw_items"] == [payload]


async def test_mcp_wordstat_discovery_schema_and_run_read(
    monkeypatch, respx_mock, tmp_path
):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        wordstat_folder_id="folder-test-123",
    )
    monkeypatch.setattr(server_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(server_mod, "get_wordstat_api_key", lambda _login: SECRET)
    respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(
            200,
            json={"totalCount": "4", "results": [{"phrase": "краска", "count": "4"}]},
        )
    )
    mcp = server_mod.build_server()
    tools = mcp._tool_manager._tools
    assert "wordstat_top" in tools["search_actions"].fn("wordstat", "read")
    assert "num_phrases" in tools["describe_action"].fn("wordstat_top")
    out = await tools["run_read"].fn("wordstat_top", {"phrase": "краска"})
    assert "краска" in out and "4" in out


async def test_wordstat_dynamics_payload(respx_mock, tmp_path):
    route = respx_mock.post(f"{BASE}/dynamics").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"date": "2026-01-01T00:00:00Z", "count": "100", "share": 0.1}
                ]
            },
        )
    )
    out = await ACTIONS["wordstat_dynamics"].run(
        _ctx(tmp_path),
        ws.WordstatDynamicsParams(
            phrase="краска",
            period="PERIOD_MONTHLY",
            from_date="2026-01-01T00:00:00Z",
            to_date="2026-01-31T23:59:59Z",
            devices=["DEVICE_PHONE"],
        ),
    )
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "phrase": "краска",
        "period": "PERIOD_MONTHLY",
        "fromDate": "2026-01-01T00:00:00Z",
        "toDate": "2026-01-31T23:59:59Z",
        "devices": ["DEVICE_PHONE"],
        "folderId": "folder-test-123",
    }
    assert "2026-01-01" in out and "100" in out


async def test_wordstat_regions_and_tree_shapes(respx_mock, tmp_path):
    regions = respx_mock.post(f"{BASE}/regions").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"region": "213", "count": "10", "share": 0.2, "affinityIndex": 4.0}
                ]
            },
        )
    )
    out = await ACTIONS["wordstat_regions"].run(
        _ctx(tmp_path),
        ws.WordstatRegionsParams(phrase="краска", region="REGION_CITIES"),
    )
    assert json.loads(regions.calls[0].request.content)["region"] == "REGION_CITIES"
    assert "213" in out and "4.0" in out

    tree = respx_mock.post(f"{BASE}/getRegionsTree").mock(
        return_value=httpx.Response(
            200,
            json={
                "regions": [
                    {
                        "id": "225",
                        "label": "Россия",
                        "children": [{"id": "213", "label": "Москва"}],
                    },
                    {"id": "149", "name": "Беларусь"},
                ]
            },
        )
    )
    tree_out = await ACTIONS["wordstat_regions_tree"].run(
        _ctx(tmp_path), ws.WordstatRegionsTreeParams()
    )
    assert json.loads(tree.calls[0].request.content) == {"folderId": "folder-test-123"}
    assert "Россия" in tree_out and "Москва" in tree_out and "Беларусь" in tree_out


async def test_wordstat_error_does_not_leak_secret(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/topRequests").mock(
        return_value=httpx.Response(403, text=f"denied {SECRET}")
    )
    out = await ACTIONS["wordstat_top"].run(
        _ctx(tmp_path), ws.WordstatTopParams(phrase="краска")
    )
    assert "Ошибка Wordstat: HTTP 403" in out
    assert SECRET not in out
    assert "[REDACTED]" in out


def test_load_settings_reads_non_secret_folder_id(tmp_path):
    path = tmp_path / "accounts.toml"
    path.write_text(
        '[auth]\nlogin = "agency-login"\n\n'
        '[aliases.m]\nlogin = "agency-login"\n\n'
        '[wordstat]\nfolder_id = "folder-abc"\n',
        encoding="utf-8",
    )
    settings = cfg.load_settings(path)
    assert settings.wordstat_folder_id == "folder-abc"


def test_set_wordstat_saves_keyring_and_folder_without_secret(
    monkeypatch, tmp_path, capsys
):
    path = tmp_path / "accounts.toml"
    original = (
        '# keep this comment\r\n'
        '[auth]\r\nlogin = "agency-login"\r\n\r\n'
        '[aliases.m]\r\nlogin = "agency-login"\r\n'
    )
    path.write_bytes(original.encode("utf-8"))
    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    values = iter(["folder-cli-1", SECRET])
    monkeypatch.setattr("directai_mcp.cli.getpass.getpass", lambda _prompt: next(values))
    saved: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, login, value: saved.append((service, login, value)),
    )
    from directai_mcp.cli import cmd_set_token

    assert cmd_set_token(wordstat=True) == 0
    assert saved == [(cfg.KEYRING_SERVICE_WORDSTAT, "agency-login", SECRET)]
    updated = path.read_bytes().decode("utf-8")
    assert updated.startswith(original)
    assert "[wordstat]\r\nfolder_id = \"folder-cli-1\"\r\n" in updated
    assert SECRET not in capsys.readouterr().out
