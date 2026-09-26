"""v1.1.23: RESPONSIVE_AD — первый + (N вариантов) в inline, полный набор в файлах."""

import httpx

import directai_mcp.catalog.ads as _a  # noqa: F401 (реестр)
import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

BASE = "https://api.direct.yandex.com/json/v5"
RURL = f"{BASE}/reports"

# Порядок как в живом Ads.get 90000000002 (тест 18): первый — «Краска...»,
# «Колеруем...» — 4-й. N=7, а не 3.
TITLES = [
    "Краска прямо по ржавчине для наружных работ",
    "Грунт-эмаль Эталон прямо по ржавчине без зачистки",
    "Срок службы покрытия — 6 лет. Грунт-эмаль Эталон",
    "Колеруем в цвета RAL и NCS от 20 кг — Эталон",
    "Работа по ржавчине до 50 мкм — грунт-эмаль Эталон",
    "Металлоконструкции, трубы и заборы: эмаль Эталон",
    "Быстросохнущая грунт-эмаль 3 в 1 «Эталон»",
]
TEXTS = ["Текст один", "Текст два", "Текст три"]

AD = {
    "Id": 90000000002,
    "Type": "RESPONSIVE_AD",
    "State": "ON",
    "Status": "ACCEPTED",
    "ResponsiveAd": {
        "Titles": [{"Title": t} for t in TITLES],
        "Texts": [{"Text": t} for t in TEXTS],
    },
}


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ads_mock(respx_mock):
    respx_mock.post(f"{BASE}/ads").mock(
        return_value=httpx.Response(200, json={"result": {"Ads": [AD]}})
    )


async def test_inline_first_title_with_count(respx_mock, tmp_path):
    _ads_mock(respx_mock)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["ads_list"].run(
        ctx, ACTIONS["ads_list"].params(account="t", ad_ids=[90000000002]))
    # Первый в порядке API + верный счётчик (7, не 3).
    assert "Краска прямо по ржавчине для наружных работ (7 вариантов)" in out
    assert "Текст один (3 вариантов)" in out
    # Остальные варианты в inline не текут (второй заголовок набора
    # отсутствует целиком, не обрезан).
    assert "Колеруем в цвета" not in out
    assert "Грунт-эмаль Эталон прямо по ржавчине без зачистки" not in out


async def test_file_csv_all_titles_pipe(respx_mock, tmp_path):
    _ads_mock(respx_mock)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["ads_list"].run(
        ctx, ACTIONS["ads_list"].params(
            account="t", ad_ids=[90000000002], output="file", format="csv"))
    path = out.split("Полный результат: ")[1].split(" (")[0]
    from pathlib import Path as _Path

    body = _Path(path).read_text(encoding="utf-8-sig")
    assert " | ".join(TITLES) in body
    assert " | ".join(TEXTS) in body
    # Колонка Titles — чистый набор без счётчика; счётчик — в Title.
    head, row = body.splitlines()[0].split(";"), body.splitlines()[1].split(";")
    assert row[head.index("Titles")] == " | ".join(TITLES)
    assert row[head.index("Title")].endswith("(7 вариантов)")


async def test_single_goal_no_sum_wording(respx_mock, tmp_path):
    """п.5: при одной цели нет «суммы по целям» и пометки про дубли."""
    respx_mock.post(RURL).mock(return_value=httpx.Response(200, text=(
        "CampaignId\tCampaignName\tAdGroupId\tAdGroupName\tAdId\t"
        "Impressions\tClicks\tCost\tConversions_9_AUTO\n"
        "7\tК\t1\tГ\t11\t100\t5\t1100.00\t2\n")))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_ads"].run(
        ctx, ACTIONS["stats_ads"].params(
            account="t", campaign_ids=[7], with_conversions=False,
            goals=["9"], attribution=["AUTO"]))
    assert "| Conversions | CPA | CR |" in out
    assert "сумма по целям" not in out
    assert "дубли визитов" not in out
