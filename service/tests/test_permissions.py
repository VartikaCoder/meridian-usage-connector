"""The permission model, exercised end to end through the four connector tools.

Real directory, real environment files, real data. Every response is validated against the
starter kit's tools.schema.json, and every denial is checked to carry no data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from meridian_usage.aggregate import UsageData
from meridian_usage.authz import Authorizer
from meridian_usage.capabilities import CapabilityStore
from meridian_usage.directory import Directory
from meridian_usage.ingest import load_all
from meridian_usage.policy import Capability, load_policy
from meridian_usage.settings import Settings
from meridian_usage.tools import ConnectorTools, InvalidArgument

PRIYA = "user_E7691888F28227066DD2FB86"  # team_lead, clinical-operations
MEI = "user_B3DEF4602E6A0912588C6D50"  # team_lead, regulatory-affairs
SOFIA = "user_0CF0516C4D9067BED584DB3F"  # team_lead, finance
KWAME = "user_D513BD91AAB20411FC510A2A"  # team_lead, it-platform
CHLOE = "user_533B58E6F66C98ACB833527A"  # platform_admin, it-platform
DANIEL = "user_699C48763F6FA2958D2CDB5D"  # member, clinical-operations
NADIA = "user_39F097897A80367CEA4152ED"  # member, it-platform

DENIAL_KEYS = {"error", "reason", "required_scope"}

type Tools = Callable[[str], ConnectorTools]


@pytest.fixture(scope="module")
def data(settings: Settings, real_directory: Directory) -> UsageData:
    return UsageData(load_all(settings.data_dir, real_directory).records, real_directory)


@pytest.fixture
def tools(settings: Settings, real_directory: Directory, data: UsageData) -> Tools:
    def make(env: str) -> ConnectorTools:
        policy = load_policy(settings.environments_dir / f"{env}.yaml")
        store = CapabilityStore(":memory:")
        store.seed_defaults(policy, real_directory.groups)
        return ConnectorTools(data, Authorizer(policy, real_directory, store))

    return make


@pytest.fixture(scope="module")
def contract(settings: Settings) -> dict[str, Any]:
    return json.loads((settings.starter_kit / "contracts" / "tools.schema.json").read_text())


def assert_matches(contract: dict[str, Any], tool: str, payload: Any) -> None:
    schema = {"$defs": contract["$defs"], **contract["tools"][tool]["output"]}
    Draft202012Validator(schema).validate(payload)


def assert_denied(contract: dict[str, Any], payload: Any, scope: str | None = None) -> None:
    schema = {"$defs": contract["$defs"], "$ref": "#/$defs/Denial"}
    Draft202012Validator(schema).validate(payload)
    assert isinstance(payload, dict) and set(payload) <= DENIAL_KEYS, "denial leaked data"
    assert payload.get("required_scope") == scope


# --- The three cases the brief asks to demonstrate ---------------------------------------


def test_allowed_read_own_group(tools: Tools, contract: dict[str, Any]) -> None:
    out = tools("prod").call(PRIYA, "usage_summary", {"period": "last_30_days"})
    assert_matches(contract, "usage_summary", out)
    assert isinstance(out, dict) and out["group"] == "clinical-operations"
    assert out["totals"]["total_tokens"] > 0


def test_denied_cross_group_read(tools: Tools, contract: dict[str, Any]) -> None:
    out = tools("prod").call(PRIYA, "usage_summary", {"group": "finance", "period": "2026-09"})
    assert_denied(contract, out)
    assert isinstance(out, dict) and "finance" in out["reason"]


def test_denied_write_for_team_lead_in_prod(tools: Tools, contract: dict[str, Any]) -> None:
    t = tools("prod")
    args = {"group": "clinical-operations", "capability": "web-search", "enabled": False}
    out = t.call(PRIYA, "set_capability", args)
    assert_denied(contract, out, scope="capabilities:write")
    assert t.authz.store.is_enabled("clinical-operations", Capability.WEB_SEARCH), "state changed"


# --- Matrix: who may read which group, per environment -----------------------------------

READ_MATRIX = [
    # env, caller, group, allowed
    ("prod", PRIYA, "clinical-operations", True),
    ("prod", PRIYA, "finance", False),
    ("prod", CHLOE, "finance", True),
    ("prod", CHLOE, "regulatory-affairs", True),
    ("prod", KWAME, "it-platform", True),
    ("prod", KWAME, "hr", False),
    ("prod", DANIEL, "clinical-operations", False),  # members cannot read in prod
    ("prod", NADIA, "it-platform", False),
    ("staging", PRIYA, "finance", False),
    ("staging", SOFIA, "finance", True),
    ("staging", DANIEL, "clinical-operations", False),
    ("staging", CHLOE, "hr", True),
    ("dev", DANIEL, "finance", True),  # dev: anyone reads anything
    ("dev", PRIYA, "hr", True),
]


@pytest.mark.parametrize(("env", "caller", "group", "allowed"), READ_MATRIX)
@pytest.mark.parametrize("tool", ["usage_summary", "top_users", "capability_status"])
def test_read_matrix(
    tools: Tools,
    contract: dict[str, Any],
    env: str,
    caller: str,
    group: str,
    allowed: bool,
    tool: str,
) -> None:
    args: dict[str, Any] = {"group": group}
    if tool != "capability_status":
        args["period"] = "2026-W36"
    out = tools(env).call(caller, tool, args)
    if allowed:
        assert_matches(contract, tool, out)
    else:
        assert isinstance(out, dict) and out.get("error") == "permission_denied"
        assert set(out) <= DENIAL_KEYS


# --- Matrix: who may change which group's controls ----------------------------------------

WRITE_MATRIX = [
    ("prod", CHLOE, "finance", True),
    ("prod", PRIYA, "clinical-operations", False),
    ("prod", DANIEL, "clinical-operations", False),
    ("staging", PRIYA, "clinical-operations", True),  # leads may toggle their own group
    ("staging", PRIYA, "finance", False),  # ...but not someone else's
    ("staging", CHLOE, "hr", True),
    ("dev", PRIYA, "finance", True),  # dev: visibility is all
    ("dev", DANIEL, "clinical-operations", False),  # members never write
]


@pytest.mark.parametrize(("env", "caller", "group", "allowed"), WRITE_MATRIX)
def test_write_matrix(
    tools: Tools, contract: dict[str, Any], env: str, caller: str, group: str, allowed: bool
) -> None:
    t = tools(env)
    before = t.authz.store.is_enabled(group, Capability.CODE_EXECUTION)
    args = {"group": group, "capability": "code-execution", "enabled": not before}
    out = t.call(caller, "set_capability", args)
    after = t.authz.store.is_enabled(group, Capability.CODE_EXECUTION)
    if allowed:
        assert_matches(contract, "set_capability", out)
        assert after is (not before)
    else:
        assert isinstance(out, dict) and out["error"] == "permission_denied"
        assert after is before, "a denied write changed state"


# --- The per-group connector control from Part A ----------------------------------------


@pytest.mark.parametrize("tool", ["usage_summary", "top_users", "capability_status"])
def test_connector_disabled_for_group_denies_every_tool(
    tools: Tools, contract: dict[str, Any], tool: str
) -> None:
    # In prod, regulatory-affairs does not have the connector by default.
    args: dict[str, Any] = {} if tool == "capability_status" else {"period": "last_7_days"}
    out = tools("prod").call(MEI, tool, args)
    assert_denied(contract, out)
    assert isinstance(out, dict) and "not enabled" in out["reason"]


def test_connector_gate_also_blocks_writes(tools: Tools, contract: dict[str, Any]) -> None:
    t = tools("staging")
    t.authz.store.set("clinical-operations", Capability.USAGE_CONNECTOR, False, actor="x")
    args = {"group": "clinical-operations", "capability": "web-search", "enabled": False}
    out = t.call(PRIYA, "set_capability", args)
    assert_denied(contract, out)
    assert t.authz.store.is_enabled("clinical-operations", Capability.WEB_SEARCH)


def test_enabling_connector_lets_the_group_in(tools: Tools) -> None:
    t = tools("prod")
    enable = {"group": "regulatory-affairs", "capability": "usage-connector", "enabled": True}
    before = t.call(MEI, "usage_summary", {"period": "2026-09"})
    assert isinstance(before, dict) and before["error"] == "permission_denied"
    t.call(CHLOE, "set_capability", enable)
    out = t.call(MEI, "usage_summary", {"period": "2026-09"})
    assert isinstance(out, dict) and out["group"] == "regulatory-affairs"


def test_disabling_connector_for_own_group_locks_the_admin_out_too(tools: Tools) -> None:
    t = tools("prod")
    disable = {"group": "it-platform", "capability": "usage-connector", "enabled": False}
    t.call(CHLOE, "set_capability", disable)
    out = t.call(CHLOE, "capability_status", {"group": "it-platform"})
    assert isinstance(out, dict) and out["error"] == "permission_denied"


# --- Identity and defaults -------------------------------------------------------------


@pytest.mark.parametrize("caller", [None, "", "user_NOT_IN_DIRECTORY"])
def test_unknown_caller_is_denied(tools: Tools, contract: dict[str, Any], caller: str) -> None:
    assert_denied(contract, tools("dev").call(caller, "usage_summary", {"period": "2026-09"}))


def test_group_defaults_to_callers_own(tools: Tools) -> None:
    out = tools("prod").call(KWAME, "top_users", {"period": "2026-09", "limit": 3})
    assert isinstance(out, list) and len(out) == 3
    assert {u["group"] for u in out} == {"it-platform"}


def test_admin_without_group_gets_own_group_not_everything(tools: Tools) -> None:
    out = tools("prod").call(CHLOE, "usage_summary", {"period": "2026-09"})
    assert isinstance(out, dict) and out["group"] == "it-platform"


def test_denial_wins_over_bad_input(tools: Tools, contract: dict[str, Any]) -> None:
    # A caller who may not read gets a denial, not a hint about what a valid period is.
    out = tools("prod").call(PRIYA, "usage_summary", {"group": "hr", "period": "nonsense"})
    assert_denied(contract, out)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("usage_summary", {"period": "2026-W99"}),
        ("usage_summary", {}),
        ("top_users", {"period": "2026-09", "limit": 0}),
        ("top_users", {"period": "2026-09", "limit": True}),
        ("set_capability", {"group": "hr", "capability": "teleport", "enabled": True}),
        ("set_capability", {"group": "hr", "capability": "web-search", "enabled": "yes"}),
        ("drop_tables", {}),
    ],
)
def test_bad_input_is_an_error_not_a_denial(tools: Tools, tool: str, args: dict[str, Any]) -> None:
    with pytest.raises(InvalidArgument):
        tools("prod").call(CHLOE, tool, args)


def test_prompt_cannot_widen_scope(tools: Tools) -> None:
    # Identity comes from the caller id the server was started with, never from arguments.
    args = {"group": "finance", "period": "2026-09", "as_user": CHLOE, "role": "platform_admin"}
    out = tools("prod").call(PRIYA, "usage_summary", args)
    assert isinstance(out, dict) and out["error"] == "permission_denied"
