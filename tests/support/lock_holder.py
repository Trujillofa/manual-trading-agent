"""Hold the ETR coordination lock in its own process, like a slow writer.

Prints ``HELD`` on stdout once the ``flock`` is owned so the parent can start
racing it without sleeping. ``--mode shared`` takes a shared lock instead, which
is what a reader that overruns holds.
"""

from __future__ import annotations

import argparse
import fcntl
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("seconds", type=float)
    parser.add_argument("--mode", choices=("exclusive", "shared"), default="exclusive")
    args = parser.parse_args()

    mode = fcntl.LOCK_EX if args.mode == "exclusive" else fcntl.LOCK_SH
    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        fcntl.flock(handle.fileno(), mode)
        print("HELD", flush=True)
        time.sleep(args.seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
