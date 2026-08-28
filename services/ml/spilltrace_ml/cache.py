"""Phase 2: turn the raw image/mask pairs into a training-ready patch cache.

The raw archive is 48 GB of LZW-compressed float32 and takes about 5.7 s per
scene to decode, so training reads from a compact cache instead:

* scenes are chosen round-robin across the 270 parent acquisitions, so a decode
  budget still covers the full geographic and temporal spread;
* splits are assigned by parent acquisition before any patch is cut, so no crop
  of one acquisition can appear in two splits;
* patches are stored as float16 decibels with a uint8 target, and normalisation
  statistics are computed from the training split only.

Nothing here writes to the supplied dataset directories.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from spilltrace_common import config as C
from spilltrace_common import dimap
from spilltrace_common.geotiff import GeoTiffError

from .dataset import (
    LABEL_INVALID,
    LABEL_OIL,
    Patch,
    PreprocessError,
    Scene,
    compute_norm_stats,
    iter_patches,
    load_scene,
    make_splits,
    select_patches,
)

SPLITS = ("train", "val", "test")


def cache_path(split: str) -> Path:
    return C.CACHE_DIR / f"patches_{split}.npz"


def index_path() -> Path:
    return C.PROCESSED_DIR / "patch_index.json"


# ---------------------------------------------------------------------------
# Scene selection
# ---------------------------------------------------------------------------


@dataclass
class ScenePair:
    name: str
    image: Path
    mask: Path
    group_key: str | None = None
    acquired: str | None = None
    bounds: list[float] | None = None
    region: str | None = None


def discover_pairs(
    image_dir: Path | None = None, mask_dir: Path | None = None
) -> list[ScenePair]:
    """Match every image with its mask by stem, using only what is on disk."""
    image_dir = image_dir or C.IMAGE_DIR
    mask_dir = mask_dir or C.MASK_DIR
    images = {
        p.stem: p
        for p in sorted(image_dir.iterdir())
        if p.suffix.lower() in {".tif", ".tiff"} and p.is_file()
    }
    masks = {
        p.stem: p
        for p in sorted(mask_dir.iterdir())
        if p.suffix.lower() in {".tif", ".tiff"} and p.is_file()
    }
    return [
        ScenePair(name=stem, image=images[stem], mask=masks[stem])
        for stem in sorted(set(images) & set(masks))
    ]


def annotate_from_audit(pairs: Sequence[ScenePair]) -> list[ScenePair]:
    """Attach the group key / time / bounds the audit already established.

    Reading them back is far cheaper than re-parsing 1200 embedded DIMAP headers,
    and it keeps the cache consistent with the published audit. When the audit is
    missing the headers are read directly instead.
    """
    lookup: dict[str, dict[str, Any]] = {}
    if C.AUDIT_JSON.exists():
        report = C.read_json(C.AUDIT_JSON) or {}
        for scene in report.get("scenes", []):
            lookup[scene["name"]] = scene
    out = []
    for pair in pairs:
        info = lookup.get(pair.name)
        if info is None:
            try:
                header = dimap.read_header(pair.image)
            except Exception:  # noqa: BLE001 - any read failure falls back to the stem
                header = None
            pair.group_key = header.group_key if header else None
            pair.acquired = dimap.iso_utc(header.scene_start) if header else None
        else:
            # The audit writes these in snake_case; accept camelCase too so a report from
            # either convention still yields a parent-product key. Reading only camelCase
            # silently left every key None, which made the split fall back to the scene
            # stem below -- one group per crop, and sibling crops of one acquisition free
            # to land on opposite sides of the train/test line.
            pair.group_key = info.get("group_key") or info.get("groupKey")
            pair.acquired = info.get("acquired_start") or info.get("acquiredStart")
            pair.bounds = info.get("bounds")
            pair.region = info.get("region")
        out.append(pair)
    return out


def choose_scenes(
    pairs: Sequence[ScenePair], max_scenes: int
) -> list[ScenePair]:
    """Pick a decode budget that still covers every parent acquisition.

    Taking the first N filenames would concentrate on a handful of acquisitions.
    Round-robin over the groups spends the budget on breadth first, then depth.
    """
    if max_scenes <= 0 or max_scenes >= len(pairs):
        return list(pairs)
    groups: dict[str, list[ScenePair]] = defaultdict(list)
    for pair in pairs:
        groups[pair.group_key or pair.name].append(pair)
    ordered = [sorted(groups[k], key=lambda p: p.name) for k in sorted(groups)]
    chosen: list[ScenePair] = []
    depth = 0
    while len(chosen) < max_scenes:
        added = False
        for members in ordered:
            if depth < len(members):
                chosen.append(members[depth])
                added = True
                if len(chosen) >= max_scenes:
                    break
        if not added:
            break
        depth += 1
    return sorted(chosen, key=lambda p: p.name)


# ---------------------------------------------------------------------------
# Cache build
# ---------------------------------------------------------------------------


def _stack(patches: Sequence[Patch]) -> tuple[np.ndarray, np.ndarray]:
    if not patches:
        return (
            np.zeros((0, 2, 1, 1), dtype=np.float16),
            np.zeros((0, 1, 1), dtype=np.uint8),
        )
    channels = np.stack([p.channels for p in patches]).astype(np.float16)
    targets = np.stack([p.target for p in patches]).astype(np.uint8)
    return channels, targets


def _patch_record(patch: Patch) -> dict[str, Any]:
    return {
        "scene": patch.scene,
        "row": patch.row,
        "col": patch.col,
        "oilFraction": round(patch.oil_fraction, 6),
        "invalidFraction": round(patch.invalid_fraction, 6),
    }


def _scene_record(scene: Scene, pair: ScenePair, split: str, kept: int) -> dict[str, Any]:
    return {
        "name": scene.name,
        "split": split,
        "groupKey": scene.group_key or pair.group_key,
        "acquiredStart": scene.acquired_start,
        "acquiredStop": scene.acquired_stop,
        "bounds": scene.bounds,
        "epsg": scene.epsg,
        "transform": scene.transform,
        "bandSource": scene.band_source,
        "region": pair.region,
        "validFraction": round(scene.valid_fraction(), 6),
        "oilFraction": (
            round(float((scene.mask == 1).mean()), 6) if scene.mask is not None else None
        ),
        "patchesKept": kept,
    }


def build_cache(
    cfg: C.PreprocessConfig | None = None,
    max_scenes: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Decode the selected scenes and write the patch cache plus its manifest."""
    cfg = cfg or C.PreprocessConfig()
    say = progress or (lambda _msg: None)
    C.ensure_dirs()

    pairs = annotate_from_audit(discover_pairs())
    if not pairs:
        raise PreprocessError(
            f"no image/mask pairs found under {C.IMAGE_DIR} and {C.MASK_DIR}"
        )
    budget = cfg.max_scenes if max_scenes is None else max_scenes
    selected = choose_scenes(pairs, budget)
    say(
        f"{len(pairs)} pairs available, {len(selected)} selected across "
        f"{len({p.group_key or p.name for p in selected})} acquisitions"
    )

    splits = make_splits(
        [{"name": p.name, "group_key": p.group_key} for p in selected], cfg
    )
    assigned = {
        name: split for split, names in splits["scenes"].items() for name in names
    }

    buckets: dict[str, list[Patch]] = {s: [] for s in SPLITS}
    scene_records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    started = time.time()

    for position, pair in enumerate(selected, start=1):
        split = assigned.get(pair.name, "train")
        try:
            scene = load_scene(pair.image, pair.mask, want_mask=True)
        except (PreprocessError, GeoTiffError, OSError, ValueError) as exc:
            failures.append({"scene": pair.name, "error": f"{type(exc).__name__}: {exc}"})
            say(f"  [{position}/{len(selected)}] {pair.name}: FAILED {exc}")
            continue
        patches = select_patches(list(iter_patches(scene, cfg)), cfg)
        buckets[split].extend(patches)
        scene_records.append(_scene_record(scene, pair, split, len(patches)))
        if position % 10 == 0 or position == len(selected):
            elapsed = time.time() - started
            rate = elapsed / position
            say(
                f"  [{position}/{len(selected)}] {pair.name} -> {split}, "
                f"{len(patches)} patches, {rate:.2f} s/scene, "
                f"{(len(selected) - position) * rate / 60.0:.1f} min left"
            )

    if not buckets["train"]:
        raise PreprocessError("no training patches survived selection")

    # Normalisation comes from the training split alone: using val or test would
    # leak held-out statistics into the model input.
    stats = compute_norm_stats(buckets["train"], tuple(cfg.clip_percentiles))
    C.write_json(C.NORM_STATS_PATH, stats)

    manifest: dict[str, Any] = {
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "config": cfg.to_dict(),
        "sceneBudget": budget,
        "pairsAvailable": len(pairs),
        "scenesSelected": len(selected),
        "scenesDecoded": len(scene_records),
        "sceneSelectionRule": (
            "scenes are taken round-robin across parent acquisitions so a partial "
            "decode budget still covers every acquisition, region and date"
        ),
        "failures": failures,
        "splits": {},
        "scenes": scene_records,
        "normStats": stats,
    }

    for split in SPLITS:
        patches = buckets[split]
        channels, targets = _stack(patches)
        path = cache_path(split)
        np.savez_compressed(path, channels=channels, targets=targets)
        oil = np.asarray([p.oil_fraction for p in patches], dtype=np.float64)
        positives = int((oil >= cfg.min_oil_fraction).sum())
        manifest["splits"][split] = {
            "file": path.name,
            "patches": len(patches),
            "positives": positives,
            "negatives": len(patches) - positives,
            "scenes": splits["counts"].get(split, 0),
            "acquisitions": splits["groupCounts"].get(split, 0),
            "oilPixelFraction": (
                round(
                    float(
                        (targets == LABEL_OIL).sum()
                        / max(1, int((targets != LABEL_INVALID).sum()))
                    ),
                    6,
                )
                if len(patches)
                else 0.0
            ),
            "invalidPixelFraction": (
                round(float((targets == LABEL_INVALID).mean()), 6) if len(patches) else 0.0
            ),
            "meanOilFractionPerPatch": round(float(oil.mean()), 6) if len(patches) else 0.0,
            "sizeBytes": path.stat().st_size,
        }
        say(
            f"{split}: {len(patches)} patches "
            f"({positives} positive) -> {path.name} "
            f"({path.stat().st_size / 1e6:.1f} MB)"
        )

    C.write_json(C.SPLITS_PATH, splits)
    C.write_json(
        index_path(),
        {
            "pipelineVersion": C.PIPELINE_VERSION,
            "patchSize": cfg.patch_size,
            "patches": {
                split: [_patch_record(p) for p in buckets[split]] for split in SPLITS
            },
        },
    )
    manifest_path = C.PROCESSED_DIR / "cache_manifest.json"
    C.write_json(manifest_path, manifest)
    say(f"wrote {manifest_path}")
    return manifest


# ---------------------------------------------------------------------------
# Cache read
# ---------------------------------------------------------------------------


def load_split(split: str) -> tuple[np.ndarray, np.ndarray]:
    """Load one cached split as ``(channels float32, targets uint8)``."""
    path = cache_path(split)
    if not path.exists():
        raise PreprocessError(f"{path} is missing; run scripts/run_preprocess.py first")
    with np.load(path) as data:
        channels = np.asarray(data["channels"], dtype=np.float32)
        targets = np.asarray(data["targets"], dtype=np.uint8)
    return channels, targets


def load_manifest() -> dict[str, Any]:
    path = C.PROCESSED_DIR / "cache_manifest.json"
    manifest = C.read_json(path)
    if manifest is None:
        raise PreprocessError(f"{path} is missing; run scripts/run_preprocess.py first")
    return manifest
