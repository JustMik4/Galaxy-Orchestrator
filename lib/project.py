"""Canonical Galaxy V2 project declarations and repository boundary checks.

Canonical ``.yml`` files deliberately use JSON, which is a valid YAML subset.  This
keeps the bootstrap dependency-free and makes its serialization unambiguous.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class ProjectConfigurationError(ValueError):
    """A tracked project declaration is missing, invalid, or unsafe."""


DECLARATION_PATHS = (
    "AGENTS.md",
    ".galaxy/project.yml",
    ".galaxy/team.yml",
    ".galaxy/checks.json",
    "galaxy.lock",
)

LOCKED_DECLARATION_PATHS = (
    "AGENTS.md",
    ".galaxy/project.yml",
    ".galaxy/team.yml",
    ".galaxy/checks.json",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

LOCAL_IGNORE_PATHS = (
    ".codex/",
    ".galaxy/local/",
    ".galaxy/runtime/",
    ".galaxy/cache/",
    ".galaxy/install/",
)

_SENSITIVE_KEYS = re.compile(
    r"(?i)(?:^|[_-])(password|passwd|secret|token|credential|api[_-]?key|account[_-]?id)(?:$|[_-])"
)
_WINDOWS_ABSOLUTE = re.compile(r"(?i)^[a-z]:[\\/]")
_GENERATED_PREFIXES = (
    ".codex/",
    ".galaxy/local/",
    ".galaxy/runtime/",
    ".galaxy/cache/",
    ".galaxy/install/",
    ".agents/skills/galaxy/",
)
_LEGACY_PREFIXES = (
    ".agents/skills/multicontroller/",
    ".multicontroller/tools/",
    ".multicontroller/examples/",
    ".multicontroller/messages/",
)
_LEGACY_FILES = frozenset({".multicontroller/install-manifest.json"})


@dataclass(frozen=True)
class ProjectConfig:
    schema_version: int
    name: str
    adapter: str
    specialist_packs: tuple[str, ...]
    specialist_names: tuple[str, ...]
    vault: Mapping[str, Any]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class TeamConfig:
    schema_version: int
    mode: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class ChecksConfig:
    schema_version: int
    commands: tuple[tuple[str, ...], ...]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class GalaxyLock:
    schema_version: int
    galaxy_version: str
    source_revision: str
    catalog_revision: str
    specialist_packs: tuple[str, ...]
    specialist_names: tuple[str, ...]
    adapter_schema: int
    project_schema: int
    migration_schema: int
    declaration_hashes: Mapping[str, str]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class GalaxyProject:
    root: Path
    config: ProjectConfig
    team: TeamConfig
    checks: ChecksConfig
    lock: GalaxyLock
    declaration_bytes: tuple[tuple[str, bytes], ...]

    @property
    def selected_packs(self) -> tuple[str, ...]:
        return self.lock.specialist_packs

    @property
    def selected_specialists(self) -> tuple[str, ...]:
        return self.lock.specialist_names


@dataclass(frozen=True)
class PollutionReport:
    git_available: bool
    tracked_generated: tuple[str, ...]
    tracked_legacy: tuple[str, ...]
    error: str | None = None

    @property
    def polluted(self) -> bool:
        return bool(self.tracked_generated or self.tracked_legacy)


def _mapping(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ProjectConfigurationError(f"missing project declaration: {path.name}") from exc
    except OSError as exc:
        raise ProjectConfigurationError(f"invalid JSON-compatible YAML in {path}: {exc}") from exc
    return _mapping_bytes(data, path)


def _mapping_bytes(data: bytes, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectConfigurationError(f"invalid JSON-compatible YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectConfigurationError(f"project declaration must be an object: {path}")
    return value


def _schema(value: Mapping[str, Any], label: str) -> int:
    result = value.get("schema_version")
    if not isinstance(result, int) or isinstance(result, bool) or result < 1:
        raise ProjectConfigurationError(f"{label}.schema_version must be a positive integer")
    return result


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ProjectConfigurationError(f"{label} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ProjectConfigurationError(f"{label} contains duplicates")
    return tuple(sorted(value))


def _validate_lock_safety(value: Any, location: str = "galaxy.lock") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProjectConfigurationError("galaxy.lock keys must be strings")
            if _SENSITIVE_KEYS.search(key):
                raise ProjectConfigurationError(f"galaxy.lock must not contain sensitive field: {location}.{key}")
            _validate_lock_safety(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_lock_safety(child, f"{location}[{index}]")
    elif isinstance(value, str):
        normalized = value.replace("\\", "/")
        if (
            _WINDOWS_ABSOLUTE.match(value)
            or normalized.startswith("/")
            or normalized.startswith("file://")
            or "../" in normalized
            or normalized.startswith("./")
        ):
            raise ProjectConfigurationError(f"galaxy.lock must not contain a local path: {location}")


def _parse_project_config(raw: Mapping[str, Any]) -> ProjectConfig:
    schema = _schema(raw, "project")
    name = raw.get("name", "project")
    adapter = raw.get("adapter", "codex")
    if not isinstance(name, str) or not name.strip():
        raise ProjectConfigurationError("project.name must be a non-empty string")
    if adapter != "codex":
        raise ProjectConfigurationError(f"unsupported project adapter: {adapter!r}")
    specialists = raw.get("specialists", {})
    if not isinstance(specialists, dict):
        raise ProjectConfigurationError("project.specialists must be an object")
    packs = _strings(specialists.get("packs", []), "project.specialists.packs")
    names = _strings(specialists.get("names", []), "project.specialists.names")
    vault = raw.get("vault", {"enabled": False})
    if not isinstance(vault, dict) or not isinstance(vault.get("enabled", False), bool):
        raise ProjectConfigurationError("project.vault must be an object with boolean enabled")
    mode = vault.get("mode", "projection")
    if mode not in ("projection", "project-owned"):
        raise ProjectConfigurationError("project.vault.mode must be projection or project-owned")
    tracked_path = vault.get("path", ".galaxy/vault")
    if not isinstance(tracked_path, str) or not tracked_path:
        raise ProjectConfigurationError("project.vault.path must be a non-empty project-relative string")
    pure = PurePosixPath(tracked_path.replace("\\", "/"))
    if (
        pure.is_absolute()
        or not pure.parts
        or _WINDOWS_ABSOLUTE.match(tracked_path)
        or ".." in pure.parts
    ):
        raise ProjectConfigurationError("tracked vault path must be project-relative; external paths are operator-local")
    return ProjectConfig(schema, name.strip(), adapter, packs, names, vault, raw)


def load_project_config(path: Path) -> ProjectConfig:
    return _parse_project_config(_mapping(path))


def _parse_team(raw: Mapping[str, Any]) -> TeamConfig:
    schema = _schema(raw, "team")
    mode = raw.get("mode")
    if mode not in ("SOLO", "CO-OP"):
        raise ProjectConfigurationError("team.mode must be SOLO or CO-OP")
    if not isinstance(raw.get("operators", []), list):
        raise ProjectConfigurationError("team.operators must be a list")
    return TeamConfig(schema, mode, raw)


def load_team(path: Path) -> TeamConfig:
    return _parse_team(_mapping(path))


def _parse_checks(raw: Mapping[str, Any]) -> ChecksConfig:
    schema = _schema(raw, "checks")
    commands = raw.get("commands", [])
    if not isinstance(commands, list):
        raise ProjectConfigurationError("checks.commands must be a list")
    normalized = []
    for command in commands:
        if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command):
            raise ProjectConfigurationError("each project check must be a non-empty argument array")
        normalized.append(tuple(command))
    return ChecksConfig(schema, tuple(normalized), raw)


def load_checks(path: Path) -> ChecksConfig:
    return _parse_checks(_mapping(path))


def _parse_lock(raw: Mapping[str, Any]) -> GalaxyLock:
    _validate_lock_safety(raw)
    schema = _schema(raw, "lock")
    galaxy = raw.get("galaxy")
    specialists = raw.get("specialists")
    adapters = raw.get("adapters")
    if not all(isinstance(item, dict) for item in (galaxy, specialists, adapters)):
        raise ProjectConfigurationError("lock requires galaxy, specialists, and adapters objects")
    version = galaxy.get("version")
    revision = galaxy.get("source_revision")
    catalog_revision = specialists.get("catalog_revision")
    for label, value in (
        ("galaxy.version", version),
        ("galaxy.source_revision", revision),
        ("specialists.catalog_revision", catalog_revision),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ProjectConfigurationError(f"lock.{label} must be a non-empty string")
    adapter_schema = adapters.get("schema_version")
    project_schema = raw.get("project_schema")
    migration_schema = raw.get("migration_schema")
    declarations = raw.get("declarations")
    for label, value in (
        ("adapters.schema_version", adapter_schema),
        ("project_schema", project_schema),
        ("migration_schema", migration_schema),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ProjectConfigurationError(f"lock.{label} must be a positive integer")
    if not isinstance(declarations, dict):
        raise ProjectConfigurationError("lock.declarations must be an object")
    if set(declarations) != set(LOCKED_DECLARATION_PATHS):
        raise ProjectConfigurationError(
            "lock.declarations must contain exactly: "
            + ", ".join(LOCKED_DECLARATION_PATHS)
        )
    for relative, declaration_digest in declarations.items():
        if not isinstance(declaration_digest, str) or not _SHA256.fullmatch(declaration_digest):
            raise ProjectConfigurationError(
                f"lock.declarations[{relative!r}] must be a lowercase SHA-256 digest"
            )
    return GalaxyLock(
        schema, version, revision, catalog_revision,
        _strings(specialists.get("packs", []), "lock.specialists.packs"),
        _strings(specialists.get("names", []), "lock.specialists.names"),
        adapter_schema, project_schema, migration_schema, dict(declarations), raw,
    )


def load_lock(path: Path) -> GalaxyLock:
    return _parse_lock(_mapping(path))


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & reparse)
        or bool(junction and junction())
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProjectConfigurationError(f"cannot inspect project boundary: {path}: {exc}") from exc


def _checked_project_root(value: Path | str) -> Path:
    """Return an absolute lexical root after checking every ancestor without following links."""
    root = Path(os.path.abspath(os.fspath(value)))
    for candidate in (*reversed(root.parents), root):
        info = _lstat(candidate)
        if info is not None and _is_link_or_reparse(candidate, info):
            raise ProjectConfigurationError(
                "link/reparse point is not a project boundary: " + str(candidate)
            )
    info = _lstat(root)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise ProjectConfigurationError(f"project root is not a directory: {root}")
    return root


def _declaration_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ProjectConfigurationError("unsafe project declaration path: " + relative)
    target = root.joinpath(*pure.parts)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ProjectConfigurationError(
            "project declaration escapes project: " + relative
        ) from exc
    current = root
    for part in pure.parts:
        current /= part
        info = _lstat(current)
        if info is not None and _is_link_or_reparse(current, info):
            raise ProjectConfigurationError(
                "link/reparse point is not a declaration path: " + relative
            )
    return target


def _read_regular_declaration(root: Path, relative: str) -> bytes:
    path = _declaration_path(root, relative)
    before = _lstat(path)
    if before is None:
        raise ProjectConfigurationError("missing project declaration: " + relative)
    if _is_link_or_reparse(path, before) or not stat.S_ISREG(before.st_mode):
        raise ProjectConfigurationError("project declaration is not a safe regular file: " + relative)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ProjectConfigurationError(
            f"cannot read project declaration {relative}: {exc}"
        ) from exc
    after = _lstat(path)
    if (
        after is None
        or _is_link_or_reparse(path, after)
        or not stat.S_ISREG(after.st_mode)
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ProjectConfigurationError("project declaration changed concurrently: " + relative)
    return data


def load_project(root: Path | str) -> GalaxyProject:
    root_path = _checked_project_root(root)
    paths = {relative: _declaration_path(root_path, relative) for relative in DECLARATION_PATHS}
    snapshot = tuple(
        (relative, _read_regular_declaration(root_path, relative))
        for relative in DECLARATION_PATHS
    )
    declaration_bytes = dict(snapshot)
    lock = _parse_lock(_mapping_bytes(declaration_bytes["galaxy.lock"], paths["galaxy.lock"]))
    for relative, expected_digest in lock.declaration_hashes.items():
        actual_digest = hashlib.sha256(declaration_bytes[relative]).hexdigest()
        if actual_digest != expected_digest:
            raise ProjectConfigurationError(
                f"project declaration does not match galaxy.lock: {relative}"
            )
    config = _parse_project_config(_mapping_bytes(
        declaration_bytes[".galaxy/project.yml"], paths[".galaxy/project.yml"]
    ))
    team = _parse_team(_mapping_bytes(
        declaration_bytes[".galaxy/team.yml"], paths[".galaxy/team.yml"]
    ))
    checks = _parse_checks(_mapping_bytes(
        declaration_bytes[".galaxy/checks.json"], paths[".galaxy/checks.json"]
    ))
    if config.schema_version != lock.project_schema:
        raise ProjectConfigurationError("project schema does not match galaxy.lock")
    if config.specialist_packs != lock.specialist_packs or config.specialist_names != lock.specialist_names:
        raise ProjectConfigurationError("selected specialists do not match galaxy.lock")
    return GalaxyProject(root_path, config, team, checks, lock, snapshot)


def declarations_unchanged(project: GalaxyProject) -> bool:
    for relative, expected in project.declaration_bytes:
        try:
            if _read_regular_declaration(project.root, relative) != expected:
                return False
        except ProjectConfigurationError:
            return False
    return True


def project_pollution(root: Path | str) -> PollutionReport:
    """Report tracked generated/legacy paths without modifying the repository."""
    root_path = Path(root).resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(root_path), "ls-files", "-z", "--", "."],
            check=False, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PollutionReport(False, (), (), str(exc))
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        return PollutionReport(False, (), (), error or "not a Git work tree")
    tracked = tuple(
        item.replace("\\", "/")
        for item in result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        if item
    )
    generated = tuple(sorted(path for path in tracked if path.startswith(_GENERATED_PREFIXES)))
    legacy = tuple(sorted(
        path for path in tracked
        if path in _LEGACY_FILES or path.startswith(_LEGACY_PREFIXES)
    ))
    return PollutionReport(True, generated, legacy)


__all__ = [
    "ChecksConfig", "DECLARATION_PATHS", "GalaxyLock", "GalaxyProject",
    "LOCKED_DECLARATION_PATHS",
    "LOCAL_IGNORE_PATHS", "PollutionReport", "ProjectConfig",
    "ProjectConfigurationError", "TeamConfig", "declarations_unchanged",
    "load_checks", "load_lock", "load_project", "load_project_config",
    "load_team", "project_pollution",
]
