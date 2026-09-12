"""Deterministic local catalog, ranking, and bounded planning context."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Iterable

from .schema import Resource, ResourceValidationError


_TOKEN = re.compile(r"[a-z0-9]+")


def _key(resource: Resource) -> tuple[str, str, str]:
    return (resource.category.casefold(), resource.name.casefold(), resource.source.casefold())


def _tokens(value: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(value.casefold()))


class ResourceCatalog:
    def __init__(self, resources: Iterable[Resource]) -> None:
        supplied = tuple(resources)
        if any(not isinstance(item, Resource) for item in supplied):
            raise ResourceValidationError("catalog entries must be Resource values")
        ordered = sorted(supplied, key=_key)
        keys = [_key(item) for item in ordered]
        if len(keys) != len(set(keys)):
            raise ResourceValidationError("catalog contains duplicate category/name/source entries")
        self.resources = tuple(ordered)

    @classmethod
    def from_mapping(cls, value: object) -> "ResourceCatalog":
        if not isinstance(value, dict) or set(value) != {"schema_version", "resources"}:
            raise ResourceValidationError("catalog requires only schema_version and resources")
        if value["schema_version"] != 1 or not isinstance(value["resources"], list):
            raise ResourceValidationError("unsupported resource catalog schema")
        return cls(Resource.from_mapping(item) for item in value["resources"])

    def to_mapping(self) -> dict[str, object]:
        return {"schema_version": 1, "resources": [item.to_mapping() for item in self.resources]}

    def to_json(self) -> str:
        return json.dumps(self.to_mapping(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        limit: int = 5,
        verified_only: bool = False,
    ) -> tuple[Resource, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 20:
            raise ValueError("limit must be an integer from 1 to 20")
        query_normalized = " ".join(query.casefold().split())
        query_tokens = _tokens(query_normalized)
        ranked: list[tuple[int, tuple[str, str, str], Resource]] = []
        for resource in self.resources:
            if category is not None and resource.category.casefold() != category.casefold():
                continue
            if verified_only and resource.verification_status != "verified":
                continue
            name = resource.name.casefold()
            name_tokens = _tokens(resource.name)
            category_tokens = _tokens(resource.category)
            description_tokens = _tokens(resource.description)
            score = 0
            if query_normalized == name:
                score += 100
            elif query_normalized in name:
                score += 60
            score += 20 * len(query_tokens & name_tokens)
            score += 8 * len(query_tokens & category_tokens)
            score += 4 * len(query_tokens & description_tokens)
            if score == 0:
                continue
            if resource.verification_status == "verified":
                score += 2
            ranked.append((-score, _key(resource), resource))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in ranked[:limit])


def load_catalog(path: str | Path) -> ResourceCatalog:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResourceValidationError("cannot load resource catalog: " + str(exc)) from exc
    return ResourceCatalog.from_mapping(value)


def build_context(
    resources: Iterable[Resource], *, max_items: int = 5, max_chars: int = 2000
) -> str:
    if not isinstance(max_items, int) or isinstance(max_items, bool) or not 1 <= max_items <= 20:
        raise ValueError("max_items must be an integer from 1 to 20")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 120:
        raise ValueError("max_chars must be at least 120")
    header = "Resource candidates (planning only; verify official documentation):"
    lines = [header]
    for resource in tuple(resources)[:max_items]:
        line = (
            f"- {resource.category} | {resource.name} | {resource.description} | "
            f"auth={resource.auth_type} | https={str(resource.https).lower()} | "
            f"verification={resource.verification_status} | docs={resource.official_docs_location}"
        )
        candidate = "\n".join([*lines, line])
        if len(candidate) > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)
