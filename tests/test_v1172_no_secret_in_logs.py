"""v1.17.2 (Security): секреты не попадают в логи, URL и ответы.

Регрессия: в v1.17.1 `login.yandex.ru/info` вызывался с
`?oauth_token=<токен>`, а логгер httpx на уровне INFO писал полный URL в
`~/.directai/logs/directai.log` — токен Директа утекал в лог при `check`.
Тесты закрывают: заголовок вместо query, тихие HTTP-логгеры во всех режимах,
фильтр-вычистку и отсутствие токена в выводе `check` / `doctor` и в файле лога.
"""

import logging

import httpx
import pytest

from directai_mcp.api import metrika as mk
from directai_mcp.log import (
    NOISY_LOGGERS,
    REDACTED,
    SecretFilter,
    quiet_http_logging,
    redact,
    setup_logging,
)

SECRET = "y0__SECRET-TOKEN-DO-NOT-LOG"
OTHER_SECRET = "y0__METRIKA-SECRET-TOO"
INFO_URL = "https://login.yandex.ru/info?format=json"


# -- токен только в заголовке ------------------------------------------------


async def test_oauth_app_info_sends_token_in_header_only(respx_mock):
    route = respx_mock.get(INFO_URL).mock(
        return_value=httpx.Response(200, json={"client_id": "app-1"}))
    info = await mk.oauth_app_info(SECRET)
    assert info["client_id"] == "app-1"
    request = route.calls[0].request
    assert request.headers["Authorization"] == "OAuth " + SECRET
    # Токена в URL быть не должно ни в каком виде.
    assert SECRET not in str(request.url)
    assert "oauth_token" not in str(request.url)
    assert request.url.query == b"format=json"


async def test_oauth_app_info_no_secret_in_error_path(respx_mock):
    """Текст ошибки тоже не должен содержать токен."""
    respx_mock.get(INFO_URL).mock(return_value=httpx.Response(401, text="no"))
    info = await mk.oauth_app_info(SECRET)
    assert info["error"] == "HTTP 401"
    assert SECRET not in str(info)


async def test_call_error_text_is_redacted(respx_mock):
    """Сетевая ошибка httpx может содержать URL — секреты вычищаются."""
    respx_mock.get(f"{mk.METRIKA_BASE}/management/v1/counters").mock(
        side_effect=httpx.ConnectError(
            f"failed for https://login.yandex.ru/info?oauth_token={SECRET}"))
    with pytest.raises(mk.MetrikaApiError) as err:
        await mk.get(SECRET, "/management/v1/counters")
    assert SECRET not in str(err.value)
    assert REDACTED in str(err.value)


# -- HTTP-логгеры молчат во всех режимах ------------------------------------


def test_quiet_http_logging_sets_warning():
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    quiet_http_logging()
    for name in NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_setup_logging_silences_http_and_filters(tmp_path):
    setup_logging(tmp_path)
    for name in NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING
    for handler in logging.getLogger().handlers:
        assert any(isinstance(f, SecretFilter) for f in handler.filters)
    logging.getLogger().handlers.clear()


def test_token_url_never_reaches_log_file(tmp_path):
    """Регрессия утечки: лог httpx с токеном в URL молчит, URL в тексте чистится."""
    setup_logging(tmp_path)
    try:
        # Так выглядел утекавший лог httpx на уровне INFO.
        logging.getLogger("httpx").info(
            'HTTP Request: GET %s?format=json&oauth_token=%s "HTTP/1.1" 200 OK',
            "https://login.yandex.ru/info", SECRET,
        )
        # Страховка-фильтр: если URL с секретом попал в текст нашего лога.
        logging.getLogger("directai_mcp.test").warning(
            "запрос не удался: https://login.yandex.ru/info?oauth_token=%s",
            OTHER_SECRET,
        )
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = (tmp_path / "logs" / "directai.log").read_text(encoding="utf-8")
    finally:
        logging.getLogger().handlers.clear()
    assert SECRET not in text
    assert OTHER_SECRET not in text
    assert "HTTP Request" not in text  # логгер httpx молчит
    assert REDACTED in text


