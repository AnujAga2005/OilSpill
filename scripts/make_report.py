"""Generate an incident report PDF for a stored case, from the command line.

The dashboard button and `POST /api/cases/<id>/dispatch` both go through the same
`generate_incident_report`, so this is the quickest way to look at the document
without starting the server:

    .venv/bin/python scripts/make_report.py            # the seeded demo case
    .venv/bin/python scripts/make_report.py --case demo --number ST-DEMO-0001
    .venv/bin/python scripts/make_report.py --dispatch ops@example.gov

`--dispatch` exercises the email path. With no SMTP host configured it writes a
.eml beside the PDF and sends nothing, which is the safe default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _package in ("common", "api", "drift", "ml"):
    sys.path.insert(0, str(ROOT / "services" / _package))

from spilltrace_api.dispatch import DispatchError, dispatch_case_email, dispatch_mode  # noqa: E402
from spilltrace_api.reports import ReportUnavailable, generate_incident_report  # noqa: E402
from spilltrace_api.store import CaseStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", default="demo", help="stored case id (default: demo)")
    parser.add_argument("--number", default=None, help="override the generated case number")
    parser.add_argument("--dispatch", default="", help="also build an email to these addresses")
    args = parser.parse_args(argv)

    case = CaseStore().load(args.case)
    if case is None:
        print(f"case {args.case!r} is not in the store. Build it with:", file=sys.stderr)
        print(f"  .venv/bin/python scripts/run_case.py --scene {args.case}", file=sys.stderr)
        return 2

    try:
        if args.dispatch:
            print(f"dispatch mode: {dispatch_mode()['mode']}")
            result = dispatch_case_email(case, args.dispatch, case_number=args.number)
            for key in ("caseNumber", "sent", "dryRun", "reportPath", "emailPath", "reason"):
                if key in result:
                    print(f"  {key}: {result[key]}")
        else:
            print(f"report: {generate_incident_report(case, case_number=args.number)}")
    except ReportUnavailable as exc:
        print(f"PDF generation unavailable: {exc}", file=sys.stderr)
        return 3
    except (DispatchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
