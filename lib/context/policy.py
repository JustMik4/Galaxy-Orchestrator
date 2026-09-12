"""Strict configuration and presets for Galaxy context economy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class ContextEconomyMode(str, Enum):
    OFF = "off"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class ContextEconomyPolicy:
    mode: ContextEconomyMode
    handoff_max_summary_tokens: int | None
    compress_success: bool
    preserve_failures: bool
    logs_inline_max_lines: int | None
    prefer_references: bool
    progressive_disclosure: bool

    @property
    def enabled(self) -> bool:
        return self.mode is not ContextEconomyMode.OFF

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "handoff": {"max_summary_tokens": self.handoff_max_summary_tokens},
            "tool_output": {
                "compress_success": self.compress_success,
                "preserve_failures": self.preserve_failures,
            },
            "logs": {"inline_max_lines": self.logs_inline_max_lines},
            "evidence": {"prefer_references": self.prefer_references},
            "specialists": {"progressive_disclosure": self.progressive_disclosure},
        }


PRESETS = MappingProxyType({
    ContextEconomyMode.OFF: ContextEconomyPolicy(
        ContextEconomyMode.OFF, None, False, True, None, False, False,
    ),
    ContextEconomyMode.BALANCED: ContextEconomyPolicy(
        ContextEconomyMode.BALANCED, 350, True, True, 80, True, True,
    ),
    ContextEconomyMode.AGGRESSIVE: ContextEconomyPolicy(
        ContextEconomyMode.AGGRESSIVE, 200, True, True, 40, True, True,
    ),
})

# Qualitative ceilings keep the root focused on decisions while allowing a
# worker/tester to retain the evidence needed for execution and diagnosis.
ROLE_BUDGETS = MappingProxyType({
    "root": "decisions-blockers-risks-references",
    "explorer": "findings-paths-references",
    "researcher": "claims-sources-uncertainty",
    "worker": "contract-changes-tests-failures",
    "hard-worker": "contract-changes-tests-failures",
    "tester": "commands-failures-locations-evidence",
    "reviewer": "findings-severity-locations-evidence",
    "architect": "constraints-decisions-risks-interfaces",
})

_TOP = frozenset({"mode", "handoff", "tool_output", "logs", "evidence", "specialists"})
_NESTED = {
    "handoff": frozenset({"max_summary_tokens"}),
    "tool_output": frozenset({"compress_success", "preserve_failures"}),
    "logs": frozenset({"inline_max_lines"}),
    "evidence": frozenset({"prefer_references"}),
    "specialists": frozenset({"progressive_disclosure"}),
}


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def parse_context_economy(value: Any = None) -> ContextEconomyPolicy:
    """Parse exact configuration. Missing configuration is the true OFF baseline."""
    if value is None:
        return PRESETS[ContextEconomyMode.OFF]
    raw = _object(value, "project.context_economy")
    unknown = set(raw).difference(_TOP)
    if unknown:
        raise ValueError("project.context_economy has unknown fields: " + ", ".join(sorted(unknown)))
    try:
        mode = ContextEconomyMode(raw.get("mode", "off"))
    except ValueError as exc:
        raise ValueError("project.context_economy.mode must be off, balanced, or aggressive") from exc
    preset = PRESETS[mode]
    values = preset.to_dict()
    for section, fields in _NESTED.items():
        if section not in raw:
            continue
        supplied = _object(raw[section], f"project.context_economy.{section}")
        extra = set(supplied).difference(fields)
        if extra:
            raise ValueError(
                f"project.context_economy.{section} has unknown fields: " + ", ".join(sorted(extra))
            )
        values[section].update(supplied)
    token_limit = values["handoff"]["max_summary_tokens"]
    line_limit = values["logs"]["inline_max_lines"]
    if token_limit is not None and (
        not isinstance(token_limit, int) or isinstance(token_limit, bool) or token_limit < 50
    ):
        raise ValueError("context handoff max_summary_tokens must be null or an integer >= 50")
    if line_limit is not None and (
        not isinstance(line_limit, int) or isinstance(line_limit, bool) or line_limit < 10
    ):
        raise ValueError("context logs inline_max_lines must be null or an integer >= 10")
    for section, key in (
        ("tool_output", "compress_success"), ("tool_output", "preserve_failures"),
        ("evidence", "prefer_references"), ("specialists", "progressive_disclosure"),
    ):
        if not isinstance(values[section][key], bool):
            raise ValueError(f"project.context_economy.{section}.{key} must be boolean")
    if not values["tool_output"]["preserve_failures"]:
        raise ValueError("context economy must preserve failures")
    if mode is ContextEconomyMode.OFF:
        baseline = PRESETS[mode]
        if values != baseline.to_dict():
            raise ValueError("context economy off cannot enable compaction overrides")
        return baseline
    return ContextEconomyPolicy(
        mode, token_limit, values["tool_output"]["compress_success"], True,
        line_limit, values["evidence"]["prefer_references"],
        values["specialists"]["progressive_disclosure"],
    )
