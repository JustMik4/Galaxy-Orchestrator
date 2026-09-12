"""Offline discovery importer for the public-apis Markdown table format."""

from __future__ import annotations

from pathlib import Path
import re

from .schema import Resource, ResourceValidationError


_LINK = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
_HEADING = re.compile(r"^###\s+(.+?)\s*$")


def _auth(value: str) -> str:
    normalized = value.strip().strip("`").casefold().replace("-", "").replace("_", "")
    if normalized in {"", "no", "none", "null"}:
        return "none"
    if normalized in {"apikey", "key"}:
        return "api_key"
    if normalized in {"oauth", "oauth2"}:
        return "oauth2"
    if normalized in {"basic", "basicauth"}:
        return "basic"
    if normalized in {"unknown", "?"}:
        return "unknown"
    return "other"


def _content(source: str | Path) -> str:
    if isinstance(source, Path):
        try:
            return source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ResourceValidationError("cannot read local public-apis source: " + str(exc)) from exc
    if not isinstance(source, str):
        raise ResourceValidationError("public-apis source must be local text or a local Path")
    if re.match(r"(?i)^\s*(?:https?|ftp)://", source):
        raise ResourceValidationError("network locations are forbidden; provide local content or a local file")
    if "\n" not in source and "\r" not in source:
        candidate = Path(source)
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ResourceValidationError("cannot read local public-apis source: " + str(exc)) from exc
    return source


def import_public_apis(
    source: str | Path, *, source_commit: str
) -> tuple[Resource, ...]:
    """Extract compact candidates without fetching or retaining README prose."""
    if not isinstance(source_commit, str) or not source_commit.strip():
        raise ResourceValidationError("source_commit is required for imported candidates")
    text = _content(source)
    category: str | None = None
    resources: list[Resource] = []
    for raw_line in text.splitlines():
        heading = _HEADING.match(raw_line.strip())
        if heading:
            category = heading.group(1).strip()
            continue
        line = raw_line.strip()
        if category is None or not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) < 4 or cells[0].casefold() == "api" or set(cells[0]) <= {"-", ":", " "}:
            continue
        match = _LINK.match(cells[0])
        if not match:
            continue
        name, location = match.groups()
        https = cells[3].strip().casefold() in {"yes", "true", "https"}
        resources.append(Resource(
            category=category,
            name=name,
            description=cells[1],
            auth_type=_auth(cells[2]),
            https=https,
            source="public-apis/public-apis",
            source_commit=source_commit.strip(),
            verification_status="unverified",
            official_docs_location=location,
        ))
    return tuple(resources)
