"""v1.1.21: сводка по типам критериев до обрезки топ-N + «показано N из M строк»."""

import httpx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import criterion_summary
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"
BASE = "https://api.direct.yandex.com/json/v5"

CHUNK = (
    "CampaignId\tAdGroupId\tAdGroupName\tCriterion\tCriterionId\t"
    "CriterionType\tImpressions\tClicks\tCost\tConversions\n"
    "7\t1\tГруппа А\tфраза один\t11\tKEYWORD\t100\t10\t1100.00\t4\n"
    "7\t1\tГруппа А\tфраза два\t12\tKEYWORD\t65\t2\t761.79\t1\n"
    "7\t1\tГруппа А\t---autotargeting\t99\tAUTOTARGETING\t1\t0\t0.00\t0\n"
)


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


async def test_summary_before_table_over_all_rows(respx_mock, tmp_path):
    """Сводка до таблицы, по всем строкам; автотаргетинг виден при limit=2."""
    respx_mock.post(RURL).mock(return_value=_tsv(CHUNK))
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=httpx.Response(200, json={"result": {"Keywords": []}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False,
            limit=2))
    head = "Сводка по типам критериев (всего строк: 3):"
    assert head in out
    # Сводка — перед таблицей фраз.
    assert out.index(head) < out.index("| # |")
    assert "- КЛЮЧИ: показы 165; клики 12; CTR 7.27%; расход 1 861.79 ₽; " \
        "доля 100.00%; конверсии 5; CPA 372.36 ₽." in out
    assert "- АВТОТАРГЕТИНГ: показы 1; клики 0; CTR 0.00%; расход 0.00 ₽; " \
        "доля 0.00%; конверсии 0; CPA —." in out
    # Обрезка явная: строка автотаргетинга не в выдаче, но в сводке есть.
    assert "показано 2 из 3 строк." in out
    assert "---autotargeting" not in out


def test_summary_buckets_other_types():
    """Неизвестный/пустой CriterionType — корзина ПРОЧЕЕ."""
    rows = [
        {"CriterionType": "KEYWORD", "Impressions": "10", "Clicks": "1",
         "Cost": "100.00", "Conversions": "2"},
        {"CriterionType": "SOMETHING_NEW", "Impressions": "5", "Clicks": "0",
         "Cost": "10.00", "Conversions": None},
        {"Impressions": "3", "Clicks": "0", "Cost": "0.00",
         "Conversions": None},
    ]
    from decimal import Decimal

    lines = criterion_summary(rows, "Conversions", Decimal("110.00"))
    assert lines[0] == "Сводка по типам критериев (всего строк: 3):"
    assert lines[1] == "- КЛЮЧИ: показы 10; клики 1; CTR 10.00%; " \
        "расход 100.00 ₽; доля 90.91%; конверсии 2; CPA 50.00 ₽."
    assert lines[2] == "- ПРОЧЕЕ: показы 8; клики 0; CTR 0.00%; " \
        "расход 10.00 ₽; доля 9.09%; конверсии 0; CPA —."
    assert len(lines) == 3


async def test_no_summary_without_rows(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(return_value=_tsv(
        "CampaignId\tAdGroupId\tCriterion\tCriterionId\tCriterionType\t"
        "Impressions\tClicks\tCost\n"))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    assert "Сводка по типам" not in out
