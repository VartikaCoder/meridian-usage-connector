"""Aggregations over UsageRecord: by platform, group, week and user.

Pure functions over an in-memory list (about 1,800 records here). They return plain
dataclasses holding Decimal money; rounding happens only when the API serialises them.
Permission checks are NOT done here: callers pass the group they are allowed to see.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from meridian_usage.directory import Directory
from meridian_usage.model import Platform, UsageRecord
from meridian_usage.periods import Coverage, Period, coverage


@dataclass(frozen=True)
class Totals:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    # None when no record in the set reports requests (Gemini billing never does).
    requests: int | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class PlatformTotals:
    platform: Platform
    totals: Totals


@dataclass(frozen=True)
class UsageSummary:
    group: str | None  # None = every group
    period: Period
    coverage: Coverage
    platforms: list[PlatformTotals]
    totals: Totals


@dataclass(frozen=True)
class UserUsage:
    user_id: str
    name: str
    email: str
    group: str
    totals: Totals


@dataclass(frozen=True)
class WeeklyRow:
    week: str  # ISO week, e.g. 2026-W36
    week_start: date
    group: str
    platform: Platform
    totals: Totals


def iso_week(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def sum_totals(records: Iterable[UsageRecord]) -> Totals:
    inp = out = 0
    cost = Decimal(0)
    requests: int | None = None
    for r in records:
        inp += r.input_tokens
        out += r.output_tokens
        cost += r.cost_usd
        if r.requests is not None:
            requests = (requests or 0) + r.requests
    return Totals(inp, out, cost, requests)


class UsageData:
    """The ingested records plus the window they cover."""

    def __init__(self, records: Sequence[UsageRecord], directory: Directory) -> None:
        if not records:
            raise ValueError("no usage records loaded")
        self.records = list(records)
        self.directory = directory
        self.data_start = min(r.usage_date for r in self.records)
        self.data_end_exclusive = max(r.usage_date for r in self.records) + timedelta(days=1)

    def _select(self, group: str | None, period: Period | None) -> list[UsageRecord]:
        return [
            r
            for r in self.records
            if (group is None or r.group_id == group)
            and (period is None or period.contains(r.usage_date))
        ]

    def coverage(self, period: Period) -> Coverage:
        return coverage(period, self.data_start, self.data_end_exclusive)

    def summary(self, period: Period, group: str | None) -> UsageSummary:
        rows = self._select(group, period)
        platforms = [
            PlatformTotals(p, sum_totals(r for r in rows if r.platform is p)) for p in Platform
        ]
        return UsageSummary(group, period, self.coverage(period), platforms, sum_totals(rows))

    def top_users(self, period: Period, group: str | None, limit: int = 10) -> list[UserUsage]:
        if not 1 <= limit <= 25:
            raise ValueError("limit must be between 1 and 25")
        by_user: dict[tuple[str, str], list[UsageRecord]] = defaultdict(list)
        for r in self._select(group, period):
            by_user[(r.user_id, r.group_id)].append(r)
        ranked = []
        for (user_id, group_id), rows in by_user.items():
            user = self.directory.users[user_id]
            ranked.append(UserUsage(user_id, user.name, user.email, group_id, sum_totals(rows)))
        # Highest tokens first; ties broken by cost, then name, so the order is deterministic.
        ranked.sort(key=lambda u: (-u.totals.total_tokens, -u.totals.cost_usd, u.name))
        return ranked[:limit]

    def weekly(self, group: str | None = None, period: Period | None = None) -> list[WeeklyRow]:
        buckets: dict[tuple[str, str, Platform], list[UsageRecord]] = defaultdict(list)
        week_start: dict[str, date] = {}
        for r in self._select(group, period):
            wk = iso_week(r.usage_date)
            week_start.setdefault(wk, r.usage_date - timedelta(days=r.usage_date.weekday()))
            buckets[(wk, r.group_id, r.platform)].append(r)
        return [
            WeeklyRow(wk, week_start[wk], g, p, sum_totals(rows))
            for (wk, g, p), rows in sorted(buckets.items())
        ]
