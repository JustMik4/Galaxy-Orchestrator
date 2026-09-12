"""Offline discovery importer for the public-apis Markdown table format."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from .schema import Resource, ResourceValidationError


_LINK = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
_HEADING = re.compile(r"^###\s+(.+?)\s*$")
_REMOTE_SCHEME = re.compile(r"(?i)^[a-z][a-z0-9+.-]*://")
_WINDOWS_DRIVE = re.compile(r"(?i)^[a-z]:[\\/]")
_PATH_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


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


def _reject_remote_path(value: str) -> None:
    candidate = value.strip()
    normalized = candidate.replace("/", "\\")
    if _REMOTE_SCHEME.match(candidate) or normalized.startswith("\\\\"):
        raise ResourceValidationError("network locations are forbidden; provide local content or a local file")


def _looks_like_path(value: str) -> bool:
    if value != value.strip():
        return False
    if _WINDOWS_DRIVE.match(value) or value.startswith(("/", "./", "../", ".\\", "..\\")):
        return True
    return Path(value).suffix.casefold() in _PATH_SUFFIXES


def _safe_local_file(value: str | Path) -> str:
    raw = str(value)
    _reject_remote_path(raw)
    if raw != raw.strip() or not raw:
        raise ResourceValidationError("local public-apis path must be non-empty without outer whitespace")
    if re.match(r"(?i)^[a-z]:", raw) and not _WINDOWS_DRIVE.match(raw):
        raise ResourceValidationError("drive-relative public-apis paths are forbidden")
    candidate = Path(raw)
    try:
        absolute = Path(os.path.abspath(candidate))
        current = Path(absolute.anchor)
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT:
            raise ResourceValidationError(
                "local public-apis path must not contain symlink or reparse points"
            )
        for component in absolute.parts[1:]:
            current /= component
            metadata = current.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & _REPARSE_POINT:
                raise ResourceValidationError(
                    "local public-apis path must not contain symlink or reparse points"
                )
        if not stat.S_ISREG(metadata.st_mode):
            raise ResourceValidationError("local public-apis source must be a regular file")
        descriptor = os.open(
            absolute,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            return stream.read()
    except ResourceValidationError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ResourceValidationError("cannot read local public-apis source: " + str(exc)) from exc


def _content(source: str | Path) -> str:
    if isinstance(source, Path):
        return _safe_local_file(source)
    if not isinstance(source, str):
        raise ResourceValidationError("public-apis source must be local text or a local Path")
    stripped = source.strip()
    if _REMOTE_SCHEME.match(stripped) or stripped.replace("/", "\\").startswith("\\\\"):
        _reject_remote_path(source)
    if "\n" not in source and "\r" not in source and _looks_like_path(source):
        return _safe_local_file(source)
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
