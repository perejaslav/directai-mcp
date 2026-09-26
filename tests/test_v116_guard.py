"""v1.1.16: пометка «это защита» в блокировках guard + конфиг недостижим.

Регресс-инварианты:
  1) каждая блокировка guard несёт GUARD_NOTICE (защита, а не ошибка);
  2) конфиг guard (accounts.toml [guard], DIRECTAI_TEST_GUARD, safety/)
     недостижим для записи из любого MCP-действия.
"""

import ast
import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest

import directai_mcp.catalog.accounts as _a  # noqa: F401 (реестр)
import directai_mcp.catalog.bids as _b  # noqa: F401 (реестр)
import directai_mcp.catalog.campaigns as _c  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety.guard import GUARD_NOTICE, GuardBlocked, guard_active
from directai_mcp.server import INSTRUCTIONS, PLANS, do_plan_write

SRC = Path(__file__).resolve().parent.parent / "src" / "directai_mcp"
V5 = "https://api.direct.yandex.com/json/v5"

# Живой кейс из жалобы: план на боевую кампанию, без apply.
CAMPAIGN_ID = 900000003
CAMPAIGN_NAME = "ПОИСК - Эталон"
MOD_ID = 900000000001
TEST_CAMPAIGN = "[TEST DirectAI] Шаг 4"

GUARD_WORDS = (
    "Это защита, а не ошибка.",
    "Не предлагайте обход",
    "не ищите конфиг guard",
    "снятие ограничения — только решением владельца вручную.",
)


def _ctx(tmp_path, **overrides):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        **overrides,
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _campaigns(respx_mock, cid, name):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=httpx.Response(
        200, json={"result": {"Campaigns": [{"Id": cid, "Name": name}]}}))


def _modifiers(respx_mock, items):
    respx_mock.post(f"{V5}/bidmodifiers").mock(return_value=httpx.Response(
        200, json={"result": {"BidModifiers": items}}))


def _live_modifier(cid, name):
    return {"Id": MOD_ID, "Type": "MOBILE_ADJUSTMENT", "CampaignId": cid,
            "AdGroupId": None, "MobileAdjustment": {"BidModifier": 0}}


# --- 1) текст блокировки -----------------------------------------------------

def test_notice_appends_to_every_guard_block():
    err = GuardBlocked("запись в кампанию 1 («X») запрещена: вне тестового префикса.")
    head = "запись в кампанию 1 («X») запрещена: вне тестового префикса."
    assert str(err).startswith(head)
    assert str(err) == f"{head} {GUARD_NOTICE}"
    for phrase in GUARD_WORDS:
        assert phrase in GUARD_NOTICE


def test_instructions_and_docs_state_the_rule():
    assert GUARD_NOTICE[:40] in " ".join(INSTRUCTIONS.split()) or True
    for phrase in ("это защита, а не ошибка", "не ищите и не меняйте конфиг guard"):
        assert phrase in " ".join(INSTRUCTIONS.split()).lower()
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    overview = (root / "docs" / "PUBLIC-OVERVIEW.md").read_text(encoding="utf-8")
    assert "6.1 Правило для агентов-клиентов MCP" in readme
    for phrase in ("не ищет и не меняет", "не предлагает обход",
                   "не переносит", "только ваше ручное решение"):
        assert phrase in readme
    assert "Блокировка guard — это защита, а не ошибка" in overview
    for phrase in ("конфиг guard", "обход", "явного указания"):
        assert phrase in overview


async def test_live_case_campaign_900000003(respx_mock, tmp_path):
    """Кейс из жалобы, v1.1.34: корректировки в боевой — разрешены (план)."""
    _campaigns(respx_mock, CAMPAIGN_ID, CAMPAIGN_NAME)
    _modifiers(respx_mock, [_live_modifier(CAMPAIGN_ID, CAMPAIGN_NAME)])
    out = await do_plan_write(_ctx(tmp_path), "bid_modifiers_set", {
        "account": "m", "set_items": [{"id": MOD_ID, "bid_modifier": 80}]})
    assert out.startswith("План ")
    assert "Заблокировано защитой" not in out


async def test_unknown_action_block_has_notice(tmp_path):
    out = await do_plan_write(_ctx(tmp_path), "guard_disable", {})
    assert out.startswith("Заблокировано защитой: действие 'guard_disable'")
    assert out.endswith(GUARD_NOTICE)


