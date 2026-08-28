"""Raster preview rendering: PNG tiles the dashboard can display directly.

The raw archive is 48 GB and must stay out of the browser bundle, so each scene
used by the demo gets small 8-bit PNGs written to ``data/processed/previews``:

* ``<scene>_vv.png`` / ``<scene>_vh.png`` - contrast-stretched backscatter
* ``<scene>_mask.png``                    - the supplied reference mask
* ``<scene>_prediction.png``              - thresholded model output, when available
* ``<scene>_probability.png``             - the same output before thresholding
* ``<scene>_comparison.png``              - agreement / false positive / missed oil

PNG is written with the standard library (``zlib`` + CRC32), so there is no
image-library dependency and the encoder stays inspectable.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from spilltrace_common import config as C

# Overlay colour for predicted oil, matching the dashboard accent.
OIL_RGB = (255, 138, 76)
REFERENCE_RGB = (94, 200, 255)
# Comparison panels use three colours so an error is visible as an error rather
# than hidden inside a single "prediction" overlay.
AGREEMENT_RGB = (110, 231, 183)  # predicted oil that the reference also marks
FALSE_POSITIVE_RGB = (255, 138, 76)  # predicted oil the reference does not mark
MISSED_RGB = (94, 200, 255)  # reference oil the model did not predict


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_png(path: str | Path, image: np.ndarray, alpha: np.ndarray | None = None) -> Path:
    """Write a greyscale, RGB or RGBA uint8 array as a PNG."""
    array = np.asarray(image)
    if array.dtype != np.uint8:
        raise ValueError(f"expected uint8, got {array.dtype}")
    if array.ndim == 2:
        array = array[:, :, None]
    height, width, channels = array.shape
    if alpha is not None:
        alpha_arr = np.asarray(alpha, dtype=np.uint8).reshape(height, width, 1)
        if channels == 1:
            array = np.concatenate([np.repeat(array, 3, axis=2), alpha_arr], axis=2)
        elif channels == 3:
            array = np.concatenate([array, alpha_arr], axis=2)
        channels = array.shape[2]
    colour_type = {1: 0, 2: 4, 3: 2, 4: 6}.get(channels)
    if colour_type is None:
        raise ValueError(f"unsupported channel count {channels}")

    # Filter type 0 (None) per scanline; the payload compresses well enough.
    stride = width * channels
    raw = np.zeros((height, stride + 1), dtype=np.uint8)
    raw[:, 1:] = array.reshape(height, stride)
    body = zlib.compress(raw.tobytes(), 6)

    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    blob = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", body)
        + _chunk(b"IEND", b"")
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)
    return path


def downsample(array: np.ndarray, factor: int) -> np.ndarray:
    """Mean-pool by an integer factor, trimming any ragged edge."""
    if factor <= 1:
        return array
    height = (array.shape[0] // factor) * factor
    width = (array.shape[1] // factor) * factor
    view = array[:height, :width].astype(np.float32)
    return view.reshape(height // factor, factor, width // factor, factor).mean(axis=(1, 3))


def downsample_max(array: np.ndarray, factor: int) -> np.ndarray:
    """Max-pool by an integer factor; used for masks so thin slicks survive."""
    if factor <= 1:
        return array
    height = (array.shape[0] // factor) * factor
    width = (array.shape[1] // factor) * factor
    view = array[:height, :width]
    return view.reshape(height // factor, factor, width // factor, factor).max(axis=(1, 3))


def stretch(
    band: np.ndarray,
    invalid: np.ndarray | None = None,
    percentiles: Sequence[float] = (2.0, 98.0),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Contrast-stretch a decibel band to uint8, ignoring invalid samples."""
    values = np.asarray(band, dtype=np.float32)
    if invalid is None:
        sample = values[np.isfinite(values)]
    else:
        sample = values[np.isfinite(values) & ~invalid]
    if sample.size == 0:
        return np.zeros(values.shape, dtype=np.uint8), {"low": None, "high": None}
    low, high = (float(v) for v in np.percentile(sample, percentiles))
    if high - low < 1e-6:
        high = low + 1e-6
    scaled = np.clip((values - low) / (high - low), 0.0, 1.0)
    # A non-finite sample is kept out of the percentiles above, but it would still reach
    # the cast, and NaN to uint8 is undefined -- it puts an arbitrary byte in the picture
    # and raises on the way. Treated as no-data instead: black, like the border.
    scaled = np.where(np.isfinite(scaled), scaled, 0.0)
    out = (scaled * 255.0).astype(np.uint8)
    if invalid is not None:
        out[invalid] = 0
    return out, {
        "low": round(low, 3),
        "high": round(high, 3),
        "percentiles": list(percentiles),
        "unit": "dB",
    }


