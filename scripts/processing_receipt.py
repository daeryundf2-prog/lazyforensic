from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

TOOL_VERSION = "1.0.3"
RECEIPT_SCHEMA_VERSION = "1.0"
STATUSES = ("complete", "partial", "failed", "not_measured")


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def resolve_source(path, base):
    root = Path(base).resolve(strict=True)
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Source escapes evidence root")
    return resolved


def sha256_file(path):
    with Path(path).open("rb") as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after) or identity(after) != identity(Path(path).stat()):
        raise OSError("Source changed while hashing")
    return digest.hexdigest()


def sha256_file_if_readable(path):
    try:
        return sha256_file(path)
    except OSError:
        return None


def _utc(value):
    if not isinstance(value, str) or not value.endswith(("Z", "+00:00")):
        raise ValueError("Expected UTC ISO timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_receipt(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("processing_receipt"), dict):
        return ["processing_receipt must be an object"]
    r = payload["processing_receipt"]
    required = {"schema_version", "case_id", "evidence_id", "status", "source", "artifacts",
                "tool", "parameters", "started_at", "finished_at", "exit_code", "warnings", "limitations", "review"}
    if not required.issubset(r):
        return ["Missing fields: " + ", ".join(sorted(required - r.keys()))]
    errors = []
    if r["schema_version"] != "1.0":
        errors.append("Invalid schema_version")
    if r["case_id"] is not None and not isinstance(r["case_id"], str):
        errors.append("Invalid case_id")
    if not isinstance(r["evidence_id"], str) or not r["evidence_id"].strip():
        errors.append("Invalid evidence_id")
    if r["status"] not in STATUSES:
        errors.append("Invalid status")
    def entry(value, nullable):
        return (isinstance(value, dict) and isinstance(value.get("path"), str)
                and bool(value["path"]) and "sha256" in value
                and ((nullable and value["sha256"] is None)
                     or (isinstance(value["sha256"], str)
                         and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)))
    if not entry(r["source"], True):
        errors.append("Invalid source")
    if not isinstance(r["artifacts"], list) or not all(entry(a, False) for a in r["artifacts"]):
        errors.append("Invalid artifacts")
    if not isinstance(r["tool"], dict) or not all(isinstance(r["tool"].get(k), str) and r["tool"][k].strip() for k in ("name", "version")):
        errors.append("Invalid tool")
    if not isinstance(r["parameters"], dict):
        errors.append("Invalid parameters")
    try:
        if _utc(r["finished_at"]) < _utc(r["started_at"]):
            errors.append("finished_at precedes started_at")
    except (ValueError, TypeError):
        errors.append("Invalid processing timestamps")
    if r["exit_code"] is not None and type(r["exit_code"]) is not int:
        errors.append("Invalid exit_code")
    for key in ("warnings", "limitations"):
        if not isinstance(r[key], list) or not all(isinstance(v, str) for v in r[key]):
            errors.append("Invalid " + key)
    review = r["review"]
    if not isinstance(review, dict) or not {"status", "reviewer", "reviewed_at"}.issubset(review):
        errors.append("Invalid review")
    elif review["status"] == "approved":
        if not isinstance(review["reviewer"], str) or not review["reviewer"].strip():
            errors.append("Approval requires reviewer")
        try:
            _utc(review["reviewed_at"])
        except (ValueError, TypeError):
            errors.append("Approval requires UTC reviewed_at")
    elif review["status"] != "pending" or review["reviewer"] is not None or review["reviewed_at"] is not None:
        errors.append("Invalid pending review")
    try:
        json.dumps(r, allow_nan=False)
    except (ValueError, TypeError):
        errors.append("Receipt must be JSON serializable without nonfinite values")
    return errors


def build_receipt(evidence_id, tool_name, tool_version, source, *, source_base,
                  started_at, status="not_measured", artifacts=(), case_id=None,
                  parameters=None, finished_at=None, exit_code=None, warnings=(),
                  limitations=(), review_status="pending", reviewer=None, reviewed_at=None):
    path = resolve_source(source, source_base)
    messages = list(warnings)
    digest = sha256_file_if_readable(path) if path.is_file() else None
    if digest is None:
        messages.append("Source SHA-256 not measured")
        if status == "complete" and path.is_file():
            status = "partial"
    artifact_entries = [{"path": str(Path(a).resolve(strict=True)), "sha256": sha256_file(a)} for a in artifacts]
    payload = {"processing_receipt": {
        "schema_version": "1.0", "case_id": case_id, "evidence_id": evidence_id,
        "status": status, "source": {"path": str(path), "sha256": digest},
        "artifacts": artifact_entries, "tool": {"name": tool_name, "version": tool_version},
        "parameters": {} if parameters is None else parameters,
        "started_at": started_at, "finished_at": finished_at or utc_now_iso(),
        "exit_code": exit_code, "warnings": messages,
        "limitations": list(limitations) + ["Processing record, not an independent custody certificate"],
        "review": {"status": review_status, "reviewer": reviewer, "reviewed_at": reviewed_at}}}
    errors = validate_receipt(payload)
    if errors:
        raise ValueError("; ".join(errors))
    return payload


def attach_receipt(payload, receipt):
    errors = validate_receipt(receipt)
    if errors:
        raise ValueError("; ".join(errors))
    return {**payload, "processing_receipt": receipt["processing_receipt"]}
