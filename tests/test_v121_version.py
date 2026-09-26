"""v1.1.11: футер версии и варнинг о протухшем процессе."""

import httpx

import directai_mcp
import directai_mcp.catalog.campaigns as _c  # noqa: F401 (реестр)
import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog import common
from directai_mcp.catalog.campaigns import CampaignsGetParams
from directai_mcp.catalog.common import (
    finalize,
    on_disk_version,
    staleness_warning,
    version_footer,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"
V501 = "https://api.direct.yandex.com/json/v501/campaigns"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def test_on_disk_version_matches_package():
    assert on_disk_version() == directai_mcp.__version__
    assert version_footer() == f"DirectAI v{directai_mcp.__version__}"


def test_no_warning_when_fresh():
    assert common.RUNNING_VERSION == on_disk_version()
    assert staleness_warning() is None


def test_warning_when_stale(monkeypatch):
    monkeypatch.setattr(common, "RUNNING_VERSION", "0.0.1")
    assert version_footer() == "DirectAI v0.0.1"
    assert staleness_warning() == (
        f"⚠ Запущена устаревшая версия v0.0.1, "
        f"на диске v{on_disk_version()} — перезапустите харнес."
    )


def test_finalize_without_flag_has_no_footer(tmp_path):
    ctx = _ctx(tmp_path)
    out = finalize(ctx, "ctx", "act", ["A"], [{"A": "1"}], None, None, [])
    assert "DirectAI v" not in out
    assert "устаревшая версия" not in out


async def test_stats_footer_last_line_fresh(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        return_value=_tsv("Impressions\tClicks\tCost\n100\t5\t1100.00\n"))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_summary"].run(
        ctx, ACTIONS["stats_summary"].params(account="m",
                                            with_conversions=False))
    assert out.splitlines()[-1] == version_footer()
    assert not out.startswith("⚠")


async def test_stats_stale_warning_first_line(monkeypatch, respx_mock, tmp_path):
    monkeypatch.setattr(common, "RUNNING_VERSION", "0.0.1")
    respx_mock.post(RURL).mock(
        return_value=_tsv("Impressions\tClicks\tCost\n100\t5\t1100.00\n"))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_summary"].run(
        ctx, ACTIONS["stats_summary"].params(account="m",
                                            with_conversions=False))
    assert out.splitlines()[0] == staleness_warning()
    assert "v0.0.1, на диске v" in out.splitlines()[0]
    assert out.splitlines()[-1] == "DirectAI v0.0.1"


async def test_campaigns_get_footer_last_line(respx_mock, tmp_path):
    item = {"Id": 7, "Name": "К", "Type": "TEXT_CAMPAIGN", "State": "ON",
            "Status": "ACCEPTED"}
    respx_mock.post(V501).mock(
        return_value=httpx.Response(200, json={"result": {"Campaigns": [item]}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["campaigns_get"].run(
        ctx, CampaignsGetParams(account="m", campaign_ids=[7]))
    assert out.splitlines()[-1] == version_footer()
