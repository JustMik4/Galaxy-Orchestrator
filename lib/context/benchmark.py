"""Deterministic A/B aggregation for externally collected context metrics."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class BenchmarkSample:
    scenario: str
    mode: str
    input_tokens: int
    output_tokens: int
    accepted: bool

    def __post_init__(self) -> None:
        if self.mode not in ("off", "balanced", "aggressive"):
            raise ValueError("benchmark mode must be off, balanced, or aggressive")
        if not self.scenario:
            raise ValueError("benchmark scenario is required")
        if not isinstance(self.accepted, bool):
            raise ValueError("benchmark accepted must be boolean")
        for value in (self.input_tokens, self.output_tokens):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("benchmark token counts must be non-negative integers")


def compare(samples: Iterable[BenchmarkSample]) -> dict[str, object]:
    """Compare matched accepted samples without inventing unavailable usage data."""
    grouped: dict[str, dict[str, BenchmarkSample]] = {}
    for sample in samples:
        if sample.accepted:
            modes = grouped.setdefault(sample.scenario, {})
            if sample.mode in modes:
                raise ValueError("duplicate accepted benchmark scenario/mode")
            modes[sample.mode] = sample
    pairs = []
    for scenario, modes in sorted(grouped.items()):
        baseline = modes.get("off")
        if baseline is None:
            continue
        for mode in ("balanced", "aggressive"):
            candidate = modes.get(mode)
            if candidate is None:
                continue
            baseline_total = baseline.input_tokens + baseline.output_tokens
            candidate_total = candidate.input_tokens + candidate.output_tokens
            pairs.append({
                "scenario": scenario,
                "mode": mode,
                "baseline_tokens": baseline_total,
                "candidate_tokens": candidate_total,
                "delta_tokens": candidate_total - baseline_total,
            })
    return {
        "matched_pairs": len(pairs),
        "pairs": pairs,
        "mean_delta_tokens": mean(item["delta_tokens"] for item in pairs) if pairs else None,
        "comparable": bool(pairs),
    }