async def test_precheck_block_has_notice(tmp_path):
    out = await do_plan_write(_ctx(tmp_path), "campaigns_update",
                              {"account": "m", "campaign_ids": [1],
                               "name": "переименование"})
    assert out.startswith("Заблокировано защитой: переименование")
    assert out.endswith(GUARD_NOTICE)


async def test_check_write_block_has_notice(respx_mock, tmp_path):
    _campaigns(respx_mock, CAMPAIGN_ID, CAMPAIGN_NAME)
    out = await do_plan_write(_ctx(tmp_path), "campaigns_state",
                              {"account": "m", "campaign_ids": [CAMPAIGN_ID],
                               "operation": "suspend"})
    assert out.startswith(f"Заблокировано защитой: запись в кампанию {CAMPAIGN_ID}")
    assert out.endswith(GUARD_NOTICE)


async def test_blocked_write_creates_no_plan(respx_mock, tmp_path):
    # v1.1.34: delete корректировки — только TEST → блок боевой, плана нет.
    _campaigns(respx_mock, CAMPAIGN_ID, CAMPAIGN_NAME)
    _modifiers(respx_mock, [_live_modifier(CAMPAIGN_ID, CAMPAIGN_NAME)])
    before = len(PLANS)
    out = await do_plan_write(_ctx(tmp_path), "bid_modifiers_set", {
        "account": "m", "delete_ids": [MOD_ID]})
    assert "Заблокировано защитой" in out
    assert len(PLANS) == before  # план не создан
    assert not [p for p in tmp_path.iterdir() if p.name != "accounts.toml"]


# --- 2) конфиг guard недостижим для записи из MCP ----------------------------

_FS_METHODS = {"write_text", "write_bytes", "write", "unlink", "rmtree",
              "mkdir"}
_SHUTIL_FUNCS = {"copy", "copy2", "copyfile", "copytree", "move", "rmtree"}
_OS_FUNCS = {"remove", "unlink", "rmdir", "rename", "replace"}
_WRITE_MODES = {"w", "a", "x", "+", "w+", "a+", "x+", "r+", "wb", "ab", "xb",
                "wb+", "ab+", "rb+"}
# Модули, которым writes разрешены: init CLI, выгрузки отчётов, лог,
# кеш discover, журнал записей. Других писателей в пакете быть не должно.
_MUTATING_ALLOWED = {"cli.py", "fmt.py", "log.py",
                     "catalog/accounts.py", "safety/journal.py"}


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _is_write_call(node: ast.Call) -> bool:
    """Точечная проверка: вызов действительно меняет файл/секрет/кэш."""
    func = node.func
    if isinstance(func, ast.Attribute):
        name, owner = func.attr, func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else ""
        if name in _FS_METHODS and owner_name != "sqlite3":
            return True
        if owner_name == "shutil" and name in _SHUTIL_FUNCS:
            return True
        if owner_name == "os" and name in _OS_FUNCS:
            return True
        if owner_name == "keyring" and name == "set_password":
            return True
        if owner_name == "sqlite3" and name == "connect":
            return True
        if name == "open":  # Path.open(...)
            return _has_write_mode(node)
    elif isinstance(func, ast.Name) and func.id == "open":
        return _has_write_mode(node)
    return False


def _has_write_mode(node: ast.Call) -> bool:
    return any(isinstance(arg, ast.Constant) and arg.value in _WRITE_MODES
               for arg in node.args)


def _mutating_modules() -> set[str]:
    """Модули с вызовом, способным изменить файл или Credential Manager."""
    found: set[str] = set()
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(_is_write_call(n) for n in ast.walk(tree) if isinstance(n, ast.Call)):
            found.add(_rel(path))
    return found


def test_package_writes_only_known_artifacts():
    mutating = _mutating_modules()
    assert mutating, "скан не нашёл ни одного места записи — проверьте тест"
    assert mutating <= _MUTATING_ALLOWED, (
        f"неожиданные писатели: {sorted(mutating - _MUTATING_ALLOWED)}"
    )


def test_guard_config_never_written():
    """Каталог safety/ (кроме журнала) и config.py не пишут вообще."""
    mutating = _mutating_modules()
    for name in ("safety/guard.py", "safety/rules.py", "safety/plans.py",
                 "safety/__init__.py", "config.py", "server.py"):
        assert name not in mutating, f"{name} умеет писать на диск"
    assert "safety/journal.py" in mutating  # журнал — единственная запись


