"""v1.2.4: Вебмастер (API v4) — 3 read-действия, отдельный токен, IPv4-транспорт."""

import inspect

import directai_mcp.catalog.webmaster as wm
import directai_mcp.config as cfg
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

NAMES = ("webmaster_hosts", "webmaster_summary", "webmaster_query")


def _ctx(tmp_path) -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="main-token", data_dir=tmp_path)


def test_webmaster_actions_registered_read_only():
    for name in NAMES:
        act = ACTIONS[name]
        assert act.mode == "read"
        assert act.run is not None
        assert act.prepare is None and act.apply is None and act.verify is None


def test_webmaster_params_schema():
    assert wm.WebmasterSummaryParams.model_fields["host_id"].is_required()
    assert wm.WebmasterQueryParams.model_fields["resource"].is_required()
    assert not wm.WebmasterQueryParams.model_fields["host_id"].is_required()
    p = wm.WebmasterQueryParams(
        resource="diagnostics", host_id="https:example.com:443"
    )
    assert p.output == "inline"
    assert p.format == "json"
    assert p.host_id == "https:example.com:443"


def test_webmaster_token_prefers_separate(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(cfg, "get_webmaster_token", lambda login: "wm-token")
    assert wm._token(ctx) == "wm-token"


def test_webmaster_token_fallback_to_main(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(cfg, "get_webmaster_token", lambda login: None)
    assert wm._token(ctx) == "main-token"


def test_webmaster_get_forces_ipv4_transport():
    src = inspect.getsource(wm._get)
    assert "AsyncHTTPTransport" in src
    assert 'local_address="0.0.0.0"' in src


async def test_webmaster_hosts_renders_offline(monkeypatch, tmp_path):
    async def fake_get(token, path, params=None):
        if path == "user":
            return {"user_id": 9000001}
        return {
            "hosts": [
                {
                    "unicode_host_url": "https://example.com/",
                    "host_id": "https:example.com:443",
                    "verified": True,
                    "main_mirror": {"unicode_host_url": "https://example.com/"},
                }
            ]
        }

    monkeypatch.setattr(wm, "_get", fake_get)
    out = await ACTIONS["webmaster_hosts"].run(
        _ctx(tmp_path), wm.WebmasterHostsParams()
    )
    assert "example.com" in out
    assert "9000001" in out
