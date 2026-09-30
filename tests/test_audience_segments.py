"""v1.2.5 (этап 1, экспериментально): Аудитории — 2 read-действия, отдельный токен."""

import inspect

import httpx
import pytest
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


@respx.mock
async def test_check_audience_not_configured_no_api_call(monkeypatch):
    from directai_mcp.cli import _check_audience

    route = respx.get(BASE).mock(
        return_value=httpx.Response(200, json={"segments": [_seg()]})
    )
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    out = await _check_audience("agency-login", "main-token")
    assert out == (
        "Аудитории: не настроены "
        "(необязательно: directai-mcp set-token --audience)"
    )
    assert not route.called


@respx.mock
async def test_check_audience_ok_with_separate_token(monkeypatch):
    from directai_mcp.cli import _check_audience

    respx.get(BASE).mock(
        return_value=httpx.Response(
            200, json={"segments": [_seg(7), _seg(9)]}
        )
    )
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: "aud-token")
    out = await _check_audience("agency-login", "main-token")
    assert out == "OK Аудитории: 2 сегментов"


def _load_script():
    """Модуль scripts/audience_segments.py как его запустит человек."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "audience_segments.py"
    )
    spec = importlib.util.spec_from_file_location(
        "audience_segments_script", path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_script_cfg(monkeypatch, tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    monkeypatch.setattr(cfg, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(cfg, "get_token", lambda login: "main-token")
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: "aud-token")


@respx.mock
async def test_script_run_list_contains_segment(monkeypatch, tmp_path):
    respx.get(BASE).mock(
        return_value=httpx.Response(200, json={"segments": [_seg(7)]})
    )
    _mock_script_cfg(monkeypatch, tmp_path)
    out = await _load_script()._run(None, "json")
    assert "Buyers" in out
    assert "7" in out


@respx.mock
async def test_script_run_get_by_id(monkeypatch, tmp_path):
    respx.get(BASE).mock(
        return_value=httpx.Response(
            200, json={"segments": [_seg(7), _seg(9, name="Geo")]}
        )
    )
    _mock_script_cfg(monkeypatch, tmp_path)
    out = await _load_script()._run(9, "json")
    assert "Geo" in out
    assert "Buyers" not in out


@respx.mock
def test_script_main_list_end_to_end(monkeypatch, tmp_path, capsys):
    respx.get(BASE).mock(
        return_value=httpx.Response(200, json={"segments": [_seg(7)]})
    )
    _mock_script_cfg(monkeypatch, tmp_path)
    rc = _load_script().main([])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "Buyers" in out


def test_script_unknown_action_clear_error():
    with pytest.raises(RuntimeError, match="не найдено в реестре"):
        _load_script()._resolve_action("no_such_action")


def _write_test_home(tmp_path):
    home = tmp_path / "dhome"
    home.mkdir(exist_ok=True)
    (home / "accounts.toml").write_text(
        '[auth]\nlogin = "agency-login"\n\n'
        '[aliases.m]\nlogin = "agency-login"\n',
        encoding="utf-8",
    )
    return home


def test_script_fresh_process_human_path(tmp_path):
    """Путь человека целиком в свежем процессе (без импорта сервера заранее).

    Старый код падал здесь с KeyError: main печатал
    "Ошибка: 'audience_segments_list'", exit 1. Новый код резолвит действие
    и упирается в API: онлайн — детерминированный 403 на dummy-токене
    (read-only GET, секретов и побочек нет), офлайн — сетевая ошибка;
    в обоих случаях exit 0 и префикс «Ошибка Аудиторий: ».
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    home = _write_test_home(tmp_path)
    script = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "audience_segments.py"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in ("http_proxy", "https_proxy", "no_proxy")
    }
    env.update(
        {
            "DIRECTAI_HOME": str(home),
            "DIRECTAI_TOKEN": "dummy-main",
            "DIRECTAI_AUDIENCE_TOKEN": "dummy-aud",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        cwd=str(Path(__file__).resolve().parent.parent),
        check=False,
    )
    combined = proc.stdout + proc.stderr
    assert "'audience_segments_list'" not in combined
    assert "Ошибка Аудиторий: " in combined
    assert proc.returncode == 0
