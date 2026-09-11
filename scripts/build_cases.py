#!/usr/bin/env python3
"""Build and store cases for named scenes, so they appear in the dashboard's picker.

    .venv/bin/python scripts/build_cases.py 00734 00100 00014
    .venv/bin/python scripts/build_cases.py --test-split --limit 8
    .venv/bin/python scripts/build_cases.py --list

The dashboard's case selector lists what the store already holds, not every scene on
disk, because a case takes about twenty seconds to compute and the picker has to answer
instantly. Anything built here is served by a running API at once -- no restart, no
rebuild -- and shows up in the dropdown on the next page load.

Only test-split scenes are built by default. A scene the model trained on will score far
higher than the published accuracy, and demonstrating on one would mean quoting a number
the run on screen contradicts. `--allow-any` overrides that, and says so in the output.

The offline `dist/` bundle is unaffected: its fixtures carry the demo case alone, so
these cases exist only while the API is running.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _package in ("common", "ml", "drift", "api"):
    sys.path.insert(0, str(ROOT / "services" / _package))

from spilltrace_common import config as C  # noqa: E402
from spilltrace_api import case as case_mod  # noqa: E402
from spilltrace_api import store as store_mod  # noqa: E402

STORE = store_mod.CaseStore()


def read_splits() -> dict[str, list[str]]:
    """Scene names by split, or an empty mapping when the split file is absent."""
    path = C.PROCESSED_DIR / "splits.json"
    if not path.exists():
        return {}
    return (json.loads(path.read_text()).get("scenes") or {})


def split_index() -> dict[str, str]:
    """Scene name -> split name."""
    return {
        name: split
        for split, members in read_splits().items()
        for name in members
    }


def scene_scores() -> dict[str, dict[str, float]]:
    """Scene name -> {oil, iou} from the whole-scene evaluation, at its own threshold.

    `preview_manifest.json` only covers the scenes preprocessing rendered previews for,
    which is a small subset. `scene_metrics.json` covers every scene that was evaluated,
    which is the whole of val and test -- and it carries the per-scene IoU, so the listing
    can show what each candidate demo scene actually scores.
    """
    path = C.PROCESSED_DIR / "scene_metrics.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text())
    threshold = str(doc.get("sceneThreshold", ""))
    out: dict[str, dict[str, float]] = {}
    for row in doc.get("perScene") or []:
        entry = (row.get("thresholds") or {}).get(threshold) or {}
        out[row["scene"]] = {
            "oil": row.get("referenceOilFraction"),
            "iou": ((entry.get("scores") or {}).get("iou")),
        }
    return out


def show_list() -> int:
    """Print the test-split scenes with their score and whether a case exists."""
    splits = read_splits()
    test = sorted(splits.get("test") or [])
    if not test:
        print("no splits.json; run scripts/run_preprocess.py first", file=sys.stderr)
        return 1

    scores = scene_scores()
    available = set(case_mod.available_scenes())
    stored = set(STORE)

    def sort_key(name: str) -> float:
        return -((scores.get(name) or {}).get("iou") or 0.0)

    print(f"{len(test)} test-split scenes, best-scoring first\n")
    print(f"{'scene':<10}{'IoU':<9}{'oil':<10}{'on disk':<10}{'case built'}")
    for name in sorted(test, key=sort_key):
        row = scores.get(name) or {}
        iou, oil = row.get("iou"), row.get("oil")
        print(
            f"{name:<10}"
            f"{(f'{iou:.3f}' if iou is not None else '-'):<9}"
            f"{(f'{oil:.4f}' if oil is not None else '-'):<10}"
            f"{('yes' if name in available else 'no'):<10}"
            f"{'yes' if name in stored else 'no'}"
        )
    print(
        "\nA spread beats a highlight reel. Judges ask whether you picked your best one,"
        "\nand the Method screen already ships the worst scene, so build a weak one too."
    )
    return 0


def build_one(scene: str, particles: int, seed: int) -> tuple[bool, str]:
    """Compute one case and store it. Returns (ok, note)."""
    request = case_mod.CaseRequest(
        scene=scene,
        particles=particles,
        previews=True,
        seed=seed,
    )
    started = time.perf_counter()
    try:
        payload = case_mod.build_case(request, progress=lambda m: print(f"    {m}", flush=True))
    except Exception as exc:  # noqa: BLE001 - one bad scene must not stop the batch
        return False, f"{type(exc).__name__}: {exc}"

    mask = payload.pop("_mask", None)
    STORE.save(scene, payload)
    if mask is not None:
        STORE.save_mask(scene, mask, payload.get("detection") or {})

    slick = payload.get("slick") or {}
    area = slick.get("totalAreaKm2")
    count = slick.get("slickCount")
    elapsed = time.perf_counter() - started
    return True, f"{area:.2f} km2 across {count} region(s) in {elapsed:.1f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build stored cases for named scenes")
    parser.add_argument("scenes", nargs="*", help="scene ids, e.g. 00734 00100")
    parser.add_argument(
        "--test-split", action="store_true", help="build test-split scenes, most oil first"
    )
    parser.add_argument("--limit", type=int, default=6, help="cap for --test-split (default 6)")
    parser.add_argument("--list", action="store_true", help="list test scenes and exit")
    parser.add_argument(
        "--allow-any",
        action="store_true",
        help="permit train/val scenes; their scores will not match the published accuracy",
    )
    parser.add_argument("--force", action="store_true", help="rebuild scenes already stored")
    parser.add_argument("--particles", type=int, default=None, help="particle count")
    args = parser.parse_args()

    if args.list:
        return show_list()

    defaults = C.DriftConfig()
    particles = args.particles or defaults.particle_count
    available = set(case_mod.available_scenes())
    where = split_index()

    if args.test_split:
        scores = scene_scores()
        test = set(read_splits().get("test") or [])
        ranked = sorted(
            (n for n in test if n in available),
            key=lambda n: -((scores.get(n) or {}).get("iou") or 0.0),
        )
        wanted = ranked[: args.limit]
    else:
        wanted = list(args.scenes)

    if not wanted:
        parser.error("name at least one scene, or pass --test-split")

    queue = []
    for scene in wanted:
        if scene not in available:
            print(f"skip {scene}: no image/mask pair on disk")
            continue
        split = where.get(scene)
        if split != "test" and not args.allow_any:
            print(
                f"skip {scene}: split is {split or 'unknown'}, not test. "
                f"Pass --allow-any if you accept that its score will beat the published figure."
            )
            continue
        if split != "test":
            print(f"WARNING {scene} is in the {split or 'unknown'} split -- do not quote its accuracy")
        if STORE.exists(scene) and not args.force:
            print(f"skip {scene}: already stored (use --force to rebuild)")
            continue
        queue.append(scene)

    if not queue:
        print("\nnothing to build")
        return 0

    print(f"\nbuilding {len(queue)} case(s) at {particles} particles\n")
    results = []
    for scene in queue:
        print(f"  {scene}")
        ok, note = build_one(scene, particles, defaults.seed)
        print(f"  {'ok  ' if ok else 'FAIL'} {scene}: {note}\n", flush=True)
        results.append((scene, ok, note))

    failed = [scene for scene, ok, _ in results if not ok]
    print(f"built {len(results) - len(failed)} of {len(results)}")
    if failed:
        print("failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    print("Restart is not needed: a running API serves these immediately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
