"""Validation helpers kept separate from parsing for adapter callers."""

from .schema import Specialist


ALLOWED_ROLES = frozenset(
    {"root", "explorer", "researcher", "worker", "hard-worker", "tester", "reviewer", "architect"}
)


def validate_specialist(specialist: Specialist) -> Specialist:
    unknown_roles = set(specialist.roles).difference(ALLOWED_ROLES)
    if unknown_roles:
        raise ValueError(f"unknown specialist roles: {', '.join(sorted(unknown_roles))}")
    if not specialist.body:
        raise ValueError("specialist guidance body must not be empty")
    return specialist

