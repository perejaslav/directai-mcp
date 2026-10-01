"""v1.2.6 (этап 2, экспериментально): Аудитории — запись из файла + delete с guard."""

import hashlib
import logging

import httpx
import pytest

import directai_mcp.catalog.audience_write as audw
import directai_mcp.config as cfg
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety import journal as journal_mod
from directai_mcp.safety.plans import Plan
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

BASE = "https://api-audience.yandex.ru/v1/management"
UPLOAD = f"{BASE}/segments/upload_csv_file"
SEGMENTS = f"{BASE}/segments"

REAL_LIKE = [
    {"id": 11, "name": "Buyers 2025", "type": "uploading",
     "status": "processed", "create_time": "2025-01-01T00:00:00Z",
     "owner": "agency-login"},
    {"id": 12, "name": "CRM base", "type": "uploading",
     "status": "processed", "create_time": "2025-02-01T00:00:00Z",
     "owner": "agency-login"},
]
TEST_SEG = {"id": 13, "name": "[TEST DirectAI] tmp",
            "type": "uploading", "status": "processed",
            "create_time": "2026-01-01T00:00:00Z", "owner": "agency-login"}


def _ctx(tmp_path, enabled: bool = True) -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        guard=True,
        audience_write_enabled=enabled,
    )
    return Ctx(settings=settings, token="main-token", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


@pytest.fixture(autouse=True)
def _aud_token(monkeypatch):
    monkeypatch.setattr(cfg, "get_audience_token", lambda login: "aud-token")


def _phone(i: int) -> str:
    return f"7900000{i:04d}"


def _write_contacts(tmp_path, name="base.csv", n=150, kind="phone"):
    path = tmp_path / name
    if kind == "phone":
        path.write_text("phone\n" + "\n".join(_phone(i) for i in range(n)) + "\n",
                        encoding="utf-8")
    else:
        path.write_text(
            "email\n" + "\n".join(f"user{i:04d}@example.com" for i in range(n)) + "\n",
            encoding="utf-8")
    return str(path)


def _plan_id(text: str) -> str:
    return text.split(" ", 1)[1].split(":", 1)[0].strip()


def test_write_actions_registered_write_only():
    for name in ("audience_segment_from_file", "audience_segment_delete"):
        act = ACTIONS[name]
        assert act.mode == "write"
        assert act.prepare is not None and act.apply is not None
        assert act.verify is not None and act.run is None


def test_norm_phones():
    assert audw._norm_phone("+7 (900) 000-00-01") == "79000000001"
    assert audw._norm_phone("89000000001") == "79000000001"
    assert audw._norm_phone("9000000001") == "79000000001"
    assert audw._norm_phone("79000000001") == "79000000001"
    assert audw._norm_phone("123") is None
    assert audw._norm_phone("790000000012") is None
    assert audw._norm_phone("") is None


def test_norm_emails():
    assert audw._norm_email("  User@Example.COM ") == "user@example.com"
    assert audw._norm_email("a@b.ru") == "a@b.ru"
    assert audw._norm_email("bad") is None
    assert audw._norm_email("a@b") is None
    assert audw._norm_email("тест@example.com") is None


def test_sha256_vector():
    assert audw._sha256_hex("test") == hashlib.sha256(b"test").hexdigest()
    assert audw._sha256_hex("test") == (
        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    )


def test_encodings_bom_and_delimiter(tmp_path):
    bom = tmp_path / "bom.csv"
    bom.write_bytes("phone\n79000000001\n".encode("utf-8-sig"))
    assert audw._load_hashes(str(bom), "phone", None)["total"] == 1
    semi = tmp_path / "semi.csv"
    semi.write_text("phone;note\n79000000001;клиент\n",
                    encoding="cp1251")
    assert audw._load_hashes(str(semi), "phone", "phone")["total"] == 1
    multi = tmp_path / "multi.csv"
    multi.write_text("note,phone\nx,79000000001\n", encoding="utf-8")
    assert audw._load_hashes(str(multi), "phone", "phone")["total"] == 1
    with pytest.raises(ValueError, match="id_column"):
        audw._load_hashes(str(multi), "phone", None)


def test_xlsx_rejected(tmp_path):
    xls = tmp_path / "base.xlsx"
    xls.write_bytes(b"PK fake")
    with pytest.raises(ValueError, match="сохраните как CSV"):
        audw._load_hashes(str(xls), "phone", None)


async def test_from_file_rejects_few_records_no_api(tmp_path, monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("API called")

    monkeypatch.setattr(audw, "_get", _boom)
    monkeypatch.setattr(audw, "_post_file", _boom)
    path = _write_contacts(tmp_path, n=5)
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_from_file",
        {"file_path": path, "segment_name": "[TEST DirectAI] few",
         "content_type": "phone"},
    )
    assert "не менее 100" in out
    assert len(PLANS) == 0


async def test_from_file_rejects_bad_name(tmp_path):
    path = _write_contacts(tmp_path)
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_from_file",
        {"file_path": path, "segment_name": "Buyers", "content_type": "phone"},
    )
    assert out.startswith("Заблокировано защитой")
    assert len(PLANS) == 0


