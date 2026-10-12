"""The user and group directory: stands in for the identity provider."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Role(StrEnum):
    MEMBER = "member"
    TEAM_LEAD = "team_lead"
    PLATFORM_ADMIN = "platform_admin"


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    name: str
    role: Role
    group_ids: tuple[str, ...]


@dataclass(frozen=True)
class Group:
    group_id: str
    name: str
    team_lead_user_id: str
    claude_rbac_group_id: str
    gcp_project_id: str


class Directory:
    """Lookups from every platform identifier back to directory users and groups."""

    def __init__(self, users: list[User], groups: list[Group]) -> None:
        self.users = {u.user_id: u for u in users}
        self.groups = {g.group_id: g for g in groups}
        self._by_email = {u.email.casefold(): u for u in users}
        self._by_rbac = {g.claude_rbac_group_id: g for g in groups}
        self._by_project = {g.gcp_project_id: g for g in groups}

    @classmethod
    def load(cls, directory_dir: Path) -> Directory:
        users_doc = json.loads((directory_dir / "users.json").read_text())
        groups_doc = json.loads((directory_dir / "groups.json").read_text())
        users = [
            User(
                user_id=u["user_id"],
                email=u["email"],
                name=u["name"],
                role=Role(u["role"]),
                group_ids=tuple(u["group_ids"]),
            )
            for u in users_doc["users"]
        ]
        groups = [
            Group(
                group_id=g["group_id"],
                name=g["name"],
                team_lead_user_id=g["team_lead_user_id"],
                claude_rbac_group_id=g["claude_rbac_group_id"],
                gcp_project_id=g["gcp_project_id"],
            )
            for g in groups_doc["groups"]
        ]
        return cls(users, groups)

    def user_by_id(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    def user_by_email(self, email: str) -> User | None:
        return self._by_email.get(email.strip().casefold())

    def group_by_rbac_id(self, rbac_group_id: str) -> Group | None:
        return self._by_rbac.get(rbac_group_id)

    def group_by_project(self, project_id: str) -> Group | None:
        return self._by_project.get(project_id)
