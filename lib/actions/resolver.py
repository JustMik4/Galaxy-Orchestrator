"""Action capability inventory and deterministic resolver.

The resolver only decides *how* an action may be performed.  It does not invoke
connectors, CLIs, APIs, or browsers.  This keeps approval and capability
decisions auditable and makes the result safe to persist as JSON.
"""
from dataclasses import asdict, dataclass
import re
from typing import Iterable, Mapping, Optional


class ResolutionCode:
    SELECTED = "SELECTED"
    BLOCKED_MISSING_CAPABILITY = "BLOCKED_MISSING_CAPABILITY"


@dataclass(frozen=True)
class ApprovalGrant:
    """One explicit approval covering an action and all of its sub-actions."""

    scope: str

    def __post_init__(self):
        if not isinstance(self.scope, str) or not self.scope.strip():
            raise ValueError("approval grant scope is required")

    def covers(self, action: str) -> bool:
        return action == self.scope or action.startswith(self.scope.rstrip(".*") + ".")


# Values earlier in this sequence win. connector/plugin/mcp are intentionally
# equivalent ranks: their stable input order is the final tie breaker.
_DEFAULT_RANK = {"native": 0, "connected-app": 0, "connector": 1,
                 "plugin": 1, "mcp": 1, "cli": 2, "api": 3, "browser": 4}
_SECRET_KEY = re.compile(r"(?:^|[_-])(token|secret|password|api[_-]?key|authorization|credential|cookie)(?:$|[_-])", re.I)


def _safe_metadata(value):
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key, item in value.items():
        if _SECRET_KEY.search(str(key)):
            continue
        if isinstance(item, Mapping):
            result[str(key)] = _safe_metadata(item)
        elif isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = item
    return result


@dataclass(frozen=True)
class Capability:
    action: str
    kind: str
    id: str
    available: bool = True
    authenticated: bool = True
    approval_required: bool = False
    metadata: Optional[Mapping[str, object]] = None

    def __post_init__(self):
        if not isinstance(self.action, str) or not self.action.strip():
            raise ValueError("capability action is required")
        if self.kind not in _DEFAULT_RANK:
            raise ValueError("unknown capability kind: " + str(self.kind))
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("capability id is required")

    def usable(self) -> bool:
        return bool(self.available and self.authenticated)

    def to_dict(self):
        value = asdict(self)
        value["metadata"] = _safe_metadata(self.metadata)
        return value


class CapabilityInventory:
    """A deterministic, insertion-preserving collection of capabilities."""

    def __init__(self, capabilities: Iterable[Capability] = ()):
        self._items = tuple(_coerce_capability(c) for c in capabilities)

    def for_action(self, action: str):
        return tuple(c for c in self._items if c.action in (action, "*") or action.startswith(c.action + ".") or (c.action.endswith(".*") and action.startswith(c.action[:-1])))

    def to_dict(self):
        return [c.to_dict() for c in self._items]


@dataclass(frozen=True)
class Decision:
    action: str
    code: str
    capability: Optional[Capability] = None
    reason: Optional[str] = None
    considered: tuple = ()

    def to_dict(self):
        return {
            "action": self.action,
            "code": self.code,
            "capability": self.capability.to_dict() if self.capability else None,
            "reason": self.reason,
            "considered": list(self.considered),
        }

    def as_dict(self):
        return self.to_dict()


class ActionResolver:
    def __init__(self, capabilities: Iterable[Capability] = (), preferences: Optional[Mapping[str, Iterable[str]]] = None):
        self.inventory = capabilities if isinstance(capabilities, CapabilityInventory) else CapabilityInventory(capabilities)
        self.preferences = dict(preferences or {})

    def resolve(self, action: str, *, approval: bool = False,
                grant: ApprovalGrant | None = None,
                preference: Optional[Iterable[str]] = None) -> Decision:
        candidates = [c for c in self.inventory.for_action(action) if c.usable()]
        override = tuple(preference if preference is not None else self.preferences.get(action, ()))
        if override:
            # Preferences may name kinds ("plugin") or concrete providers ("github-mcp").
            position = {str(value): index for index, value in enumerate(override)}
            candidates.sort(key=lambda c: (position.get(c.kind, position.get(c.id, len(position) + _DEFAULT_RANK.get(c.kind, 99))), _DEFAULT_RANK.get(c.kind, 99), c.id))
        else:
            candidates.sort(key=lambda c: (_DEFAULT_RANK.get(c.kind, 99), c.id))
        considered = tuple(c.id for c in candidates)
        approved = bool(approval or (grant is not None and grant.covers(action)))
        for capability in candidates:
            if (capability.kind == "browser" or capability.approval_required) and not approved:
                continue
            return Decision(action, ResolutionCode.SELECTED, capability, considered=considered)
        approval_seen = any(c.kind == "browser" or c.approval_required for c in candidates)
        reason = "approval-required" if approval_seen else "no-usable-capability"
        return Decision(action, ResolutionCode.BLOCKED_MISSING_CAPABILITY, reason=reason, considered=considered)


def resolve_action(action: str, capabilities: Iterable[Capability] = (), *, approval: bool = False,
                   grant: ApprovalGrant | None = None, preferences=None) -> Decision:
    return ActionResolver(capabilities, preferences).resolve(action, approval=approval, grant=grant)


def _coerce_capability(value):
    if isinstance(value, Capability):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        # Accept provider/name aliases used by inventory JSON files.
        if "id" not in data:
            data["id"] = data.pop("provider", data.pop("name", ""))
        return Capability(**data)
    raise TypeError("capabilities must be Capability objects or mappings")


def github_capabilities(*, native=None, connector=None, plugin=None, cli=None, api=None, browser=None):
    """Build a GitHub inventory from simple provider ids or Capability objects."""
    values = []
    for kind, value in (("native", native), ("connector", connector), ("plugin", plugin), ("cli", cli), ("api", api), ("browser", browser)):
        if value is None:
            continue
        entries = value if isinstance(value, (list, tuple)) else (value,)
        for item in entries:
            values.append(item if isinstance(item, Capability) else Capability("github.*", kind, str(item)))
    return CapabilityInventory(values)
