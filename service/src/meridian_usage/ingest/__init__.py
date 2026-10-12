"""Load both platform exports into one list of UsageRecord."""

from __future__ import annotations

import json
from pathlib import Path

from meridian_usage.directory import Directory
from meridian_usage.ingest.claude import parse_claude
from meridian_usage.ingest.gemini import parse_gemini
from meridian_usage.model import IngestResult


def load_all(data_dir: Path, directory: Directory) -> IngestResult:
    result = IngestResult()

    claude_dir = data_dir / "claude"
    usage = json.loads((claude_dir / "user_usage_report.json").read_text())
    cost = json.loads((claude_dir / "user_cost_report.json").read_text())
    result.extend(parse_claude(usage, cost, directory))

    for export in sorted((data_dir / "gemini").glob("gcp_billing_export_*.jsonl")):
        with export.open() as f:
            rows = (json.loads(line) for line in f if line.strip())
            result.extend(parse_gemini(rows, directory))

    return result


__all__ = ["load_all"]
