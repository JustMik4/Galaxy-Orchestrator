"""Context-economy primitives for compact, loss-aware orchestration."""

from .benchmark import BenchmarkSample, compare
from .builder import ContextBundle, ContextBuilder, ContextReferenceRegistry
from .evidence import EvidenceReference, EvidenceStore, redact_sensitive
from .handoff import HandoffCompactor, HandoffResult
from .policy import ContextEconomyMode, ContextEconomyPolicy, ROLE_BUDGETS, parse_context_economy
from .telemetry import ContextTelemetry
from .tool_output import ToolOutputCompressor, ToolOutputResult

__all__ = [
    "BenchmarkSample", "ContextBundle", "ContextBuilder", "ContextEconomyMode",
    "ContextEconomyPolicy", "ContextReferenceRegistry", "ContextTelemetry",
    "EvidenceReference", "EvidenceStore", "HandoffCompactor", "HandoffResult",
    "ROLE_BUDGETS", "ToolOutputCompressor", "ToolOutputResult", "parse_context_economy",
    "compare", "redact_sensitive",
]
