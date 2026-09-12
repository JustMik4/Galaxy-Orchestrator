"""Constrained Markdown/frontmatter parser for the Galaxy specialist schema.

This intentionally is not a general YAML parser. It accepts only scalar top-level
fields and lists of scalar values under ``galaxy``.
"""

import re

from .schema import Specialist


class SpecialistParseError(ValueError):
    """The document is outside the constrained or safe Galaxy schema."""


_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOP_FIELDS = frozenset({"name", "description", "galaxy"})
_GALAXY_LIST_FIELDS = frozenset(
    {"domains", "task_classes", "roles", "triggers", "paths"}
)
_GALAXY_SCALAR_FIELDS = frozenset({"pack"})
_FORBIDDEN = frozenset(
    {
        "model", "models", "effort", "reasoning_effort", "reasoning",
        "sandbox", "sandbox_mode", "scope", "scopes", "credentials",
        "credential", "network", "network_permissions", "delegation",
        "delegate", "integration", "integration_authority", "quota",
        "quota_thresholds", "browser_approval", "browser", "ownership",
        "owner", "permissions", "filesystem_permissions", "branch_protection",
    }
)
_BODY_AUTHORITY = re.compile(
    r"(?i)(?:^|[.!?;]\s*|\b)(?:[-*]\s*)?(?:(?:set|use|require|override|grant|allow|deny|choose|change|"
    r"configure|must\s+use|must\s+set)\s+(?:the\s+)?)?(?:model|reasoning(?:\s+effort)?|"
    r"effort|sandbox(?:\s+mode)?|scope|credentials?|network(?:\s+permissions?)?|"
    r"delegation|integration(?:\s+authority)?|quota(?:\s+thresholds?)?|"
    r"browser(?:\s+approval)?|ownership|filesystem\s+permissions?|branch\s+protection)"
    r"(?:\s*[:=]\s*|\s+(?:to|is|must|should|may|can|without)\b)"
    r"|\b(?:choose|select|pick|switch\s+to)\s+(?:the\s+)?(?:[a-z0-9_-]+\s+){0,3}models?\b"
    r"|\b(?:lower|raise|increase|reduce|decrease)\s+(?:the\s+)?(?:reasoning\s+)?effort\b"
    r"|\b(?:work|run|operate)\s+(?:inside|in|within)\s+(?:an?\s+)?(?:[a-z0-9_-]+\s+){0,2}sandbox\b"
    r"|\bnetwork[-\s]+enabled\s+sandbox\b"
    r"|\bdelegat(?:e|es|ed|ing)\s+(?:freely|tasks?|work|to\s+(?:agents?|workers?|subagents?)|"
    r"without\s+(?:approval|restriction|limits?))\b"
)


def _key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _scalar(value: str, line_number: int) -> str:
    value = value.strip()
    if not value:
        raise SpecialistParseError(f"line {line_number}: scalar value is required")
    quoted = value[0:1] in {"'", '"'}
    if quoted:
        if len(value) < 2 or value[-1] != value[0]:
            raise SpecialistParseError(f"line {line_number}: unterminated quoted scalar")
        value = value[1:-1]
    if not quoted and any(token in value for token in ("{", "}", "[", "]", "&", "*", "!")):
        raise SpecialistParseError(f"line {line_number}: complex YAML is not supported")
    value = value.strip()
    if not value:
        raise SpecialistParseError(f"line {line_number}: empty scalar is not allowed")
    return value


