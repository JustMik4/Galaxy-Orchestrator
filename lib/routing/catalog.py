"""Host-verified model/effort capability catalog."""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, order=True)
class Route:
    model: str
    effort: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model, str)
            or not isinstance(self.effort, str)
            or not self.model.strip()
            or not self.effort.strip()
        ):
            raise ValueError("model and effort must be non-empty")

    @property
    def pair(self) -> tuple[str, str]:
        return self.model, self.effort


@dataclass(frozen=True)
class ModelCapability:
    route: Route
    capability: int
    expected_cost: float
    frontier: bool = False
    family: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.capability, bool) or not isinstance(self.capability, int) or self.capability < 0:
            raise ValueError("capability must be a non-negative integer")
        if (
            isinstance(self.expected_cost, bool)
            or not isinstance(self.expected_cost, (int, float))
            or self.expected_cost < 0
        ):
            raise ValueError("expected_cost must be non-negative")


class CapabilityCatalog:
    """Configured capabilities filtered by pairs observed on the current host."""

    def __init__(
        self,
        configured: Iterable[ModelCapability],
        observed_pairs: Iterable[tuple[str, str] | Route] | None = None,
    ) -> None:
        entries = tuple(configured)
        by_pair: dict[tuple[str, str], ModelCapability] = {}
        for entry in entries:
            if entry.route.pair in by_pair:
                raise ValueError(f"duplicate configured route: {entry.route.pair!r}")
            by_pair[entry.route.pair] = entry
        self._configured = by_pair
        if observed_pairs is None:
            # Configuration is not host telemetry.  An omitted observation
            # set therefore means that no route has been verified yet.
            observed = set()
        else:
            observed = {
                value.pair if isinstance(value, Route) else tuple(value)
                for value in observed_pairs
            }
            if any(len(pair) != 2 for pair in observed):
                raise ValueError("observed capability must be a (model, effort) pair")
        self._observed = frozenset(observed)

    @property
    def configured(self) -> tuple[ModelCapability, ...]:
        return tuple(self._configured.values())

    @property
    def observed_pairs(self) -> frozenset[tuple[str, str]]:
        return self._observed

    @classmethod
    def from_pairs(
        cls,
        pairs: Iterable[tuple[str, str, int, float]],
    ) -> "CapabilityCatalog":
        entries = [
            ModelCapability(Route(model, effort), capability, cost)
            for model, effort, capability, cost in pairs
        ]
        return cls(entries, [entry.route for entry in entries])

    def supports(self, route: Route) -> bool:
        return route.pair in self._configured and route.pair in self._observed

    def get(self, route: Route) -> ModelCapability | None:
        return self._configured.get(route.pair) if self.supports(route) else None

    def available(self, *, allow_frontier: bool = True) -> tuple[ModelCapability, ...]:
        eligible = (
            entry
            for pair, entry in self._configured.items()
            if pair in self._observed and (allow_frontier or not entry.frontier)
        )
        return tuple(sorted(eligible, key=lambda item: (item.expected_cost, item.capability, item.route)))
