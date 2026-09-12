"""Deprecated compatibility shim for the canonical :mod:`galaxy` CLI."""

from __future__ import annotations

import warnings
import sys

try:
    from .galaxy import *  # noqa: F401,F403
    from .galaxy import __all__, main
except ImportError:  # Direct execution with ``lib`` on sys.path.
    from galaxy import *  # type: ignore  # noqa: F401,F403
    from galaxy import __all__, main  # type: ignore


def deprecated_main() -> int:
    notice = "DEPRECATED: multicontroller.py; use the canonical galaxy command"
    print(notice, file=sys.stderr)
    warnings.warn(
        notice, DeprecationWarning, stacklevel=2,
    )
    return main()


if __name__ == "__main__":
    raise SystemExit(deprecated_main())
