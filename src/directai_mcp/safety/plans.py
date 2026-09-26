"""Write plans: 12-hex id, 15-min TTL, single use (SPEC 6.3)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

PLAN_TTL = 900.0


@dataclass
class Plan:
    plan_id: str
    action: str
    account_login: str
    params: dict
    before: object
    requests: list[tuple[str, str, dict]]
    preview: str
    warnings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)
    used: bool = False


class PlanStore:
    """In-memory single-process store."""

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    def put(self, plan: Plan) -> str:
        plan.plan_id = secrets.token_hex(6)
        plan.created_at = time.monotonic()
        self._plans[plan.plan_id] = plan
        return plan.plan_id

    def take(self, plan_id: str) -> Plan | None:
        """Return plan and mark used; None if unknown, expired or used."""
        plan = self._plans.get(plan_id)
        if plan is None or plan.used:
            return None
        if time.monotonic() - plan.created_at > PLAN_TTL:
            del self._plans[plan_id]
            return None
        plan.used = True
        return plan

    def peek(self, plan_id: str) -> Plan | None:
        plan = self._plans.get(plan_id)
        if plan is None or plan.used:
            return None
        if time.monotonic() - plan.created_at > PLAN_TTL:
            return None
        return plan

    def __len__(self) -> int:
        return len(self._plans)

    def clear(self) -> None:
        self._plans.clear()
