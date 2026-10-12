from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from meridian_usage.directory import Directory
from meridian_usage.ingest.attribution import RowError
from meridian_usage.ingest.gemini import parse_bq_day, parse_gemini, parse_sku
from meridian_usage.model import Platform
from tests.conftest import gemini_row


class TestSku:
    @pytest.mark.parametrize(
        ("description", "expected"),
        [
            ("Gemini 2.5 Flash Text Input Tokens - Predictions", ("gemini-2.5-flash", "input")),
            ("Gemini 2.5 Pro Text Output Tokens - Predictions", ("gemini-2.5-pro", "output")),
            (
                "Gemini 2.5 Flash Lite Text Input Tokens - Predictions",
                ("gemini-2.5-flash-lite", "input"),
            ),
        ],
    )
    def test_parses_model_and_direction(self, description: str, expected: tuple[str, str]) -> None:
        assert parse_sku(description) == expected

    @pytest.mark.parametrize(
        "description",
        ["Gemini 2.5 Flash Image Output - Predictions", "Vertex AI Online Prediction", ""],
    )
    def test_rejects_non_text_token_skus(self, description: str) -> None:
        with pytest.raises(RowError):
            parse_sku(description)


def test_bigquery_timestamp_to_utc_day() -> None:
    assert parse_bq_day("2026-08-05 00:00:00 UTC", "2026-08-06 00:00:00 UTC") == date(2026, 8, 5)


class TestMapping:
    def test_input_and_output_rows_fold_into_one_record(self, tiny_directory: Directory) -> None:
        r = parse_gemini([gemini_row("Input"), gemini_row("Output")], tiny_directory)
        assert r.rejected == []
        [rec] = r.records
        assert rec.platform is Platform.GEMINI
        assert (rec.user_id, rec.group_id) == ("user_A", "finance")
        assert (rec.input_tokens, rec.output_tokens, rec.total_tokens) == (1000, 200, 1200)
        assert rec.product == "close-assistant" and rec.model == "gemini-2.5-flash"
        assert rec.usage_date == date(2026, 8, 5)

    def test_cost_is_exact_decimal_not_float_sum(self, tiny_directory: Directory) -> None:
        [rec] = parse_gemini([gemini_row("Input"), gemini_row("Output")], tiny_directory).records
        assert rec.cost_usd == Decimal("0.3")  # 0.1 + 0.2 in float is 0.30000000000000004

    def test_requests_unknown_not_zero(self, tiny_directory: Directory) -> None:
        [rec] = parse_gemini([gemini_row()], tiny_directory).records
        assert rec.requests is None

    def test_different_agents_stay_separate(self, tiny_directory: Directory) -> None:
        other = gemini_row(
            labels=[
                {"key": "owner", "value": "alice@meridianls.example"},
                {"key": "agent", "value": "another-agent"},
            ]
        )
        assert len(parse_gemini([gemini_row(), other], tiny_directory).records) == 2

    def test_credits_reduce_cost(self, tiny_directory: Directory) -> None:
        row = gemini_row(credits=[{"name": "promo", "amount": -0.05}])
        [rec] = parse_gemini([row], tiny_directory).records
        assert rec.cost_usd == Decimal("0.05")

    def test_owner_label_is_case_insensitive(self, tiny_directory: Directory) -> None:
        row = gemini_row(labels=[{"key": "owner", "value": "ALICE@meridianls.example"}])
        assert len(parse_gemini([row], tiny_directory).records) == 1

    def test_missing_owner_is_rejected(self, tiny_directory: Directory) -> None:
        r = parse_gemini([gemini_row(labels=[])], tiny_directory)
        assert r.records == [] and "owner" in r.rejected[0].reason

    def test_project_of_another_group_is_rejected(self, tiny_directory: Directory) -> None:
        row = gemini_row(project={"id": "meridian-hr-prod", "labels": []})
        r = parse_gemini([row], tiny_directory)
        assert r.records == [] and "not a member of hr" in r.rejected[0].reason

    def test_unknown_project_is_rejected(self, tiny_directory: Directory) -> None:
        row = gemini_row(project={"id": "someone-elses-project", "labels": []})
        assert "does not map" in parse_gemini([row], tiny_directory).rejected[0].reason

    def test_model_label_disagreeing_with_sku_is_rejected(self, tiny_directory: Directory) -> None:
        row = gemini_row(
            labels=[
                {"key": "owner", "value": "alice@meridianls.example"},
                {"key": "model", "value": "gemini-2.5-pro"},
            ]
        )
        assert "disagrees" in parse_gemini([row], tiny_directory).rejected[0].reason

    def test_non_vertex_service_is_rejected(self, tiny_directory: Directory) -> None:
        row = gemini_row(service={"id": "x", "description": "BigQuery"})
        assert "Vertex" in parse_gemini([row], tiny_directory).rejected[0].reason
