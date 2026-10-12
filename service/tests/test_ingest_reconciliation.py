"""Reconcile ingestion of the real starter-kit exports against the raw files.

These recompute totals straight from the source JSON, independently of the parsers, so a
regression that drops, double-counts or mis-scales rows fails here.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal

import pytest

from meridian_usage.directory import Directory
from meridian_usage.ingest import load_all
from meridian_usage.model import IngestResult, Platform
from meridian_usage.settings import Settings


@pytest.fixture(scope="module")
def ingested(settings: Settings, real_directory: Directory) -> IngestResult:
    return load_all(settings.data_dir, real_directory)


def _claude_raw(settings: Settings) -> tuple[dict, dict]:  # type: ignore[type-arg]
    d = settings.data_dir / "claude"
    return (
        json.loads((d / "user_usage_report.json").read_text()),
        json.loads((d / "user_cost_report.json").read_text()),
    )


def _gemini_raw(settings: Settings) -> list[dict]:  # type: ignore[type-arg]
    [path] = (settings.data_dir / "gemini").glob("*.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_nothing_rejected_or_warned(ingested: IngestResult) -> None:
    assert ingested.rejected == []
    assert ingested.warnings == []


def test_claude_cost_reconciles_to_the_cent_string(
    ingested: IngestResult, settings: Settings
) -> None:
    _, cost = _claude_raw(settings)
    raw_usd = sum(Decimal(r["amount"]) for r in cost["data"]) / 100
    ours = sum(r.cost_usd for r in ingested.records if r.platform is Platform.CLAUDE)
    assert ours == raw_usd == Decimal("2830.28776008")


def test_gemini_cost_reconciles(ingested: IngestResult, settings: Settings) -> None:
    raw_usd = sum(Decimal(str(r["cost"])) for r in _gemini_raw(settings))
    ours = sum(r.cost_usd for r in ingested.records if r.platform is Platform.GEMINI)
    assert ours == raw_usd == Decimal("287.839433")


def test_claude_tokens_reconcile(ingested: IngestResult, settings: Settings) -> None:
    usage, _ = _claude_raw(settings)
    raw = sum(r["total_tokens"] for r in usage["data"])
    ours = sum(r.total_tokens for r in ingested.records if r.platform is Platform.CLAUDE)
    assert ours == raw


def test_gemini_tokens_reconcile_by_direction(ingested: IngestResult, settings: Settings) -> None:
    rows = _gemini_raw(settings)
    raw_in = sum(int(r["usage"]["amount"]) for r in rows if " Input " in r["sku"]["description"])
    raw_out = sum(int(r["usage"]["amount"]) for r in rows if " Output " in r["sku"]["description"])
    gem = [r for r in ingested.records if r.platform is Platform.GEMINI]
    assert sum(r.input_tokens for r in gem) == raw_in
    assert sum(r.output_tokens for r in gem) == raw_out


def test_record_counts(ingested: IngestResult) -> None:
    counts = Counter(r.platform for r in ingested.records)
    assert counts[Platform.CLAUDE] == 1346  # one per usage row
    assert counts[Platform.GEMINI] == 990 // 2  # input + output SKU rows fold together


def test_every_record_belongs_to_its_users_directory_group(
    ingested: IngestResult, real_directory: Directory
) -> None:
    for r in ingested.records:
        assert r.group_id in real_directory.users[r.user_id].group_ids


def test_window_is_w32_to_w39(ingested: IngestResult) -> None:
    weeks = {r.usage_date.isocalendar()[:2] for r in ingested.records}
    assert weeks == {(2026, w) for w in range(32, 40)}


def test_every_group_and_user_has_usage(ingested: IngestResult, real_directory: Directory) -> None:
    assert {r.group_id for r in ingested.records} == set(real_directory.groups)
    assert {r.user_id for r in ingested.records} == set(real_directory.users)
