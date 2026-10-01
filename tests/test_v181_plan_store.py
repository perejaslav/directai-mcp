"""v1.8.1: shared on-disk plan store (one file per plan, single apply)."""

import json
import threading

import httpx

from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety.plans import Plan, PlanStore
from directai_mcp.server import do_apply_write

BASE = "https://api.direct.yandex.com/json/v5"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=True,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _plan(action="keywords_state"):
    return Plan(
        plan_id="",
        action=action,
        account_login="test-login",
        params={},
        before=None,
        requests=[],
        preview="p",
    )


def _keywords_ok(respx_mock):
    respx_mock.post(f"{BASE}/keywords").mock(
        side_effect=[
            httpx.Response(200, json={"result": {"SuspendResults": [{}]}}),
            httpx.Response(
                200, json={"result": {"Keywords": [{"Id": 7, "State": "SUSPENDED"}]}}
            ),
        ]
    )


def _keyword_plan_data():
    return {
        "action": "keywords_state",
        "account_login": "test-login",
        "params": {"account": "t", "keyword_ids": [7], "operation": "suspend"},
        "before": {7: "ON"},
        "requests": [
            ["keywords", "suspend", {"SelectionCriteria": {"Ids": [7]}}]
        ],
        "preview": "p",
    }


def test_cross_instance_take(tmp_path):
    """Plan created by one store instance is claimed by another (same dir)."""
    first = PlanStore(tmp_path / "plans")
    second = PlanStore(tmp_path / "plans")
    pid = first.put(_plan())
    assert second.status_of(pid) == "ok"
    assert second.peek(pid) is not None
    plan, reason = second.take_detailed(pid)
    assert reason == "ok" and plan is not None
    assert first.status_of(pid) == "used"
    assert first.take(pid) is None


def test_reapply_rejected_as_used(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    store = PlanStore(tmp_path / "plans")
    data = _keyword_plan_data()
    pid = store.put(Plan(plan_id="", warnings=[], created_at=0.0, **data))
    out = await_apply(ctx, pid, respx_mock)
    assert "статус applied" in out
    out2 = await_apply(ctx, pid, respx_mock)
    assert "уже применён" in out2


def await_apply(ctx, pid, respx_mock):
    import asyncio

    _keywords_ok(respx_mock)
    return asyncio.run(do_apply_write(ctx, pid, acknowledge_warnings=True))


def test_unknown_id_says_not_found(tmp_path):
    import asyncio

    out = asyncio.run(do_apply_write(_ctx(tmp_path), "deadbeef1234"))
    assert "не найден" in out


def test_bad_id_format_says_not_found(tmp_path):
    store = PlanStore(tmp_path / "plans")
    assert store.status_of("../../evil") == "not_found"
    assert store.take("../../evil") is None


def test_expired_says_expired(tmp_path):
    import asyncio

    ctx = _ctx(tmp_path)
    store = PlanStore(tmp_path / "plans")
    pid = store.put(_plan())
    path = tmp_path / "plans" / f"{pid}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["created_at"] -= 10000.0
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert store.status_of(pid) == "expired"
    out = asyncio.run(do_apply_write(ctx, pid))
    assert "просрочен" in out


def test_race_two_takes_one_winner(tmp_path):
    store = PlanStore(tmp_path / "plans")
    pid = store.put(_plan())
    results = []

    def _claim():
        _plan, reason = PlanStore(tmp_path / "plans").take_detailed(pid)
        results.append(reason)

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "used"]


def test_applied_survives_prune_with_used_message(tmp_path):
    store = PlanStore(tmp_path / "plans")
    pid = store.put(_plan())
    _taken, reason = store.take_detailed(pid)
    assert reason == "ok"
    store.mark_terminal(pid, "applied")
    store.prune()
    assert store.status_of(pid) == "used"
    assert store.take(pid) is None
