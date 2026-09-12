"""Compact, authority-free metadata for external planning resources."""

from __future__ import annotations

from dataclasses import dataclass, replace
import ipaddress
import re
from typing import Any, Mapping
from urllib.parse import urlsplit


class ResourceValidationError(ValueError):
    """Resource metadata is incomplete, unsafe, or outside the compact schema."""


AUTH_TYPES = frozenset({"none", "api_key", "oauth2", "basic", "unknown", "other"})
VERIFICATION_STATUSES = frozenset({"unverified", "verified", "rejected"})
_FIELDS = frozenset({
    "category", "name", "description", "auth_type", "https", "source",
    "source_commit", "verification_status", "official_docs_location",
})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ResourceValidationError(f"{field} must be a string")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum or _CONTROL.search(normalized):
        raise ResourceValidationError(f"{field} must contain 1..{maximum} safe characters")
    return normalized


def _docs(value: Any, *, verified: bool) -> str:
    if not isinstance(value, str):
        raise ResourceValidationError("official_docs_location must be a string")
    if value != value.strip() or any(character.isspace() for character in value) or "\\" in value:
        raise ResourceValidationError("official_docs_location contains unsafe URL characters")
    location = _text(value, "official_docs_location", 500)
    requirement = "an HTTPS URL" if verified else "an HTTP(S) URL"
    try:
        parsed = urlsplit(location)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except (ValueError, UnicodeError) as exc:
        raise ResourceValidationError(
            f"official_docs_location must be {requirement}"
        ) from exc
    if parsed.scheme not in ({"https"} if verified else {"https", "http"}) or not parsed.netloc:
        raise ResourceValidationError(f"official_docs_location must be {requirement}")
    if username is not None or password is not None:
        raise ResourceValidationError("official_docs_location must not contain credentials")
    if hostname is None or not hostname or port is not None and not 1 <= port <= 65535:
        raise ResourceValidationError("official_docs_location has an invalid host or port")
    try:
        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
        else:
            try:
                ipaddress.IPv4Address(hostname)
            except ValueError:
                if len(hostname) > 253 or hostname.startswith(".") or hostname.endswith("."):
                    raise ValueError("invalid DNS hostname")
                if re.fullmatch(r"[0-9.]+", hostname):
                    raise ValueError("invalid IPv4 hostname")
                labels = hostname.split(".")
                if any(not _DNS_LABEL.fullmatch(label) for label in labels):
                    raise ValueError("invalid DNS hostname")
    except (ValueError, UnicodeError) as exc:
        raise ResourceValidationError("official_docs_location has an invalid host") from exc
    if parsed.scheme != parsed.scheme.lower():
        requirement = "an HTTPS URL" if verified else "an HTTP(S) URL"
        raise ResourceValidationError(f"official_docs_location must be {requirement}")
    return location


@dataclass(frozen=True)
class Resource:
    category: str
    name: str
    description: str
    auth_type: str
    https: bool
    source: str
    source_commit: str
    verification_status: str
    official_docs_location: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _text(self.category, "category", 80))
        object.__setattr__(self, "name", _text(self.name, "name", 120))
        object.__setattr__(self, "description", _text(self.description, "description", 300))
        object.__setattr__(self, "source", _text(self.source, "source", 160))
        object.__setattr__(self, "source_commit", _text(self.source_commit, "source_commit", 160))
        if not isinstance(self.auth_type, str):
            raise ResourceValidationError("auth_type must be a string")
        if self.auth_type not in AUTH_TYPES:
            raise ResourceValidationError("unsupported auth_type: " + str(self.auth_type))
        if not isinstance(self.https, bool):
            raise ResourceValidationError("https must be a boolean")
        if not isinstance(self.verification_status, str):
            raise ResourceValidationError("verification_status must be a string")
        if self.verification_status not in VERIFICATION_STATUSES:
            raise ResourceValidationError(
                "unsupported verification_status: " + str(self.verification_status)
            )
        verified = self.verification_status == "verified"
        object.__setattr__(
            self, "official_docs_location", _docs(self.official_docs_location, verified=verified)
        )
        if verified and not self.https:
            raise ResourceValidationError("verified resources must declare HTTPS support")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Resource":
        if not isinstance(value, Mapping):
            raise ResourceValidationError("resource must be an object")
        unknown = set(value) - _FIELDS
        missing = _FIELDS - set(value)
        if unknown:
            raise ResourceValidationError("unknown resource fields: " + ", ".join(sorted(unknown)))
        if missing:
            raise ResourceValidationError("missing resource fields: " + ", ".join(sorted(missing)))
        return cls(**{field: value[field] for field in _FIELDS})

    def to_mapping(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "name": self.name,
            "description": self.description,
            "auth_type": self.auth_type,
            "https": self.https,
            "source": self.source,
            "source_commit": self.source_commit,
            "verification_status": self.verification_status,
            "official_docs_location": self.official_docs_location,
        }


def verify_resource(resource: Resource, official_docs_location: str) -> Resource:
    """Return a verified copy; imported candidates are never changed implicitly."""
    if not isinstance(resource, Resource):
        raise ResourceValidationError("resource must be a Resource")
    location = _docs(official_docs_location, verified=True)
    return replace(
        resource,
        https=True,
        verification_status="verified",
        official_docs_location=location,
    )