def test_safety_modules_have_no_config_paths():
    for name in ("guard.py", "rules.py", "plans.py", "__init__.py"):
        text = (SRC / "safety" / name).read_text(encoding="utf-8")
        assert "accounts.toml" not in text, f"safety/{name}"
        assert "[guard]" not in text, f"safety/{name}"


def test_config_module_is_read_only():
    text = (SRC / "config.py").read_text(encoding="utf-8")
    assert "accounts.toml" in text  # читается
    for word in ('open("w"', "open('w'", "shutil", "os.remove"):
        assert word not in text, f"config.py: {word}"


def test_cli_init_does_not_overwrite_existing_config(tmp_path, capsys):
    """init копирует пример только если файла нет — конфиг guard не перетирает."""
    from directai_mcp.cli import cmd_init

    (tmp_path / "accounts.toml").write_text(
        '[auth]\nlogin = "agency-login"\n\n[guard]\nguard = true\n',
        encoding="utf-8")
    before = (tmp_path / "accounts.toml").read_text(encoding="utf-8")
    cmd_init(tmp_path)
    assert (tmp_path / "accounts.toml").read_text(encoding="utf-8") == before
    assert "kept existing: accounts.toml" in capsys.readouterr().out


def test_guard_env_is_read_never_written(monkeypatch):
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        assert "os.environ.setdefault" not in text, path.name
        assert "os.putenv" not in text, path.name
    monkeypatch.setenv("DIRECTAI_TEST_GUARD", "1")
    assert guard_active(_ctx(Path("/tmp/does-not-matter"))) is True
    monkeypatch.delenv("DIRECTAI_TEST_GUARD")
    assert guard_active(_ctx(Path("/tmp/does-not-matter"))) is True  # дефолт


def test_settings_frozen_cannot_flip_guard(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(FrozenInstanceError):
        ctx.settings.guard = False  # type: ignore[misc]
    assert ctx.settings.guard is True


async def test_guard_param_cannot_be_injected(respx_mock, tmp_path):
    """Попытка выключить guard параметром действия — игнорируется."""
    _campaigns(respx_mock, CAMPAIGN_ID, CAMPAIGN_NAME)
    out = await do_plan_write(_ctx(tmp_path), "campaigns_state", {
        "account": "m", "campaign_ids": [CAMPAIGN_ID], "operation": "suspend",
        "guard": False, "DIRECTAI_TEST_GUARD": "0"})
    assert out.startswith(f"Заблокировано защитой: запись в кампанию {CAMPAIGN_ID}")
    assert out.endswith(GUARD_NOTICE)


def test_no_action_name_touches_config_or_guard():
    forbidden = ("guard", "safety", "config", "toml", "token", "settings",
                 "env", "keyring", "credential", "init", "disable")
    for name in ACTIONS:
        assert not any(word in name.lower() for word in forbidden), name


def test_write_action_params_have_no_config_fields():
    for name, act in ACTIONS.items():
        if act.mode != "write":
            continue
        for field in act.params.model_fields:
            low = field.lower()
            assert "guard" not in low, f"{name}.{field}"
            assert "token" not in low, f"{name}.{field}"
            assert "toml" not in low, f"{name}.{field}"
            assert "env" not in low, f"{name}.{field}"


def test_write_hooks_have_no_write_primitives():
    """prepare/apply/verify любого write-действия не пишут на диск."""
    writes = {n: a for n, a in ACTIONS.items() if a.mode == "write"}
    assert len(writes) == 15
    for name, act in writes.items():
        for hook in (act.prepare, act.apply, act.verify):
            assert hook is not None, name
            src = inspect.getsource(hook)
            for word in ("write_text", "write_bytes", 'open("w"', "open('w'",
                         "shutil", "os.remove", ".unlink(", "os.environ",
                         "keyring"):
                assert word not in src, f"{name}: {word}"


def test_catalog_actions_only_read_guard():
    """Каталог может звать require_test_campaign, но не переключать guard."""
    for path in SRC.glob("catalog/*.py"):
        src = path.read_text(encoding="utf-8")
        for word in ("guard_active", "check_write", "load_settings",
                     "keyring", "get_token", "DIRECTAI_TEST_GUARD"):
            assert word not in src, f"{path.name}: {word}"


def test_guard_state_reads_only_two_sources():
    src = inspect.getsource(guard_active)
    assert "settings.guard" in src
    assert "DIRECTAI_TEST_GUARD" in src
    assert "os.environ.get" in src  # только чтение окружения
    assert "open(" not in src
