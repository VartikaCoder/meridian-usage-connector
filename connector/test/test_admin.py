"""Admin service logic (framework-independent), against real data and the prod policy."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from meridian_usage.admin import AdminService
from meridian_usage.authz import Denial
from meridian_usage.directory import User
from meridian_usage.settings import Settings
from meridian_usage.tools import InvalidArgument
from tests.test_permissions import CHLOE, DANIEL, MEI, PRIYA


@pytest.fixture
def make(settings: Settings, tmp_path: Path) -> Any:
    def build(env: str) -> AdminService:
        return AdminService.from_settings(
            replace(settings, environment=env, db_path=tmp_path / f"{env}.sqlite3")
        )

    return build


@pytest.fixture
def prod(make: Any) -> AdminService:
    return make("prod")  # type: ignore[no-any-return]


def who(svc: AdminService, user_id: str) -> User:
    u = svc.resolve_caller(user_id)
    assert isinstance(u, User)
    return u


class TestIdentity:
    def test_resolves_by_id_or_email(self, prod: AdminService) -> None:
        assert who(prod, "priya.nair@meridianls.example").user_id == PRIYA
        assert who(prod, "PRIYA.NAIR@meridianls.example").user_id == PRIYA

    @pytest.mark.parametrize("header", [None, "", "nobody@example.com"])
    def test_unknown_is_denied(self, prod: AdminService, header: str | None) -> None:
        assert isinstance(prod.resolve_caller(header), Denial)

    def test_me_describes_effective_permissions(self, prod: AdminService) -> None:
        me = prod.me(who(prod, PRIYA))
        assert me["scopes"] == ["usage:read"]
        assert me["visible_groups"] == ["clinical-operations"]
        assert me["connector_enabled"] is True
        assert prod.me(who(prod, MEI))["connector_enabled"] is False  # prod default

    def test_me_lists_no_groups_without_read_scope(self, prod: AdminService) -> None:
        assert prod.me(who(prod, DANIEL))["visible_groups"] == []


class TestUsage:
    def test_lead_sees_only_own_group_and_no_org_total(self, prod: AdminService) -> None:
        out = prod.usage_by_group(who(prod, PRIYA), "2026-09")
        assert isinstance(out, dict)
        assert [g["group"] for g in out["groups"]] == ["clinical-operations"]
        assert out["org_totals"] is None

    def test_admin_sees_all_groups_that_add_up(self, prod: AdminService) -> None:
        out = prod.usage_by_group(who(prod, CHLOE), "2026-09")
        assert isinstance(out, dict) and len(out["groups"]) == 6
        parts = sum(g["totals"]["cost_usd"] for g in out["groups"])
        assert parts == pytest.approx(out["org_totals"]["totals"]["cost_usd"], abs=1e-5)

    def test_member_in_prod_is_denied_with_scope(self, prod: AdminService) -> None:
        out = prod.usage_by_group(who(prod, DANIEL), "2026-09")
        assert isinstance(out, Denial) and out.required_scope is not None

    def test_admin_page_is_not_gated_by_the_connector_switch(self, prod: AdminService) -> None:
        # Mei's group has the connector off in prod, but the admin service still answers.
        out = prod.usage_by_group(who(prod, MEI), "2026-09")
        assert isinstance(out, dict) and out["groups"][0]["group"] == "regulatory-affairs"

    def test_weekly_cross_group_denied(self, prod: AdminService) -> None:
        assert isinstance(prod.weekly(who(prod, PRIYA), "finance"), Denial)

    def test_weekly_defaults_to_visible_groups(self, prod: AdminService) -> None:
        rows = prod.weekly(who(prod, PRIYA), None)
        assert isinstance(rows, list) and {r["group"] for r in rows} == {"clinical-operations"}
        assert len({r["week"] for r in rows}) == 8

    def test_bad_period_after_permission(self, prod: AdminService) -> None:
        assert isinstance(prod.usage_by_group(who(prod, DANIEL), "junk"), Denial)
        with pytest.raises(InvalidArgument):
            prod.usage_by_group(who(prod, CHLOE), "junk")


class TestCapabilities:
    def test_lead_reads_own_controls_only(self, prod: AdminService) -> None:
        out = prod.capabilities(who(prod, PRIYA), None)
        assert isinstance(out, list) and {s["group"] for s in out} == {"clinical-operations"}
        assert len(out) == 3

    def test_admin_change_is_persisted_and_audited(self, prod: AdminService) -> None:
        out = prod.set_capability(who(prod, CHLOE), "finance", "usage-connector", True)
        assert isinstance(out, dict) and out["updated_by"] == "chloe.dubois@meridianls.example"
        audit = prod.audit(who(prod, CHLOE), "finance")
        assert isinstance(audit, list) and audit[0]["changed_by"] == out["updated_by"]

    def test_lead_write_denied_in_prod(self, prod: AdminService) -> None:
        out = prod.set_capability(who(prod, PRIYA), "clinical-operations", "web-search", False)
        assert isinstance(out, Denial) and out.required_scope is not None
        assert out.required_scope.value == "capabilities:write"

    def test_state_survives_a_restart(self, make: Any) -> None:
        first: AdminService = make("prod")
        first.set_capability(who(first, CHLOE), "hr", "web-search", False)
        second: AdminService = make("prod")  # same db file, fresh process state
        states = second.capabilities(who(second, CHLOE), "hr")
        assert isinstance(states, list)
        assert {s["capability"]: s["enabled"] for s in states}["web-search"] is False


def test_ingest_report_only_for_admins(prod: AdminService) -> None:
    assert isinstance(prod.ingest_report(who(prod, PRIYA)), Denial)
    report = prod.ingest_report(who(prod, CHLOE))
    assert isinstance(report, dict)
    assert (report["records"], report["rejected"], report["warnings"]) == (1841, [], [])


def test_unknown_environment_fails_fast(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        AdminService.from_settings(replace(settings, environment="qa", db_path=tmp_path / "x"))
