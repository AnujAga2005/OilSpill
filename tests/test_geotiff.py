"""Tests for the hand-written GeoTIFF reader.

The reader exists because no GDAL/rasterio wheel is installable here, so it is the
single point of failure for the whole pipeline: every metric, polygon and drift run
is computed from bytes this module decodes. Self-consistency is therefore not enough
evidence. Two independent witnesses are used instead:

1. **libtiff**, reached through ``cv2.imwrite``. OpenCV can write LZW, Deflate and
   PackBits TIFFs but refuses to *read* IEEE-float ones (it asserts on
   ``SAMPLEFORMAT_IEEEFP``), which is why it cannot replace this reader. As an
   encoder it is a genuinely separate implementation, so a byte-for-byte match on a
   libtiff-produced stream is real evidence the decoders agree.
2. **The supplied scenes themselves**, for the pathological case libtiff round-trips
   never produce: trailing byte-alignment padding that forms a syntactically valid
   LZW code. ``Oil/00270.tif`` contains one and is pinned here as a regression.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from spilltrace_common import config as C
from spilltrace_common import geotiff as G

# The scene whose tile 15 ends with padding bits that decode to a valid-looking code.
PADDED_SCENE = "00270"
PADDED_TILE = 15
PIXEL_DEG = 8.9831528e-05  # measured across all 1200 supplied scenes

IMAGE = C.IMAGE_DIR / f"{PADDED_SCENE}.tif"
MASK = C.MASK_DIR / f"{PADDED_SCENE}.tif"

needs_image = pytest.mark.skipif(not IMAGE.exists(), reason=f"{IMAGE} not present")
needs_mask = pytest.mark.skipif(not MASK.exists(), reason=f"{MASK} not present")


@pytest.fixture(scope="module")
def tmpdir_path() -> Path:
    """Scratch directory that respects the sandbox's TMPDIR."""
    return Path(os.environ.get("TMPDIR", "/tmp"))


def write_with_libtiff(path: Path, image: np.ndarray, compression: int) -> None:
    """Encode ``image`` with libtiff, or skip the test if that codec is unavailable."""
    ok = cv2.imwrite(str(path), image, [cv2.IMWRITE_TIFF_COMPRESSION, compression])
    if not ok:
        pytest.skip(f"libtiff in this build cannot write compression {compression}")


