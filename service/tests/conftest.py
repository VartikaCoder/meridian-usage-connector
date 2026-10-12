from __future__ import annotations

import copy
from typing import Any

import pytest

from meridian_usage.directory import Directory, Group, Role, User
from meridian_usage.settings import Settings

ALICE = User("user_A", "alice@meridianls.example", "Alice", Role.MEMBER, ("finance",))
BOB = User("user_B", "bob@meridianls.example", "Bob", Role.TEAM_LEAD, ("hr",))
FINANCE = Group("finance", "Finance", "user_F", "rbac_FIN", "meridian-finance-prod")
HR = Group("hr", "Human Resources", "user_B", "rbac_HR", "meridian-hr-prod")


@pytest.fixture
def tiny_directory() -> Directory:
    return Directory([ALICE, BOB], [FINANCE, HR])


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture(scope="session")
def real_directory(settings: Settings) -> Directory:
    return Directory.load(settings.data_dir / "directory")


def claude_usage_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "actor": {
            "type": "user_actor",
            "user_id": "user_A",
            "email": "alice@meridianls.example",
            "name": "Alice",
            "deleted": False,
        },
        "rbac_group_id": "rbac_FIN",
        "starting_at": "2026-08-03T00:00:00.000Z",
        "ending_at": "2026-08-04T00:00:00.000Z",
        "product": "chat",
        "model": "claude-sonnet-5-5",
        "uncached_input_tokens": 100,
        "cache_read_input_tokens": 1000,
        "cache_creation": {"ephemeral_5m_input_tokens": 20, "ephemeral_1h_input_tokens": 5},
        "output_tokens": 50,
        "total_tokens": 1175,
        "requests": 3,
        "server_tool_use": {"web_search_requests": 2},
    }
    return _merge(row, overrides)


def claude_cost_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "actor": {"type": "user_actor", "user_id": "user_A", "email": "alice@meridianls.example"},
        "rbac_group_id": "rbac_FIN",
        "starting_at": "2026-08-03T00:00:00.000Z",
        "ending_at": "2026-08-04T00:00:00.000Z",
        "product": "chat",
        "model": "claude-sonnet-5-5",
        "cost_type": "tokens",
        "amount": "88.900050",
        "list_amount": "88.900050",
        "currency": "USD",
    }
    return _merge(row, overrides)


def gemini_row(direction: str = "Input", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "service": {"id": "C7E2-9256-1C43", "description": "Vertex AI"},
        "sku": {
            "id": "X",
            "description": f"Gemini 2.5 Flash Text {direction} Tokens - Predictions",
        },
        "usage_start_time": "2026-08-05 00:00:00 UTC",
        "usage_end_time": "2026-08-06 00:00:00 UTC",
        "project": {"id": "meridian-finance-prod", "labels": []},
        "labels": [
            {"key": "owner", "value": "alice@meridianls.example"},
            {"key": "agent", "value": "close-assistant"},
            {"key": "model", "value": "gemini-2.5-flash"},
        ],
        "cost": 0.1 if direction == "Input" else 0.2,
        "currency": "USD",
        "usage": {"amount": 1000.0 if direction == "Input" else 200.0, "unit": "count"},
        "credits": [],
    }
    return _merge(row, overrides)


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    out.update(overrides)
    return out
