"""The single usage model both platforms are mapped into."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class Platform(StrEnum):
    CLAUDE = "claude"
    GEMINI = "gemini"


@dataclass(frozen=True)
class UsageRecord:
    """One user's usage of one model through one product on one UTC day.

    `input_tokens + output_tokens == total_tokens` holds on both platforms. On Claude,
    input includes cache reads and cache writes; their split is kept in the cache fields.
    `product` is Claude's product (chat, claude_code, cowork) or Gemini's `agent` label.
    Fields a platform does not report are None rather than 0.
    """

    platform: Platform
    usage_date: date
    user_id: str
    group_id: str
    product: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    requests: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    web_search_requests: int | None = None
    source: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class IngestIssue:
    """A source row that was rejected, or accepted with a caveat."""

    source: str
    reason: str


@dataclass
class IngestResult:
    records: list[UsageRecord] = field(default_factory=list)
    rejected: list[IngestIssue] = field(default_factory=list)
    warnings: list[IngestIssue] = field(default_factory=list)

    def extend(self, other: IngestResult) -> None:
        self.records.extend(other.records)
        self.rejected.extend(other.rejected)
        self.warnings.extend(other.warnings)
