"""goals.toml loading (step 7, no Metrika API)."""

from directai_mcp.config import load_settings

ACCOUNTS = """\
[auth]
login = "x"

[accounts.t]
login = "x"
"""


def test_goal_names_loaded(tmp_path):
    (tmp_path / "accounts.toml").write_text(ACCOUNTS, encoding="utf-8")
    (tmp_path / "goals.toml").write_text(
        '[goals]\n"1" = "Заказ"\n"2" = ""\n', encoding="utf-8"
    )
    settings = load_settings(tmp_path / "accounts.toml")
    assert settings.goal_names == {"1": "Заказ"}


def test_goal_names_missing_file_ok(tmp_path):
    (tmp_path / "accounts.toml").write_text(ACCOUNTS, encoding="utf-8")
    settings = load_settings(tmp_path / "accounts.toml")
    assert settings.goal_names == {}