def mixed_content(rows: int = 512, cols: int = 640) -> np.ndarray:
    """An image that exercises every branch of the LZW state machine.

    A flat region produces long matches and a table that fills slowly; a gradient
    produces medium matches; uniform noise fills the 12-bit table repeatedly and so
    forces both the 9->10->11->12 width transitions and several ClearCode resets.
    """
    rng = np.random.default_rng(7)
    img = np.zeros((rows, cols), np.uint8)
    img[:, : cols // 3] = 33
    band = np.arange(cols // 3, dtype=np.uint8)
    img[rows // 5 : rows // 2, cols // 3 : cols // 3 + band.size] = np.tile(band, (rows // 2 - rows // 5, 1))
    img[rows // 2 :, :] = rng.integers(0, 256, size=(rows - rows // 2, cols), dtype=np.uint8)
    return img


def pack9(*codes: int) -> bytes:
    """Pack 9-bit LZW codes MSB-first and zero-pad to a whole number of bytes."""
    bits = 9 * len(codes)
    total = -(-bits // 8) * 8
    packed = 0
    for code in codes:
        packed = (packed << 9) | code
    return (packed << (total - bits)).to_bytes(total // 8, "big")


# ---------------------------------------------------------------------------
# LZW: agreement with libtiff
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,compression",
    [("lzw", 5), ("uncompressed", 1), ("deflate", 8), ("packbits", 32773)],
)
def test_libtiff_written_scene_round_trips_byte_for_byte(tmpdir_path, name, compression):
    """Every codec we claim to support must reproduce libtiff's input exactly."""
    img = mixed_content()
    path = tmpdir_path / f"spilltrace_codec_{name}.tif"
    write_with_libtiff(path, img, compression)

    arr, meta = G.read_geotiff(str(path))

    assert arr.shape == (1, img.shape[0], img.shape[1])
    assert np.array_equal(arr[0].astype(np.uint8), img)
    # 512 rows over libtiff's default strip size leaves a short final strip, so this
    # also covers the `expected` byte count being smaller for the last block.
    assert meta.n_blocks > 1
    assert meta.rows_per_strip is not None
    assert meta.height % meta.rows_per_strip != 0


def test_libtiff_multiband_and_16_bit_lzw(tmpdir_path):
    """Contiguous multi-sample data and >8-bit samples with a horizontal predictor."""
    rng = np.random.default_rng(11)
    three_band = rng.integers(0, 256, size=(64, 80, 3), dtype=np.uint8)
    sixteen_bit = rng.integers(0, 65536, size=(64, 80), dtype=np.uint16)

    rgb_path = tmpdir_path / "spilltrace_rgb.tif"
    u16_path = tmpdir_path / "spilltrace_u16.tif"
    write_with_libtiff(rgb_path, three_band, 5)
    write_with_libtiff(u16_path, sixteen_bit, 5)

    rgb, rgb_meta = G.read_geotiff(str(rgb_path))
    u16, u16_meta = G.read_geotiff(str(u16_path))

    assert rgb_meta.samples == 3
    assert rgb_meta.planar_config == 1
    assert rgb.shape == (3, 64, 80)
    # OpenCV writes channels in BGR order; band content must match one ordering exactly.
    planes = np.transpose(three_band, (2, 0, 1))
    assert np.array_equal(rgb.astype(np.uint8), planes[::-1]) or np.array_equal(rgb.astype(np.uint8), planes)

    assert u16_meta.dtype == "<u2"
    assert np.array_equal(u16[0].astype(np.uint16), sixteen_bit)
    # libtiff defaults to predictor 2 here, so _undo_predictor was exercised above.
    assert u16_meta.predictor == 2


def test_predictor_is_undone_not_ignored(tmpdir_path):
    """A ramp is the case where forgetting the predictor still looks plausible.

    Horizontal differencing turns a left-to-right ramp into a constant, which decodes
    to a flat image if the predictor is skipped -- an error that a noise image would
    expose as garbage but a ramp would hide as "smooth".
    """
    ramp = np.tile(np.arange(256, dtype=np.uint8), (32, 1))
    path = tmpdir_path / "spilltrace_ramp.tif"
    write_with_libtiff(path, ramp, 5)

    arr, meta = G.read_geotiff(str(path))

    assert meta.predictor == 2
    assert np.array_equal(arr[0].astype(np.uint8), ramp)
    assert arr[0, 0, :].std() > 0  # not collapsed to a constant row


# ---------------------------------------------------------------------------
# LZW: the trailing-padding case
# ---------------------------------------------------------------------------


def build_padded_stream() -> bytes:
    """Three 9-bit codes packed MSB-first, the third being alignment padding.

    Codes 65 and 66 emit ``b"AB"`` and grow the table to 259 entries. Code 300 is
    then undefined, which is exactly the shape of the real corruption: a decoder
    that ignores ``expected`` raises on bits that are not data at all.
    """
    return pack9(65, 66, 300)


def test_padding_bits_are_ignored_when_the_block_size_is_known():
    assert G.lzw_decode(build_padded_stream(), expected=2) == b"AB"


def test_padding_bits_still_raise_when_the_block_size_is_unknown():
    """`expected=0` is the honest-failure path, not a silently truncated one."""
    with pytest.raises(G.GeoTiffError, match="undefined code 300"):
        G.lzw_decode(build_padded_stream(), expected=0)


def test_a_final_entry_straddling_the_boundary_is_trimmed():
    """Two bytes requested, but the third code emits a 2-byte entry."""
    # 65 -> "A"; 66 -> "B" (table gains "AB" = 258); 258 -> "AB".
    stream = pack9(65, 66, 258, 257)
    assert G.lzw_decode(stream, expected=4) == b"ABAB"
    assert G.lzw_decode(stream, expected=3) == b"ABA"


def test_clear_code_resets_the_table():
    """After a reset, a previously defined code must be undefined again."""
    # 65 "A", 66 "B" (defines 258="AB"), ClearCode, then 258 -> undefined.
    stream = pack9(65, 66, 256, 258, 257)
    with pytest.raises(G.GeoTiffError, match="undefined code 258"):
        G.lzw_decode(stream, expected=0)


def test_a_truncated_stream_returns_what_it_decoded():
    """Running out of bytes mid-code is not corruption of the bytes already emitted."""
    assert G.lzw_decode(pack9(65, 66)[:3], expected=0) == b"AB"


@needs_image
def test_real_scene_tile_needs_the_expected_byte_count():
    """Regression for the failure that broke 1 of 240 scenes in the cache build.

    Tile 15 of this scene decodes all 2,097,152 of its bytes and consumes its entire
    code stream, then the byte-alignment padding forms a 10-bit code 514 against a
    511-entry table. Honouring ``expected`` is what makes the tile readable.
    """
    with G.GeoTiff(str(IMAGE)) as tif:
        meta = tif.meta
        assert meta.tiled and meta.compression == 5
        offsets, counts = tif._block_table()
        assert PADDED_TILE < len(offsets)
        per_tile = meta.tile_width * meta.tile_height * meta.samples * np.dtype(meta.dtype).itemsize
        tif._fh.seek(offsets[PADDED_TILE])
        raw = tif._fh.read(counts[PADDED_TILE])

    assert len(raw) == counts[PADDED_TILE]
    assert len(G.lzw_decode(raw, expected=per_tile)) == per_tile
    with pytest.raises(G.GeoTiffError, match="undefined code"):
        G.lzw_decode(raw, expected=0)


# ---------------------------------------------------------------------------
# PackBits
# ---------------------------------------------------------------------------


def test_packbits_literal_run_and_repeat():
    # 2 literals, then 4 copies of 0xAA, then a 128 no-op, then 1 literal.
    data = bytes([1, 0x10, 0x20, 257 - 4, 0xAA, 128, 0, 0x30])
    assert G.packbits_decode(data) == bytes([0x10, 0x20, 0xAA, 0xAA, 0xAA, 0xAA, 0x30])


def test_packbits_ignores_a_truncated_trailing_header():
    """A repeat header with no byte after it must not raise or invent data."""
    assert G.packbits_decode(bytes([0, 0x99, 257 - 3])) == bytes([0x99])


# ---------------------------------------------------------------------------
# Affine
# ---------------------------------------------------------------------------


def north_up(origin_lon: float, origin_lat: float, pixel: float) -> G.Affine:
    return G.Affine(origin_lon, pixel, 0.0, origin_lat, 0.0, -pixel)


def test_affine_maps_pixel_centres_and_inverts():
    t = north_up(-89.5, 28.6, PIXEL_DEG)

    assert t.apply(0, 0) == (-89.5, 28.6)
    x, y = t.apply(100, 200)
    assert x == pytest.approx(-89.5 + 100 * PIXEL_DEG)
    assert y == pytest.approx(28.6 - 200 * PIXEL_DEG)

    col, row = t.world_to_pixel(x, y)
    assert col == pytest.approx(100.0, abs=1e-6)
    assert row == pytest.approx(200.0, abs=1e-6)


def test_affine_inverse_of_a_rotated_transform_round_trips():
    """Four of the supplied masks carry a non-north-up transform, so rotation matters."""
    t = G.Affine(10.0, 0.0, 0.5, 20.0, -0.25, 0.0)
    for col, row in [(0, 0), (3, 7), (11.5, -2.5)]:
        x, y = t.apply(col, row)
        back_col, back_row = t.world_to_pixel(x, y)
        assert back_col == pytest.approx(col, abs=1e-9)
        assert back_row == pytest.approx(row, abs=1e-9)


def test_affine_reports_pixel_size_and_scales_for_decimation():
    t = north_up(0.0, 0.0, PIXEL_DEG)
    assert t.pixel_width == pytest.approx(PIXEL_DEG)
    assert t.pixel_height == pytest.approx(PIXEL_DEG)

    half = t.scaled(2.0, 2.0)
    assert half.pixel_width == pytest.approx(2 * PIXEL_DEG)
    # Decimating must not move the raster origin.
    assert half.apply(0, 0) == t.apply(0, 0)
    assert half.apply(1, 1) == t.apply(2, 2)


def test_affine_as_list_matches_the_gdal_style_order():
    t = north_up(-89.5, 28.6, PIXEL_DEG)
    assert t.as_list() == [-89.5, PIXEL_DEG, 0.0, 28.6, 0.0, -PIXEL_DEG]


def test_singular_transform_cannot_be_inverted():
    with pytest.raises(G.GeoTiffError):
        G.Affine(0.0, 1.0, 1.0, 0.0, 2.0, 2.0).inverse()


# ---------------------------------------------------------------------------
# dtype resolution
# ---------------------------------------------------------------------------


def test_dtype_resolution_covers_the_formats_present_in_the_dataset():
    assert G._numpy_dtype(32, 3, True) == np.dtype(">f4")
    assert G._numpy_dtype(8, 1, False) == np.dtype("|u1")
    assert G._numpy_dtype(16, 2, True) == np.dtype(">i2")
    assert G._numpy_dtype(64, 3, False) == np.dtype("<f8")


def test_unsupported_dtype_is_rejected_rather_than_guessed():
    with pytest.raises(G.GeoTiffError):
        G._numpy_dtype(12, 1, False)


# ---------------------------------------------------------------------------
# Real supplied files
# ---------------------------------------------------------------------------


@needs_mask
def test_mask_matches_libtiffs_own_read():
    """The masks are uint8, so OpenCV *can* read them -- an end-to-end cross-check.

    This covers the stripped path (RowsPerStrip=1) and the geo tag parsing at once.
    """
    mine, meta = G.read_geotiff(str(MASK))
    ref = cv2.imread(str(MASK), cv2.IMREAD_UNCHANGED)

    assert ref is not None, "OpenCV could not read the mask; the cross-check is void"
    assert mine.shape == (1, meta.height, meta.width)
    assert np.array_equal(mine[0].astype(np.uint8), ref)
    assert set(np.unique(mine[0]).tolist()) <= {0, 1}
    assert meta.rows_per_strip == 1
    assert meta.n_blocks == meta.height


@needs_mask
def test_decimated_read_samples_the_full_raster():
    step = 8
    full, meta = G.read_geotiff(str(MASK))
    small, _ = G.read_geotiff(str(MASK), step=step)

    assert small.shape == (1, -(-meta.height // step), -(-meta.width // step))
    assert np.array_equal(small[0], full[0, ::step, ::step])


@needs_image
def test_image_metadata_matches_the_audited_dataset_shape():
    meta = G.read_meta(str(IMAGE))

    assert meta.big_endian is True
    assert meta.dtype == ">f4"
    assert list(meta.bits_per_sample) == [32, 32]
    assert meta.samples == 2
    assert meta.tiled and (meta.tile_width, meta.tile_height) == (512, 512)
    assert meta.geo.epsg == 4326
    # The TIFF itself carries no GDAL_NODATA tag; the audit's 0.0 no-data value comes
    # from the SNAP DIMAP blob in tag 65000, so the reader must not invent one here.
    assert meta.nodata is None

    t = meta.geo.transform
    assert t is not None
    assert t.pixel_width == pytest.approx(PIXEL_DEG, rel=1e-9)
    assert t.pixel_height == pytest.approx(PIXEL_DEG, rel=1e-9)
    assert t.c == 0.0 and t.e == 0.0  # north-up

    bounds = meta.bounds()
    assert bounds is not None
    assert bounds[0] < bounds[2] and bounds[1] < bounds[3]

    body = meta.to_dict()
    assert body["compression"] == "LZW"
    assert body["organisation"] == "tiled"


@needs_image
def test_band_selection_returns_the_same_pixels_as_a_full_read():
    """Band order is load-bearing: VH is index 0 and VV is index 1 in these products."""
    both, meta = G.read_geotiff(str(IMAGE), step=4)
    vv_only, _ = G.read_geotiff(str(IMAGE), bands=[1], step=4)

    assert meta.samples == 2
    assert vv_only.shape == (1, both.shape[1], both.shape[2])
    assert np.array_equal(vv_only[0], both[1])


@needs_image
def test_decoded_radar_values_are_finite_and_in_a_physical_range():
    """A codec bug typically shows up as NaN, inf, or values far outside SAR dB."""
    arr, _ = G.read_geotiff(str(IMAGE), step=4)
    valid = arr[arr != 0.0]

    assert valid.size > 0
    assert np.isfinite(valid).all()
    # The audit measured -78..+18 dB across all 1200 scenes.
    assert valid.min() > -120.0
    assert valid.max() < 40.0
    # VV backscatter over water sits well below 0 dB.
    vv = arr[1]
    vv_valid = vv[vv != 0.0]
    assert -60.0 < float(np.median(vv_valid)) < 5.0


@needs_image
def test_snap_metadata_is_reachable_without_loading_the_raster():
    """The BEAM-DIMAP blob in tag 65000 is where acquisition time and product id live."""
    with G.GeoTiff(str(IMAGE)) as tif:
        bulk = tif.bulk_tags()
        assert 65000 in bulk and bulk[65000] > 0
        head = tif.read_bulk_tag(65000, limit=4096)

    text = head.decode("utf-8", "replace")
    assert "Dimap_Document" in text or "DATASET_NAME" in text


@needs_image
def test_a_non_tiff_file_is_rejected_clearly(tmpdir_path):
    path = tmpdir_path / "spilltrace_not_a_tiff.bin"
    path.write_bytes(b"this is not a tiff header at all")
    with pytest.raises(G.GeoTiffError):
        G.read_meta(str(path))
