from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from court_evidence_sheet import build_rows, load_files
from processing_receipt import TOOL_VERSION, build_receipt, resolve_source, sha256_file, utc_now_iso


def resolve_manifest_root(src, data):
    nested = data.get("steps", {}).get("manifest", {}).get("result", {})
    value = data.get("root") or nested.get("root")
    if not isinstance(value, str) or not value.strip():
        return None
    root = Path(value)
    return (root if root.is_absolute() else src.resolve().parent / root).resolve(strict=True)


def resolve_evidence_path(raw, root):
    return resolve_source(raw, root)


def to_evidence_json(rows, root=None):
    items = []
    for row in rows:
        claimed = row.get("sha256", "")
        raw = row["path"]
        measured = None
        warnings = []
        status = "not_measured"
        path = str(raw)
        if root is None:
            warnings.append("Manifest root absent; source resolution and hash not measured")
        else:
            try:
                resolved = resolve_evidence_path(raw, root)
                path = str(resolved)
                measured = sha256_file(resolved)
                status = "verified" if measured == claimed else "mismatch"
                if status == "mismatch":
                    warnings.append("Manifest SHA-256 differs from readable source")
            except (ValueError, OSError) as exc:
                status = "unavailable"
                warnings.append(str(exc))
        items.append({
            "label": row["no"], "title": row["name"], "file": path,
            "author": "작성자불상", "date": (row.get("mtime") or "")[:10],
            "date_basis": "filesystem_mtime_not_authorship", "purpose": row["purpose"],
            "sha256": claimed, "verified_sha256": measured,
            "provenance": {"status": status, "warnings": warnings},
        })
    return {"evidence_list": items}


def main(argv=None):
    started = utc_now_iso()
    ap = argparse.ArgumentParser(description="Manifest to evidence JSON draft with provenance")
    ap.add_argument("input")
    ap.add_argument("--party", choices=["갑", "을"], default="갑")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--purpose", action="append", default=[])
    ap.add_argument("--purpose-file")
    ap.add_argument("-o", "--output")
    ap.add_argument("--case-id")
    ap.add_argument("--evidence-id")
    ap.add_argument("--review-status", choices=["pending", "approved"], default="pending")
    ap.add_argument("--reviewer")
    ap.add_argument("--reviewed-at")
    args = ap.parse_args(argv)
    try:
        src = Path(args.input).resolve(strict=True)
        initial_hash = sha256_file(src)
        data = json.loads(src.read_text(encoding="utf-8"))
        root = resolve_manifest_root(src, data)
        purposes = {}
        if args.purpose_file:
            purposes.update(json.loads(Path(args.purpose_file).read_text(encoding="utf-8")))
        for value in args.purpose:
            if "=" not in value:
                raise ValueError("--purpose requires file=purpose")
            name, purpose = value.split("=", 1)
            purposes[name.strip()] = purpose.strip()
        rows = build_rows(load_files(src), args.party, args.start, purposes)
        payload = to_evidence_json(rows, root)
        issues = [item for item in payload["evidence_list"] if item["provenance"]["status"] != "verified"]
        code = int(any(i["provenance"]["status"] in ("mismatch", "unavailable") for i in issues))
        warnings = [f"{i['label']}: {w}" for i in issues for w in i["provenance"]["warnings"]]
        receipt = build_receipt(
            args.evidence_id or str(src), "evidence_export", TOOL_VERSION, src,
            source_base=src.parent, started_at=started, case_id=args.case_id,
            status="partial" if issues else "complete", exit_code=code,
            parameters={"party": args.party, "start": args.start, "root": str(root) if root else None},
            warnings=warnings, limitations=["Date is recorded mtime, not authorship",
                "Embedded receipt omits enclosing JSON self-hash; artifacts is empty",
                "Evidence identifiers default to source path when not explicitly supplied"],
            review_status=args.review_status, reviewer=args.reviewer, reviewed_at=args.reviewed_at)
        if receipt["processing_receipt"]["source"]["sha256"] != initial_hash:
            raise ValueError("Manifest changed during export")
        payload.update(receipt)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = Path(args.output).resolve()
            sources = [src]
            if root is not None:
                sources.extend(resolve_evidence_path(r["path"], root) for r in rows
                               if (root / r["path"]).exists())
            if any(output == p or (output.exists() and output.samefile(p)) for p in sources):
                raise ValueError("Output would overwrite source evidence or manifest")
            output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return code
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
