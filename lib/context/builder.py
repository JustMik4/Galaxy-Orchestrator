"""Reference de-duplication and progressive specialist disclosure."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable, Mapping

from .policy import ContextEconomyPolicy


@dataclass(frozen=True)
class ContextBundle:
    inline: tuple[str, ...]
    references: tuple[str, ...]
    specialist_index: tuple[Mapping[str, object], ...] = ()


class ContextReferenceRegistry:
    def __init__(self) -> None:
        self._references: dict[str, str] = {}

    def add(self, content: str, reference: str) -> bool:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        reused = digest in self._references
        self._references.setdefault(digest, reference)
        return reused

    def reference_for(self, content: str) -> str | None:
        return self._references.get(hashlib.sha256(content.encode("utf-8")).hexdigest())


class ContextBuilder:
    def __init__(self, policy: ContextEconomyPolicy, registry: ContextReferenceRegistry | None = None):
        self.policy = policy
        self.registry = registry or ContextReferenceRegistry()

    def build(self, items: Iterable[tuple[str, str]], *, specialist_catalog=(), selected_specialists=()) -> ContextBundle:
        inline: list[str] = []
        references: list[str] = []
        for content, reference in items:
            known = self.registry.reference_for(content)
            if self.policy.prefer_references and known is not None:
                references.append(known)
            else:
                inline.append(content)
                self.registry.add(content, reference)
        selected = frozenset(selected_specialists)
        if not self.policy.progressive_disclosure:
            index = tuple(specialist_catalog)
        else:
            index = tuple(
                item for item in specialist_catalog
                if str(item.get("name", "")) in selected
            )
        return ContextBundle(tuple(inline), tuple(dict.fromkeys(references)), index)
