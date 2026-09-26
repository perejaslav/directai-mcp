"""v1.1.15: accounts_balance — Live v4 Get, расход 7 дн., запас дней."""

import json as _json

import httpx

import directai_mcp.catalog.accounts as _a  # noqa: F401 (реестр)
from directai_mcp.api.live import LIVE_V4_URL, LiveError
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

LIVE = LIVE_V4_URL
REPORTS = "https://api.direct.yandex.com/json/v5/reports"


def _ctx(tmp_path, **accounts):
    if not accounts:
        accounts = {"s": AccountEntry(alias="s", login="client-v")}
    settings = Settings(
        auth_login="agency-login",
        accounts=accounts,
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _live(accounts, problems=()):
    return httpx.Response(200, json={"data": {
        "Accounts": accounts,
        "ActionsResult": [
            {"Login": login, "Errors": [
                {"FaultCode": 515, "FaultString": "no shared account"}]}
            for login in problems
        ],
    }})


def _acc(login, amount="19341.2", avail="19050.01"):
    return {"Currency": "RUB", "AccountID": 90000015, "Login": login,
            "Amount": amount, "AmountAvailableForTransfer": avail,
            "AgencyName": None}


def _tsv(costs):
    lines = ["Date\tCost"]
    for i, cost in enumerate(costs, start=1):
        lines.append(f"2026-09-{i:02d}\t{cost}")
    return httpx.Response(200, text="\n".join(lines) + "\n")


async def test_balance_only_get_no_finance(respx_mock, tmp_path):
    route = respx_mock.post(LIVE).mock(
        return_value=_live([_acc("client-v")]))
    respx_mock.post(REPORTS).mock(
        return_value=_tsv(["100.00"] * 7))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_balance"].run(
        ctx, ACTIONS["accounts_balance"].params(account="s"))
    sent = _json.loads(route.calls[0].request.content)
    assert sent["method"] == "AccountManagement"
    assert sent["param"]["Action"] == "Get"
    assert sent["token"] == "fake"
    raw = _json.dumps(sent)
    assert "finance_token" not in raw and "operation_num" not in raw
    assert "19 341.20" in out and "19 050.01" in out and "RUB" in out


async def test_averages_and_runway(respx_mock, tmp_path):
    respx_mock.post(LIVE).mock(
        return_value=_live([_acc("client-v")]))
    # 5 активных дней по 71 557.184 → итог 357 785.92.
    respx_mock.post(REPORTS).mock(return_value=_tsv(
        ["71557.184"] * 5 + ["0.00", "--"]))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_balance"].run(
        ctx, ACTIONS["accounts_balance"].params(account="s"))
    assert "357 785.92" in out
    assert "51 112.27" in out  # по календарным дням (/7)
    assert "71 557.18" in out  # по активным дням (/5)
    assert "хватит на ~0.3 дней (по активным дням)" in out


async def test_multi_login_table(respx_mock, tmp_path):
    route = respx_mock.post(LIVE).mock(return_value=_live(
        [_acc("client-v"), _acc("client-e", "1100.00", "900.00")],
        problems=["ghost"]))
    respx_mock.post(REPORTS).mock(return_value=_tsv(["10.00"] * 7))
    ctx = _ctx(tmp_path,
               s=AccountEntry(alias="s", login="client-v"),
               r=AccountEntry(alias="r", login="client-e"),
               g=AccountEntry(alias="g", login="ghost"))
    out = await ACTIONS["accounts_balance"].run(
        ctx, ACTIONS["accounts_balance"].params(account="all"))
    assert route.call_count == 3  # живьём: мульти отвергается 71 — по вызову на логин
    for call in route.calls:
        sent = _json.loads(call.request.content)
        assert len(sent["param"]["SelectionCriteria"]["Logins"]) == 1
    assert "client-v" in out and "client-e" in out
    assert "⚠ ghost" in out


async def test_live_error_envelope(respx_mock, tmp_path):
    respx_mock.post(LIVE).mock(return_value=httpx.Response(
        200, json={"error_code": 53, "error_str": "Authorization error",
                   "error_detail": ""}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_balance"].run(
        ctx, ACTIONS["accounts_balance"].params(account="s"))
    assert "ошибка 53" in out


async def test_zero_active_days_no_crash(respx_mock, tmp_path):
    respx_mock.post(LIVE).mock(
        return_value=_live([_acc("client-v")]))
    respx_mock.post(REPORTS).mock(
        return_value=_tsv(["0.00", "--", "0.00", "--", "0.00", "--", "0.00"]))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["accounts_balance"].run(
        ctx, ACTIONS["accounts_balance"].params(account="s"))
    assert "0.00" in out and "Ср/акт. день" in out


def test_no_write_operations_in_code():
    from pathlib import Path

    for name in ("api/live.py", "catalog/accounts.py"):
        text = (Path(__file__).resolve().parent.parent
                / "src" / "directai_mcp" / Path(name)).read_text(encoding="utf-8")
        for word in ("TransferMoney", "Deposit", "Invoice", "finance_token",
                     "operation_num", "CreateInvoice"):
            assert word not in text, f"{name}: {word}"
    assert issubclass(LiveError, Exception)
