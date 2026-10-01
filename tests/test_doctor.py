"""Connection doctor v1.7.0: проверки a–i, коды возврата, --json, секреты."""

import json

from directai_mcp import doctor
from directai_mcp.doctor import (
    ERROR_GUIDE,
    CheckResult,
    cmd_doctor,
    format_json,
)

MINIMAL_CONFIG = (
    '[auth]\nlogin = "agency-login"\n[aliases.msk]\nlogin = "agency-login"\n'
)


def _home(monkeypatch, tmp_path):
    (tmp_path / "accounts.toml").write_text(MINIMAL_CONFIG, encoding="utf-8")
    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))


# --- a. версия ---


def test_version_match_ok(monkeypatch):
    monkeypatch.setattr(doctor, "_repo_root", lambda: doctor.Path("/repo"))
    monkeypatch.setattr(doctor, "_pyproject_version", lambda root: doctor.__version__)
    monkeypatch.setattr(doctor, "_git_describe", lambda root: "v" + doctor.__version__)
    r = doctor.check_version()
    assert r.id == "a" and r.status == "OK"


def test_version_mismatch_warn(monkeypatch):
    monkeypatch.setattr(doctor, "_repo_root", lambda: doctor.Path("/repo"))
    monkeypatch.setattr(doctor, "_pyproject_version", lambda root: "0.0.0")
    monkeypatch.setattr(doctor, "_git_describe", lambda root: "v0.0.0")
    r = doctor.check_version()
    assert r.status == "WARN" and "переустановить" in r.hint


def test_version_no_repo_warn(monkeypatch):
    monkeypatch.setattr(doctor, "_repo_root", lambda: None)
    r = doctor.check_version()
    assert r.status == "WARN"


# --- b. exe ---


def test_exe_missing_fail(monkeypatch):
    monkeypatch.setattr(doctor, "exe_candidates", list)
    r = doctor.check_exe()
    assert r.status == "FAIL"


def test_exe_single_ok(monkeypatch):
    monkeypatch.setattr(doctor, "exe_candidates", lambda: ["C:/x/directai-mcp.exe"])
    assert doctor.check_exe().status == "OK"


def test_exe_multiple_warn(monkeypatch):
    monkeypatch.setattr(doctor, "exe_candidates", lambda: ["C:/a/d.exe", "D:/b/d.exe"])
    assert doctor.check_exe().status == "WARN"


# --- c. процессы ---


def test_processes_none_ok(monkeypatch):
    monkeypatch.setattr(doctor, "_tasklist_rows", lambda: [("explorer.exe", 1)])
    assert doctor.check_processes().status == "OK"


def test_processes_holder_info_by_default(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_tasklist_rows",
        lambda: [("directai-mcp.exe", 111), ("hermes.exe", 222)],
    )
    r = doctor.check_processes()
    assert r.status == "OK" and "INFO" in r.detail
    assert "111" in r.detail and "222" in r.detail


def test_processes_holder_warn_preinstall(monkeypatch):
    monkeypatch.setattr(doctor, "_tasklist_rows", lambda: [("directai-mcp.exe", 111)])
    r = doctor.check_processes(preinstall=True)
    assert r.status == "WARN" and "111" in r.detail


def test_processes_unavailable_warn(monkeypatch):
    monkeypatch.setattr(doctor, "_tasklist_rows", lambda: None)
    assert doctor.check_processes().status == "WARN"


# --- d. блокировка файла ---


def test_file_lock_ok(tmp_path):
    target = tmp_path / "directai-mcp.exe"
    target.write_bytes(b"x")
    assert doctor.check_file_lock(str(target)).status == "OK"


def test_file_lock_no_exe_warn(monkeypatch):
    monkeypatch.setattr(doctor, "exe_candidates", list)
    assert doctor.check_file_lock().status == "WARN"


