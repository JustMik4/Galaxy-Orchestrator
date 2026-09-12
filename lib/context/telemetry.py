"""Context metrics kept separate from account quota telemetry."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ContextTelemetry:
    mode: str
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    compacted_items: int = 0
    reused_references: int = 0
    evidence_bytes: int = 0
    context_insufficient_retries: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name != "mode" and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, object]:
        # Deliberately has no quota fields; quota and context are independent signals.
        return asdict(self)
