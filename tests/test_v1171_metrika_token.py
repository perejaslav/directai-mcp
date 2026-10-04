"""v1.17.1: отдельный OAuth-токен Метрики.

Выбор токена (отдельный / fallback на токен Директа), запрет записи целей
без отдельного токена, чтение без него с примечанием, строки check / doctor /
describe_action и команда set-metrika-token. Только моки: живые счётчики,
ключи и токены не трогаются.
"""

import asyncio

import httpx
import pytest

import directai_mcp.catalog.metrika_goal_write as gw
from directai_mcp.catalog.metrika_goals import cache_scope
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import (
    KEYRING_SERVICE_METRIKA,
    METRIKA_APP_CLIENT_ID,
    METRIKA_TOKEN_ENV_VAR,
    AccountEntry,
    Settings,
    get_metrika_token,
    metrika_token_info,
    metrika_token_label,
)
from directai_mcp.server import _metrika_token_status

DIRECT_TOKEN = "direct-secret-token"
METRIKA_TOKEN = "metrika-secret-token"
BASE = "https://api-metrika.yandex.net"
LOGIN = "agency-login"


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    """Ключи Credential Manager и переменная окружения — под контролем."""
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    monkeypatch.delenv(METRIKA_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv("DIRECTAI_TOKEN", raising=False)


def _keyring_with(monkeypatch, service: str, login: str, token: str) -> None:
    import keyring

    def _get(svc, user):
        if svc == service and user == login:
            return token
        return None

    monkeypatch.setattr(keyring, "get_password", _get)


def _settings():
    return Settings(auth_login=LOGIN, accounts={"m": AccountEntry("m", LOGIN)})


def _write_config() -> None:
    """accounts.toml с [auth] login — load_settings() должен его прочитать."""

    from directai_mcp.config import data_dir

    path = data_dir() / "accounts.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            f'[auth]\nlogin = "{LOGIN}"\n\n'
            f'[aliases.m]\nlogin = "{LOGIN}"\n\n[guard]\nguard = true\n'
        )


def _ctx(token: str = DIRECT_TOKEN, metrika: str = "", source: str = "direct"):
    return Ctx(
        settings=_settings(),
        token=token,
        metrika_token=metrika,
        metrika_token_source=source if metrika else "direct",
        data_dir=None,
    )


# -- хранение и чтение токена ----------------------------------------------


def test_metrika_token_read_from_env(monkeypatch):
    monkeypatch.setenv(METRIKA_TOKEN_ENV_VAR, METRIKA_TOKEN)
    assert get_metrika_token(LOGIN) == METRIKA_TOKEN


def test_metrika_token_read_from_keyring(monkeypatch):
    _keyring_with(monkeypatch, KEYRING_SERVICE_METRIKA, LOGIN, METRIKA_TOKEN)
    assert get_metrika_token(LOGIN) == METRIKA_TOKEN


def test_metrika_token_missing_is_none(monkeypatch):
    assert get_metrika_token(LOGIN) is None


def test_metrika_token_key_is_separate_from_direct(monkeypatch):
    """Ключ Метрики не совпадает с ключом Директа: токены не путаются."""
    from directai_mcp.config import KEYRING_SERVICE

    assert KEYRING_SERVICE_METRIKA != KEYRING_SERVICE
    assert KEYRING_SERVICE_METRIKA == "directai-mcp-metrika"


def test_token_info_sources(monkeypatch):
    assert metrika_token_info(LOGIN) == {"source": "none", "length": 0}
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)
    assert metrika_token_info(LOGIN) == {"source": "direct", "length": len(DIRECT_TOKEN)}
    _keyring_with(monkeypatch, KEYRING_SERVICE_METRIKA, LOGIN, METRIKA_TOKEN)
    info = metrika_token_info(LOGIN)
    assert info["source"] == "metrika"
    assert info["length"] == len(METRIKA_TOKEN)
    label = metrika_token_label(LOGIN)
    assert "отдельный токен Метрики" in label
    assert METRIKA_TOKEN not in label


# -- выбор токена в Ctx -----------------------------------------------------


