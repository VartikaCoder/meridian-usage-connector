"""Google Cloud billing export (Vertex AI Gemini SKUs) -> UsageRecord.

Grain: project x UTC day x SKU, with `owner` / `agent` / `model` labels on the row.
- Input and output tokens are separate SKUs, so rows are folded into one record per
  (day, project, owner, agent, model). Repeated rows for the same SKU are summed, as
  billing rows are additive.
- The model and token direction come from `sku.description`; the `model` label is only a
  cross-check.
- `cost` is a JSON number in USD; it goes through `str()` into Decimal so float error is not
  compounded by summation. Cost is taken after credits (cost + sum(credits.amount)).
- No request counts exist in billing data, so `requests` is None, not 0.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from meridian_usage.directory import Directory
from meridian_usage.ingest.attribution import RowError, attribute
from meridian_usage.model import IngestIssue, IngestResult, Platform, UsageRecord

SKU_PATTERN = re.compile(
    r"^Gemini (?P<version>\d+(?:\.\d+)*) (?P<tier>[A-Za-z-]+(?: [A-Za-z-]+)*?) "
    r"Text (?P<direction>Input|Output) Tokens - Predictions$"
)
BQ_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"

type Key = tuple[date, str, str, str, str]  # (day, project, owner email, agent, model)


@dataclass
class _Accumulator:
    user_id: str
    group_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal(0)
    sources: list[str] = field(default_factory=list)


def parse_sku(description: str) -> tuple[str, str]:
    """'Gemini 2.5 Flash Text Input Tokens - Predictions' -> ('gemini-2.5-flash', 'input')."""
    m = SKU_PATTERN.match(description)
    if not m:
        raise RowError(f"not a Gemini text token SKU: {description!r}")
    model = f"gemini-{m['version']}-{m['tier'].lower().replace(' ', '-')}"
    return model, m["direction"].lower()


def parse_bq_day(start: str, end: str) -> date:
    s = datetime.strptime(start, BQ_TIME_FORMAT).replace(tzinfo=UTC)
    e = datetime.strptime(end, BQ_TIME_FORMAT).replace(tzinfo=UTC)
    if s.time() != datetime.min.time() or e - s != timedelta(days=1):
        raise RowError(f"usage window is not one UTC day: {start} .. {end}")
    return s.date()


def to_decimal_usd(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise RowError(f"cost is not a number: {value!r}")
    d = Decimal(str(value))
    if not d.is_finite():
        raise RowError(f"cost is not finite: {value!r}")
    return d


def _label(labels: list[dict[str, str]] | None, key: str) -> str | None:
    return next((lab["value"] for lab in labels or [] if lab.get("key") == key), None)


def parse_gemini(rows: Iterable[dict[str, Any]], directory: Directory) -> IngestResult:
    result = IngestResult()
    acc: dict[Key, _Accumulator] = {}

    for i, row in enumerate(rows):
        src = f"gemini:billing[{i}]"
        try:
            key, entry_user, entry_group, direction, tokens, cost = _parse_row(row, directory)
        except (RowError, KeyError, TypeError) as e:
            reason = f"missing field {e}" if isinstance(e, KeyError) else str(e)
            result.rejected.append(IngestIssue(src, reason))
            continue
        a = acc.setdefault(key, _Accumulator(entry_user, entry_group))
        if direction == "input":
            a.input_tokens += tokens
        else:
            a.output_tokens += tokens
        a.cost += cost
        a.sources.append(src)

    for (day, _project, _owner, agent, model), a in sorted(acc.items()):
        result.records.append(
            UsageRecord(
                platform=Platform.GEMINI,
                usage_date=day,
                user_id=a.user_id,
                group_id=a.group_id,
                product=agent,
                model=model,
                input_tokens=a.input_tokens,
                output_tokens=a.output_tokens,
                cost_usd=a.cost,
                source=",".join(a.sources),
            )
        )
    return result


def _parse_row(
    row: dict[str, Any], directory: Directory
) -> tuple[Key, str, str, str, int, Decimal]:
    if row["service"]["description"] != "Vertex AI":
        raise RowError(f"not a Vertex AI row: {row['service']['description']!r}")
    if row.get("currency") != "USD":
        raise RowError(f"unsupported currency {row.get('currency')!r}")
    if row["usage"]["unit"] != "count":
        raise RowError(f"usage unit is {row['usage']['unit']!r}, expected token 'count'")

    model, direction = parse_sku(row["sku"]["description"])
    label_model = _label(row.get("labels"), "model")
    if label_model is not None and label_model != model:
        raise RowError(f"model label {label_model!r} disagrees with SKU model {model!r}")

    owner = _label(row.get("labels"), "owner")
    if not owner:
        raise RowError("no owner label; cannot attribute to a person")
    project_id = row["project"]["id"]
    user, group = attribute(
        user=directory.user_by_email(owner),
        user_ref=f"owner {owner}",
        group=directory.group_by_project(project_id),
        group_ref=f"project {project_id}",
    )

    amount = row["usage"]["amount"]
    tokens = int(amount)
    if tokens != amount or tokens < 0:
        raise RowError(f"token count is not a non-negative whole number: {amount!r}")

    credits = sum((to_decimal_usd(c["amount"]) for c in row.get("credits") or []), Decimal(0))
    cost = to_decimal_usd(row["cost"]) + credits

    day = parse_bq_day(row["usage_start_time"], row["usage_end_time"])
    agent = _label(row.get("labels"), "agent") or "unlabelled"
    key: Key = (day, project_id, user.email.casefold(), agent, model)
    return key, user.user_id, group.group_id, direction, tokens, cost
