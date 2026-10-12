from __future__ import annotations

import copy
from typing import Any

import pytest

from meridian_usage.directory import Directory, Role
from meridian_usage.policy import (
    Capability,
    PolicyError,
    Scope,
    Visibility,
    load_policy,
    parse_policy,
)
from meridian_usage.settings import Settings

MEMBER, LEAD, ADMIN = Role.MEMBER, Role.TEAM_LEAD, Role.PLATFORM_ADMIN


def policy(settings: Settings, env: str):  # type: ignore[no-untyped-def]
    return load_policy(settings.environments_dir / f"{env}.yaml")


class TestStarterKitEnvironments:
    """The table in starter-kit/README.md, asserted cell by cell."""

    @pytest.mark.parametrize(
        ("env", "readers", "writers", "visibility"),
        [
            ("dev", {MEMBER, LEAD, ADMIN}, {LEAD, ADMIN}, Visibility.ALL),
            ("staging", {LEAD, ADMIN}, {LEAD, ADMIN}, Visibility.OWN_GROUP),
            ("prod", {LEAD, ADMIN}, {ADMIN}, Visibility.OWN_GROUP),
        ],
    )
    def test_grants(
        self,
        settings: Settings,
        env: str,
        readers: set[Role],
        writers: set[Role],
        visibility: Visibility,
    ) -> None:
        p = policy(settings, env)
        assert p.roles_for(Scope.USAGE_READ) == readers
        assert p.roles_for(Scope.CAPABILITIES_WRITE) == writers
        assert p.read_visibility is visibility

    def test_admins_see_all_groups_even_under_own_group(self, settings: Settings) -> None:
        p = policy(settings, "prod")
        assert p.sees_all_groups(ADMIN)
        assert not p.sees_all_groups(LEAD)

    def test_prod_connector_default_only_for_two_groups(self, settings: Settings) -> None:
        d = policy(settings, "prod").capability_defaults[Capability.USAGE_CONNECTOR]
        assert d.for_group("it-platform") and d.for_group("clinical-operations")
        assert not d.for_group("finance")

    @pytest.mark.parametrize(
        ("env", "enabled"), [("dev", True), ("staging", False), ("prod", False)]
    )
    def test_code_execution_default(self, settings: Settings, env: str, enabled: bool) -> None:
        d = policy(settings, env).capability_defaults[Capability.CODE_EXECUTION]
        assert d.for_group("finance") is enabled

    @pytest.mark.parametrize("env", ["dev", "staging", "prod"])
    def test_validates_against_directory(
        self, settings: Settings, real_directory: Directory, env: str
    ) -> None:
        policy(settings, env).validate_against(real_directory)

    def test_server_names(self, settings: Settings) -> None:
        assert policy(settings, "prod").server_name == "meridian-usage"
        assert policy(settings, "dev").server_name == "meridian-usage-dev"


VALID: dict[str, Any] = {
    "environment": "test",
    "scopes": {
        "usage:read": {"roles": ["team_lead"], "group_visibility": "own_group"},
        "capabilities:write": {"roles": ["platform_admin"]},
    },
    "capability_defaults": {
        "usage-connector": "enabled",
        "web-search": "disabled",
        "code-execution": {"enabled_for": ["it-platform"]},
    },
    "host": {"server_name": "x"},
}


def mutated(path: list[str], value: Any) -> dict[str, Any]:
    doc = copy.deepcopy(VALID)
    target = doc
    for key in path[:-1]:
        target = target[key]
    if value is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return doc


DELETE = object()


class TestStrictParsing:
    def test_valid_document_parses(self) -> None:
        assert parse_policy(VALID).environment == "test"

    def test_scope_without_roles_is_granted_to_nobody(self) -> None:
        p = parse_policy(mutated(["scopes", "capabilities:write", "roles"], []))
        assert p.roles_for(Scope.CAPABILITIES_WRITE) == frozenset()

    def test_missing_scope_is_granted_to_nobody(self) -> None:
        p = parse_policy(mutated(["scopes", "capabilities:write"], DELETE))
        assert p.roles_for(Scope.CAPABILITIES_WRITE) == frozenset()

    @pytest.mark.parametrize(
        ("path", "value", "message"),
        [
            (["scopes", "usage:read", "roles"], ["team_leader"], "unknown value"),
            (["scopes", "usage:raed"], {"roles": []}, "unknown value"),
            (["scopes", "usage:read", "group_visibility"], "everyone", "unknown value"),
            (["scopes", "capabilities:write", "group_visibility"], "all", "unknown keys"),
            (["capability_defaults", "web-search"], "on", "expected"),
            (["capability_defaults", "web-search"], DELETE, "no default"),
            (["capability_defaults", "telepathy"], "enabled", "unknown value"),
            (["host"], DELETE, "missing 'host'"),
            (["surprise"], 1, "unknown keys"),
        ],
    )
    def test_rejects_bad_documents(self, path: list[str], value: Any, message: str) -> None:
        with pytest.raises(PolicyError, match=message):
            parse_policy(mutated(path, value))

    def test_enabled_for_unknown_group_fails_validation(self, real_directory: Directory) -> None:
        doc = mutated(["capability_defaults", "code-execution"], {"enabled_for": ["it-platfrom"]})
        with pytest.raises(PolicyError, match="unknown groups"):
            parse_policy(doc).validate_against(real_directory)