def _split(document: str) -> tuple[list[str], str]:
    normalized = document.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SpecialistParseError("specialist must start with YAML frontmatter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise SpecialistParseError("unterminated YAML frontmatter") from exc
    return lines[1:end], "\n".join(lines[end + 1 :]).strip()


def _parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], dict[str, object]]:
    top: dict[str, str] = {}
    galaxy: dict[str, object] = {}
    active_list: str | None = None
    in_galaxy = False
    galaxy_seen = False
    for number, raw in enumerate(lines, start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            raise SpecialistParseError(f"line {number}: tabs are not supported")
        text = raw.strip()
        if indent == 0:
            active_list = None
            in_galaxy = False
            if ":" not in text:
                raise SpecialistParseError(f"line {number}: expected key: value")
            raw_key, raw_value = text.split(":", 1)
            key = _key(raw_key)
            if key in _FORBIDDEN:
                raise SpecialistParseError(f"line {number}: forbidden authority field {raw_key!r}")
            if key not in _TOP_FIELDS:
                raise SpecialistParseError(f"line {number}: unknown field {raw_key!r}")
            if key == "galaxy":
                if galaxy_seen:
                    raise SpecialistParseError(f"line {number}: duplicate field 'galaxy'")
                if raw_value.strip():
                    raise SpecialistParseError(f"line {number}: galaxy must be a mapping")
                in_galaxy = True
                galaxy_seen = True
            else:
                if key in top:
                    raise SpecialistParseError(f"line {number}: duplicate field {key!r}")
                top[key] = _scalar(raw_value, number)
            continue
        if indent == 2 and in_galaxy:
            active_list = None
            if ":" not in text:
                raise SpecialistParseError(f"line {number}: expected Galaxy key")
            raw_key, raw_value = text.split(":", 1)
            key = _key(raw_key)
            if key in _FORBIDDEN:
                raise SpecialistParseError(f"line {number}: forbidden authority field {raw_key!r}")
            if key in _GALAXY_LIST_FIELDS:
                if raw_value.strip():
                    raise SpecialistParseError(f"line {number}: {key} must be a list")
                if key in galaxy:
                    raise SpecialistParseError(f"line {number}: duplicate field {key!r}")
                galaxy[key] = []
                active_list = key
            elif key in _GALAXY_SCALAR_FIELDS:
                if key in galaxy:
                    raise SpecialistParseError(f"line {number}: duplicate field {key!r}")
                galaxy[key] = _scalar(raw_value, number)
            else:
                raise SpecialistParseError(f"line {number}: unknown Galaxy field {raw_key!r}")
            continue
        if indent == 4 and in_galaxy and active_list and text.startswith("- "):
            values = galaxy[active_list]
            assert isinstance(values, list)
            values.append(_scalar(text[2:], number))
            continue
        raise SpecialistParseError(f"line {number}: unsupported YAML structure")
    if not galaxy_seen:
        raise SpecialistParseError("missing required field: galaxy")
    return top, galaxy


def _normalized(values: object) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(sorted({str(item).strip().lower() for item in values if str(item).strip()}))


def _sanitize_body(body: str) -> tuple[str, tuple[str, ...]]:
    kept: list[str] = []
    warnings: list[str] = []
    for number, line in enumerate(body.splitlines(), start=1):
        if _BODY_AUTHORITY.search(line):
            warnings.append(f"body line {number}: stripped authority directive")
        else:
            kept.append(line.rstrip())
    return "\n".join(kept).strip(), tuple(warnings)


def parse_specialist(document: str, *, source: str = "builtin") -> Specialist:
    frontmatter, body = _split(document)
    top, galaxy = _parse_frontmatter(frontmatter)
    missing = {"name", "description"}.difference(top)
    if missing:
        raise SpecialistParseError(f"missing required fields: {', '.join(sorted(missing))}")
    name = top["name"].lower()
    if not _NAME.fullmatch(name):
        raise SpecialistParseError("name must be lowercase kebab-case")
    safe_body, warnings = _sanitize_body(body)
    pack = str(galaxy.get("pack", "core")).strip().lower()
    if not _NAME.fullmatch(pack):
        raise SpecialistParseError("pack must be lowercase kebab-case")
    return Specialist(
        name=name,
        description=top["description"].strip(),
        domains=_normalized(galaxy.get("domains")),
        task_classes=_normalized(galaxy.get("task_classes")),
        roles=_normalized(galaxy.get("roles")),
        triggers=_normalized(galaxy.get("triggers")),
        paths=tuple(sorted({str(item).strip() for item in galaxy.get("paths", [])})),
        pack=pack,
        body=safe_body,
        source=source,
        warnings=warnings,
    )
