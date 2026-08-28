"""CLI: Phase 2 preprocessing - splits, patch cache, normalisation, previews.

    python scripts/run_preprocess.py [--max-scenes N] [--patch-size N]
                                     [--previews N] [--quiet]

Reads only from the supplied dataset directories and writes to
``data/processed/``. Nothing in the input directories is modified.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ml"))

from spilltrace_common import config as C  # noqa: E402
from spilltrace_ml import cache as cache_mod  # noqa: E402
from spilltrace_ml.dataset import load_scene  # noqa: E402
from spilltrace_ml.preview import render_scene_previews  # noqa: E402


def build_previews(manifest: dict, count: int, say) -> list[dict]:
    """Render preview PNGs for a spread of scenes across the three splits."""
    if count <= 0:
        return []
    scenes = manifest["scenes"]
    picked: list[dict] = []
    for split in ("test", "val", "train"):
        members = [s for s in scenes if s["split"] == split and s.get("oilFraction")]
        members.sort(key=lambda s: -(s.get("oilFraction") or 0.0))
        picked.extend(members[: max(1, count // 3)])
    picked = picked[:count]

    entries = []
    for position, record in enumerate(picked, start=1):
        image = C.IMAGE_DIR / f"{record['name']}.tif"
        mask = C.MASK_DIR / f"{record['name']}.tif"
        if not image.exists() or not mask.exists():
            continue
        scene = load_scene(image, mask, want_mask=True)
        entry = render_scene_previews(scene)
        entry["split"] = record["split"]
        entry["acquiredStart"] = record.get("acquiredStart")
        entry["region"] = record.get("region")
        entry["referenceOilFraction"] = record.get("oilFraction")
        entries.append(entry)
        say(f"  preview [{position}/{len(picked)}] {record['name']} ({record['split']})")
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the SpillTrace patch cache")
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=None,
        help="decode budget; 0 means every matched pair (about two hours)",
    )
    parser.add_argument("--patch-size", type=int, default=None, help="override patch size")
    parser.add_argument(
        "--previews", type=int, default=9, help="how many scenes get preview PNGs"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    say = (lambda msg: None) if args.quiet else print
    cfg = C.PreprocessConfig()
    if args.patch_size:
        cfg.patch_size = args.patch_size
        cfg.stride = args.patch_size

    started = time.time()
    manifest = cache_mod.build_cache(cfg, max_scenes=args.max_scenes, progress=say)

    previews = build_previews(manifest, args.previews, say)
    if previews:
        C.write_json(
            C.PROCESSED_DIR / "preview_manifest.json",
            {
                "pipelineVersion": C.PIPELINE_VERSION,
                "generatedUtc": C.utc_now_iso(),
                "label": C.LABEL_SATELLITE,
                "note": (
                    "8-bit previews rendered from the supplied GeoTIFFs; the 48 GB raw "
                    "archive is never served to the browser"
                ),
                "scenes": previews,
            },
        )

    print("")
    print("SpillTrace preprocessing")
    for split, info in manifest["splits"].items():
        print(
            f"  {split:5s} {info['patches']:5d} patches "
            f"({info['positives']} positive) from {info['scenes']} scenes / "
            f"{info['acquisitions']} acquisitions"
        )
    stats = manifest["normStats"]
    print(f"  channels {stats['channels']}  mean {stats['mean']}  std {stats['std']}")
    print(f"  clip low {stats['clipLow']}  clip high {stats['clipHigh']}")
    print(f"  previews {len(previews)}")
    print(f"  elapsed {time.time() - started:.1f} s")
    if manifest["failures"]:
        print(f"  FAILURES {len(manifest['failures'])}: {manifest['failures'][:3]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
