#!/usr/bin/env python3
"""
Pre-demo helper: corrupt one entry in the Evidence Store DB so the
"Verify Evidence Integrity" button shows a tamper detection on stage.

Run ONCE before the demo, AFTER the pipeline has populated some records:

    python docs/corrupt_demo_entry.py

To restore: delete evidence_store/evidence.db and re-run the pipeline.
"""
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "evidence_store" / "evidence.db"

if not DB_PATH.exists():
    print(f"Error: {DB_PATH} does not exist.")
    print("Run the pipeline first to generate evidence records, then run this script.")
    raise SystemExit(1)

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

count = conn.execute("SELECT COUNT(*) as n FROM evidence").fetchone()["n"]
if count == 0:
    print("Evidence store is empty — run the pipeline first.")
    conn.close()
    raise SystemExit(1)

# Corrupt the first row
row = conn.execute("SELECT row_index, payload_json FROM evidence ORDER BY row_index ASC LIMIT 1").fetchone()
original = json.loads(row["payload_json"])
original["_tampered_by"] = "adversary"

conn.execute(
    "UPDATE evidence SET payload_json = ? WHERE row_index = ?",
    (json.dumps(original, sort_keys=True), row["row_index"])
)
conn.commit()
conn.close()

print(f"✓ Row {row['row_index']} (record #1) corrupted.")
print(f"  Click 'Verify Evidence Integrity' on the dashboard to detect it.")
print(f"  Expected result: ❌ Chain broken at record #1 — tamper detected")
