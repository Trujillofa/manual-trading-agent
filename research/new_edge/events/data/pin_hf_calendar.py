#!/usr/bin/env python3
"""Download and pin the HuggingFace Forex Factory calendar snapshot with provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from datasets import load_dataset

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "pinned"
SOURCE_URL = "https://huggingface.co/datasets/Ehsanrs2/Forex_Factory_Calendar"


def main() -> None:
    parser = argparse.ArgumentParser(description="Pin HF Forex Factory calendar snapshot")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory for CSV + provenance JSON",
    )
    parser.add_argument(
        "--tag",
        default="2026-06-18",
        help="Date tag for pinned filenames",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / f"forex_factory_calendar_hf_{args.tag}.csv"
    prov_path = args.output_dir / f"forex_factory_calendar_hf_{args.tag}.provenance.json"

    print(f"Downloading {SOURCE_URL} ...")
    ds = load_dataset("Ehsanrs2/Forex_Factory_Calendar", split="train")
    df = ds.to_pandas()
    df.to_csv(csv_path, index=False)

    sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    provenance = {
        "source_url": SOURCE_URL,
        "source_format": "csv",
        "pinned_file": str(csv_path),
        "row_count": len(df),
        "date_min": str(df["DateTime"].min()),
        "date_max": str(df["DateTime"].max()),
        "sha256": sha,
        "pinned_at_utc": datetime.now(UTC).isoformat(),
        "note": "Community scrape; third-party provenance — not official Forex Factory feed.",
    }
    prov_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    print(f"Rows: {len(df):,}")
    print(f"CSV: {csv_path}")
    print(f"SHA256: {sha}")
    print(f"Provenance: {prov_path}")


if __name__ == "__main__":
    main()
