"""v1.2.5 (этап 1, экспериментально): Аудитории — 2 read-действия, отдельный токен."""

import inspect

import httpx
import respx

import directai_mcp.catalog.audience_segments as aud
import directai_mcp.config as cfg
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

NAMES = ("audience_segments_list", "audience_segment_get")
BASE = "https://api-audience.yandex.ru/v1/management/segments"


def _ctx(tmp_path, token="main-token") -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token=token, data_dir=tmp_path)


def _seg(sid=7, **extra):
    seg = {
        "id": sid,
        "name": "Buyers",
        "type": "uploading",
        "status": "processed",
        "create_time": "2026-09-01T00:00:00Z",
        "owner": "agency-login",
        "content_type": "crm",
        "hashed": True,
        "matched_quantity": 150,
    }
    seg.update(extra)
    return seg


def test_audience_actions_registered_read_only():
    for name in NAMES:
        act = ACTIONS[name]
        assert act.mode == "read"
        assert act.run is not None
        assert act.prepare is None and act.apply is None and act.verify is None


def test_audience_params_schema():
    assert aud.AudienceSegmentGetParams.model_fields["segment_id"].is_required()
    p = aud.AudienceSegmentGetParams(segment_id=7)
    assert p.output == "inline"
    assert p.format == "json"
    assert aud.AudienceSegmentsListParams().limit is None


def test_audience_token_prefers_separate(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: "aud-token")
    assert aud._token(ctx) == "aud-token"


def test_audience_token_fallback_to_main(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    assert aud._token(ctx) == "main-token"


async def test_audience_no_token(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path, token="")
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    out = await ACTIONS["audience_segments_list"].run(
        ctx, aud.AudienceSegmentsListParams()
    )
    assert "нет токена" in out
    assert "set-token --audience" in out


def test_audience_get_forces_ipv4_oauth():
    import directai_mcp.api.audience as api

    src = inspect.getsource(api._get)
    assert "AsyncHTTPTransport" in src
    assert 'local_address="0.0.0.0"' in src
    assert '"OAuth " + token' in src or "'OAuth ' + token" in src or '"OAuth "' in src


@respx.mock
async def test_audience_list_renders_and_sends_oauth(monkeypatch, tmp_path):
    route = respx.get(BASE).mock(
        return_value=httpx.Response(200, json={"segments": [_seg()]})
    )
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    out = await ACTIONS["audience_segments_list"].run(
        _ctx(tmp_path), aud.AudienceSegmentsListParams()
    )
    assert route.called
    assert route.calls[0].request.headers["Authorization"] == "OAuth main-token"
    assert "Buyers" in out
    assert "150" in out


@respx.mock
async def test_audience_segment_get_by_id(monkeypatch, tmp_path):
    respx.get(BASE).mock(
        return_value=httpx.Response(
            200, json={"segments": [_seg(7), _seg(9, name="Geo")]}
        )
    )
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    out = await ACTIONS["audience_segment_get"].run(
        _ctx(tmp_path), aud.AudienceSegmentGetParams(segment_id=9)
    )
    assert "Geo" in out
    assert "Buyers" not in out


@respx.mock
async def test_audience_segment_get_missing(monkeypatch, tmp_path):
    respx.get(BASE).mock(
        return_value=httpx.Response(200, json={"segments": [_seg(7)]})
    )
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    out = await ACTIONS["audience_segment_get"].run(
        _ctx(tmp_path), aud.AudienceSegmentGetParams(segment_id=404)
    )
    assert "не найден" in out


@respx.mock
async def test_audience_empty_list(monkeypatch, tmp_path):
    respx.get(BASE).mock(
        return_value=httpx.Response(200, json={"segments": []})
    )
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    out = await ACTIONS["audience_segments_list"].run(
        _ctx(tmp_path), aud.AudienceSegmentsListParams()
    )
    assert "сегментов 0" in out


@respx.mock
async def test_audience_401_403_clear_text_no_token_leak(
    monkeypatch, tmp_path
):
    for code, hint in (
        (401, "set-token --audience"),
        (403, "нет прав Аудиторий"),
    ):
        respx.get(BASE).mock(
            return_value=httpx.Response(code, text="denied")
        )
        monkeypatch.setattr(
            cfg, "get_audience_token", lambda login: "aud-secret-xyz"
        )
        out = await ACTIONS["audience_segments_list"].run(
            _ctx(tmp_path), aud.AudienceSegmentsListParams()
        )
        assert hint in out
        assert "aud-secret-xyz" not in out
