"""Re-render the archived public measurements without reading or acquiring evidence."""
import json
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parent
sys.path.insert(0, str(EXAMPLE.parents[1] / "scripts"))
from report_draft import load_case_info, write_drafts


def main():
    output = EXAMPLE / "output"
    integrity = output / "무결성검증.json"
    records = json.loads(integrity.read_text(encoding="utf-8"))
    survey = json.loads((output / "선조사.json").read_text(encoding="utf-8"))
    refs = records["reference"]
    root = Path(records["evidence_root"])
    # Analysis environment transcribed from section 4 of the archived v1.1 draft.
    env = {"started": records["analysis_started"], "finished": records["analysis_finished"],
           "version": survey["processing_receipt"]["tool"]["version"], "os": "Darwin 27.2.0 (arm64)",
           "python": "3.12.12", "optional": "Pillow 있음, ffmpeg 있음, ffprobe 있음, fpcalc 있음"}
    ctx = {"root": root.resolve(), "root_name": root.name, "case": load_case_info(EXAMPLE / "case.txt"),
           "inventory": records["files"], "links": [], "images": records["images"], "survey": survey,
           "post": records["post_check"], "references": [{**r, "hash": r[r["algorithm"]]} for r in refs["entries"]],
           "reference_sources": refs["sources"], "environment": env, "skip_image_verify": False, "docx": True,
           "events": json.loads((output / "events.json").read_text(encoding="utf-8"))}
    _, verification = write_drafts(output, ctx, integrity)
    print(verification["verdict"])
    return verification["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
