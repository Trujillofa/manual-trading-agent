"""Cross-process coordination for ETR state.

There is exactly one lock for ETR state: ``etr_coordination.lock`` next to
``etr_state.json``. Every writer (this poller, and any direct-fetch/freshness
worker) must take it exclusively around read-modify-write of the state file and
must never unlink the inode: ``flock`` ownership belongs to the open file
description, so deleting the file would let two writers lock different inodes.

The lock is only for state transactions. Network work (renderer, ETR site,
Telegram) must happen outside it; otherwise a slow render can stall every other
ETR cycle for its full timeout. Acquisition is bounded, so a stuck holder turns
into a skipped cycle rather than a wedged process.
"""

from __future__ import annotations

import asyncio
import fcntl
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import BinaryIO

from src.etr.state import etr_state_path

LOCK_NAME = "etr_coordination.lock"
DEFAULT_POLL_TIMEOUT_SECONDS = 30.0
DEFAULT_READ_TIMEOUT_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.05


class CoordinationBusyError(TimeoutError):
    """Another process held the ETR coordination lock past our deadline."""


def coordination_lock_path() -> Path:
    # Same file name and directory the freshness work uses. Do not rename or
    # place a second lock elsewhere; two names are two writers.
    return etr_state_path().with_name(LOCK_NAME)


def _open_lock_file() -> BinaryIO:
    path = coordination_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a+b")


def _acquire(fh, *, exclusive: bool, timeout: float) -> None:
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fh.fileno(), mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CoordinationBusyError("ETR state coordination busy") from None
            time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))


@contextmanager
def coordination_lock(
    *, exclusive: bool = True, timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Bounded blocking flock. Closing the file releases it."""
    fh = _open_lock_file()
    try:
        _acquire(fh, exclusive=exclusive, timeout=timeout)
        yield
    finally:
        fh.close()


@asynccontextmanager
async def async_coordination_lock(
    *, exclusive: bool = True, timeout: float = DEFAULT_POLL_TIMEOUT_SECONDS
):
    fh = _open_lock_file()
    try:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), mode | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise CoordinationBusyError("ETR state coordination busy") from None
                await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
        yield
    finally:
        fh.close()


def state_read_lock(timeout: float = DEFAULT_READ_TIMEOUT_SECONDS):
    """Shared lock for freshness/cache readers.

    Atomic replacement already prevents torn reads; the shared lock makes the
    read order explicit and keeps readers behind a writer that is mid-commit.
    """
    return coordination_lock(exclusive=False, timeout=timeout)


@asynccontextmanager
async def poll_lock(timeout: float | None = None):
    """Exclusive lock held only for the state transaction, never for I/O.

    ``timeout`` is resolved at call time so tests and operators can bound it.
    """
    async with async_coordination_lock(
        exclusive=True,
        timeout=DEFAULT_POLL_TIMEOUT_SECONDS if timeout is None else timeout,
    ):
        yield
