"""Standard example entrypoint for the skills code review agent."""

from __future__ import annotations

try:
    from .run_review import main
except ImportError:
    from run_review import main


if __name__ == "__main__":
    main()
