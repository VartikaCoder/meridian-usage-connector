from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian_usage.capabilities import CapabilityStore
from meridian_usage.directory import Directory
from meridian_usage.policy import Capability, load_policy
from meridian_usage.settings import Settings

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def prod_store(settings: Settings, real_directory: Directory, clock: FakeClock) -> CapabilityStore:
    store = CapabilityStore(":memory:", clock)
    store.seed_defaults(load_policy(settings.environments_dir / "prod.yaml"), real_directory.groups)
    return store


def test_seeding_applies_prod_defaults(prod_store: CapabilityStore) -> None:
    assert prod_store.is_enabled("it-platform", Capability.USAGE_CONNECTOR)
    assert prod_store.is_enabled("clinical-operations", Capability.USAGE_CONNECTOR)
    assert not prod_store.is_enabled("finance", Capability.USAGE_CONNECTOR)
    assert prod_store.is_enabled("finance", Capability.WEB_SEARCH)
    assert not prod_store.is_enabled("finance", Capability.CODE_EXECUTION)
    state = prod_store.get("finance", Capability.WEB_SEARCH)
    assert state is not None and state.updated_by == "defaults:prod"
    assert state.updated_at == "2026-10-01T09:00:00Z"


def test_reseeding_never_overwrites_a_decision(
    prod_store: CapabilityStore, settings: Settings, real_directory: Directory
) -> None:
    prod_store.set("finance", Capability.USAGE_CONNECTOR, True, actor="chloe@x")
    added = prod_store.seed_defaults(
        load_policy(settings.environments_dir / "prod.yaml"), real_directory.groups
    )
    assert added == 0
    assert prod_store.is_enabled("finance", Capability.USAGE_CONNECTOR)


def test_set_records_who_and_when(prod_store: CapabilityStore, clock: FakeClock) -> None:
    clock.now = T1
    state = prod_store.set("hr", Capability.CODE_EXECUTION, True, actor="chloe@x")
    assert (state.enabled, state.updated_by, state.updated_at) == (
        True,
        "chloe@x",
        "2026-10-02T09:00:00Z",
    )
    assert prod_store.get("hr", Capability.CODE_EXECUTION) == state


def test_audit_log_keeps_every_change(prod_store: CapabilityStore) -> None:
    prod_store.set("hr", Capability.CODE_EXECUTION, True, actor="a")
    prod_store.set("hr", Capability.CODE_EXECUTION, False, actor="b")
    entries = [e for e in prod_store.audit_log("hr") if e.capability is Capability.CODE_EXECUTION]
    assert [(e.enabled, e.changed_by) for e in entries] == [
        (False, "defaults:prod"),
        (True, "a"),
        (False, "b"),
    ]


def test_missing_row_means_disabled() -> None:
    store = CapabilityStore(":memory:")
    assert not store.is_enabled("finance", Capability.WEB_SEARCH)
    assert store.list_for_group("finance") == []


def test_state_survives_restart(tmp_path: Path) -> None:
    db = tmp_path / "caps.sqlite3"
    first = CapabilityStore(db)
    first.set("hr", Capability.WEB_SEARCH, False, actor="a")
    first.close()
    second = CapabilityStore(db)
    assert second.get("hr", Capability.WEB_SEARCH) is not None
    assert not second.is_enabled("hr", Capability.WEB_SEARCH)
