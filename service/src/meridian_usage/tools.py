"""The four connector tools, enforced and serialised to the shapes in tools.schema.json.

Each call runs the full check chain from `authz` (known caller, connector enabled for their
group, scope, group visibility) before touching any data, and returns either the payload or a
Denial dict. Bad input (an unparseable period, an unknown capability) raises InvalidArgument,
which is a different thing from being refused.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from meridian_usage.aggregate import Totals, UsageData
from meridian_usage.authz import Authorizer, Denial
from meridian_usage.directory import User
from meridian_usage.periods import Period, PeriodError, parse_period
from meridian_usage.policy import Capability

type Payload = dict[str, Any] | list[dict[str, Any]]

MICRO_DOLLAR = Decimal("0.000001")
TOOL_NAMES = ("usage_summary", "top_users", "capability_status", "set_capability")


class InvalidArgument(ValueError):
    """The request is malformed (as opposed to refused)."""


def usd(amount: Decimal) -> float:
    """Money leaves the service as a JSON number rounded to micro-dollars."""
    return float(amount.quantize(MICRO_DOLLAR, rounding=ROUND_HALF_EVEN))


class ConnectorTools:
    def __init__(self, data: UsageData, authz: Authorizer) -> None:
        self.data = data
        self.authz = authz

    def call(self, caller_id: str | None, tool: str, args: dict[str, Any]) -> Payload:
        caller = self.authz.identify(caller_id)
        if isinstance(caller, Denial):
            return caller.to_dict()
        gate = self.authz.connector_gate(caller)
        if gate is not None:
            return gate.to_dict()
        match tool:
            case "usage_summary":
                return self.usage_summary(caller, args.get("group"), _required(args, "period"))
            case "top_users":
                return self.top_users(
                    caller, args.get("group"), _required(args, "period"), args.get("limit", 10)
                )
            case "capability_status":
                return self.capability_status(caller, args.get("group"))
            case "set_capability":
                return self.set_capability(
                    caller,
                    _required(args, "group"),
                    _required(args, "capability"),
                    _required(args, "enabled"),
                )
            case _:
                raise InvalidArgument(f"unknown tool {tool!r}")

    def usage_summary(self, caller: User, group: str | None, period: str) -> Payload:
        g = self.authz.read_group(caller, group)
        if isinstance(g, Denial):
            return g.to_dict()
        s = self.data.summary(period_arg(period), g)
        return {
            "group": g,
            "period": {
                "label": s.period.label,
                "start": s.period.start.isoformat(),
                "end_exclusive": s.period.end_exclusive.isoformat(),
                "data_covers": {
                    "start": s.coverage.start.isoformat() if s.coverage.start else None,
                    "end_exclusive": (
                        s.coverage.end_exclusive.isoformat() if s.coverage.end_exclusive else None
                    ),
                    "complete": s.coverage.complete,
                },
            },
            "platforms": [
                {"platform": p.platform.value, **totals_json(p.totals)} for p in s.platforms
            ],
            "totals": totals_json(s.totals),
        }

    def top_users(self, caller: User, group: str | None, period: str, limit: Any) -> Payload:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
            raise InvalidArgument("limit must be an integer from 1 to 25")
        g = self.authz.read_group(caller, group)
        if isinstance(g, Denial):
            return g.to_dict()
        return [
            {
                "user_id": u.user_id,
                "name": u.name,
                "email": u.email,
                "group": u.group,
                "total_tokens": u.totals.total_tokens,
                "cost_usd": usd(u.totals.cost_usd),
            }
            for u in self.data.top_users(period_arg(period), g, limit)
        ]

    def capability_status(self, caller: User, group: str | None) -> Payload:
        g = self.authz.read_group(caller, group)
        if isinstance(g, Denial):
            return g.to_dict()
        return [
            {
                "group": s.group,
                "capability": s.capability.value,
                "enabled": s.enabled,
                "updated_at": s.updated_at,
                "updated_by": s.updated_by,
            }
            for s in self.authz.store.list_for_group(g)
        ]

    def set_capability(self, caller: User, group: str, capability: str, enabled: Any) -> Payload:
        cap = _capability(capability)
        if not isinstance(enabled, bool):
            raise InvalidArgument("enabled must be true or false")
        g = self.authz.write_group(caller, group)
        if isinstance(g, Denial):
            return g.to_dict()
        state = self.authz.store.set(g, cap, enabled, actor=caller.email)
        return {
            "group": state.group,
            "capability": state.capability.value,
            "enabled": state.enabled,
            "updated_at": state.updated_at,
            "updated_by": state.updated_by,
        }


def totals_json(t: Totals) -> dict[str, Any]:
    return {
        "requests": t.requests,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "total_tokens": t.total_tokens,
        "cost_usd": usd(t.cost_usd),
    }


def period_arg(text: Any) -> Period:
    if not isinstance(text, str):
        raise InvalidArgument("period must be a string")
    try:
        return parse_period(text)
    except PeriodError as e:
        raise InvalidArgument(str(e)) from e


def _capability(value: Any) -> Capability:
    try:
        return Capability(value)
    except ValueError as e:
        options = ", ".join(c.value for c in Capability)
        raise InvalidArgument(f"unknown capability {value!r}; expected one of {options}") from e


def _required(args: dict[str, Any], key: str) -> Any:
    if key not in args:
        raise InvalidArgument(f"missing required argument {key!r}")
    return args[key]
