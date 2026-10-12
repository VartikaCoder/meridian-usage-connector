"""Admin service logic, independent of the web framework.

Every method takes the already-identified caller and returns a JSON-ready payload or a
`Denial`. The HTTP layer (`app.py`) only maps those to responses. The connector's extra
gate (usage-connector enabled for the caller's group) does NOT apply here: this is the
platform owners' admin surface, governed by scopes and group visibility alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meridian_usage.aggregate import UsageData
from meridian_usage.authz import Authorizer, Denial
from meridian_usage.capabilities import CapabilityStore
from meridian_usage.directory import Directory, User
from meridian_usage.ingest import load_all
from meridian_usage.model import IngestResult
from meridian_usage.policy import Scope, load_policy
from meridian_usage.settings import Settings
from meridian_usage.tools import ConnectorTools, Payload, period_arg, totals_json, usd

type Result = Payload | Denial


@dataclass
class AdminService:
    data: UsageData
    authz: Authorizer
    ingest: IngestResult

    @classmethod
    def from_settings(cls, settings: Settings) -> AdminService:
        """Load directory, policy, exports and capability store for one environment."""
        directory = Directory.load(settings.data_dir / "directory")
        policy_file = settings.environments_dir / f"{settings.environment}.yaml"
        if not policy_file.is_file():
            raise FileNotFoundError(f"no policy for environment {settings.environment!r}")
        policy = load_policy(policy_file)
        policy.validate_against(directory)
        ingest = load_all(settings.data_dir, directory)
        store = CapabilityStore(settings.db_path)
        store.seed_defaults(policy, directory.groups)
        data = UsageData(ingest.records, directory)
        return cls(data, Authorizer(policy, directory, store), ingest)

    @property
    def tools(self) -> ConnectorTools:
        return ConnectorTools(self.data, self.authz)

    def resolve_caller(self, header_value: str | None) -> User | Denial:
        """Simulated identity: the header carries a directory user id or email."""
        if header_value:
            by_email = self.authz.directory.user_by_email(header_value)
            if by_email is not None:
                return by_email
        return self.authz.identify(header_value)

    def directory_users(self) -> list[dict[str, Any]]:
        """For the page's user switcher only: identity is simulated in this exercise."""
        users = sorted(self.authz.directory.users.values(), key=lambda u: (u.group_ids, u.name))
        return [
            {
                "user_id": u.user_id,
                "name": u.name,
                "email": u.email,
                "role": u.role.value,
                "group_ids": list(u.group_ids),
            }
            for u in users
        ]

    def me(self, caller: User) -> dict[str, Any]:
        policy = self.authz.policy
        return {
            "user_id": caller.user_id,
            "name": caller.name,
            "email": caller.email,
            "role": caller.role.value,
            "group_ids": list(caller.group_ids),
            "environment": policy.environment,
            "scopes": [s.value for s in Scope if policy.has_scope(caller.role, s)],
            "visible_groups": (
                self.authz.visible_groups(caller)
                if policy.has_scope(caller.role, Scope.USAGE_READ)
                else []
            ),
            "connector_enabled": self.authz.connector_gate(caller) is None,
        }

    def usage_by_group(self, caller: User, period: str) -> Result:
        """One summary per group the caller may see, plus an org total for those who see all."""
        groups = self._readable_groups(caller)
        if isinstance(groups, Denial):
            return groups
        p = period_arg(period)
        summaries = []
        for g in groups:
            s = self.tools.usage_summary(caller, g, period)
            if isinstance(s, dict) and s.get("error"):
                return Denial(str(s["reason"]))
            summaries.append(s)
        out: dict[str, Any] = {"period": p.label, "groups": summaries, "org_totals": None}
        if self.authz.read_all_groups(caller) is None:
            org = self.data.summary(p, None)
            out["org_totals"] = {
                "platforms": [
                    {"platform": x.platform.value, **totals_json(x.totals)} for x in org.platforms
                ],
                "totals": totals_json(org.totals),
            }
        return out

    def weekly(self, caller: User, group: str | None) -> Result:
        groups = self._readable_groups(caller, group)
        if isinstance(groups, Denial):
            return groups
        rows = [r for g in groups for r in self.data.weekly(group=g)]
        rows.sort(key=lambda r: (r.week, r.group, r.platform))
        return [
            {
                "week": r.week,
                "week_start": r.week_start.isoformat(),
                "group": r.group,
                "platform": r.platform.value,
                "total_tokens": r.totals.total_tokens,
                "cost_usd": usd(r.totals.cost_usd),
            }
            for r in rows
        ]

    def capabilities(self, caller: User, group: str | None) -> Result:
        groups = self._readable_groups(caller, group)
        if isinstance(groups, Denial):
            return groups
        out: list[dict[str, Any]] = []
        for g in groups:
            states = self.tools.capability_status(caller, g)
            if isinstance(states, dict):
                return Denial(str(states["reason"]))
            out.extend(states)
        return out

    def set_capability(self, caller: User, group: str, capability: str, enabled: bool) -> Result:
        out = self.tools.set_capability(caller, group, capability, enabled)
        if isinstance(out, dict) and out.get("error"):
            return Denial(str(out["reason"]), _scope(out.get("required_scope")))
        return out

    def audit(self, caller: User, group: str | None) -> Result:
        groups = self._readable_groups(caller, group)
        if isinstance(groups, Denial):
            return groups
        entries = [e for g in groups for e in self.authz.store.audit_log(g)]
        entries.sort(key=lambda e: e.seq, reverse=True)
        return [
            {
                "group": e.group,
                "capability": e.capability.value,
                "enabled": e.enabled,
                "changed_at": e.changed_at,
                "changed_by": e.changed_by,
            }
            for e in entries
        ]

    def ingest_report(self, caller: User) -> Result:
        """How the exports mapped. Org-wide, so only for callers who can read every group."""
        denied = self.authz.read_all_groups(caller)
        if denied is not None:
            return denied
        r = self.ingest
        return {
            "records": len(r.records),
            "rejected": [{"source": i.source, "reason": i.reason} for i in r.rejected],
            "warnings": [{"source": i.source, "reason": i.reason} for i in r.warnings],
            "data_window": {
                "start": self.data.data_start.isoformat(),
                "end_exclusive": self.data.data_end_exclusive.isoformat(),
            },
        }

    def _readable_groups(self, caller: User, group: str | None = None) -> list[str] | Denial:
        if group is not None:
            g = self.authz.read_group(caller, group)
            return g if isinstance(g, Denial) else [g]
        # No group named: everything the caller may read (needs the read scope first).
        first = self.authz.read_group(caller, None)
        if isinstance(first, Denial) and first.required_scope is not None:
            return first
        return self.authz.visible_groups(caller)


def _scope(value: Any) -> Scope | None:
    return Scope(value) if value else None
