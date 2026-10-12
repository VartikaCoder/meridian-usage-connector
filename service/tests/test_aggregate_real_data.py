"""Consistency checks over the real starter-kit data: different slices must add up."""

from __future__ import annotations

from datetime import date

import pytest

from meridian_usage.aggregate import UsageData, sum_totals
from meridian_usage.directory import Directory
from meridian_usage.ingest import load_all
from meridian_usage.periods import parse_period
from meridian_usage.settings import Settings


@pytest.fixture(scope="module")
def data(settings: Settings, real_directory: Directory) -> UsageData:
    return UsageData(load_all(settings.data_dir, real_directory).records, real_directory)


def test_data_window(data: UsageData) -> None:
    assert (data.data_start, data.data_end_exclusive) == (date(2026, 8, 3), date(2026, 9, 28))


def test_last_7_days_equals_week_39(data: UsageData) -> None:
    # 2026-09-21..27 is exactly ISO week 39, so both must agree.
    assert data.summary(parse_period("last_7_days"), None).totals == (
        data.summary(parse_period("2026-W39"), None).totals
    )


def test_groups_add_up_to_the_whole(data: UsageData) -> None:
    period = parse_period("last_30_days")
    whole = data.summary(period, None).totals
    parts = [data.summary(period, g).totals for g in data.directory.groups]
    assert sum(p.cost_usd for p in parts) == whole.cost_usd
    assert sum(p.total_tokens for p in parts) == whole.total_tokens


def test_weeks_add_up_to_the_whole(data: UsageData) -> None:
    weekly = data.weekly()
    assert {r.week for r in weekly} == {f"2026-W{w}" for w in range(32, 40)}
    assert sum(r.totals.cost_usd for r in weekly) == sum_totals(data.records).cost_usd


def test_months_add_up_to_the_whole(data: UsageData) -> None:
    aug = data.summary(parse_period("2026-08"), None).totals
    sep = data.summary(parse_period("2026-09"), None).totals
    assert aug.cost_usd + sep.cost_usd == sum_totals(data.records).cost_usd


def test_top_users_cover_the_group(data: UsageData) -> None:
    period = parse_period("2026-09")
    top = data.top_users(period, "finance", limit=25)
    assert sum(u.totals.total_tokens for u in top) == (
        data.summary(period, "finance").totals.total_tokens
    )
    tokens = [u.totals.total_tokens for u in top]
    assert tokens == sorted(tokens, reverse=True)