def mask_png(mask: np.ndarray, rgb: Sequence[int]) -> tuple[np.ndarray, np.ndarray]:
    """Build an RGBA overlay: coloured where the mask is set, transparent elsewhere."""
    binary = np.asarray(mask) > 0
    colour = np.zeros(binary.shape + (3,), dtype=np.uint8)
    for channel, value in enumerate(rgb):
        colour[..., channel] = np.where(binary, value, 0)
    alpha = np.where(binary, 255, 0).astype(np.uint8)
    return colour, alpha


def probability_png(
    probability: np.ndarray, floor: float = 0.05
) -> tuple[np.ndarray, np.ndarray]:
    """A continuous heatmap of predicted oil probability, as RGBA.

    The binary mask answers "is this oil at the chosen threshold"; this answers "how
    close was it", which is the picture that shows an operator where the threshold is
    doing the work. Below ``floor`` the layer is fully transparent, so open water does
    not tint the whole scene and the backscatter beneath stays readable.

    The ramp runs blue -> amber -> white through the same hues the rest of the interface
    uses, and it is monotonic in luminance so it stays legible in greyscale print.
    """
    values = np.clip(np.asarray(probability, dtype=np.float32), 0.0, 1.0)
    # Stops at p = 0, 0.25, 0.5, 0.75, 1.0. Their luminances rise strictly, which is what
    # makes the ramp readable in greyscale: the midpoint is a muted blue-grey rather than
    # the brighter one the hue alone would suggest, because a brighter midpoint prints as
    # the same grey as the amber just above it and the operating point stops being visible.
    stops = np.array(
        [
            [30, 58, 138],  # deep blue: possible, but well under any threshold
            [56, 130, 210],
            [110, 140, 175],
            [255, 138, 76],  # the oil accent, at the usual operating point
            [255, 244, 214],  # near-certain
        ],
        dtype=np.float32,
    )
    position = values * (len(stops) - 1)
    lower = np.clip(np.floor(position).astype(np.int32), 0, len(stops) - 2)
    fraction = (position - lower)[..., None]
    colour = (
        stops[lower] * (1.0 - fraction) + stops[lower + 1] * fraction
    ).astype(np.uint8)
    # Ramp the alpha as well, so weak responses read as weak rather than as a solid
    # patch of blue that looks like a detection.
    alpha = np.where(
        values < floor,
        0.0,
        60.0 + 195.0 * np.clip((values - floor) / max(1.0 - floor, 1e-6), 0.0, 1.0),
    ).astype(np.uint8)
    return colour, alpha


def render_comparison(
    band: np.ndarray,
    invalid: np.ndarray | None,
    reference: np.ndarray | None,
    prediction: np.ndarray | None,
    alpha: float = 0.55,
) -> np.ndarray:
    """Composite one tile as RGB: agreement, false positive and missed oil.

    Three colours rather than one, because a single "prediction" overlay hides
    exactly the information a reviewer needs - where the model was wrong.
    """
    grey, _ = stretch(band, invalid)
    image = np.repeat(grey[:, :, None], 3, axis=2).astype(np.float32)
    ref = np.zeros(grey.shape, dtype=bool) if reference is None else np.asarray(reference) > 0
    pred = (
        np.zeros(grey.shape, dtype=bool) if prediction is None else np.asarray(prediction) > 0
    )
    layers = (
        (ref & pred, AGREEMENT_RGB),
        (pred & ~ref, FALSE_POSITIVE_RGB),
        (ref & ~pred, MISSED_RGB),
    )
    for selection, rgb in layers:
        if not selection.any():
            continue
        for channel, value in enumerate(rgb):
            image[..., channel] = np.where(
                selection,
                (1.0 - alpha) * image[..., channel] + alpha * value,
                image[..., channel],
            )
    if invalid is not None:
        image[np.asarray(invalid) > 0] = 24.0
    return np.clip(image, 0.0, 255.0).astype(np.uint8)


