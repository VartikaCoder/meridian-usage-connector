"""Per-environment access policy, loaded from starter-kit/environments/<env>.yaml.

The YAML is treated as the single source of grants. Loading is strict: an unknown key, role,
scope, group or capability is an error, and anything not granted is denied. A scope with no
roles listed is granted to nobody.

Group visibility for `usage:read`:
- `all`: every role holding the scope may read every group.
- `own_group`: callers read only their own group(s), except `platform_admin`, who reads all
  groups (the directory defines platform admins as "may see every group").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from meridian_usage.directory import Directory, Role


class Scope(StrEnum):
    USAGE_READ = "usage:read"
    CAPABILITIES_WRITE = "capabilities:write"


class Capability(StrEnum):
    USAGE_CONNECTOR = "usage-connector"
    WEB_SEARCH = "web-search"
    CODE_EXECUTION = "code-execution"


class Visibility(StrEnum):
    ALL = "all"
    OWN_GROUP = "own_group"


class PolicyError(ValueError):
    """The environment file is not a valid policy."""


@dataclass(frozen=True)
class CapabilityDefault:
    """enabled everywhere, disabled everywhere, or enabled only for listed groups."""

    enabled: bool
    enabled_for: frozenset[str] | None = None

    def for_group(self, group_id: str) -> bool:
        if self.enabled_for is not None:
            return group_id in self.enabled_for
        return self.enabled


@dataclass(frozen=True)
class Policy:
    environment: str
    scope_roles: Mapping[Scope, frozenset[Role]]
    read_visibility: Visibility
    capability_defaults: Mapping[Capability, CapabilityDefault]
    server_name: str

    def roles_for(self, scope: Scope) -> frozenset[Role]:
        return self.scope_roles.get(scope, frozenset())

    def has_scope(self, role: Role, scope: Scope) -> bool:
        return role in self.roles_for(scope)

    def sees_all_groups(self, role: Role) -> bool:
        return self.read_visibility is Visibility.ALL or role is Role.PLATFORM_ADMIN

    def validate_against(self, directory: Directory) -> None:
        for cap, default in self.capability_defaults.items():
            unknown = (default.enabled_for or frozenset()) - set(directory.groups)
            if unknown:
                raise PolicyError(f"{cap}: enabled_for names unknown groups {sorted(unknown)}")


def load_policy(path: Path) -> Policy:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise PolicyError(f"{path.name}: expected a mapping")
    return parse_policy(doc, source=path.name)


def parse_policy(doc: Mapping[str, Any], source: str = "policy") -> Policy:
    _only_keys(doc, {"environment", "scopes", "capability_defaults", "host"}, source)
    env = _require(doc, "environment", str, source)

    scopes_doc = _require(doc, "scopes", dict, source)
    scope_roles: dict[Scope, frozenset[Role]] = {}
    visibility: Visibility | None = None
    for name, spec in scopes_doc.items():
        scope = _enum(Scope, name, f"{source}: scope")
        if not isinstance(spec, dict):
            raise PolicyError(f"{source}: scope {name} must be a mapping")
        allowed = {"roles", "group_visibility"} if scope is Scope.USAGE_READ else {"roles"}
        _only_keys(spec, allowed, f"{source}: scope {name}")
        roles = spec.get("roles") or []
        if not isinstance(roles, list):
            raise PolicyError(f"{source}: scope {name}.roles must be a list")
        scope_roles[scope] = frozenset(_enum(Role, r, f"{source}: {name} role") for r in roles)
        if scope is Scope.USAGE_READ:
            visibility = _enum(
                Visibility, spec.get("group_visibility", "own_group"), f"{source}: visibility"
            )

    defaults_doc = _require(doc, "capability_defaults", dict, source)
    defaults = {
        _enum(Capability, k, f"{source}: capability"): _parse_default(v, f"{source}: {k}")
        for k, v in defaults_doc.items()
    }
    missing = set(Capability) - set(defaults)
    if missing:
        raise PolicyError(f"{source}: no default for capabilities {sorted(missing)}")

    host = _require(doc, "host", dict, source)
    _only_keys(host, {"server_name"}, f"{source}: host")

    return Policy(
        environment=env,
        scope_roles=scope_roles,
        read_visibility=visibility or Visibility.OWN_GROUP,
        capability_defaults=defaults,
        server_name=_require(host, "server_name", str, f"{source}: host"),
    )


def _parse_default(value: Any, where: str) -> CapabilityDefault:
    if value == "enabled":
        return CapabilityDefault(enabled=True)
    if value == "disabled":
        return CapabilityDefault(enabled=False)
    if isinstance(value, dict) and set(value) == {"enabled_for"}:
        groups = value["enabled_for"]
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            raise PolicyError(f"{where}: enabled_for must be a list of group ids")
        return CapabilityDefault(enabled=False, enabled_for=frozenset(groups))
    raise PolicyError(f"{where}: expected 'enabled', 'disabled' or {{enabled_for: [...]}}")


def _require(doc: Mapping[str, Any], key: str, typ: type, where: str) -> Any:
    if key not in doc:
        raise PolicyError(f"{where}: missing '{key}'")
    if not isinstance(doc[key], typ):
        raise PolicyError(f"{where}: '{key}' must be a {typ.__name__}")
    return doc[key]


def _only_keys(doc: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = set(doc) - allowed
    if extra:
        raise PolicyError(f"{where}: unknown keys {sorted(extra)}")


def _enum[E: StrEnum](cls: type[E], value: Any, where: str) -> E:
    try:
        return cls(value)
    except ValueError as e:
        raise PolicyError(f"{where}: unknown value {value!r}") from e
