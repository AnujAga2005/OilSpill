"""CLI: run the Phase 1 dataset audit.

    python scripts/run_audit.py [--pixel-sample N] [--limit N] [--skip-masks]

Writes ``DATA_AUDIT.md`` and ``data/processed/audit.json``. Exits non-zero when
image/mask pairing is invalid, so the pipeline stops rather than training on a
broken dataset.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ml"))

from spilltrace_common import config as C  # noqa: E402
from spilltrace_ml.audit import DEFAULT_PIXEL_SAMPLE, audit  # noqa: E402
from spilltrace_ml.audit_report import render_markdown  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the supplied SpillTrace datasets")
    parser.add_argument(
        "--pixel-sample",
        type=int,
        default=DEFAULT_PIXEL_SAMPLE,
        help="how many scenes to fully decode for value statistics (0 disables)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="audit only the first N pairs (debugging)"
    )
    parser.add_argument(
        "--skip-masks",
        action="store_true",
        help="do not decode mask pixels (skips unique-value and oil-fraction checks)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    args = parser.parse_args(argv)

    C.ensure_dirs()
    report = audit(
        pixel_sample=args.pixel_sample,
        scan_masks=not args.skip_masks,
        limit=args.limit,
        progress=not args.quiet,
    )
    C.write_json(C.AUDIT_JSON, report)
    C.AUDIT_MD.write_text(render_markdown(report), encoding="utf-8")

    counts = report["counts"]
    problems = report["problems"]
    print("")
    print("SpillTrace data audit")
    print(f"  images {counts['images']}  masks {counts['masks']}  "
          f"matched {counts['matchedPairs']}  acquisitions {counts['distinctAcquisitions']}")
    print(f"  errors {problems['errorCount']}  warnings {problems['warningCount']}")
    print(f"  drift mode: {report.get('forcing', {}).get('driftMode', 'unknown')}")
    print(f"  wrote {C.AUDIT_MD}")
    print(f"  wrote {C.AUDIT_JSON}")

    if not report["pairing"]["valid"]:
        print("")
        print("FAIL: image/mask pairing is not valid. See DATA_AUDIT.md.")
        return 1
    print("  pairing: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
