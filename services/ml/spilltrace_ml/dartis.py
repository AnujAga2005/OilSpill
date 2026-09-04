"""Reader for the DARTIS 2019 oil-slick / look-alike patch set.

The dataset (doi:10.1594/PANGAEA.980773, Yang & Singha 2025) is the only labelled
*negative* set this project has: 2290 patches of phenomena that look like oil in
SAR and are not, against 1365 patches holding 3225 annotated oil objects. It is
what a false-positive rate is measured from -- see ``KNOWN-ISSUES.md`` item 4.

Three properties of the archive shape this module:

* **The labels live in the index, not only in the XML.** Each row of the
  tab-delimited export carries one object's bounding box in patch pixels
  (``obj_patchloc_*``), so the oil boxes are readable without downloading a
  single ``.xml``. No-oil patches have an empty XML cell because they have
  nothing to annotate.
* **The patches are ~20 m/pixel**, computed from the four published corners --
  640 px spans about 12.8 km. This project's scenes are ~9.5 m/pixel, so a patch
  has to be resampled before a model trained here is allowed to see it, or the
  network meets slicks at half their learned scale.
* **The imagery is 8-bit JPEG**, so calibrated decibels are gone and the
  greyscale stretch is not published. Anything measured across both this set and
  this project's decibel scenes must therefore be invariant to an unknown affine
  rescaling -- which is the constraint the feature set in ``lookalike.py`` is
  built around.

Filenames encode the grouping the paper describes: ``nw-0123-07-000456.jpg`` is
subset ``nw`` (no oil, open water), patch 123, K-Means sub-cluster 07. Oil
patches are plainer -- ``ow-0001.jpg`` -- and carry no cluster field.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

EARTH_RADIUS_M = 6371008.8

SUBSET_LABELS = {
    "oc": "oil, coastal",
    "ow": "oil, open water",
    "nc": "no oil, coastal",
    "nw": "no oil, open water",
}

OIL_SUBSETS = frozenset({"oc", "ow"})
NO_OIL_SUBSETS = frozenset({"nc", "nw"})


class DartisError(RuntimeError):
    """The index or an image is not in the shape this reader expects."""


@dataclass(frozen=True)
class Box:
    """One annotated oil object, in patch pixel coordinates."""

    xmin: int
    ymin: int
    xmax: int
    ymax: int

    @property
    def area_px(self) -> int:
        return max(0, self.xmax - self.xmin) * max(0, self.ymax - self.ymin)


@dataclass(frozen=True)
class Patch:
    """One 640 px patch: where it is, when it was taken, and what is in it."""

    name: str
    subset: str
    cluster: str | None
    tag: str
    patch_name: str
    sentinel_id: str
    start: datetime | None
    end: datetime | None
    width: int
    height: int
    corners: tuple[tuple[float, float], ...]
    boxes: tuple[Box, ...]

    @property
    def has_oil(self) -> bool:
        return self.subset in OIL_SUBSETS

    @property
    def coastal(self) -> bool:
        return self.subset in ("oc", "nc")

    @property
    def product(self) -> str:
        """The parent Sentinel-1 product, used to group patches for splitting.

        Several patches are cut from one satellite pass. Grouping on this is what
        keeps a cross-validation fold from testing on a neighbour of its own
        training data -- the mistake ``KNOWN-ISSUES.md`` item 1 records.
        """
        return self.sentinel_id or self.patch_name

    def spacing_m(self) -> tuple[float, float]:
        """Ground spacing (along-row, along-column) in metres per pixel.

        Taken from the published corners rather than assumed, because the patches
        are cut in radar geometry and are not north-up: the top edge of
        ``ow-0001`` runs west, not east.
        """
        if len(self.corners) != 4:
            raise DartisError(f"{self.name}: expected 4 corners, got {len(self.corners)}")
        ul, ur, br, bl = self.corners
        row = 0.5 * (_haversine_m(ul, ur) + _haversine_m(bl, br))
        col = 0.5 * (_haversine_m(ul, bl) + _haversine_m(ur, br))
        return row / max(self.width, 1), col / max(self.height, 1)

    def centre(self) -> tuple[float, float]:
        lon = sum(c[0] for c in self.corners) / len(self.corners)
        lat = sum(c[1] for c in self.corners) / len(self.corners)
        return lon, lat


def _haversine_m(a: Sequence[float], b: Sequence[float]) -> float:
    lon1, lat1 = float(a[0]), float(a[1])
    lon2, lat2 = float(b[0]), float(b[1])
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(max(0.0, min(1.0, h))))


def _time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _cluster_of(name: str) -> str | None:
    """The K-Means sub-cluster in a no-oil filename, or None for an oil patch.

    ``nc-0001-00-000001.jpg`` -> ``00``. The paper clusters the look-alikes so a
    false-positive rate can be reported per phenomenon family rather than as one
    pooled number that hides which family the detector actually falls for.
    """
    parts = Path(name).stem.split("-")
    if len(parts) >= 4 and parts[2].isdigit():
        return parts[2]
    return None


def _column(header: Sequence[str], needle: str) -> int:
    for index, title in enumerate(header):
        if needle in title:
            return index
    raise DartisError(f"column containing {needle!r} not found in {list(header)}")


def find_index(directory: Path) -> Path:
    """Locate the PANGAEA export inside ``directory``.

    It arrives named ``DARTIS_2019.tab`` -- tab-delimited *text*, not a MapInfo
    TAB -- and the name is not worth depending on.
    """
    candidates = sorted(directory.glob("*.tab")) + sorted(directory.glob("*.tsv"))
    if not candidates:
        raise DartisError(
            f"no tab-delimited index in {directory}. Download it from the PANGAEA "
            "page ('Download dataset as tab-delimited text', UTF-8) and move it there; "
            "scripts/fetch_dartis2019.py documents the whole sequence."
        )
    if len(candidates) > 1:
        listing = ", ".join(c.name for c in candidates)
        raise DartisError(f"several candidate indexes in {directory}: {listing}")
    return candidates[0]


def read_index(path: Path) -> list[Patch]:
    """Parse the export into one :class:`Patch` per image, boxes collapsed.

    PANGAEA prefixes the header with a citation block delimited by ``/*`` and
    ``*/``; the real header is the first line after it. A patch holding three oil
    objects appears as three consecutive rows sharing one JPEG name, so the row
    count is the object count and the boxes have to be gathered.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.strip() == "*/":
            start = index + 1
            break
    if start >= len(lines):
        raise DartisError(f"{path} has no rows after its metadata block")
    header = lines[start].split("\t")

    jpg_i = _column(header, "jpg_file")
    subset_i = _column(header, "subset")
    tag_i = _column(header, "tag")
    patch_i = _column(header, "patch_name")
    sentinel_i = _column(header, "Sentinel_ID")
    start_i = _column(header, "start_time")
    end_i = _column(header, "end_time")
    width_i = _column(header, "patch_width")
    height_i = _column(header, "patch_height")
    corner_i = [
        (_column(header, "patch_ul_lon"), _column(header, "patch_ul_lat")),
        (_column(header, "patch_ur_lon"), _column(header, "patch_ur_lat")),
        (_column(header, "patch_br_lon"), _column(header, "patch_br_lat")),
        (_column(header, "patch_bl_lon"), _column(header, "patch_bl_lat")),
    ]
    box_i = [
        _column(header, "obj_patchloc_xmin"),
        _column(header, "obj_patchloc_ymin"),
        _column(header, "obj_patchloc_xmax"),
        _column(header, "obj_patchloc_ymax"),
    ]

    order: list[str] = []
    seen: dict[str, dict[str, Any]] = {}
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        name = cells[jpg_i].strip()
        if not name:
            continue
        if name not in seen:
            order.append(name)
            corners = []
            for lon_i, lat_i in corner_i:
                try:
                    corners.append((float(cells[lon_i]), float(cells[lat_i])))
                except (TypeError, ValueError):
                    corners = []
                    break
            subset = cells[subset_i].strip().split(":")[0].strip() or name.split("-")[0]
            seen[name] = {
                "name": name,
                "subset": subset,
                "cluster": _cluster_of(name),
                "tag": cells[tag_i].strip(),
                "patch_name": cells[patch_i].strip(),
                "sentinel_id": cells[sentinel_i].strip(),
                "start": _time(cells[start_i]),
                "end": _time(cells[end_i]),
                "width": _int(cells[width_i], 640),
                "height": _int(cells[height_i], 640),
                "corners": tuple(corners),
                "boxes": [],
            }
        raw = [cells[i].strip() for i in box_i]
        if all(raw):
            box = Box(_int(raw[0]), _int(raw[1]), _int(raw[2]), _int(raw[3]))
            if box.area_px > 0:
                seen[name]["boxes"].append(box)

    out: list[Patch] = []
    for name in order:
        record = seen[name]
        record["boxes"] = tuple(record["boxes"])
        out.append(Patch(**record))
    return out


