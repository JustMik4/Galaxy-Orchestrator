"""Content-aware command output compression with failure preservation."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .evidence import EvidenceReference, EvidenceStore, redact_sensitive
from .policy import ContextEconomyPolicy


_FAILURE = re.compile(
    r"(?i)(failed|failure|error|exception|traceback|assert|fatal|panic|\bnot ok\b|:[0-9]+(?::[0-9]+)?)"
)


@dataclass(frozen=True)
class ToolOutputResult:
    inline: str
    compacted: bool
    reference: EvidenceReference | None = None
    original_lines: int = 0
    inline_lines: int = 0


class ToolOutputCompressor:
    def __init__(self, policy: ContextEconomyPolicy, evidence: EvidenceStore):
        self.policy = policy
        self.evidence = evidence

    def compress(self, task_id: str, output: str, *, exit_code: int, command: str = "command") -> ToolOutputResult:
        lines = str(output).splitlines()
        if not self.policy.enabled:
            return ToolOutputResult(str(output), False, None, len(lines), len(lines))
        output = redact_sensitive(str(output))
        lines = output.splitlines()
        limit = self.policy.logs_inline_max_lines or len(lines)
        if exit_code == 0 and len(lines) <= limit and not (
            self.policy.compress_success and len(lines) > 12
        ):
            return ToolOutputResult(str(output), False, None, len(lines), len(lines))
        reference = self.evidence.persist(task_id, output, label="tool-output")
        if exit_code != 0 and len(lines) <= limit:
            return ToolOutputResult(output, False, reference, len(lines), len(lines))
        if exit_code == 0:
            selected = lines[: min(8, limit)]
            selected.append(
                f"[success: {len(lines)} lines; full sanitized output: {reference.path} sha256={reference.sha256}]"
            )
        else:
            indexes = {index for index, line in enumerate(lines) if _FAILURE.search(line)}
            expanded = set()
            for index in indexes:
                expanded.update(range(max(0, index - 1), min(len(lines), index + 2)))
            selected = [lines[index] for index in sorted(expanded)[: max(1, limit - 1)]]
            if not selected:
                selected = lines[-max(1, limit - 1):]
            selected.append(
                f"[failed exit={exit_code}; full sanitized output: {reference.path} sha256={reference.sha256}]"
            )
        inline = "\n".join(selected) + ("\n" if selected else "")
        return ToolOutputResult(inline, True, reference, len(lines), len(selected))