def render_scene_previews(
    scene: Any,
    prediction: np.ndarray | None = None,
    probability: np.ndarray | None = None,
    out_dir: Path | None = None,
    max_side: int = 512,
) -> dict[str, Any]:
    """Write the preview set for one scene and return its manifest entry."""
    out_dir = out_dir or C.PREVIEW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    factor = max(1, int(round(max(scene.height, scene.width) / max_side)))

    invalid_small = downsample_max(scene.invalid.astype(np.uint8), factor) > 0
    files: dict[str, str] = {}
    stretches: dict[str, Any] = {}
    for index, name in enumerate(("vv", "vh")):
        small = downsample(scene.channels[index], factor)
        image, info = stretch(small, invalid_small)
        path = out_dir / f"{scene.name}_{name}.png"
        write_png(path, image)
        files[name] = path.name
        stretches[name] = info

    if scene.mask is not None:
        small = downsample_max(scene.mask.astype(np.uint8), factor)
        colour, alpha = mask_png(small, REFERENCE_RGB)
        path = out_dir / f"{scene.name}_mask.png"
        write_png(path, colour, alpha)
        files["referenceMask"] = path.name

    if prediction is not None:
        small = downsample_max(np.asarray(prediction).astype(np.uint8), factor)
        colour, alpha = mask_png(small, OIL_RGB)
        path = out_dir / f"{scene.name}_prediction.png"
        write_png(path, colour, alpha)
        files["prediction"] = path.name

    if probability is not None:
        # Max-pooled, not mean-pooled: a 4x downsample of a mean would dilute a narrow
        # high-probability filament into the water around it and understate it.
        small = downsample_max(np.asarray(probability, dtype=np.float32), factor)
        colour, alpha = probability_png(small)
        path = out_dir / f"{scene.name}_probability.png"
        write_png(path, colour, alpha)
        files["probability"] = path.name

    if prediction is not None and scene.mask is not None:
        # One tile in which a mistake looks like a mistake: agreement, false positive and
        # missed oil in three colours over the VV backscatter.
        band = downsample(scene.channels[0], factor)
        image = render_comparison(
            band,
            invalid_small,
            downsample_max(scene.mask.astype(np.uint8), factor),
            downsample_max(np.asarray(prediction).astype(np.uint8), factor),
        )
        path = out_dir / f"{scene.name}_comparison.png"
        write_png(path, image)
        files["comparison"] = path.name

    return {
        "scene": scene.name,
        "downsampleFactor": factor,
        "previewSize": [
            int(scene.width // factor),
            int(scene.height // factor),
        ],
        "sourceSize": [int(scene.width), int(scene.height)],
        "bounds": scene.bounds,
        "epsg": scene.epsg,
        "files": files,
        "contrastStretch": stretches,
        "legend": {
            "vv": "Sigma0 VV in decibels, contrast-stretched between the percentiles above",
            "vh": "Sigma0 VH in decibels, contrast-stretched between the percentiles above",
            "referenceMask": "supplied ground-truth oil, in blue",
            "prediction": "predicted oil at the case threshold, in amber",
            "probability": "predicted oil probability before thresholding; blue is low, "
            "amber is around the usual operating point, white is near-certain",
            "comparison": "green where prediction and reference agree, amber where the "
            "model predicted oil the reference does not mark, blue where the reference "
            "marks oil the model missed",
        },
        "label": C.LABEL_SATELLITE,
    }