def test_write_disabled_by_default():
    assert Settings(auth_login="x").audience_write_enabled is False


async def test_write_rejected_when_disabled(tmp_path):
    path = _write_contacts(tmp_path)
    out = await do_plan_write(
        _ctx(tmp_path, enabled=False), "audience_segment_from_file",
        {"file_path": path, "segment_name": "[TEST DirectAI] off",
         "content_type": "phone"},
    )
    assert out.startswith("Заблокировано защитой")
    assert "write_enabled=false" in out
    assert len(PLANS) == 0


async def test_delete_rejected_when_disabled(tmp_path, respx_mock):
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(
            200, json={"segments": REAL_LIKE + [TEST_SEG]})
    )
    out = await do_plan_write(
        _ctx(tmp_path, enabled=False), "audience_segment_delete",
        {"segment_id": 13},
    )
    assert out.startswith("Заблокировано защитой")
    assert "write_enabled=false" in out
    assert len(PLANS) == 0


async def test_delete_requires_acknowledge(tmp_path, respx_mock):
    respx_mock.get(SEGMENTS).mock(
        side_effect=[
            httpx.Response(200, json={"segments": REAL_LIKE + [TEST_SEG]}),
            httpx.Response(200, json={"segments": REAL_LIKE + [TEST_SEG]}),
        ]
    )
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_delete", {"segment_id": 13})
    assert out.startswith("План ")
    out = await do_apply_write(_ctx(tmp_path), _plan_id(out))
    assert "acknowledge_warnings=true" in out


def test_load_settings_audience_write_flag(tmp_path):
    from directai_mcp.config import load_settings

    cfg_path = tmp_path / "accounts.toml"
    cfg_path.write_text(
        '[auth]\nlogin = "agency-login"\n'
        '[aliases.m]\nlogin = "agency-login"\n',
        encoding="utf-8",
    )
    assert load_settings(cfg_path).audience_write_enabled is False
    cfg_path.write_text(
        '[auth]\nlogin = "agency-login"\n'
        '[aliases.m]\nlogin = "agency-login"\n'
        '[audience]\nwrite_enabled = true\n',
        encoding="utf-8",
    )
    assert load_settings(cfg_path).audience_write_enabled is True


