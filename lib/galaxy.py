"""Canonical Galaxy Orchestrator command-line interface.

The V1 policy helpers remain available during the V2 compatibility window.
V2 project, migration, doctor, lifecycle, and vault commands are registered in
this module so both entry points execute one implementation.
"""

from __future__ import annotations

try:
    from .legacy import (
        CLASSES,
        LADDER,
        check_dag,
        decide,
        gate,
        grant,
        normalized_path,
        overlap,
        reclaim,
        reputation,
        usage,
    )
except ImportError:  # Direct execution with ``lib`` on sys.path.
    from legacy import (  # type: ignore
        CLASSES,
        LADDER,
        check_dag,
        decide,
        gate,
        grant,
        normalized_path,
        overlap,
        reclaim,
        reputation,
        usage,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the canonical CLI.

    The compatibility command surface is delegated to the proven V1 parser
    until the V2 command groups are installed later in the integration phase.
    """
    if argv is not None:
        import sys

        previous = sys.argv
        try:
            sys.argv = [previous[0], *argv]
            return _legacy_main()
        finally:
            sys.argv = previous
    return _legacy_main()


try:
    from .legacy import main as _legacy_main
except ImportError:
    from legacy import main as _legacy_main  # type: ignore


__all__ = [
    "CLASSES",
    "LADDER",
    "check_dag",
    "decide",
    "gate",
    "grant",
    "main",
    "normalized_path",
    "overlap",
    "reclaim",
    "reputation",
    "usage",
]


if __name__ == "__main__":
    raise SystemExit(main())