def summarise(patches: Iterable[Patch]) -> dict[str, Any]:
    """Counts by subset and by sub-cluster, for checking a partial download."""
    items = list(patches)
    subsets: dict[str, int] = {}
    clusters: dict[str, int] = {}
    objects = 0
    for patch in items:
        subsets[patch.subset] = subsets.get(patch.subset, 0) + 1
        objects += len(patch.boxes)
        if patch.cluster is not None:
            key = f"{patch.subset}-{patch.cluster}"
            clusters[key] = clusters.get(key, 0) + 1
    return {
        "patches": len(items),
        "oilPatches": sum(n for s, n in subsets.items() if s in OIL_SUBSETS),
        "noOilPatches": sum(n for s, n in subsets.items() if s in NO_OIL_SUBSETS),
        "annotatedObjects": objects,
        "bySubset": dict(sorted(subsets.items())),
        "byCluster": dict(sorted(clusters.items())),
        "subsetLabels": {k: v for k, v in SUBSET_LABELS.items() if k in subsets},
    }


def present(patches: Iterable[Patch], images_dir: Path) -> list[Patch]:
    """Only the patches whose JPEG is actually on disk.

    The archive is served file by file, so a partial fetch is the normal state.
    Every measurement reports how much of the set it saw rather than assuming.
    """
    return [p for p in patches if (images_dir / p.name).is_file()]


