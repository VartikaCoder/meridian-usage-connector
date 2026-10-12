"""Reporting periods: last_7_days, last_30_days, ISO week (2026-W36), month (2026-09).

All periods are half-open UTC date ranges [start, end_exclusive). Relative periods are
anchored to the end of the data window (2026-09-28, exclusive), not to today, so answers are
stable and reproducible. Each resolved period also says how much of it the data covers, so a
partial month is never mistaken for a full one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_ANCHOR = date(2026, 9, 28)  # end of the starter-kit data window, exclusive

_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_RELATIVE_DAYS = {"last_7_days": 7, "last_30_days": 30}


class PeriodError(ValueError):
    """The period string is not one of the supported forms."""


@dataclass(frozen=True)
class Period:
    label: str
    start: date
    end_exclusive: date

    def contains(self, d: date) -> bool:
        return self.start <= d < self.end_exclusive

    @property
    def days(self) -> int:
        return (self.end_exclusive - self.start).days


@dataclass(frozen=True)
class Coverage:
    """The part of a period that the loaded data actually covers."""

    start: date | None
    end_exclusive: date | None
    complete: bool


def parse_period(text: str, anchor: date = DEFAULT_ANCHOR) -> Period:
    text = text.strip()
    if text in _RELATIVE_DAYS:
        return Period(text, anchor - timedelta(days=_RELATIVE_DAYS[text]), anchor)

    if m := _WEEK.match(text):
        year, week = int(m[1]), int(m[2])
        try:
            monday = date.fromisocalendar(year, week, 1)
        except ValueError as e:
            raise PeriodError(f"{text} is not a valid ISO week") from e
        return Period(text, monday, monday + timedelta(days=7))

    if m := _MONTH.match(text):
        year, month = int(m[1]), int(m[2])
        if not 1 <= month <= 12:
            raise PeriodError(f"{text} is not a valid month")
        first = date(year, month, 1)
        nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return Period(text, first, nxt)

    raise PeriodError(
        f"unsupported period {text!r}: use last_7_days, last_30_days, "
        "an ISO week like 2026-W36 or a month like 2026-09"
    )


def coverage(period: Period, data_start: date, data_end_exclusive: date) -> Coverage:
    start = max(period.start, data_start)
    end = min(period.end_exclusive, data_end_exclusive)
    if start >= end:
        return Coverage(None, None, complete=False)
    return Coverage(start, end, complete=(start, end) == (period.start, period.end_exclusive))
