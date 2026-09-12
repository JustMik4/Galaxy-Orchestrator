"""Temporary repository-level compatibility entry point."""

from lib.multicontroller import *  # noqa: F401,F403
from lib.multicontroller import deprecated_main


if __name__ == "__main__":
    raise SystemExit(deprecated_main())