async def test_from_file_flow_applied(tmp_path, respx_mock):
    path = _write_contacts(tmp_path)
    upload = respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 21}}))
    confirm = respx_mock.post(f"{BASE}/segment/21/confirm").mock(
        return_value=httpx.Response(200, json={"segment": {"id": 21}}))
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(
            200, json={"segments": [{**TEST_SEG, "id": 21,
                                     "name": "[TEST DirectAI] flow",
                                     "matched_quantity": 140}]})
    )
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_from_file",
        {"file_path": path, "segment_name": "[TEST DirectAI] flow",
         "content_type": "phone"},
    )
    assert out.startswith("План ")
    assert "152-ФЗ" in out
    assert "7900000" not in out
    pid = _plan_id(out)
    assert "acknowledge_warnings=true" in out
    out = await do_apply_write(_ctx(tmp_path), pid, True)
    assert "статус applied" in out
    assert "сегмент 21" in out
    assert upload.called and confirm.called
    req = upload.calls[0].request
    assert req.headers["Authorization"] == "OAuth aud-token"
    assert "multipart/form-data" in req.headers["Content-Type"]
    assert b'name="file"' in req.content
    confirm_body = confirm.calls[0].request.content
    import json as _json

    assert _json.loads(confirm_body) == {"segment": {
        "name": "[TEST DirectAI] flow", "hashed": True,
        "hashing_alg": "SHA256", "content_type": "crm"}}


async def test_poll_variants(tmp_path, respx_mock):
    path = _write_contacts(tmp_path)
    respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 22}}))
    respx_mock.post(f"{BASE}/segment/22/confirm").mock(
        return_value=httpx.Response(200, json={"segment": {"id": 22}}))
    for status, final in (
        ("few_data", "applied"),
        ("processing_failed", "partial"),
    ):
        PLANS.clear()
        respx_mock.get(SEGMENTS).mock(
            return_value=httpx.Response(
                200, json={"segments": [{**TEST_SEG, "id": 22,
                                         "name": "[TEST DirectAI] v",
                                         "status": status}]})
        )
        out = await do_plan_write(
            _ctx(tmp_path), "audience_segment_from_file",
            {"file_path": path, "segment_name": "[TEST DirectAI] v",
             "content_type": "phone"},
        )
        out = await do_apply_write(_ctx(tmp_path), _plan_id(out), True)
        assert f"статус {final}" in out
        assert status in out


async def test_poll_timeout_unverified(tmp_path, respx_mock):
    path = _write_contacts(tmp_path)
    respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 23}}))
    respx_mock.post(f"{BASE}/segment/23/confirm").mock(
        return_value=httpx.Response(200, json={"segment": {"id": 23}}))
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(
            200, json={"segments": [{**TEST_SEG, "id": 23,
                                     "name": "[TEST DirectAI] t",
                                     "status": "is_processed"}]})
    )
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_from_file",
        {"file_path": path, "segment_name": "[TEST DirectAI] t",
         "content_type": "phone", "wait_timeout_sec": 2},
    )
    out = await do_apply_write(_ctx(tmp_path), _plan_id(out), True)
    assert "статус unverified" in out
    assert "ещё обрабатывается" in out
    assert "audience_segment_get" in out


async def test_apply_rejects_swapped_file(tmp_path, respx_mock):
    path = _write_contacts(tmp_path)
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_from_file",
        {"file_path": path, "segment_name": "[TEST DirectAI] swap",
         "content_type": "phone"},
    )
    pid = _plan_id(out)
    from pathlib import Path as _Path

    _Path(path).write_text(
        "phone\n" + "\n".join(f"7911000{i:04d}" for i in range(150)) + "\n",
        encoding="utf-8",
    )
    upload = respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 99}}))
    out = await do_apply_write(_ctx(tmp_path), pid, True)
    assert "статус failed" in out
    assert "файл изменён после построения плана" in out
    assert not upload.called


async def test_apply_rejects_changed_total(tmp_path, respx_mock):
    path = _write_contacts(tmp_path)
    params = {"file_path": path, "segment_name": "[TEST DirectAI] total",
              "content_type": "phone"}
    pid = PLANS.put(Plan(
        plan_id="", action="audience_segment_from_file",
        account_login="agency-login", params=params,
        before={"file_path": path,
                "file_sha256": audw._sha256_file(path),
                "content_type": "phone",
                "segment_name": "[TEST DirectAI] total",
                "read": 150, "valid": 150, "invalid": 0,
                "duplicates": 0, "total": 999},
        requests=[], preview="p",
    ))
    upload = respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 99}}))
    out = await do_apply_write(_ctx(tmp_path), pid, True)
    assert "статус failed" in out
    assert "счётчик записей изменился" in out
    assert not upload.called