def test_read_uses_separate_token():
    ctx = _ctx(metrika=METRIKA_TOKEN, source="metrika")
    assert ctx.metrika_read_token() == METRIKA_TOKEN
    assert ctx.notes == []


def test_read_falls_back_to_direct_token_with_note():
    ctx = _ctx()
    assert ctx.metrika_read_token() == DIRECT_TOKEN
    assert len(ctx.notes) == 1
    note = ctx.notes[0]
    assert "set-metrika-token --login agency-login" in note
    assert "запись целей" in note
    assert DIRECT_TOKEN not in note
    # Повторный вызов не дублирует примечание.
    assert ctx.metrika_read_token() == DIRECT_TOKEN
    assert len(ctx.notes) == 1


def test_write_requires_separate_token():
    ctx = _ctx()
    with pytest.raises(ValueError) as err:
        ctx.require_metrika_write()
    text = str(err.value)
    assert "set-metrika-token --login agency-login" in text
    assert "metrika:write" in text
    assert DIRECT_TOKEN not in text


def test_write_token_source_guard():
    """Токен есть, но источник не «metrika» — всё равно отказ (честная проверка)."""
    ctx = _ctx(metrika=METRIKA_TOKEN, source="direct")
    with pytest.raises(ValueError, match="set-metrika-token"):
        ctx.require_metrika_write()
    ok = _ctx(metrika=METRIKA_TOKEN, source="metrika")
    assert ok.require_metrika_write() == METRIKA_TOKEN


def test_write_refused_before_any_request(respx_mock):
    """Ни одного HTTP-запроса: токен проверяется до обращения к API."""
    route = respx_mock.get(f"{BASE}/management/v1/counter/1")
    with pytest.raises(ValueError, match="set-metrika-token"):
        asyncio.run(gw._prepare_create(
            _ctx(),
            None,
            gw.MetrikaGoalCreateParams(
                counter_id=1, type="number", name="Глубина", depth=2),
        ))
    assert not route.called


def test_cache_scope_differs_per_token():
    assert cache_scope(DIRECT_TOKEN) != cache_scope(METRIKA_TOKEN)
    assert cache_scope(DIRECT_TOKEN) == cache_scope(DIRECT_TOKEN)
    assert DIRECT_TOKEN not in cache_scope(DIRECT_TOKEN)


# -- check / doctor / describe_action ---------------------------------------


async def test_check_metrika_line_reports_direct_fallback(respx_mock, monkeypatch):
    _write_config()
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)
    from directai_mcp.cli import _check_metrika

    respx_mock.get(f"{BASE}/management/v1/counters").mock(
        return_value=httpx.Response(200, json={"counters": [{"id": 1}]}))
    respx_mock.get("https://login.yandex.ru/info").mock(
        return_value=httpx.Response(200, json={"client_id": "direct-app-id"}))
    line = await _check_metrika(_settings(), DIRECT_TOKEN)
    assert line.startswith("OK Метрика:")
    assert "токен Директа (только чтение)" in line
    assert "set-metrika-token --login agency-login" in line
    assert "счётчиков 1" in line
    assert DIRECT_TOKEN not in line


async def test_check_metrika_line_reports_separate_token(respx_mock, monkeypatch):
    _write_config()
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)
    _keyring_with(monkeypatch, KEYRING_SERVICE_METRIKA, LOGIN, METRIKA_TOKEN)
    from directai_mcp.cli import _check_metrika

    respx_mock.get(f"{BASE}/management/v1/counters").mock(
        return_value=httpx.Response(200, json={"counters": []}))
    respx_mock.get("https://login.yandex.ru/info").mock(
        return_value=httpx.Response(
            200, json={"client_id": METRIKA_APP_CLIENT_ID}))
    line = await _check_metrika(_settings(), METRIKA_TOKEN)
    assert "отдельный токен Метрики" in line
    assert METRIKA_APP_CLIENT_ID[:8] in line
    assert "metrika:write" in line
    assert METRIKA_TOKEN not in line


