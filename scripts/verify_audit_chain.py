#!/usr/bin/env python3
"""verify_audit_chain.py - Cryptographic verification of .lazyforensic/audit_trail.jsonl hash chain.

Verifies the integrity of the PostToolUse audit trail:
- Each record's `prev_hash` must match the SHA-256 hex digest of the preceding line.
- First record must have prev_hash = None / null.
- All records must have valid timestamps and required fields.
- Reports the exact line and record where the chain is broken if tampering occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path


def verify_audit_chain(
    trail_path: str | Path,
    allow_empty: bool = True,
) -> dict:
    path = Path(trail_path)
    if not path.is_file():
        if allow_empty:
            return {
                "status": "PASS",
                "file": str(path),
                "total_records": 0,
                "valid_chain": True,
                "message": "Audit trail file does not exist yet (empty session).",
                "errors": [],
            }
        return {
            "status": "FAIL",
            "file": str(path),
            "total_records": 0,
            "valid_chain": False,
            "message": f"Audit trail file not found: {path}",
            "errors": [f"File not found: {path}"],
        }

    raw_text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in raw_text.splitlines() if line.strip()]

    if not lines:
        return {
            "status": "PASS",
            "file": str(path),
            "total_records": 0,
            "valid_chain": True,
            "message": "Audit trail is empty.",
            "errors": [],
        }

    errors: list[str] = []
    previous_line_hash: str | None = None
    prev_timestamp: datetime | None = None

    for idx, raw_line in enumerate(lines):
        line_num = idx + 1
        try:
            entry = json.loads(raw_line)
        except Exception as exc:
            errors.append(f"Line {line_num}: Malformed JSON record ({exc})")
            break

        if not isinstance(entry, dict):
            errors.append(f"Line {line_num}: Record is not a JSON object")
            break

        # Check required fields
        for field in ("timestamp", "file"):
            if field not in entry:
                errors.append(f"Line {line_num}: Missing required field '{field}'")

        # Timestamp format & monotonicity check
        ts_str = entry.get("timestamp")
        if ts_str:
            try:
                # Support trailing 'Z' or ISO formats
                cleaned_ts = ts_str.replace("Z", "+00:00")
                ts = datetime.fromisoformat(cleaned_ts)
                if prev_timestamp and ts < prev_timestamp:
                    errors.append(
                        f"Line {line_num}: Non-monotonic timestamp detected ({ts_str} < {prev_timestamp.isoformat()})"
                    )
                prev_timestamp = ts
            except Exception:
                errors.append(f"Line {line_num}: Invalid ISO-8601 timestamp '{ts_str}'")

        # Hash chain verification
        current_prev_hash = entry.get("prev_hash")
        if idx == 0:
            if current_prev_hash is not None:
                errors.append(
                    f"Line {line_num} (Genesis): prev_hash must be null, found '{current_prev_hash}'"
                )
        else:
            if current_prev_hash != previous_line_hash:
                errors.append(
                    f"Line {line_num}: Hash chain broken! "
                    f"Expected prev_hash='{previous_line_hash}', but record specifies '{current_prev_hash}'"
                )

        # Compute hash of current line for the next iteration
        previous_line_hash = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()

    is_valid = len(errors) == 0
    return {
        "status": "PASS" if is_valid else "FAIL",
        "file": str(path),
        "total_records": len(lines),
        "valid_chain": is_valid,
        "message": f"Successfully verified {len(lines)} linked audit records." if is_valid else f"Audit chain verification failed with {len(errors)} error(s).",
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify cryptographic hash chain in LazyForensic audit_trail.jsonl"
    )
    parser.add_argument(
        "trail",
        nargs="?",
        default=".lazyforensic/audit_trail.jsonl",
        help="Path to audit_trail.jsonl (default: .lazyforensic/audit_trail.jsonl)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument(
        "--no-empty",
        action="store_true",
        help="Fail if the audit trail file does not exist or is empty",
    )
    args = parser.parse_args(argv)

    result = verify_audit_chain(args.trail, allow_empty=not args.no_empty)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["status"] == "PASS":
            print(f"[PASS] {result['message']} (records: {result['total_records']})")
        else:
            print(f"[FAIL] {result['message']}", file=sys.stderr)
            for err in result["errors"]:
                print(f"  - ERROR: {err}", file=sys.stderr)

    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
