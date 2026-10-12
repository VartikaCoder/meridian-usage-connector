from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from meridian_usage.aggregate import UsageData, iso_week, sum_totals
from meridian_usage.directory import Directory
from meridian_usage.model import Platform, UsageRecord
from meridian_usage.periods import parse_period


def rec(
    user: str = "user_A",
    group: str = "finance",
    day: date = date(2026, 9, 1),
    platform: Platform = Platform.CLAUDE,
    tokens: tuple[int, int] = (100, 10),
    cost: str = "1.00",
) -> UsageRecord:
    return UsageRecord(
        platform=platform,
        usage_date=day,
        user_id=user,
        group_id=group,
        product="chat",
        model="m",
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cost_usd=Decimal(cost),
        requests=1 if platform is Platform.CLAUDE else None,
    )


@pytest.fixture
def data(tiny_directory: Directory) -> UsageData:
    return UsageData(
        [
            rec(),
            rec(platform=Platform.GEMINI, tokens=(50, 50), cost="0.25"),
            rec(day=date(2026, 9, 8)),  # next ISO week
            rec(user="user_B", group="hr", tokens=(1000, 0), cost="5"),
        ],
        tiny_directory,
    )


class TestTotals:
    def test_requests_are_none_when_no_record_reports_them(self) -> None:
        assert sum_totals([rec(platform=Platform.GEMINI)]).requests is None

    def test_requests_sum_only_known_values(self) -> None:
        t = sum_totals([rec(), rec(), rec(platform=Platform.GEMINI)])
        assert t.requests == 2

    def test_empty(self) -> None:
        t = sum_totals([])
        assert (t.total_tokens, t.cost_usd, t.requests) == (0, 0, None)


class TestSummary:
    def test_filters_to_group_and_period(self, data: UsageData) -> None:
        s = data.summary(parse_period("2026-W36"), "finance")
        assert s.totals.total_tokens == 110 + 100
        assert s.totals.cost_usd == Decimal("1.25")

    def test_always_lists_both_platforms(self, data: UsageData) -> None:
        s = data.summary(parse_period("2026-W36"), "hr")
        by = {p.platform: p.totals for p in s.platforms}
        assert set(by) == {Platform.CLAUDE, Platform.GEMINI}
        assert by[Platform.GEMINI].total_tokens == 0

    def test_group_none_means_all_groups(self, data: UsageData) -> None:
        s = data.summary(parse_period("2026-09"), None)
        assert s.totals.cost_usd == Decimal("7.25")

    def test_reports_coverage(self, data: UsageData) -> None:
        s = data.summary(parse_period("2026-09"), None)
        assert not s.coverage.complete
        assert s.coverage.start == date(2026, 9, 1)


class TestTopUsers:
    def test_ranked_by_total_tokens(self, data: UsageData) -> None:
        top = data.top_users(parse_period("2026-09"), None)
        assert [u.user_id for u in top] == ["user_B", "user_A"]
        assert top[0].name == "Bob" and top[0].group == "hr"

    def test_respects_group(self, data: UsageData) -> None:
        top = data.top_users(parse_period("2026-09"), "finance")
        assert [u.user_id for u in top] == ["user_A"]

    def test_limit(self, data: UsageData) -> None:
        assert len(data.top_users(parse_period("2026-09"), None, limit=1)) == 1

    @pytest.mark.parametrize("limit", [0, 26])
    def test_limit_bounds(self, data: UsageData, limit: int) -> None:
        with pytest.raises(ValueError, match="limit"):
            data.top_users(parse_period("2026-09"), None, limit=limit)


class TestWeekly:
    def test_buckets_by_week_group_platform(self, data: UsageData) -> None:
        rows = data.weekly(group="finance")
        keys = [(r.week, r.platform) for r in rows]
        assert keys == [
            ("2026-W36", Platform.CLAUDE),
            ("2026-W36", Platform.GEMINI),
            ("2026-W37", Platform.CLAUDE),
        ]
        assert rows[0].week_start == date(2026, 8, 31)


def test_iso_week_label_pads() -> None:
    assert iso_week(date(2026, 1, 1)) == "2026-W01"
