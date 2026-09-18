from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

TOOL_VERSION = "1.1.0"
RECEIPT_SCHEMA_VERSION = "1.0"
STATUSES = ("complete", "partial", "failed", "not_measured")
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


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
    def identity(s):
        fields = (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        # NTFS reports a different st_ctime_ns via fstat() vs stat() on an unchanged file
        return fields if os.name == "nt" else fields + (s.st_ctime_ns,)
    if identity(before) != identity(after) or identity(after) != identity(Path(path).stat()):
        raise OSError("Source changed while hashing")
    return digest.hexdigest()


def sha256_file_if_readable(path):
    try:
        return sha256_file(path)
    except OSError:
        return None


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("Expected ISO timestamp")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.utcoffset() is None or date.utcoffset().total_seconds() != 0:
        raise ValueError("Expected UTC ISO timestamp")
    return date


def _entry(value, nullable):
    return (isinstance(value, dict) and isinstance(value.get("path"), str)
            and bool(value["path"]) and "sha256" in value
            and ((nullable and value["sha256"] is None)
                 or (isinstance(value["sha256"], str)
                     and SHA256_RE.fullmatch(value["sha256"]) is not None)))


def validate_receipt(receipt):
    """Validate an inner processing_receipt object. See docs/processing-receipt-contract.md."""
    if not isinstance(receipt, dict):
        return ["processing_receipt must be an object"]
    required = {"schema_version", "case_id", "evidence_id", "status", "source", "artifacts",
                "tool", "parameters", "started_at", "finished_at", "exit_code",
                "warnings", "limitations", "review"}
    errors = ["Missing " + k for k in sorted(required - receipt.keys())]
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        errors.append("Invalid schema_version")
    if receipt.get("case_id") is not None and not isinstance(receipt["case_id"], str):
        errors.append("Invalid case_id")
    if not isinstance(receipt.get("evidence_id"), str) or not receipt["evidence_id"].strip():
        errors.append("Invalid evidence_id")
    status = receipt.get("status")
    if status not in STATUSES:
        errors.append("Invalid status")
    if not _entry(receipt.get("source"), True):
        errors.append("Invalid source")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not all(_entry(a, False) for a in artifacts):
        errors.append("Invalid artifacts")
    tool = receipt.get("tool")
    if not isinstance(tool, dict) or not all(
            isinstance(tool.get(k), str) and tool[k].strip() for k in ("name", "version")):
        errors.append("Invalid tool")
    if not isinstance(receipt.get("parameters"), dict):
        errors.append("Invalid parameters")
    code = receipt.get("exit_code")
    if code is not None and type(code) is not int:
        errors.append("Invalid exit_code")
    for key in ("warnings", "limitations"):
        if not isinstance(receipt.get(key), list) or not all(isinstance(v, str) for v in receipt[key]):
            errors.append("Invalid " + key)
    dates = {}
    timestamps_nullable = status == "not_measured"
    for key in ("started_at", "finished_at"):
        value = receipt.get(key)
        if value is None:
            if not timestamps_nullable:
                errors.append("Missing " + key)
            continue
        try:
            dates[key] = _utc(value)
        except (ValueError, TypeError):
            errors.append("Invalid " + key)
    if "started_at" in dates and "finished_at" in dates and dates["finished_at"] < dates["started_at"]:
        errors.append("finished_at precedes started_at")
    review = receipt.get("review")
    if not isinstance(review, dict):
        errors.append("Invalid review")
        review = {}
    if review.get("status") not in ("pending", "approved"):
        errors.append("Invalid review status")
    elif review["status"] == "approved":
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
            errors.append("Approval requires reviewer")
        try:
            dates["reviewed_at"] = _utc(review.get("reviewed_at"))
        except (ValueError, TypeError):
            errors.append("Approval requires UTC reviewed_at")
    elif review.get("reviewer") is not None or review.get("reviewed_at") is not None:
        errors.append("Invalid pending review")
    if "reviewed_at" in dates and "finished_at" in dates and dates["reviewed_at"] < dates["finished_at"]:
        errors.append("Review precedes completion")
    source = receipt.get("source")
    if status == "complete" and (code != 0 or not isinstance(source, dict) or not source.get("sha256")):
        errors.append("Complete receipt requires measured source and zero exit_code")
    try:
        json.dumps(receipt, allow_nan=False)
    except (ValueError, TypeError):
        errors.append("Receipt must be JSON serializable without nonfinite values")
    return errors


def validate_payload(payload):
    """Validate a wrapped {"processing_receipt": ...} payload at the input boundary."""
    if not isinstance(payload, dict) or "processing_receipt" not in payload:
        return ["processing_receipt must be an object"]
    return validate_receipt(payload["processing_receipt"])


def build_receipt(evidence_id, tool_name, tool_version, source, *, source_base,
                  started_at, status="not_measured", artifacts=(), case_id=None,
                  parameters=None, finished_at=None, exit_code=None, warnings=(),
                  limitations=()):
    path = resolve_source(source, source_base)
    messages = list(warnings)
    digest = sha256_file_if_readable(path) if path.is_file() else None
    if digest is None:
        messages.append("Source SHA-256 not measured")
        if status == "complete":
            status = "partial"
    artifact_entries = [{"path": str(Path(a).resolve(strict=True)), "sha256": sha256_file(a)} for a in artifacts]
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION, "case_id": case_id,
        "evidence_id": evidence_id, "status": status,
        "source": {"path": str(path), "sha256": digest},
        "artifacts": artifact_entries, "tool": {"name": tool_name, "version": tool_version},
        "parameters": {} if parameters is None else parameters,
        "started_at": started_at, "finished_at": finished_at or utc_now_iso(),
        "exit_code": exit_code, "warnings": messages,
        "limitations": list(limitations) + ["Processing record, not an independent custody certificate"],
        "review": {"status": "pending", "reviewer": None, "reviewed_at": None}}
    errors = validate_receipt(receipt)
    if errors:
        raise ValueError("; ".join(errors))
    return receipt


def attach_receipt(payload, receipt):
    errors = validate_receipt(receipt)
    if errors:
        raise ValueError("; ".join(errors))
    return {**payload, "processing_receipt": receipt}
