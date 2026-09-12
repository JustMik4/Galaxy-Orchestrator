"""Canonical Galaxy V2 project declarations and repository boundary checks.

Canonical ``.yml`` files deliberately use JSON, which is a valid YAML subset.  This
keeps the bootstrap dependency-free and makes its serialization unambiguous.
"""

from __future__ import annotations

import hashlib
import json
import re
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
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectConfigurationError(f"missing project declaration: {path.name}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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


def load_project_config(path: Path) -> ProjectConfig:
    raw = _mapping(path)
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
    if pure.is_absolute() or _WINDOWS_ABSOLUTE.match(tracked_path) or ".." in pure.parts:
        raise ProjectConfigurationError("tracked vault path must be project-relative; external paths are operator-local")
    return ProjectConfig(schema, name.strip(), adapter, packs, names, vault, raw)


def load_team(path: Path) -> TeamConfig:
    raw = _mapping(path)
    schema = _schema(raw, "team")
    mode = raw.get("mode")
    if mode not in ("SOLO", "CO-OP"):
        raise ProjectConfigurationError("team.mode must be SOLO or CO-OP")
    if not isinstance(raw.get("operators", []), list):
        raise ProjectConfigurationError("team.operators must be a list")
    return TeamConfig(schema, mode, raw)


def load_checks(path: Path) -> ChecksConfig:
    raw = _mapping(path)
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


def load_lock(path: Path) -> GalaxyLock:
    raw = _mapping(path)
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


def load_project(root: Path | str) -> GalaxyProject:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ProjectConfigurationError(f"project root is not a directory: {root_path}")
    paths = {relative: root_path.joinpath(*PurePosixPath(relative).parts) for relative in DECLARATION_PATHS}
    snapshot = tuple((relative, path.read_bytes()) for relative, path in paths.items() if path.is_file())
    if len(snapshot) != len(paths):
        missing = sorted(set(paths).difference(relative for relative, _ in snapshot))
        raise ProjectConfigurationError(f"missing project declarations: {', '.join(missing)}")
    config = load_project_config(paths[".galaxy/project.yml"])
    team = load_team(paths[".galaxy/team.yml"])
    checks = load_checks(paths[".galaxy/checks.json"])
    lock = load_lock(paths["galaxy.lock"])
    declaration_bytes = dict(snapshot)
    for relative, expected_digest in lock.declaration_hashes.items():
        actual_digest = hashlib.sha256(declaration_bytes[relative]).hexdigest()
        if actual_digest != expected_digest:
            raise ProjectConfigurationError(
                f"project declaration does not match galaxy.lock: {relative}"
            )
    if config.schema_version != lock.project_schema:
        raise ProjectConfigurationError("project schema does not match galaxy.lock")
    if config.specialist_packs != lock.specialist_packs or config.specialist_names != lock.specialist_names:
        raise ProjectConfigurationError("selected specialists do not match galaxy.lock")
    return GalaxyProject(root_path, config, team, checks, lock, snapshot)


def declarations_unchanged(project: GalaxyProject) -> bool:
    for relative, expected in project.declaration_bytes:
        path = project.root.joinpath(*PurePosixPath(relative).parts)
        try:
            if path.read_bytes() != expected:
                return False
        except OSError:
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
