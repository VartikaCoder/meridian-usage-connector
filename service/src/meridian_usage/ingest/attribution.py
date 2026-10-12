"""The one rule for turning a platform row into (user, group), shared by both platforms.

The group comes from the platform's own identifier (Claude RBAC group, GCP project), because
that is where the usage ran and who pays for it. It must agree with the directory: the user
has to be a member of that group. If not, the row is rejected rather than reassigned, because
silently moving spend between teams is the worst failure this service could have.
"""

from __future__ import annotations

from meridian_usage.directory import Group, User


class RowError(ValueError):
    """A source row that cannot be mapped; it becomes a rejection, never a guess."""


def attribute(
    *,
    user: User | None,
    user_ref: str,
    group: Group | None,
    group_ref: str,
) -> tuple[User, Group]:
    if user is None:
        raise RowError(f"{user_ref} is not in the directory")
    if group is None:
        raise RowError(f"{group_ref} does not map to a directory group")
    if group.group_id not in user.group_ids:
        raise RowError(
            f"{user.email} is not a member of {group.group_id} ({group_ref}); "
            f"directory groups: {', '.join(user.group_ids) or 'none'}"
        )
    return user, group
