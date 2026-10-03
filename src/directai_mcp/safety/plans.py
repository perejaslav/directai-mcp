"""Write plans: 12-hex id, 15-min TTL, single use (SPEC 6.3).

v1.8.1: shared on-disk store. Plans used to live in process memory
(``PlanStore._plans``), so ``apply_write`` in a different OS process could
not see a plan created by ``plan_write`` ("plan_id неизвестен"). Now one
file per plan lives in ``<data_dir>/plans/`` (next to ``journal.sqlite``),
visible to every server process. Writes are atomic (temp + rename) and a
plan is claimed exactly once via an exclusive lock file
(``pending -> applying -> applied/failed``).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

PLAN_TTL = 900.0
#: Terminal plan files (already applied/failed) are kept this long so that
#: a repeated apply still reports "already applied" instead of "not found".
PLAN_DONE_TTL = 86400.0
PLAN_DIR_NAME = "plans"

PLAN_ID_RE = re.compile(r"^[0-9a-f]{12}$")

STATUS_PENDING = "pending"
STATUS_APPLYING = "applying"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
_TERMINAL = (STATUS_APPLIED, STATUS_FAILED)

REASON_OK = "ok"
REASON_NOT_FOUND = "not_found"
REASON_EXPIRED = "expired"
REASON_USED = "used"


@dataclass
class Plan:
    plan_id: str
    action: str
    account_login: str
    params: dict
    before: object
    requests: list
    preview: str
    warnings: list[str] = field(default_factory=list)
    # v1.15.0: причины «опасной операции» (guard mode=confirm).
    danger: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: str = STATUS_PENDING


def plans_dir_for(data_dir: Path) -> Path:
    """Plans directory next to the operation journal."""
    return Path(data_dir) / PLAN_DIR_NAME


def _valid_plan_id(plan_id: str) -> bool:
    return bool(PLAN_ID_RE.match(plan_id or ""))


class PlanStore:
    """File-backed store shared by all server processes.

    ``directory`` defaults to ``<data_dir>/plans`` resolved lazily, so tests
    overriding ``DIRECTAI_HOME`` get an isolated store.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None

    def _dir(self) -> Path:
        if self._directory is not None:
            root = self._directory
        else:
            from directai_mcp.config import data_dir

            root = plans_dir_for(data_dir())
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _path(self, plan_id: str) -> Path:
        return self._dir() / f"{plan_id}.json"

    def _lock_path(self, plan_id: str) -> Path:
        return self._dir() / f"{plan_id}.lock"

    # -- serialization -------------------------------------------------

    @staticmethod
    def _dump(plan: Plan) -> bytes:
        doc = {
            "plan_id": plan.plan_id,
            "action": plan.action,
            "account_login": plan.account_login,
            "params": plan.params,
            "before": plan.before,
            "requests": [list(r) for r in plan.requests],
            "preview": plan.preview,
            "warnings": list(plan.warnings or []),
            "danger": list(plan.danger or []),
            "created_at": plan.created_at,
            "status": plan.status,
        }
        return json.dumps(doc, ensure_ascii=False, default=repr).encode("utf-8")

    @staticmethod
    def _load(doc: dict) -> Plan:
        return Plan(
            plan_id=str(doc["plan_id"]),
            action=str(doc["action"]),
            account_login=str(doc["account_login"]),
            params=dict(doc.get("params") or {}),
            before=doc.get("before"),
            requests=[list(r) for r in (doc.get("requests") or [])],
            preview=str(doc.get("preview") or ""),
            warnings=list(doc.get("warnings") or []),
            danger=list(doc.get("danger") or []),
            created_at=float(doc.get("created_at") or 0.0),
            status=str(doc.get("status") or STATUS_PENDING),
        )

    def _read_doc(self, plan_id: str) -> dict | None:
        try:
            with self._path(plan_id).open("rb") as f:
                doc = json.load(f)
        except (FileNotFoundError, ValueError, OSError):
            return None
        return doc if isinstance(doc, dict) else None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    # -- maintenance ---------------------------------------------------

    def prune(self) -> int:
        """Delete expired files. Returns the number removed."""
        try:
            root = self._dir()
        except OSError:
            return 0
        now = time.time()
        removed = 0
        try:
            names = os.listdir(root)
        except OSError:
            return 0
        for name in names:
            if not name.endswith(".json") or len(name) != 17:
                continue
            path = root / name
            try:
                with path.open("rb") as f:
                    doc = json.load(f)
                created = float(doc.get("created_at") or 0.0)
                status = str(doc.get("status") or STATUS_PENDING)
            except (ValueError, OSError, TypeError):
                continue
            ttl = PLAN_DONE_TTL if status in _TERMINAL else PLAN_TTL
            if now - created > ttl:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    # -- public API ----------------------------------------------------

    def put(self, plan: Plan) -> str:
        plan.plan_id = secrets.token_hex(6)
        plan.created_at = time.time()
        plan.status = STATUS_PENDING
        self._atomic_write(self._path(plan.plan_id), self._dump(plan))
        self.prune()
        return plan.plan_id

    def status_of(self, plan_id: str) -> str:
        """Pure (non-deleting) reason: ok | not_found | expired | used."""
        if not _valid_plan_id(plan_id):
            return REASON_NOT_FOUND
        doc = self._read_doc(plan_id)
        if doc is None:
            return REASON_NOT_FOUND
        try:
            created = float(doc.get("created_at") or 0.0)
        except (TypeError, ValueError):
            return REASON_NOT_FOUND
        status = str(doc.get("status") or STATUS_PENDING)
        if status != STATUS_PENDING:
            return REASON_USED
        if time.time() - created > PLAN_TTL:
            return REASON_EXPIRED
        return REASON_OK

    def peek(self, plan_id: str) -> Plan | None:
        if self.status_of(plan_id) != REASON_OK:
            return None
        doc = self._read_doc(plan_id)
        if doc is None:
            return None
        try:
            return self._load(doc)
        except (KeyError, TypeError, ValueError):
            return None

    #: A lock file older than this is considered left by a crashed process
    #: and may be stolen by a new claimant.
    LOCK_STALE_AFTER = 60.0

    def _acquire_lock(self, plan_id: str) -> bool:
        """Create the exclusive lock file. True if we own it."""
        try:
            fd = os.open(
                self._lock_path(plan_id),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.close(fd)
            return True
        except FileExistsError:
            pass
        except OSError:
            return False
        try:
            age = time.time() - self._lock_path(plan_id).stat().st_mtime
        except OSError:
            return False
        if age <= self.LOCK_STALE_AFTER:
            return False
        try:
            self._lock_path(plan_id).unlink()
        except OSError:
            return False
        try:
            fd = os.open(
                self._lock_path(plan_id),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.close(fd)
            return True
        except OSError:
            return False

    def _release_lock(self, plan_id: str) -> None:
        try:
            self._lock_path(plan_id).unlink()
        except OSError:
            pass

    def take_detailed(self, plan_id: str) -> tuple[Plan | None, str]:
        """Claim a plan exactly once. Returns (plan, reason)."""
        if not _valid_plan_id(plan_id):
            return None, REASON_NOT_FOUND
        reason = self.status_of(plan_id)
        if reason == REASON_EXPIRED:
            try:
                self._path(plan_id).unlink()
            except OSError:
                pass
            self.prune()
            return None, REASON_EXPIRED
        if reason != REASON_OK:
            self.prune()
            return None, reason
        if not self._acquire_lock(plan_id):
            # Another process is claiming (or has claimed) this plan.
            self.prune()
            if self.status_of(plan_id) == REASON_OK:
                return None, REASON_USED
            return None, self.status_of(plan_id)
        try:
            # Re-check inside the lock: a concurrent process may have
            # claimed the plan between our first check and the lock.
            if self.status_of(plan_id) != REASON_OK:
                return None, self.status_of(plan_id)
            doc = self._read_doc(plan_id)
            if doc is None:
                return None, REASON_NOT_FOUND
            try:
                plan = self._load(doc)
            except (KeyError, TypeError, ValueError):
                return None, REASON_NOT_FOUND
            plan.status = STATUS_APPLYING
            try:
                self._atomic_write(self._path(plan_id), self._dump(plan))
            except OSError:
                return None, REASON_NOT_FOUND
            return plan, REASON_OK
        finally:
            self._release_lock(plan_id)
            self.prune()

    def take(self, plan_id: str) -> Plan | None:
        """Return plan and claim it; None if unknown, expired or used."""
        plan, _ = self.take_detailed(plan_id)
        return plan

    def mark_terminal(self, plan_id: str, status: str) -> None:
        """Best-effort applied/failed marking after apply (keeps "used")."""
        if status not in _TERMINAL or not _valid_plan_id(plan_id):
            return
        doc = self._read_doc(plan_id)
        if doc is None:
            return
        doc["status"] = status
        try:
            self._atomic_write(self._path(plan_id), json.dumps(
                doc, ensure_ascii=False, default=repr).encode("utf-8"))
        except OSError:
            pass

    def __len__(self) -> int:
        self.prune()
        try:
            return sum(
                1 for n in os.listdir(self._dir()) if n.endswith(".json")
            )
        except OSError:
            return 0

    def clear(self) -> None:
        try:
            root = self._dir()
        except OSError:
            return
        try:
            names = os.listdir(root)
        except OSError:
            return
        for name in names:
            if name.endswith((".json", ".lock")):
                try:
                    (root / name).unlink()
                except OSError:
                    pass
