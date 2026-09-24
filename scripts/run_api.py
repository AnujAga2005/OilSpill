#!/usr/bin/env python3
"""Start the SpillTrace API and dashboard.

    .venv/bin/python scripts/run_api.py                 # API + dashboard on :8765
    .venv/bin/python scripts/run_api.py --port 9000
    .venv/bin/python scripts/run_api.py --demo          # also build the demo case if absent
    .venv/bin/python scripts/run_api.py --api-only      # no static files
    .venv/bin/python scripts/run_api.py --build-demo    # build the demo case and exit

The server starts and serves whatever cases are already stored on disk. The offline demo
case is *not* built on start unless you ask for it with --demo: it is a ~20s pipeline run,
and every deployment and most local runs already have the cases they need committed.
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
    # Building the demo case is opt-in. The two flags share a destination so `--demo` and
    # `--no-demo` are exact opposites; the default is off, so a bare run never spends ~20s
    # rebuilding a case at boot. `--no-demo` is kept (and is a no-op against the default) so
    # existing callers -- the Dockerfile among them -- keep working unchanged.
    demo = parser.add_mutually_exclusive_group()
    demo.add_argument(
        "--demo",
        dest="demo",
        action="store_true",
        default=False,
        help="build the offline demo case on start if it is absent",
    )
    demo.add_argument(
        "--no-demo",
        dest="demo",
        action="store_false",
        help="do not build the demo case (the default; kept for compatibility)",
    )
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
        demo=args.demo,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