@pytest.mark.parametrize(
    "url",
    [
        "https://login.yandex.ru/info?oauth_token=SEC1",
        "https://api-metrika.yandex.net/x?token=SEC2",
        "https://cloud.yandex.ru/?apikey=SEC3",
        "https://yandex.ru/?access_token=SEC4&refresh_token=SEC5",
        "https://yandex.ru/?api_key=SEC6",
    ],
)
def test_redact_masks_secret_params(url):
    cleaned = redact(url)
    for secret in ("SEC1", "SEC2", "SEC3", "SEC4", "SEC5", "SEC6"):
        assert secret not in cleaned
    assert REDACTED in cleaned


def test_redact_keeps_non_secret_params():
    url = "https://host/path?ids=123&date1=2026-10-01&metrics=ym:s:visits"
    assert redact(url) == url


def test_secret_filter_handles_dict_args():
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1,
        "url=%(url)s", ({"url": "https://x/?oauth_token=SEC7"},), None,
    )
    SecretFilter().filter(record)
    text = record.getMessage()
    assert "SEC7" not in text
    assert REDACTED in text


def test_secret_filter_keeps_percent_formatting():
    """Чистка не должна ломать %-подстановку (иначе теряется весь лог)."""
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1,
        "GET https://y.ru/?oauth_token=%s -> %s", ("SEC8", "200 OK"), None,
    )
    SecretFilter().filter(record)
    text = record.getMessage()
    assert "SEC8" not in text
    assert REDACTED in text
    assert "200 OK" in text


# -- check и doctor на моках: токена нет в выводе ---------------------------


async def test_check_output_has_no_token(respx_mock, monkeypatch, tmp_path,
                                         capsys, monkeypatch_home):
    from directai_mcp import cli

    monkeypatch.setenv("DIRECTAI_TOKEN", SECRET)
    _keyring_with(monkeypatch, SECRET)
    respx_mock.get("https://login.yandex.ru/info").mock(
        return_value=httpx.Response(200, json={"client_id": "app-1"}))
    respx_mock.get(f"{mk.METRIKA_BASE}/management/v1/counters").mock(
        return_value=httpx.Response(200, json={"counters": [{"id": 1}]}))
    # check целиком: Директ замокан частично не нужен — строки с токеном нет
    # даже при сетевых ошибках Direct, поэтому проверим строку Метрики.
    line = await cli._check_metrika(_settings(), SECRET)
    out = capsys.readouterr().out + line
    assert "Метрика:" in out
    assert SECRET not in out
    log_text = _log_text(tmp_path)
    assert SECRET not in log_text


def test_doctor_json_has_no_token(monkeypatch, tmp_path, capsys, monkeypatch_home):
    from directai_mcp import doctor

    monkeypatch.setenv("DIRECTAI_TOKEN", SECRET)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    doctor.cmd_doctor(json_output=True, skip_api=True)
    out = capsys.readouterr().out
    assert "Токены" in out or "checks" in out
    assert SECRET not in out
    assert SECRET not in _log_text(tmp_path)


# -- helpers ----------------------------------------------------------------


@pytest.fixture
def monkeypatch_home(monkeypatch, tmp_path):
    """DIRECTAI_HOME = tmp_path плюс минимальный accounts.toml."""

    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    path = tmp_path / "accounts.toml"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            '[auth]\nlogin = "agency-login"\n\n'
            '[aliases.m]\nlogin = "agency-login"\n\n[guard]\nguard = true\n'
        )
    yield


def _settings():
    from directai_mcp.config import AccountEntry, Settings

    return Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry("m", "agency-login")},
    )


def _keyring_with(monkeypatch, token: str) -> None:
    import keyring

    from directai_mcp.config import KEYRING_SERVICE_METRIKA

    monkeypatch.setattr(
        keyring, "get_password",
        lambda svc, user: token if svc == KEYRING_SERVICE_METRIKA else None,
    )


def _log_text(tmp_path) -> str:
    path = tmp_path / "logs" / "directai.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""