"""Zero-LLM specialist selection from deterministic task evidence."""

import fnmatch
import re
from typing import Iterable

from .index import SpecialistCatalog
from .schema import SelectionEvidence, SelectionMode, SpecialistSelection


_WORD = re.compile(r"[a-z0-9][a-z0-9+.#_-]*", re.IGNORECASE)


def _terms(values: Iterable[str]) -> frozenset[str]:
    return frozenset(token.lower() for value in values for token in _WORD.findall(value))


class SpecialistRouter:
    AUTO_THRESHOLD = 0.85
    SHORTLIST_THRESHOLD = 0.55

    def __init__(self, catalog: SpecialistCatalog) -> None:
        self.catalog = catalog

    def select(
        self,
        *,
        paths: Iterable[str] = (),
        task_class: str | None = None,
        errors: Iterable[str] = (),
        keywords: Iterable[str] = (),
    ) -> SpecialistSelection:
        normalized_paths = tuple(sorted(path.replace("\\", "/").lower() for path in paths))
        keyword_terms = _terms(keywords)
        error_terms = _terms(errors)
        all_terms = keyword_terms | error_terms
        scored: list[SelectionEvidence] = []
        for item in self.catalog.specialists:
            if item.name == "core":
                continue
            score = 0.0
            matches: list[str] = []
            if task_class and task_class.lower() in item.task_classes:
                score += 0.20
                matches.append(f"task:{task_class.lower()}")
            matched_paths = sorted(
                {pattern for pattern in item.paths for path in normalized_paths if fnmatch.fnmatch(path, pattern.lower())}
            )
            if matched_paths:
                score += 0.65
                matches.extend(f"path:{value}" for value in matched_paths)
            trigger_matches = sorted(
                trigger for trigger in item.triggers
                if trigger in all_terms
                or (len(trigger) >= 4 and any(trigger in term for term in all_terms))
            )
            if trigger_matches:
                score += min(0.75, 0.65 + 0.05 * (len(trigger_matches) - 1))
                matches.extend(f"trigger:{value}" for value in trigger_matches)
            domain_matches = sorted(set(item.domains) & keyword_terms)
            if domain_matches:
                score += 0.55
                matches.extend(f"domain:{value}" for value in domain_matches)
            if score:
                scored.append(
                    SelectionEvidence(item.name, round(min(score, 1.0), 2), tuple(matches))
                )
        ranked = tuple(sorted(scored, key=lambda item: (-item.score, item.specialist)))
        confidence = ranked[0].score if ranked else 0.0
        if confidence >= self.AUTO_THRESHOLD:
            recommended = ranked[0].specialist
            alternatives = tuple(item.specialist for item in ranked[1:3])
            return SpecialistSelection(
                SelectionMode.AUTO, recommended, confidence, (recommended,), alternatives, ranked[:3]
            )
        if confidence >= self.SHORTLIST_THRESHOLD:
            shortlist = tuple(item.specialist for item in ranked[:3])
            return SpecialistSelection(
                SelectionMode.SHORTLIST,
                shortlist[0],
                confidence,
                shortlist,
                shortlist[1:],
                ranked[:3],
            )
        return SpecialistSelection(SelectionMode.GENERIC, None, confidence, (), (), ranked[:3])
