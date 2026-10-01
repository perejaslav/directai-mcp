"""Test isolation: every test gets its own DIRECTAI_HOME.

v1.8.1: write plans live on disk under <data_dir>/plans, so without this
fixture tests would share (and pollute) the real user data dir.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("DIRECTAI_HOME", str(tmp_path))
