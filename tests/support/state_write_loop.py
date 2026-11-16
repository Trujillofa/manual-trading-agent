"""Rewrite ETR state forever so a test can SIGKILL the writer mid-flight."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.etr.models import AssetState  # noqa: E402
from src.etr.state import save_etr_state  # noqa: E402

os.environ.setdefault("MANUAL_TRADING_AGENT_LOG_DIR", sys.argv[1])
state = {
    f"asset-{index}": AssetState(
        fingerprint=f"fp-{index}",
        report={"asset": "btc", "bias": "x" * 200, "index": index},
        last_polled_at="2026-11-16T00:00:00+00:00",
    )
    for index in range(500)
}

while True:
    save_etr_state(state)
