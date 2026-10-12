"""Authorisation: the single place that decides who may read or change what.

Every decision returns either the allowed value or a `Denial`, never partial data. The rules:

1. The caller must be a user in the directory.
2. (Connector calls only) `usage-connector` must be enabled for the caller's group.
3. The caller's role must hold the scope the action needs, per the environment policy.
4. The target group must be visible to the caller: their own group, or every group when the
   policy's visibility is `all` or the caller is a platform admin.
   Changing a group's capabilities needs `capabilities:write` AND visibility of that group:
   you can only change what you can see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meridian_usage.capabilities import CapabilityStore
from meridian_usage.directory import Directory, User
from meridian_usage.policy import Capability, Policy, Scope


@dataclass(frozen=True)
class Denial:
    reason: str
    required_scope: Scope | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": "permission_denied", "reason": self.reason}
        if self.required_scope is not None:
            out["required_scope"] = self.required_scope.value
        return out


class Authorizer:
    def __init__(self, policy: Policy, directory: Directory, store: CapabilityStore) -> None:
        self.policy = policy
        self.directory = directory
        self.store = store

    def identify(self, user_id: str | None) -> User | Denial:
        user = self.directory.user_by_id(user_id) if user_id else None
        if user is None:
            return Denial("caller is not a known user in the directory")
        return user

    def connector_gate(self, caller: User) -> Denial | None:
        """The connector answers only if it is enabled for (one of) the caller's groups."""
        if any(self.store.is_enabled(g, Capability.USAGE_CONNECTOR) for g in caller.group_ids):
            return None
        return Denial(
            f"the usage connector is not enabled for {_groups(caller)}; "
            "a platform admin can enable it"
        )

    def visible_groups(self, caller: User) -> list[str]:
        if self.policy.sees_all_groups(caller.role):
            return sorted(self.directory.groups)
        return sorted(caller.group_ids)

    def can_see(self, caller: User, group: str) -> bool:
        return group in self.visible_groups(caller)

    def read_group(self, caller: User, requested: str | None) -> str | Denial:
        """Resolve the group a read is about, defaulting to the caller's own group."""
        if not self.policy.has_scope(caller.role, Scope.USAGE_READ):
            return Denial(
                f"role {caller.role} may not read usage in {self.policy.environment}",
                Scope.USAGE_READ,
            )
        group = requested if requested is not None else self._own_group(caller)
        if isinstance(group, Denial):
            return group
        return self._visible(caller, group, "read")

    def read_all_groups(self, caller: User) -> Denial | None:
        """For org-wide views (no group filter)."""
        if not self.policy.has_scope(caller.role, Scope.USAGE_READ):
            return Denial(
                f"role {caller.role} may not read usage in {self.policy.environment}",
                Scope.USAGE_READ,
            )
        if not self.policy.sees_all_groups(caller.role):
            return Denial(f"{caller.role} may only read their own group ({_groups(caller)})")
        return None

    def write_group(self, caller: User, group: str) -> str | Denial:
        if not self.policy.has_scope(caller.role, Scope.CAPABILITIES_WRITE):
            return Denial(
                f"role {caller.role} may not change capability controls "
                f"in {self.policy.environment}",
                Scope.CAPABILITIES_WRITE,
            )
        return self._visible(caller, group, "change")

    def _visible(self, caller: User, group: str, verb: str) -> str | Denial:
        if group not in self.directory.groups:
            return Denial(f"unknown group {group!r}")
        if not self.can_see(caller, group):
            return Denial(
                f"{caller.role} may only {verb} their own group ({_groups(caller)}), not {group}"
            )
        return group

    @staticmethod
    def _own_group(caller: User) -> str | Denial:
        if len(caller.group_ids) == 1:
            return caller.group_ids[0]
        if not caller.group_ids:
            return Denial("caller belongs to no group")
        return Denial(f"caller belongs to several groups ({_groups(caller)}); name one")


def _groups(user: User) -> str:
    return ", ".join(user.group_ids) or "no group"
