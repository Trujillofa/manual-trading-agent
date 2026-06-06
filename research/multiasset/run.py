"""Future single entrypoint for the multi-asset momentum research program.

For now this is a thin placeholder / dispatcher while we are in the gross-first
phase (see GROSS_FIRST_BUILD_ORDER_2026-06-06.md).

Later (after the gross gate passes) it can grow to:
  --gross
  --net
  --search
  --paper etc.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    print("research/multiasset/run.py — gross-first phase active.")
    print("Use: python -m research.multiasset.run_gross_check  (or the backfill commands).")
    print("See docs/research/multiasset/ for the current sequence and gate criteria.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
