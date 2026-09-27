"""v1.2.3: serverInfo.version в initialize = __version__ пакета."""

import directai_mcp
from directai_mcp.server import build_server


def test_server_info_version_matches_package():
    mcp = build_server()
    opts = mcp._mcp_server.create_initialization_options()
    assert opts.server_name == "directai-mcp"
    assert opts.server_version == directai_mcp.__version__


def test_server_info_version_not_sdk_version():
    from importlib.metadata import version

    mcp = build_server()
    opts = mcp._mcp_server.create_initialization_options()
    assert opts.server_version != version("mcp")