async def test_check_metrika_line_warns_about_foreign_app(respx_mock, monkeypatch):
    _write_config()
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)
    _keyring_with(monkeypatch, KEYRING_SERVICE_METRIKA, LOGIN, METRIKA_TOKEN)
    from directai_mcp.cli import _check_metrika

    respx_mock.get(f"{BASE}/management/v1/counters").mock(
        return_value=httpx.Response(200, json={"counters": []}))
    respx_mock.get("https://login.yandex.ru/info").mock(
        return_value=httpx.Response(200, json={"client_id": "other-app"}))
    line = await _check_metrika(_settings(), METRIKA_TOKEN)
    assert "ВНИМАНИЕ" in line
    assert "другому приложению" in line


def test_doctor_metrika_check_direct_fallback(monkeypatch):
    _write_config()
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)
    from directai_mcp.doctor import check_metrika

    res = check_metrika(skip_api=True)
    assert res.id == "l"
    assert res.status == "OK"
    assert "токен Директа" in res.detail
    assert "запись целей недоступна" in res.detail
    assert "set-metrika-token" in (res.hint or "")


def test_doctor_metrika_check_with_separate_token(monkeypatch):
    from directai_mcp import doctor

    _write_config()

    _keyring_with(monkeypatch, KEYRING_SERVICE_METRIKA, LOGIN, METRIKA_TOKEN)
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)

    async def _probe(token):
        assert token == METRIKA_TOKEN
        return {"client_id": METRIKA_APP_CLIENT_ID}, 3

    monkeypatch.setattr(doctor, "_metrika_probe", _probe)
    res = doctor.check_metrika(skip_api=False)
    assert res.status == "OK"
    assert "отдельный токен Метрики" in res.detail
    assert "счётчиков 3" in res.detail
    assert "metrika:write без записи не проверяется" in res.detail
    assert METRIKA_TOKEN not in res.detail


def test_doctor_metrika_check_warns_foreign_app(monkeypatch):
    from directai_mcp import doctor

    _write_config()

    _keyring_with(monkeypatch, KEYRING_SERVICE_METRIKA, LOGIN, METRIKA_TOKEN)
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)

    async def _probe(token):
        return {"client_id": "00000000-other"}, 1

    monkeypatch.setattr(doctor, "_metrika_probe", _probe)
    res = doctor.check_metrika(skip_api=False)
    assert res.status == "WARN"
    assert "перевыпустите токен" in (res.hint or "")


def test_describe_action_status_line(monkeypatch):
    _write_config()
    monkeypatch.setenv("DIRECTAI_TOKEN", DIRECT_TOKEN)
    line = _metrika_token_status()
    assert line.startswith("Метрика:")
    assert "токен Директа (только чтение)" in line
    assert "запись целей недоступна" in line
    assert DIRECT_TOKEN not in line

    _keyring_with(monkeypatch, KEYRING_SERVICE_METRIKA, LOGIN, METRIKA_TOKEN)
    line = _metrika_token_status()
    assert "отдельный токен Метрики" in line
    assert METRIKA_TOKEN not in line


# -- команда set-metrika-token ---------------------------------------------


def test_set_metrika_token_saves_under_own_key(monkeypatch, tmp_path):
    from directai_mcp import cli

    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    saved: dict = {}

    import keyring

    monkeypatch.setattr(
        keyring, "set_password",
        lambda svc, user, token: saved.update(
            {svc: (user, token)}))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": METRIKA_TOKEN)
    monkeypatch.setattr(
        cli, "setup_logging", lambda *a, **k: None)

    # Значение токена не должно попасть в напечатанные инструкции.
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.cmd_set_metrika_token(LOGIN) == 0
    printed = buf.getvalue()
    assert saved == {KEYRING_SERVICE_METRIKA: (LOGIN, METRIKA_TOKEN)}
    assert METRIKA_TOKEN not in printed
    assert METRIKA_APP_CLIENT_ID in printed
    assert METRIKA_OAUTH in printed
    assert "metrika:write" in printed


def test_set_metrika_token_empty_is_not_saved(monkeypatch, tmp_path):
    from directai_mcp import cli

    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "   ")
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    assert cli.cmd_set_metrika_token(LOGIN) == 1


METRIKA_OAUTH = "https://oauth.yandex.ru/authorize"