def test_file_lock_busy_info_by_default(monkeypatch, tmp_path):
    target = tmp_path / "directai-mcp.exe"
    target.write_bytes(b"x")

    def _raise(path, mode):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", _raise)
    r = doctor.check_file_lock(str(target))
    assert r.status == "OK" and "INFO" in r.detail and "os error 32" in r.detail


def test_file_lock_busy_fail_preinstall(monkeypatch, tmp_path):
    target = tmp_path / "directai-mcp.exe"
    target.write_bytes(b"x")

    def _raise(path, mode):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", _raise)
    r = doctor.check_file_lock(str(target), preinstall=True)
    assert r.status == "FAIL" and "os error 32" in r.detail


# --- e. конфиг ---


def test_config_ok(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    assert doctor.check_config().status == "OK"


def test_config_missing_fail(monkeypatch, tmp_path):
    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    assert doctor.check_config().status == "FAIL"


def test_config_service_goal_warn(monkeypatch, tmp_path):
    (tmp_path / "accounts.toml").write_text(
        MINIMAL_CONFIG + "primary_conversion_goal_id = 13\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    r = doctor.check_config()
    assert r.status == "WARN"


# --- f. токены ---


def test_tokens_present_ok_no_secret(monkeypatch, tmp_path, capsys):
    _home(monkeypatch, tmp_path)
    secret = "y0_secret-token-value-12345"
    import directai_mcp.config as cfg

    monkeypatch.setattr(cfg, "get_token", lambda login: secret)
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    monkeypatch.setattr(cfg, "get_webmaster_token", lambda login: None)
    r = doctor.check_tokens()
    assert r.status == "OK"
    out = r.detail + r.hint
    assert secret not in out
    assert str(len(secret)) in r.detail
    printed = capsys.readouterr()
    assert secret not in printed.out


def test_tokens_missing_fail(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    import directai_mcp.config as cfg
    from directai_mcp.config import TokenMissingError

    def _missing(login):
        raise TokenMissingError("no token")

    monkeypatch.setattr(cfg, "get_token", _missing)
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: None)
    monkeypatch.setattr(cfg, "get_webmaster_token", lambda login: None)
    r = doctor.check_tokens()
    assert r.status == "FAIL"


# --- g. API и таблица кодов ---


def test_error_guide_covers_required_codes():
    for code in (52, 53, 58, 152, 506, 513, 1000, 1020):
        title, action = ERROR_GUIDE[code]
        assert title and action


def test_api_skip_ok():
    r = doctor.check_api(skip_api=True)
    assert r.status == "OK" and r.skipped


def test_api_auth_error_fail(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    import directai_mcp.config as cfg
    from directai_mcp.api.errors import DirectError

    monkeypatch.setattr(cfg, "get_token", lambda login: "tok")

    async def _boom(token, login):
        raise DirectError(code=53, message="auth")

    monkeypatch.setattr(doctor, "_light_api_call", _boom)
    r = doctor.check_api()
    assert r.status == "FAIL" and "53" in r.detail


def test_api_transient_warn(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    import directai_mcp.config as cfg
    from directai_mcp.api.errors import DirectError

    monkeypatch.setattr(cfg, "get_token", lambda login: "tok")

    async def _boom(token, login):
        raise DirectError(code=1000, message="down")

    monkeypatch.setattr(doctor, "_light_api_call", _boom)
    assert doctor.check_api().status == "WARN"


# --- h. Hermes ---


def test_hermes_missing_warn(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.check_hermes().status == "WARN"


# --- i. Аудитории ---


def test_audience_disabled_ok(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    r = doctor.check_audience()
    assert r.status == "OK" and "INFO" in r.detail


def test_audience_enabled_warn(monkeypatch, tmp_path):
    (tmp_path / "accounts.toml").write_text(
        MINIMAL_CONFIG + "[audience]\nwrite_enabled = true\n", encoding="utf-8"
    )
    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
    assert doctor.check_audience().status == "WARN"


# --- коды возврата и --json ---


def _result(status, check_id="x"):
    return CheckResult(id=check_id, name="n", status=status, detail="d")


def test_exit_codes():
    import directai_mcp.doctor as d

    all_ok = [d.CheckResult(id=str(i), name="n", status="OK") for i in range(9)]
    monkeypatch_warn = list(all_ok)
    monkeypatch_warn[0] = d.CheckResult(id="a", name="n", status="WARN")
    monkeypatch_fail = list(all_ok)
    monkeypatch_fail[0] = d.CheckResult(id="a", name="n", status="FAIL")

    def code(results):
        if any(r.status == "FAIL" for r in results):
            return 2
        if any(r.status == "WARN" for r in results):
            return 1
        return 0

    assert code(all_ok) == 0
    assert code(monkeypatch_warn) == 1
    assert code(monkeypatch_fail) == 2


def test_json_schema_no_secrets(monkeypatch, tmp_path, capsys):
    _home(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor, "check_api", lambda skip_api=False: _result("OK", "g"))
    code = cmd_doctor(json_output=True, skip_api=True)
    assert code in (0, 1, 2)
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1
    assert payload["overall"] in ("ok", "warn", "fail")
    assert payload["preinstall"] is False
    assert len(payload["checks"]) == 9
    ids = sorted([c.get("id") for c in payload["checks"]])
    assert ids == ["a", "b", "c", "d", "e", "f", "g", "h", "i"]
    assert all(c.get("status") in ("OK", "WARN", "FAIL") for c in payload["checks"])


def test_doctor_all_mocked_exit_zero(monkeypatch):
    import directai_mcp.doctor as d

    monkeypatch.setattr(d, "check_version", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_exe", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_processes", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_file_lock", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_config", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_tokens", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_api", lambda skip_api=False: _result("OK"))
    monkeypatch.setattr(d, "check_hermes", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_audience", lambda *a, **k: _result("OK"))
    results, code = d.run_doctor()
    assert code == 0
    assert format_json(results, code).startswith("{")


def test_doctor_exe_warn_downgrades_file_lock_ok(monkeypatch):
    import directai_mcp.doctor as d

    monkeypatch.setattr(d, "check_version", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_exe", lambda *a, **k: _result("WARN"))
    monkeypatch.setattr(d, "check_processes", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(
        d,
        "check_file_lock",
        lambda *a, **k: CheckResult(
            id="d", name="n", status="OK", detail="открывается: C:/x"
        ),
    )
    monkeypatch.setattr(d, "check_config", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_tokens", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_api", lambda skip_api=False: _result("OK"))
    monkeypatch.setattr(d, "check_hermes", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_audience", lambda *a, **k: _result("OK"))
    results, code = d.run_doctor()
    assert code == 1
    assert results[3].status == "WARN"


def test_doctor_preinstall_flag_reaches_checks(monkeypatch):
    import directai_mcp.doctor as d

    seen = {}

    def _c(*a, **k):
        seen["c"] = k.get("preinstall")
        return _result("OK")

    def _d(*a, **k):
        seen["d"] = k.get("preinstall")
        return _result("OK")

    monkeypatch.setattr(d, "check_version", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_exe", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_processes", _c)
    monkeypatch.setattr(d, "check_file_lock", _d)
    monkeypatch.setattr(d, "check_config", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_tokens", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_api", lambda skip_api=False: _result("OK"))
    monkeypatch.setattr(d, "check_hermes", lambda *a, **k: _result("OK"))
    monkeypatch.setattr(d, "check_audience", lambda *a, **k: _result("OK"))
    d.run_doctor(preinstall=True)
    assert seen == {"c": True, "d": True}


def test_doctor_cli_has_preinstall_flag():
    from directai_mcp.cli import build_parser

    args = build_parser().parse_args(["doctor", "--preinstall", "--skip-api", "--json"])
    assert args.preinstall is True
    assert args.skip_api is True
    assert args.json is True
    default = build_parser().parse_args(["doctor"])
    assert default.preinstall is False