async def test_orphan_upload_id_in_failed(tmp_path, respx_mock):
    path = _write_contacts(tmp_path)
    respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 41}}))
    respx_mock.post(f"{BASE}/segment/41/confirm").mock(
        return_value=httpx.Response(500, text="boom"))
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(200, json={"segments": []}))
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_from_file",
        {"file_path": path, "segment_name": "[TEST DirectAI] orphan",
         "content_type": "phone"},
    )
    out = await do_apply_write(_ctx(tmp_path), _plan_id(out), True)
    assert "статус failed" in out
    assert "41" in out
    assert "неподтвержд" in out
    assert "7900000" not in out


async def test_temp_file_removed(tmp_path, monkeypatch, respx_mock):
    tmpdir = tmp_path / "sys-tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(audw.tempfile, "gettempdir", lambda: str(tmpdir))
    path = _write_contacts(tmp_path)
    respx_mock.post(UPLOAD).mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, json={"segment": {"id": 24}}),
        ]
    )
    respx_mock.post(f"{BASE}/segment/24/confirm").mock(
        return_value=httpx.Response(200, json={"segment": {"id": 24}}))
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(
            200, json={"segments": [{**TEST_SEG, "id": 24,
                                     "name": "[TEST DirectAI] tmp"}]})
    )
    params = {"file_path": path, "segment_name": "[TEST DirectAI] tmp",
              "content_type": "phone"}
    out = await do_plan_write(_ctx(tmp_path), "audience_segment_from_file", params)
    out = await do_apply_write(_ctx(tmp_path), _plan_id(out), True)
    assert "статус failed" in out
    assert list(tmpdir.iterdir()) == []
    PLANS.clear()
    out = await do_plan_write(_ctx(tmp_path), "audience_segment_from_file", params)
    out = await do_apply_write(_ctx(tmp_path), _plan_id(out), True)
    assert "статус applied" in out
    assert list(tmpdir.iterdir()) == []


async def test_no_pdn_in_plan_log_journal(tmp_path, respx_mock, caplog):
    raws: list[str] = []
    for i in range(150):
        base = f"7900111{i:04d}"
        if i % 3 == 0:
            raws.append(f"+7 (900) 111-{i // 100:02d}-{i % 100:02d}")
        elif i % 3 == 1:
            raws.append(f"8-{base[1:4]}-{base[4:7]}-{base[7:9]}-{base[9:]}")
        else:
            raws.append(base[1:])
    path = tmp_path / "mix.csv"
    path.write_text("contact\n" + "\n".join(raws) + "\n", encoding="utf-8")
    forbidden = set(raws)
    for raw in raws:
        normed = audw._normalize("email" if "@" in raw else "phone", raw)
        assert normed is not None
        forbidden.add(normed)
        forbidden.add(audw._sha256_hex(normed))
    respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 25}}))
    respx_mock.post(f"{BASE}/segment/25/confirm").mock(
        return_value=httpx.Response(200, json={"segment": {"id": 25}}))
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(
            200, json={"segments": [{**TEST_SEG, "id": 25,
                                     "name": "[TEST DirectAI] pdn"}]})
    )
    ctx = _ctx(tmp_path)
    with caplog.at_level(logging.WARNING):
        plan_text = await do_plan_write(
            ctx, "audience_segment_from_file",
            {"file_path": str(path), "segment_name": "[TEST DirectAI] pdn",
             "content_type": "phone", "id_column": "contact"},
        )
        assert plan_text.startswith("План ")
        out = await do_apply_write(ctx, _plan_id(plan_text), True)
    assert "статус applied" in out
    blob = plan_text + "\n" + out + "\n" + caplog.text
    conn = journal_mod.connect(tmp_path)
    try:
        rows = conn.execute(
            "SELECT params_json, before_json, request_json, response_json,"
            " after_json, summary FROM operations"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "журнал пуст"
    blob += "\n" + "\n".join(" ".join(str(cell or "") for cell in row) for row in rows)
    missing = [value for value in forbidden if value and value in blob]
    assert missing == [], f"ПДн в выводе/журнале: {missing[:3]}"


async def test_delete_real_like_blocked(tmp_path, respx_mock):
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(
            200, json={"segments": REAL_LIKE + [TEST_SEG]})
    )
    for sid in (11, 12, 999):
        out = await do_plan_write(
            _ctx(tmp_path), "audience_segment_delete", {"segment_id": sid})
        assert out.startswith("Заблокировано защитой") or "не найден" in out, sid
    assert len(PLANS) == 0
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_delete", {"segment_id": 13})
    assert out.startswith("План ")


