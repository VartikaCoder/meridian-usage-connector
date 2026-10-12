from __future__ import annotations

from datetime import date

import pytest

from meridian_usage.periods import DEFAULT_ANCHOR, PeriodError, coverage, parse_period

DATA_START, DATA_END = date(2026, 8, 3), date(2026, 9, 28)


class TestParse:
    def test_last_7_days_is_anchored_to_end_of_data(self) -> None:
        p = parse_period("last_7_days")
        assert (p.start, p.end_exclusive) == (date(2026, 9, 21), DEFAULT_ANCHOR)
        assert p.days == 7

    def test_last_30_days(self) -> None:
        p = parse_period("last_30_days")
        assert (p.start, p.end_exclusive) == (date(2026, 8, 29), date(2026, 9, 28))

    def test_anchor_is_configurable(self) -> None:
        p = parse_period("last_7_days", anchor=date(2026, 1, 8))
        assert (p.start, p.end_exclusive) == (date(2026, 1, 1), date(2026, 1, 8))

    def test_iso_week_starts_monday(self) -> None:
        p = parse_period("2026-W36")
        assert (p.start, p.end_exclusive) == (date(2026, 8, 31), date(2026, 9, 7))
        assert p.start.weekday() == 0

    def test_iso_week_can_start_in_previous_year(self) -> None:
        assert parse_period("2026-W01").start == date(2025, 12, 29)

    def test_month(self) -> None:
        p = parse_period("2026-09")
        assert (p.start, p.end_exclusive) == (date(2026, 9, 1), date(2026, 10, 1))

    def test_december_rolls_over(self) -> None:
        assert parse_period("2026-12").end_exclusive == date(2027, 1, 1)

    @pytest.mark.parametrize(
        "bad", ["2025-W53", "2026-W00", "2026-13", "2026-00", "last_month", "", "2026-9"]
    )
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(PeriodError):
            parse_period(bad)


class TestCoverage:
    def test_full_week_inside_data_is_complete(self) -> None:
        c = coverage(parse_period("2026-W36"), DATA_START, DATA_END)
        assert c.complete and c.start == date(2026, 8, 31)

    def test_august_is_partial_because_data_starts_on_the_3rd(self) -> None:
        c = coverage(parse_period("2026-08"), DATA_START, DATA_END)
        assert not c.complete
        assert (c.start, c.end_exclusive) == (date(2026, 8, 3), date(2026, 9, 1))

    def test_september_is_partial_because_data_ends_on_the_27th(self) -> None:
        c = coverage(parse_period("2026-09"), DATA_START, DATA_END)
        assert (c.end_exclusive, c.complete) == (DATA_END, False)

    def test_period_outside_data_has_no_coverage(self) -> None:
        c = coverage(parse_period("2026-W40"), DATA_START, DATA_END)
        assert (c.start, c.end_exclusive, c.complete) == (None, None, False)
