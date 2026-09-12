"""Immutable normalized models for Galaxy specialist guidance."""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, order=True)
class Specialist:
    name: str
    description: str
    domains: tuple[str, ...]
    task_classes: tuple[str, ...]
    roles: tuple[str, ...]
    triggers: tuple[str, ...]
    paths: tuple[str, ...]
    pack: str
    body: str
    source: str = "builtin"
    warnings: tuple[str, ...] = ()


class SelectionMode(str, Enum):
    AUTO = "auto"
    SHORTLIST = "shortlist"
    GENERIC = "generic"


@dataclass(frozen=True)
class SelectionEvidence:
    specialist: str
    score: float
    matches: tuple[str, ...]


@dataclass(frozen=True)
class SpecialistSelection:
    mode: SelectionMode
    recommended: str | None
    confidence: float
    shortlist: tuple[str, ...]
    alternatives: tuple[str, ...]
    evidence: tuple[SelectionEvidence, ...]