def load_image(
    patch: Patch,
    images_dir: Path,
    target_spacing_m: float | None = None,
) -> tuple[np.ndarray, float]:
    """Load one patch as float32 digital numbers, optionally resampled.

    Returns ``(plane, spacing_m)``. With ``target_spacing_m`` set, the patch is
    scaled so one output pixel covers that much ground -- the step that lets a
    model trained at 9.5 m/pixel see a 20 m/pixel patch at the scale it learned.
    ``INTER_AREA`` and ``INTER_LINEAR`` are chosen by direction because area
    averaging is right for shrinking and wrong for growing.
    """
    path = images_dir / patch.name
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise DartisError(f"could not decode {path}")
    if raw.ndim == 3:
        raw = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
    plane = raw.astype(np.float32)

    row_spacing, col_spacing = patch.spacing_m()
    native = 0.5 * (row_spacing + col_spacing)
    if target_spacing_m is None or target_spacing_m <= 0:
        return plane, native

    scale = native / float(target_spacing_m)
    if abs(scale - 1.0) < 1e-3:
        return plane, native
    height = max(8, int(round(plane.shape[0] * scale)))
    width = max(8, int(round(plane.shape[1] * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resampled = cv2.resize(plane, (width, height), interpolation=interpolation)
    return resampled.astype(np.float32), float(target_spacing_m)


def oil_mask(patch: Patch, shape: tuple[int, int]) -> np.ndarray:
    """A boolean mask of the annotated oil boxes, scaled to ``shape``.

    These are bounding boxes, not pixel labels: a box contains oil *and* the
    water around it. It is usable for "did the detector fire where oil is", never
    for IoU against a segmentation.
    """
    mask = np.zeros(shape, dtype=bool)
    if not patch.boxes:
        return mask
    sy = shape[0] / max(patch.height, 1)
    sx = shape[1] / max(patch.width, 1)
    for box in patch.boxes:
        y0 = max(0, min(shape[0], int(math.floor(box.ymin * sy))))
        y1 = max(0, min(shape[0], int(math.ceil(box.ymax * sy))))
        x0 = max(0, min(shape[1], int(math.floor(box.xmin * sx))))
        x1 = max(0, min(shape[1], int(math.ceil(box.xmax * sx))))
        if y1 > y0 and x1 > x0:
            mask[y0:y1, x0:x1] = True
    return mask
