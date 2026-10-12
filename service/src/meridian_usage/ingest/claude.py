"""Claude Enterprise Analytics API: user_usage_report + user_cost_report -> UsageRecord.

Grain of both reports: user x UTC day x product x model (cost adds x cost_type).
- Cost `amount` is a decimal string in cents ("88.900050" == $0.889).
- A usage key's cost is the sum of its `tokens` and `web_search` cost rows.
- Input tokens = uncached + cache reads + cache writes (5m and 1h), so input + output
  equals the API's `total_tokens`, which we check.
- Money is never lost: a cost row with no usage row still becomes a record (zero
  tokens) and a usage row with no cost row becomes a zero-cost record; both warn.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from meridian_usage.directory import Directory
from meridian_usage.ingest.attribution import RowError, attribute
from meridian_usage.model import IngestIssue, IngestResult, Platform, UsageRecord

CENTS_PER_DOLLAR = Decimal(100)

type Key = tuple[str, str, str, str]  # (user_id, starting_at, product, model)


def cents_to_usd(amount: str) -> Decimal:
    if not isinstance(amount, str):
        raise RowError(f"cost amount must be a decimal string in cents, got {amount!r}")
    try:
        cents = Decimal(amount)
    except InvalidOperation as e:
        raise RowError(f"cost amount is not a decimal: {amount!r}") from e
    if not cents.is_finite():
        raise RowError(f"cost amount is not finite: {amount!r}")
    return cents / CENTS_PER_DOLLAR


def parse_day_bucket(starting_at: str, ending_at: str) -> date:
    """RFC 3339 UTC bucket bounds -> the UTC day, insisting on a midnight-aligned 1-day bucket."""
    start = datetime.fromisoformat(starting_at)
    end = datetime.fromisoformat(ending_at)
    if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
        raise RowError(f"bucket is not in UTC: {starting_at} .. {ending_at}")
    start = start.astimezone(UTC)
    if start.time() != datetime.min.time() or end - start != timedelta(days=1):
        raise RowError(f"bucket is not one UTC day: {starting_at} .. {ending_at}")
    return start.date()


def _key(row: dict[str, Any]) -> Key:
    return (row["actor"]["user_id"], row["starting_at"], row["product"], row["model"])


def _actor_user_id(row: dict[str, Any]) -> tuple[str, str]:
    actor = row.get("actor") or {}
    if actor.get("type") != "user_actor":
        raise RowError(f"actor is not a user: {actor.get('type')!r}")
    return actor["user_id"], actor.get("email", "")


def parse_claude(
    usage_doc: dict[str, Any], cost_doc: dict[str, Any], directory: Directory
) -> IngestResult:
    result = IngestResult()

    costs: dict[Key, Decimal] = defaultdict(Decimal)
    cost_first_row: dict[Key, tuple[str, dict[str, Any]]] = {}
    for i, row in enumerate(cost_doc["data"]):
        src = f"claude:user_cost_report[{i}]"
        try:
            if row.get("currency") != "USD":
                raise RowError(f"unsupported currency {row.get('currency')!r}")
            if row.get("cost_type") not in ("tokens", "web_search"):
                raise RowError(f"unknown cost_type {row.get('cost_type')!r}")
            k = _key(row)
            costs[k] += cents_to_usd(row["amount"])
            cost_first_row.setdefault(k, (src, row))
        except (RowError, KeyError) as e:
            result.rejected.append(IngestIssue(src, _reason(e)))

    seen: set[Key] = set()
    for i, row in enumerate(usage_doc["data"]):
        src = f"claude:user_usage_report[{i}]"
        try:
            k = _key(row)
        except KeyError as e:
            result.rejected.append(IngestIssue(src, _reason(e)))
            continue
        if k in seen:
            result.rejected.append(
                IngestIssue(src, "duplicate usage row for user/day/product/model")
            )
            continue
        seen.add(k)
        try:
            record = _usage_row_to_record(row, costs.get(k), directory, src)
        except (RowError, KeyError) as e:
            result.rejected.append(IngestIssue(src, _reason(e)))
            if k in costs:
                # The cost belongs to a row we could not map: reject it too, never re-home it.
                result.rejected.append(
                    IngestIssue(cost_first_row[k][0], "its usage row was rejected")
                )
                del costs[k]
            continue
        if k not in costs:
            result.warnings.append(IngestIssue(src, "no cost row; recorded at $0"))
        result.records.append(record)

    for k in sorted(costs.keys() - seen):
        src, first = cost_first_row[k]
        try:
            result.records.append(_cost_only_record(first, costs[k], directory, src))
            result.warnings.append(IngestIssue(src, "cost with no usage row; recorded, 0 tokens"))
        except (RowError, KeyError) as e:
            result.rejected.append(IngestIssue(src, _reason(e)))

    return result


def _usage_row_to_record(
    row: dict[str, Any], cost: Decimal | None, directory: Directory, src: str
) -> UsageRecord:
    user_id, email = _actor_user_id(row)
    user, group = attribute(
        user=directory.user_by_id(user_id),
        user_ref=f"user_id {user_id}",
        group=directory.group_by_rbac_id(row["rbac_group_id"]),
        group_ref=f"rbac_group_id {row['rbac_group_id']}",
    )
    if email and user.email.casefold() != email.casefold():
        raise RowError(f"actor email {email} does not match directory email for {user_id}")

    uncached = _non_negative(row, "uncached_input_tokens")
    cache_read = _non_negative(row, "cache_read_input_tokens")
    creation = row.get("cache_creation") or {}
    cache_write = int(creation.get("ephemeral_5m_input_tokens", 0)) + int(
        creation.get("ephemeral_1h_input_tokens", 0)
    )
    output = _non_negative(row, "output_tokens")
    input_tokens = uncached + cache_read + cache_write
    if input_tokens + output != row["total_tokens"]:
        raise RowError(
            f"token buckets sum to {input_tokens + output}, total_tokens says {row['total_tokens']}"
        )

    return UsageRecord(
        platform=Platform.CLAUDE,
        usage_date=parse_day_bucket(row["starting_at"], row["ending_at"]),
        user_id=user.user_id,
        group_id=group.group_id,
        product=row["product"],
        model=row["model"],
        input_tokens=input_tokens,
        output_tokens=output,
        cost_usd=cost if cost is not None else Decimal(0),
        requests=int(row["requests"]),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        web_search_requests=int((row.get("server_tool_use") or {}).get("web_search_requests", 0)),
        source=src,
    )


def _cost_only_record(
    row: dict[str, Any], cost: Decimal, directory: Directory, src: str
) -> UsageRecord:
    user_id, _ = _actor_user_id(row)
    user, group = attribute(
        user=directory.user_by_id(user_id),
        user_ref=f"user_id {user_id}",
        group=directory.group_by_rbac_id(row["rbac_group_id"]),
        group_ref=f"rbac_group_id {row['rbac_group_id']}",
    )
    return UsageRecord(
        platform=Platform.CLAUDE,
        usage_date=parse_day_bucket(row["starting_at"], row["ending_at"]),
        user_id=user.user_id,
        group_id=group.group_id,
        product=row["product"],
        model=row["model"],
        input_tokens=0,
        output_tokens=0,
        cost_usd=cost,
        source=src,
    )


def _non_negative(row: dict[str, Any], field: str) -> int:
    value = int(row[field])
    if value < 0:
        raise RowError(f"{field} is negative: {value}")
    return value


def _reason(e: Exception) -> str:
    return f"missing field {e}" if isinstance(e, KeyError) else str(e)
