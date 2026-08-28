#!/usr/bin/env python3
"""Start the SpillTrace API and dashboard.

    .venv/bin/python scripts/run_api.py                 # API + dashboard on :8765
    .venv/bin/python scripts/run_api.py --port 9000
    .venv/bin/python scripts/run_api.py --no-demo       # skip demo-case generation
    .venv/bin/python scripts/run_api.py --api-only      # no static files
    .venv/bin/python scripts/run_api.py --build-demo    # build the demo case and exit

The demo case is built on first start if it is absent, so the dashboard has something to
show without the operator having to know which endpoint to POST first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _package in ("common", "ml", "drift", "api"):
    sys.path.insert(0, str(ROOT / "services" / _package))

from spilltrace_api import server as server_mod  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the SpillTrace API server")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    parser.add_argument("--api-only", action="store_true", help="do not serve apps/web")
    parser.add_argument("--no-demo", action="store_true", help="do not build the demo case")
    parser.add_argument(
        "--build-demo",
        action="store_true",
        help="build (or rebuild) the offline demo case, then exit without serving",
    )
    parser.add_argument(
        "--particles", type=int, default=None, help="particle count for the demo case"
    )
    args = parser.parse_args()

    if args.build_demo:
        scene = server_mod.build_demo(force=True, particles=args.particles)
        if scene is None:
            print("demo case was not built", file=sys.stderr)
            return 1
        print(f"demo case built from scene {scene}")
        return 0

    server_mod.run(
        host=args.host,
        port=args.port,
        serve_frontend=not args.api_only,
        demo=not args.no_demo,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