async def test_delete_flow_applied(tmp_path, respx_mock):
    # GET читают по очереди: guard (check_write), prepare, verify.
    respx_mock.get(SEGMENTS).mock(
        side_effect=[
            httpx.Response(200, json={"segments": REAL_LIKE + [TEST_SEG]}),
            httpx.Response(200, json={"segments": REAL_LIKE + [TEST_SEG]}),
            httpx.Response(200, json={"segments": REAL_LIKE}),
        ]
    )
    delete = respx_mock.delete(f"{BASE}/segment/13").mock(
        return_value=httpx.Response(200, json={"success": True}))
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_delete", {"segment_id": 13})
    assert "«[TEST DirectAI] tmp»" in out
    out = await do_apply_write(_ctx(tmp_path), _plan_id(out), True)
    assert "статус applied" in out
    assert "удалён" in out
    assert delete.called
    assert delete.calls[0].request.headers["Authorization"] == "OAuth aud-token"


async def test_apply_twice_rejected(tmp_path, respx_mock):
    respx_mock.get(SEGMENTS).mock(
        side_effect=[
            httpx.Response(200, json={"segments": REAL_LIKE + [TEST_SEG]}),
            httpx.Response(200, json={"segments": REAL_LIKE + [TEST_SEG]}),
            httpx.Response(200, json={"segments": REAL_LIKE}),
        ]
    )
    respx_mock.delete(f"{BASE}/segment/13").mock(
        return_value=httpx.Response(200, json={"success": True}))
    out = await do_plan_write(
        _ctx(tmp_path), "audience_segment_delete", {"segment_id": 13})
    pid = _plan_id(out)
    out = await do_apply_write(_ctx(tmp_path), pid, True)
    assert "статус applied" in out
    out = await do_apply_write(_ctx(tmp_path), pid, True)
    assert "неизвестен, просрочен или уже применён" in out


def _load_write_script():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent / "scripts" / "audience_write.py"
    )
    spec = importlib.util.spec_from_file_location("audience_write_script", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_write_yes_and_no(tmp_path, respx_mock, monkeypatch, capsys):
    script = _load_write_script()
    monkeypatch.setattr(cfg, "load_settings",
                        lambda *a, **k: _ctx(tmp_path).settings)
    monkeypatch.setattr(cfg, "get_token", lambda login: "main-token")
    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    path = _write_contacts(tmp_path)
    upload = respx_mock.post(UPLOAD).mock(
        return_value=httpx.Response(200, json={"segment": {"id": 26}}))
    respx_mock.post(f"{BASE}/segment/26/confirm").mock(
        return_value=httpx.Response(200, json={"segment": {"id": 26}}))
    respx_mock.get(SEGMENTS).mock(
        return_value=httpx.Response(
            200, json={"segments": [{**TEST_SEG, "id": 26,
                                     "name": "[TEST DirectAI] cli"}]})
    )
    rc = script.main(
        ["plan-from-file", "--file", path, "--name", "[TEST DirectAI] cli",
         "--content-type", "phone"],
        input_fn=lambda _: "нет",
    )
    assert rc == 1
    assert not upload.called
    rc = script.main(
        ["plan-from-file", "--file", path, "--name", "[TEST DirectAI] cli",
         "--content-type", "phone"],
        input_fn=lambda _: "ДА",
    )
    assert rc == 0
    assert upload.called
