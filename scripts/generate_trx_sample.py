#!/usr/bin/env python3
"""
Generate sample-trx-sample.csv from the full transaction file.

Includes EIJI PATISSERIE (10546361) + top shiptos for price diversity.
Target: ~5-10K rows, under 100MB for GitHub.
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

# Demo shipto + top shiptos for price lookup diversity
SAMPLE_SHIPTO = "10546361"
MAX_SHIPTOS = 10
MAX_ROWS = 10_000

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
FULL_PATH = PROJECT_DIR / "sample-trx-jan26-feb26.csv"
OUTPUT_PATH = PROJECT_DIR / "sample-trx-sample.csv"


def _norm(v: str | None) -> str:
    return (v or "").replace(",", "").strip()


def main() -> None:
    if not FULL_PATH.exists():
        print(f"Full file not found: {FULL_PATH}")
        print("Run this script when sample-trx-jan26-feb26.csv is present.")
        return

    with FULL_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    # Count by shipto (RECEIVED only)
    received = [r for r in rows if (r.get("order_status") or "").strip() == "RECEIVED"]
    by_shipto: Counter[str] = Counter()
    for r in received:
        st = _norm(r.get("ship_to_code", ""))
        if st:
            by_shipto[st] += 1

    # Top shiptos: EIJI first, then by count
    top_shiptos = {SAMPLE_SHIPTO}
    for st, _ in by_shipto.most_common(MAX_SHIPTOS):
        top_shiptos.add(st)

    # Filter rows
    selected = []
    for r in rows:
        st = _norm(r.get("ship_to_code", ""))
        if st in top_shiptos:
            selected.append(r)
        if len(selected) >= MAX_ROWS:
            break

    # Write sample
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)

    size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)
    print(f"Wrote {OUTPUT_PATH} ({len(selected):,} rows, {size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
