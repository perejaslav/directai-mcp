"""v1.16.1: статус версий в describe_action без аргументов.

Три числа: server_version (метаданные пакета, importlib.metadata),
code_version (код в памяти процесса), disk_version (исходники на диске).
Расхождения code/disk — «перезапустите MCP-сервер», server_version/code —
«переустановите пакет».
"""
import importlib.metadata

import pytest

import directai_mcp
import directai_mcp.server as server_mod
from directai_mcp.catalog import common


def _tool(name="describe_action"):
    return server_mod.build_server()._tool_manager._tools[name]


def test_status_has_three_versions():
    out = _tool().fn()
    assert "server_version (пакет, importlib.metadata)" in out
    assert f"code_version (код, запущенный в этом процессе): {directai_mcp.__version__}" in out
    assert f"disk_version (исходники на диске): {common.on_disk_version()}" in out


def test_status_uses_package_metadata(monkeypatch):
    seen = []

    def _fake(name):
        seen.append(name)
        return "9.9.9"

    monkeypatch.setattr(importlib.metadata, "version", _fake)
    out = _tool().fn()
    assert seen == ["directai-mcp"]  # версия пакета ищется один раз за вызов
    assert "server_version (пакет, importlib.metadata): 9.9.9" in out
    assert "переустановите пакет" in out


def test_restart_warning_when_disk_differs(monkeypatch):
    monkeypatch.setattr(common, "on_disk_version", lambda: "99.0.0")
    monkeypatch.setattr(common, "package_version", lambda: common.RUNNING_VERSION)
    out = common.server_status()
    assert "перезапустите MCP-сервер" in out
    assert "v99.0.0" in out
    assert "переустановите пакет" not in out


def test_no_restart_warning_when_versions_match(monkeypatch):
    monkeypatch.setattr(common, "on_disk_version", lambda: common.RUNNING_VERSION)
    monkeypatch.setattr(common, "package_version", lambda: common.RUNNING_VERSION)
    out = common.server_status()
    assert "перезапустите MCP-сервер" not in out
    assert "переустановите пакет" not in out


def test_warnings_helper_is_pure():
    assert common.server_version_warnings("1.2.3", "1.2.3", "1.2.3") == []
    out = common.server_version_warnings("1.2.3", None, None)
    assert out == []  # неизвестные версии — не выдумываем расхождение


def test_package_not_found_is_dash(monkeypatch):
    def _boom(_name):
        raise importlib.metadata.PackageNotFoundError("directai-mcp")

    monkeypatch.setattr(importlib.metadata, "version", _boom)
    assert common.package_version() is None
    out = common.server_status()
    assert "server_version (пакет, importlib.metadata): —" in out


def test_describe_action_with_name_still_works():
    out = _tool().fn("campaigns_list")
    assert out.startswith("Действие campaigns_list [read]:")
    assert "Параметры (JSON Schema):" in out


def test_empty_name_is_status_not_error():
    assert _tool().fn("").startswith("DirectAI MCP: статус сервера")


@pytest.mark.parametrize("missing", [None])
def test_signature_allows_missing_name(missing):
    import inspect

    params = inspect.signature(_tool().fn).parameters
    assert params["name"].default is None
    assert missing is None