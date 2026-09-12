"""Cold catalog and deterministic compact index."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .parser import parse_specialist
from .schema import Specialist
from .validator import validate_specialist


@dataclass(frozen=True)
class HotSet:
    specialists: tuple[Specialist, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.specialists)


class SpecialistCatalog:
    """A cold catalog: bodies remain local and are loaded only on explicit use."""

    def __init__(self, specialists: Iterable[Specialist]) -> None:
        ordered = tuple(sorted(specialists, key=lambda item: item.name))
        if len({item.name for item in ordered}) != len(ordered):
            raise ValueError("duplicate specialist name")
        self._specialists = ordered
        self._by_name = {item.name: item for item in ordered}

    @classmethod
    def from_directory(cls, root: Path) -> "SpecialistCatalog":
        specialists = []
        for path in sorted(root.rglob("SKILL.md"), key=lambda item: item.as_posix().lower()):
            relative = path.relative_to(root).as_posix()
            item = parse_specialist(path.read_text(encoding="utf-8"), source=f"builtin:{relative}")
            specialists.append(validate_specialist(item))
        return cls(specialists)

    @property
    def specialists(self) -> tuple[Specialist, ...]:
        return self._specialists

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self._specialists)

    def get(self, name: str) -> Specialist:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"unknown specialist: {name}") from exc

    def hot_set(self, *, packs: Iterable[str] = (), names: Iterable[str] = ()) -> HotSet:
        requested_names = frozenset(names)
        requested_packs = frozenset(packs)
        unknown = requested_names.difference(self._by_name)
        if unknown:
            raise KeyError(f"unknown specialists: {', '.join(sorted(unknown))}")
        selected = tuple(
            item for item in self._specialists
            if item.name in requested_names or item.pack in requested_packs
        )
        return HotSet(selected)


def build_index(catalog: SpecialistCatalog) -> str:
    entries = []
    for item in catalog.specialists:
        normalized_hash = hashlib.sha256(
            (item.name + "\n" + item.description + "\n" + item.body).encode("utf-8")
        ).hexdigest()
        entries.append(
            {
                "description": item.description,
                "domains": list(item.domains),
                "hash": normalized_hash,
                "name": item.name,
                "pack": item.pack,
                "paths": list(item.paths),
                "roles": list(item.roles),
                "task_classes": list(item.task_classes),
                "triggers": list(item.triggers),
            }
        )
    return json.dumps(
        {"schema_version": 1, "specialists": entries},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

