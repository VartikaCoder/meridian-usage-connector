from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from meridian_usage.directory import Directory
from meridian_usage.ingest.attribution import RowError
from meridian_usage.ingest.claude import cents_to_usd, parse_claude, parse_day_bucket
from meridian_usage.model import IngestResult, Platform
from tests.conftest import claude_cost_row, claude_usage_row


def run(usage: list[dict[str, Any]], cost: list[dict[str, Any]], d: Directory) -> IngestResult:
    return parse_claude({"data": usage}, {"data": cost}, d)


class TestCents:
    def test_cents_string_becomes_exact_dollars(self) -> None:
        assert cents_to_usd("88.900050") == Decimal("0.88900050")

    def test_no_float_drift(self) -> None:
        assert cents_to_usd("0.1") + cents_to_usd("0.2") == Decimal("0.003")

    @pytest.mark.parametrize("bad", [88.9, "abc", "NaN", None])
    def test_rejects_non_decimal_strings(self, bad: object) -> None:
        with pytest.raises(RowError):
            cents_to_usd(bad)  # type: ignore[arg-type]


class TestDayBucket:
    def test_rfc3339_utc_day(self) -> None:
        assert parse_day_bucket("2026-08-03T00:00:00.000Z", "2026-08-04T00:00:00.000Z") == date(
            2026, 8, 3
        )

    def test_rejects_hourly_bucket(self) -> None:
        with pytest.raises(RowError, match="one UTC day"):
            parse_day_bucket("2026-08-03T00:00:00Z", "2026-08-03T01:00:00Z")

    def test_rejects_non_utc_offset(self) -> None:
        with pytest.raises(RowError, match="UTC"):
            parse_day_bucket("2026-08-03T00:00:00+05:30", "2026-08-04T00:00:00+05:30")


class TestMapping:
    def test_maps_one_row(self, tiny_directory: Directory) -> None:
        r = run([claude_usage_row()], [claude_cost_row()], tiny_directory)
        assert r.rejected == [] and r.warnings == []
        [rec] = r.records
        assert rec.platform is Platform.CLAUDE
        assert (rec.user_id, rec.group_id) == ("user_A", "finance")
        assert rec.usage_date == date(2026, 8, 3)
        assert rec.cost_usd == Decimal("0.88900050")

    def test_input_includes_cache_reads_and_writes(self, tiny_directory: Directory) -> None:
        [rec] = run([claude_usage_row()], [claude_cost_row()], tiny_directory).records
        assert rec.input_tokens == 100 + 1000 + 20 + 5
        assert rec.cache_read_tokens == 1000 and rec.cache_write_tokens == 25
        assert rec.total_tokens == 1175

    def test_web_search_cost_is_added_to_token_cost(self, tiny_directory: Directory) -> None:
        cost = [claude_cost_row(), claude_cost_row(cost_type="web_search", amount="2.000000")]
        [rec] = run([claude_usage_row()], cost, tiny_directory).records
        assert rec.cost_usd == Decimal("0.90900050")
        assert rec.web_search_requests == 2

    def test_rejects_when_total_does_not_add_up(self, tiny_directory: Directory) -> None:
        r = run([claude_usage_row(total_tokens=9999)], [claude_cost_row()], tiny_directory)
        assert r.records == []
        assert "total_tokens" in r.rejected[0].reason
        assert "usage row was rejected" in r.rejected[1].reason  # its cost is not re-homed

    def test_unknown_user_is_rejected_not_guessed(self, tiny_directory: Directory) -> None:
        actor = {"type": "user_actor", "user_id": "user_GHOST", "email": "g@x"}
        r = run([claude_usage_row(actor=actor)], [], tiny_directory)
        assert r.records == []
        assert "not in the directory" in r.rejected[0].reason

    def test_group_disagreeing_with_directory_is_rejected(self, tiny_directory: Directory) -> None:
        # Alice is in finance, but the row says HR's RBAC group.
        r = run([claude_usage_row(rbac_group_id="rbac_HR")], [], tiny_directory)
        assert r.records == []
        assert "not a member of hr" in r.rejected[0].reason

    def test_unknown_rbac_group_is_rejected(self, tiny_directory: Directory) -> None:
        r = run([claude_usage_row(rbac_group_id="rbac_NOPE")], [], tiny_directory)
        assert "does not map" in r.rejected[0].reason

    def test_actor_email_mismatch_is_rejected(self, tiny_directory: Directory) -> None:
        actor = {"type": "user_actor", "user_id": "user_A", "email": "bob@meridianls.example"}
        r = run([claude_usage_row(actor=actor)], [], tiny_directory)
        assert "does not match" in r.rejected[0].reason

    def test_email_match_is_case_insensitive(self, tiny_directory: Directory) -> None:
        actor = {"type": "user_actor", "user_id": "user_A", "email": "Alice@MeridianLS.example"}
        r = run([claude_usage_row(actor=actor)], [claude_cost_row()], tiny_directory)
        assert r.rejected == [] and len(r.records) == 1

    def test_non_usd_cost_is_rejected(self, tiny_directory: Directory) -> None:
        r = run([claude_usage_row()], [claude_cost_row(currency="EUR")], tiny_directory)
        assert any("currency" in i.reason for i in r.rejected)

    def test_duplicate_usage_rows_are_rejected(self, tiny_directory: Directory) -> None:
        r = run([claude_usage_row(), claude_usage_row()], [claude_cost_row()], tiny_directory)
        assert len(r.records) == 1
        assert "duplicate" in r.rejected[0].reason


class TestMoneyIsNeverLost:
    def test_usage_without_cost_is_kept_at_zero_with_warning(
        self, tiny_directory: Directory
    ) -> None:
        r = run([claude_usage_row()], [], tiny_directory)
        assert r.records[0].cost_usd == 0
        assert "no cost row" in r.warnings[0].reason

    def test_cost_without_usage_is_kept_with_warning(self, tiny_directory: Directory) -> None:
        r = run([], [claude_cost_row()], tiny_directory)
        [rec] = r.records
        assert rec.cost_usd == Decimal("0.88900050") and rec.total_tokens == 0
        assert "no usage row" in r.warnings[0].reason
