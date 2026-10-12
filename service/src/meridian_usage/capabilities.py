"""Persisted per-group capability controls (SQLite), with an append-only audit log.

On first start the environment's defaults are written for every (group, capability) pair
that has no row yet, so an existing decision is never overwritten by a redeploy. Every change,
including the seeding, is recorded in `capability_audit`.

This module does not authorise anything: callers must check `capabilities:write` first.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from meridian_usage.policy import Capability, Policy

type Clock = Callable[[], datetime]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_state (
    group_id    TEXT NOT NULL,
    capability  TEXT NOT NULL,
    enabled     INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    PRIMARY KEY (group_id, capability)
);
CREATE TABLE IF NOT EXISTS capability_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    TEXT NOT NULL,
    capability  TEXT NOT NULL,
    enabled     INTEGER NOT NULL,
    changed_at  TEXT NOT NULL,
    changed_by  TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class CapabilityState:
    group: str
    capability: Capability
    enabled: bool
    updated_at: str
    updated_by: str


@dataclass(frozen=True)
class AuditEntry:
    seq: int  # insertion order; timestamps alone tie within a second
    group: str
    capability: Capability
    enabled: bool
    changed_at: str
    changed_by: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class CapabilityStore:
    def __init__(self, db_path: Path | str, clock: Clock = _utc_now) -> None:
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._clock = clock

    def close(self) -> None:
        self._db.close()

    def seed_defaults(self, policy: Policy, group_ids: Iterable[str]) -> int:
        """Write the environment's defaults where no decision exists yet. Returns rows added."""
        actor = f"defaults:{policy.environment}"
        now = _iso(self._clock())
        added = 0
        with self._db:
            for group in sorted(group_ids):
                for cap in Capability:
                    enabled = policy.capability_defaults[cap].for_group(group)
                    cur = self._db.execute(
                        "INSERT OR IGNORE INTO capability_state VALUES (?, ?, ?, ?, ?)",
                        (group, cap.value, int(enabled), now, actor),
                    )
                    if cur.rowcount:
                        added += 1
                        self._audit(group, cap, enabled, now, actor)
        return added

    def get(self, group: str, capability: Capability) -> CapabilityState | None:
        row = self._db.execute(
            "SELECT group_id, capability, enabled, updated_at, updated_by FROM capability_state "
            "WHERE group_id = ? AND capability = ?",
            (group, capability.value),
        ).fetchone()
        return _state(row) if row else None

    def is_enabled(self, group: str, capability: Capability) -> bool:
        """Missing rows count as disabled: nothing is on unless something turned it on."""
        state = self.get(group, capability)
        return state is not None and state.enabled

    def list_for_group(self, group: str) -> list[CapabilityState]:
        rows = self._db.execute(
            "SELECT group_id, capability, enabled, updated_at, updated_by FROM capability_state "
            "WHERE group_id = ? ORDER BY capability",
            (group,),
        ).fetchall()
        return [_state(r) for r in rows]

    def set(self, group: str, capability: Capability, enabled: bool, actor: str) -> CapabilityState:
        now = _iso(self._clock())
        with self._db:
            self._db.execute(
                "INSERT INTO capability_state VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (group_id, capability) DO UPDATE SET "
                "enabled = excluded.enabled, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (group, capability.value, int(enabled), now, actor),
            )
            self._audit(group, capability, enabled, now, actor)
        return CapabilityState(group, capability, enabled, now, actor)

    def audit_log(self, group: str | None = None) -> list[AuditEntry]:
        sql = (
            "SELECT id, group_id, capability, enabled, changed_at, changed_by FROM capability_audit"
        )
        params: tuple[str, ...] = ()
        if group is not None:
            sql += " WHERE group_id = ?"
            params = (group,)
        rows = self._db.execute(sql + " ORDER BY id", params).fetchall()
        return [AuditEntry(r[0], r[1], Capability(r[2]), bool(r[3]), r[4], r[5]) for r in rows]

    def _audit(self, group: str, cap: Capability, enabled: bool, at: str, actor: str) -> None:
        self._db.execute(
            "INSERT INTO capability_audit (group_id, capability, enabled, changed_at, changed_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (group, cap.value, int(enabled), at, actor),
        )


def _state(row: tuple[str, str, int, str, str]) -> CapabilityState:
    return CapabilityState(row[0], Capability(row[1]), bool(row[2]), row[3], row[4])
