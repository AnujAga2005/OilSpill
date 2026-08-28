"""Dependency-free GeoTIFF reader.

Written for the SpillTrace datasets, whose real structure was determined by
inspecting the supplied files rather than assumed:

* ``Oil/*.tif``      big-endian classic TIFF, 2048x2048, 2 samples of float32
                     (SampleFormat 3), LZW compressed (Compression 5), tiled
                     512x512, chunky planar config, georeferenced through
                     ModelTransformation (34264) + GeoKeyDirectory (34735).
* ``Mask_oil/*.tif`` big-endian classic TIFF, 2048x2048, 1 sample of uint8,
                     uncompressed, one row per strip, written by SCIFIO and
                     carrying **no** geo tags.

The module therefore supports the union of those layouts plus the common
variants that show up in the wider Sentinel-1 ecosystem, so that swapping in
another scene does not break the pipeline:

* classic TIFF and BigTIFF, little- and big-endian
* tiled and stripped organisation
* chunky (PlanarConfig 1) and planar (PlanarConfig 2) sample interleaving
* Compression 1 (none), 5 (LZW), 8/32946 (Deflate), 32773 (PackBits)
* horizontal differencing predictor (Predictor 2)
* uint8/16/32, int8/16/32 and float32/64 sample formats

Only the standard library and NumPy are used.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# TIFF constants
# ---------------------------------------------------------------------------

T_BYTE, T_ASCII, T_SHORT, T_LONG, T_RATIONAL = 1, 2, 3, 4, 5
T_SBYTE, T_UNDEFINED, T_SSHORT, T_SLONG, T_SRATIONAL = 6, 7, 8, 9, 10
T_FLOAT, T_DOUBLE, T_LONG8, T_SLONG8, T_IFD8 = 11, 12, 16, 17, 18

# (struct code, byte size) per TIFF field type
_FIELD = {
    T_BYTE: ("B", 1),
    T_ASCII: ("s", 1),
    T_SHORT: ("H", 2),
    T_LONG: ("I", 4),
    T_RATIONAL: ("I", 4),  # two LONGs per value
    T_SBYTE: ("b", 1),
    T_UNDEFINED: ("B", 1),
    T_SSHORT: ("h", 2),
    T_SLONG: ("i", 4),
    T_SRATIONAL: ("i", 4),  # two SLONGs per value
    T_FLOAT: ("f", 4),
    T_DOUBLE: ("d", 8),
    T_LONG8: ("Q", 8),
    T_SLONG8: ("q", 8),
    T_IFD8: ("Q", 8),
}

TAG_IMAGE_WIDTH = 256
TAG_IMAGE_LENGTH = 257
TAG_BITS_PER_SAMPLE = 258
TAG_COMPRESSION = 259
TAG_PHOTOMETRIC = 262
TAG_DESCRIPTION = 270
TAG_STRIP_OFFSETS = 273
TAG_SAMPLES_PER_PIXEL = 277
TAG_ROWS_PER_STRIP = 278
TAG_STRIP_BYTE_COUNTS = 279
TAG_PLANAR_CONFIG = 284
TAG_PREDICTOR = 317
TAG_TILE_WIDTH = 322
TAG_TILE_LENGTH = 323
TAG_TILE_OFFSETS = 324
TAG_TILE_BYTE_COUNTS = 325
TAG_EXTRA_SAMPLES = 338
TAG_SAMPLE_FORMAT = 339
TAG_MODEL_PIXEL_SCALE = 33550
TAG_MODEL_TIEPOINT = 33922
TAG_MODEL_TRANSFORMATION = 34264
TAG_GEO_KEY_DIRECTORY = 34735
TAG_GEO_DOUBLE_PARAMS = 34736
TAG_GEO_ASCII_PARAMS = 34737
TAG_GDAL_METADATA = 42112
TAG_GDAL_NODATA = 42113

COMPRESSION_NONE = 1
COMPRESSION_LZW = 5
COMPRESSION_DEFLATE_OLD = 8
COMPRESSION_PACKBITS = 32773
COMPRESSION_DEFLATE = 32946

SAMPLE_FORMAT_UINT = 1
SAMPLE_FORMAT_INT = 2
SAMPLE_FORMAT_IEEE = 3

# GeoTIFF GeoKey ids we care about
GK_MODEL_TYPE = 1024
GK_RASTER_TYPE = 1025
GK_GEOGRAPHIC_TYPE = 2048
GK_GEOG_CITATION = 2049
GK_PROJECTED_CS_TYPE = 3072
GK_PROJ_CITATION = 3073

# Tags whose payload is bulk vendor metadata; skipped to keep audits small.
_BULK_TAGS = frozenset({TAG_GDAL_METADATA, 65000})

_MAX_PIXELS = 512 * 1024 * 1024  # refuse absurd allocations from corrupt headers


class GeoTiffError(Exception):
    """Raised when a file is not a TIFF we can decode."""


# ---------------------------------------------------------------------------
# Decompression helpers
# ---------------------------------------------------------------------------


def lzw_decode(data: bytes, expected: int = 0) -> bytes:
    """Decode TIFF-flavoured LZW (MSB-first codes, early change).

    TIFF LZW differs from GIF LZW: codes are packed most-significant-bit first
    and the code width grows one entry *early* (at 511/1023/2047).

    ``expected`` is the uncompressed size of the block and is a correctness
    requirement, not a hint. A block's code stream is padded to a byte boundary,
    and those padding bits can form a syntactically valid code that indexes past
    the table; one tile of ``Oil/00270.tif`` does exactly that, producing a
    spurious 10-bit code 514 after all 2,097,152 bytes have been emitted. libtiff
    stops at the requested byte count for the same reason, so this decoder does
    too, and only falls back to waiting for an EOI code when the caller cannot
    say how much output to expect.

    The supplied SAR scenes are float32 noise, which LZW *expands* -- a 33.5 MB
    raster arrives as a 41.5 MB stream of ~33 million codes. This loop therefore
    dominates the whole pipeline's runtime and is written for speed: a running
    bit buffer instead of per-code slicing, and one flat table indexed directly
    by code so the common path is a single list lookup.
    """
    out = bytearray()
    append = out.extend
    # Entries 256 (Clear) and 257 (EOI) are placeholders so that `code` indexes
    # the table directly and `len(table)` is always the next code to assign.
    base = [bytes((i,)) for i in range(256)] + [b"", b""]
    table = list(base)
    prev = b""
    code_width = 9
    mask = 511
    limit = 512  # widen when len(table) + 1 reaches this ("early change")
    stop_at = expected if expected > 0 else (1 << 62)
    bitbuf = 0
    bitcnt = 0
    pos = 0
    n = len(data)

    while True:
        while bitcnt < code_width:
            if pos >= n:
                return bytes(out)
            bitbuf = (bitbuf << 8) | data[pos]
            pos += 1
            bitcnt += 8
        bitcnt -= code_width
        code = (bitbuf >> bitcnt) & mask
        # Discard consumed high bits: without this `bitbuf` grows into a
        # multi-megabyte Python int and every shift becomes O(size).
        bitbuf &= (1 << bitcnt) - 1

        if code < 256:
            entry = base[code]
        elif code == 257:  # EOI
            break
        elif code == 256:  # ClearCode
            del table[258:]
            code_width = 9
            mask = 511
            limit = 512
            prev = b""
            continue
        else:
            size = len(table)
            if code < size:
                entry = table[code]
            elif code == size and prev:
                entry = prev + prev[:1]
            else:
                raise GeoTiffError(
                    f"LZW stream references undefined code {code} with a table of "
                    f"{size} entries after {len(out)} of {expected or 'unknown'} bytes"
                )

        append(entry)
        if len(out) >= stop_at:
            # A final entry may straddle the boundary; the block is exactly this long.
            return bytes(out[:stop_at])
        if prev:
            table.append(prev + entry[:1])
            if len(table) + 1 >= limit and code_width < 12:
                code_width += 1
                limit = 1 << code_width
                mask = limit - 1
        prev = entry
    return bytes(out)


def packbits_decode(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        header = data[i]
        i += 1
        if header < 128:
            count = header + 1
            out += data[i : i + count]
            i += count
        elif header > 128:
            count = 257 - header
            if i < n:
                out += bytes((data[i],)) * count
            i += 1
        # header == 128 is a no-op
    return bytes(out)


def _decompress(data: bytes, compression: int, expected: int) -> bytes:
    if compression == COMPRESSION_NONE:
        return data
    if compression == COMPRESSION_LZW:
        return lzw_decode(data, expected)
    if compression in (COMPRESSION_DEFLATE, COMPRESSION_DEFLATE_OLD):
        return zlib.decompress(data)
    if compression == COMPRESSION_PACKBITS:
        return packbits_decode(data)
    raise GeoTiffError(f"unsupported TIFF compression {compression}")


def _numpy_dtype(bits: int, sample_format: int, big_endian: bool) -> np.dtype:
    prefix = ">" if big_endian else "<"
    if sample_format == SAMPLE_FORMAT_IEEE:
        if bits == 32:
            return np.dtype(prefix + "f4")
        if bits == 64:
            return np.dtype(prefix + "f8")
        if bits == 16:
            return np.dtype(prefix + "f2")
        raise GeoTiffError(f"unsupported float width {bits}")
    kind = "i" if sample_format == SAMPLE_FORMAT_INT else "u"
    if bits not in (8, 16, 32, 64):
        raise GeoTiffError(f"unsupported integer width {bits}")
    if bits == 8:
        return np.dtype(kind + "1")
    return np.dtype(prefix + kind + str(bits // 8))


# ---------------------------------------------------------------------------
# Geo metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Affine:
    """Pixel -> world mapping, GDAL-style coefficients.

    ``x = a + b*col + c*row`` and ``y = d + e*col + f*row``.
    """

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def apply(self, col: float, row: float) -> tuple[float, float]:
        return (
            self.a + self.b * col + self.c * row,
            self.d + self.e * col + self.f * row,
        )

    def apply_array(self, cols: np.ndarray, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        cols = np.asarray(cols, dtype=np.float64)
        rows = np.asarray(rows, dtype=np.float64)
        return (
            self.a + self.b * cols + self.c * rows,
            self.d + self.e * cols + self.f * rows,
        )

    def inverse(self) -> "Affine":
        det = self.b * self.f - self.c * self.e
        if abs(det) < 1e-300:
            raise GeoTiffError("affine transform is not invertible")
        ib, ic = self.f / det, -self.c / det
        ie, if_ = -self.e / det, self.b / det
        ia = -(ib * self.a + ic * self.d)
        id_ = -(ie * self.a + if_ * self.d)
        return Affine(ia, ib, ic, id_, ie, if_)

    def world_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        inv = self.inverse()
        return inv.apply(x, y)

    @property
    def pixel_width(self) -> float:
        return float(np.hypot(self.b, self.e))

    @property
    def pixel_height(self) -> float:
        return float(np.hypot(self.c, self.f))

    def as_list(self) -> list[float]:
        return [self.a, self.b, self.c, self.d, self.e, self.f]

    def scaled(self, fx: float, fy: float) -> "Affine":
        """Affine for a raster downsampled by ``fx``/``fy`` in col/row."""
        return Affine(self.a, self.b * fx, self.c * fy, self.d, self.e * fx, self.f * fy)


@dataclass
class GeoInfo:
    crs: str | None = None
    crs_source: str | None = None
    epsg: int | None = None
    transform: Affine | None = None
    transform_source: str | None = None
    citation: str | None = None
    geo_keys: dict[int, Any] = field(default_factory=dict)

    @property
    def is_geographic(self) -> bool:
        return self.epsg == 4326 or (self.crs or "").upper().startswith("EPSG:4326")


def _parse_geo_keys(directory: Sequence[int], doubles: Sequence[float], ascii_blob: str) -> dict[int, Any]:
    """Expand a GeoKeyDirectory (34735) into ``{key_id: value}``."""
    keys: dict[int, Any] = {}
    if len(directory) < 4:
        return keys
    count = int(directory[3])
    for i in range(count):
        base = 4 + i * 4
        if base + 3 >= len(directory):
            break
        key_id, location, value_count, value_offset = (int(v) for v in directory[base : base + 4])
        if location == 0:
            keys[key_id] = value_offset
        elif location == TAG_GEO_DOUBLE_PARAMS:
            vals = list(doubles[value_offset : value_offset + value_count])
            keys[key_id] = vals[0] if value_count == 1 else vals
        elif location == TAG_GEO_ASCII_PARAMS:
            raw = ascii_blob[value_offset : value_offset + value_count]
            keys[key_id] = raw.rstrip("|").rstrip("\x00")
    return keys


def _build_geo_info(tags: dict[int, Any]) -> GeoInfo:
    info = GeoInfo()

    directory = tags.get(TAG_GEO_KEY_DIRECTORY)
    if directory:
        doubles = tags.get(TAG_GEO_DOUBLE_PARAMS) or []
        blob = tags.get(TAG_GEO_ASCII_PARAMS) or ""
        if isinstance(blob, (bytes, bytearray)):
            blob = blob.decode("latin1", "replace")
        info.geo_keys = _parse_geo_keys(directory, doubles, blob)

        projected = info.geo_keys.get(GK_PROJECTED_CS_TYPE)
        geographic = info.geo_keys.get(GK_GEOGRAPHIC_TYPE)
        if isinstance(projected, int) and 1024 <= projected < 32767:
            info.epsg = int(projected)
            info.crs_source = "GeoKey ProjectedCSTypeGeoKey (3072)"
        elif isinstance(geographic, int) and 1024 <= geographic < 32767:
            info.epsg = int(geographic)
            info.crs_source = "GeoKey GeographicTypeGeoKey (2048)"
        if info.epsg is not None:
            info.crs = f"EPSG:{info.epsg}"
        citation = info.geo_keys.get(GK_PROJ_CITATION) or info.geo_keys.get(GK_GEOG_CITATION)
        if isinstance(citation, str) and citation:
            info.citation = citation
            if info.crs is None:
                info.crs = citation
                info.crs_source = "GeoKey citation"

    matrix = tags.get(TAG_MODEL_TRANSFORMATION)
    scale = tags.get(TAG_MODEL_PIXEL_SCALE)
    tie = tags.get(TAG_MODEL_TIEPOINT)
    if matrix and len(matrix) >= 16:
        m = [float(v) for v in matrix]
        # Row-major 4x4: x = m0*col + m1*row + m3 ; y = m4*col + m5*row + m7
        info.transform = Affine(m[3], m[0], m[1], m[7], m[4], m[5])
        info.transform_source = "ModelTransformation (34264)"
    elif scale and tie and len(scale) >= 2 and len(tie) >= 6:
        sx, sy = float(scale[0]), float(scale[1])
        col0, row0, _, x0, y0, _ = (float(v) for v in tie[:6])
        info.transform = Affine(x0 - col0 * sx, sx, 0.0, y0 + row0 * sy, 0.0, -sy)
        info.transform_source = "ModelPixelScale (33550) + ModelTiepoint (33922)"
    return info


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


@dataclass
class TiffMeta:
    path: str
    big_endian: bool
    bigtiff: bool
    width: int
    height: int
    samples: int
    bits_per_sample: tuple[int, ...]
    sample_formats: tuple[int, ...]
    compression: int
    predictor: int
    planar_config: int
    photometric: int | None
    tiled: bool
    tile_width: int | None
    tile_height: int | None
    rows_per_strip: int | None
    n_blocks: int
    nodata: float | None
    description: str | None
    extra_samples: tuple[int, ...]
    geo: GeoInfo
    dtype: str

    @property
    def compression_name(self) -> str:
        return {
            COMPRESSION_NONE: "none",
            COMPRESSION_LZW: "LZW",
            COMPRESSION_DEFLATE: "Deflate",
            COMPRESSION_DEFLATE_OLD: "Deflate (old tag 8)",
            COMPRESSION_PACKBITS: "PackBits",
        }.get(self.compression, f"code {self.compression}")

    @property
    def sample_format_name(self) -> str:
        fmt = self.sample_formats[0] if self.sample_formats else SAMPLE_FORMAT_UINT
        return {
            SAMPLE_FORMAT_UINT: "unsigned int",
            SAMPLE_FORMAT_INT: "signed int",
            SAMPLE_FORMAT_IEEE: "IEEE float",
        }.get(fmt, f"code {fmt}")

    def bounds(self) -> tuple[float, float, float, float] | None:
        """(min_x, min_y, max_x, max_y) of the raster footprint."""
        t = self.geo.transform
        if t is None:
            return None
        corners = [t.apply(0, 0), t.apply(self.width, 0), t.apply(0, self.height), t.apply(self.width, self.height)]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_dict(self) -> dict[str, Any]:
        b = self.bounds()
        return {
            "path": self.path,
            "byte_order": "big-endian" if self.big_endian else "little-endian",
            "bigtiff": self.bigtiff,
            "width": self.width,
            "height": self.height,
            "samples_per_pixel": self.samples,
            "bits_per_sample": list(self.bits_per_sample),
            "sample_format": self.sample_format_name,
            "dtype": self.dtype,
            "compression": self.compression_name,
            "predictor": self.predictor,
            "planar_config": self.planar_config,
            "organisation": "tiled" if self.tiled else "stripped",
            "tile_size": [self.tile_width, self.tile_height] if self.tiled else None,
            "rows_per_strip": self.rows_per_strip,
            "blocks": self.n_blocks,
            "nodata": self.nodata,
            "extra_samples": list(self.extra_samples),
            "description": self.description,
            "crs": self.geo.crs,
            "crs_source": self.geo.crs_source,
            "epsg": self.geo.epsg,
            "transform": self.geo.transform.as_list() if self.geo.transform else None,
            "transform_source": self.geo.transform_source,
            "bounds": list(b) if b else None,
        }


class GeoTiff:
    """Reader for a single-IFD (first page) GeoTIFF.

    Use as a context manager, or call :meth:`close` when done.
    """

    def __init__(self, path: str):
        self.path = str(path)
        self._bulk: dict[int, tuple[int, int]] = {}
        self._bulk_inline: dict[int, bytes] = {}
        self._fh = open(self.path, "rb")
        try:
            self._tags, self.big_endian, self.bigtiff = self._read_ifd()
            self.meta = self._build_meta()
        except Exception:
            self._fh.close()
            raise

    def bulk_tags(self) -> dict[int, int]:
        """Map of skipped bulk tag -> byte length."""
        return {tag: size for tag, (_, size) in self._bulk.items()}

    def read_bulk_tag(self, tag: int, limit: int = 0) -> bytes:
        """Fetch a bulk tag's bytes, optionally only the first ``limit``."""
        entry = self._bulk.get(tag)
        if entry is None:
            return b""
        offset, size = entry
        if offset < 0:
            return self._bulk_inline.get(tag, b"")
        want = size if limit <= 0 else min(size, limit)
        here = self._fh.tell()
        self._fh.seek(offset)
        data = self._fh.read(want)
        self._fh.seek(here)
        return data

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "GeoTiff":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    # -- header parsing ----------------------------------------------------
    def _read_ifd(self) -> tuple[dict[int, Any], bool, bool]:
        fh = self._fh
        fh.seek(0)
        head = fh.read(16)
        if len(head) < 8:
            raise GeoTiffError("file is too short to be a TIFF")
        if head[:2] == b"II":
            bo = "<"
        elif head[:2] == b"MM":
            bo = ">"
        else:
            raise GeoTiffError("missing TIFF byte-order marker (II/MM)")
        magic = struct.unpack(bo + "H", head[2:4])[0]
        if magic == 42:
            bigtiff = False
            ifd_offset = struct.unpack(bo + "I", head[4:8])[0]
        elif magic == 43:
            bigtiff = True
            offset_size = struct.unpack(bo + "H", head[4:6])[0]
            if offset_size != 8:
                raise GeoTiffError(f"unsupported BigTIFF offset size {offset_size}")
            ifd_offset = struct.unpack(bo + "Q", head[8:16])[0]
        else:
            raise GeoTiffError(f"unsupported TIFF magic number {magic}")

        fh.seek(ifd_offset)
        if bigtiff:
            n_entries = struct.unpack(bo + "Q", fh.read(8))[0]
            entry_size, count_code, value_bytes = 20, "Q", 8
        else:
            n_entries = struct.unpack(bo + "H", fh.read(2))[0]
            entry_size, count_code, value_bytes = 12, "I", 4
        if n_entries > 4096:
            raise GeoTiffError(f"implausible IFD entry count {n_entries}")

        raw_entries = fh.read(entry_size * n_entries)
        tags: dict[int, Any] = {}
        for i in range(n_entries):
            entry = raw_entries[i * entry_size : (i + 1) * entry_size]
            if len(entry) < entry_size:
                break
            tag, field_type = struct.unpack(bo + "HH", entry[:4])
            count = struct.unpack(bo + count_code, entry[4 : 4 + (8 if bigtiff else 4)])[0]
            payload = entry[4 + (8 if bigtiff else 4) :]
            if tag in _BULK_TAGS:
                # Multi-megabyte blobs (SNAP's embedded DIMAP XML is 5.6 MB) are
                # not decoded with every header read. Remember where they live so
                # read_bulk_tag() can fetch them on demand.
                spec = _FIELD.get(field_type)
                size = spec[1] if spec else 1
                total = count * size
                if total > value_bytes:
                    self._bulk[tag] = (
                        struct.unpack(bo + count_code, payload)[0],
                        total,
                    )
                else:
                    self._bulk[tag] = (-1, total)
                    self._bulk_inline[tag] = payload[:total]
                continue
            spec = _FIELD.get(field_type)
            if spec is None:
                continue
            code, size = spec
            n_units = count * (2 if field_type in (T_RATIONAL, T_SRATIONAL) else 1)
            total = size * n_units
            if total > 64 * 1024 * 1024:
                continue
            if total <= value_bytes:
                data = payload[:total]
            else:
                value_offset = struct.unpack(bo + count_code, payload)[0]
                here = fh.tell()
                fh.seek(value_offset)
                data = fh.read(total)
                fh.seek(here)
            tags[tag] = self._decode_field(field_type, count, data, bo)
        return tags, bo == ">", bigtiff

    @staticmethod
    def _decode_field(field_type: int, count: int, data: bytes, bo: str) -> Any:
        if field_type in (T_ASCII,):
            return data.split(b"\x00")[0].decode("latin1", "replace")
        if field_type == T_UNDEFINED:
            return data
        code, size = _FIELD[field_type]
        if field_type in (T_RATIONAL, T_SRATIONAL):
            raw = struct.unpack(bo + code * (count * 2), data[: size * count * 2])
            out = []
            for i in range(count):
                num, den = raw[2 * i], raw[2 * i + 1]
                out.append(float(num) / float(den) if den else 0.0)
            return tuple(out)
        values = struct.unpack(bo + code * count, data[: size * count])
        return tuple(values)

    def _tag(self, tag: int, default: Any = None) -> Any:
        value = self._tags.get(tag, default)
        if isinstance(value, tuple) and len(value) == 1:
            return value[0]
        return value

    def _build_meta(self) -> TiffMeta:
        width = int(self._tag(TAG_IMAGE_WIDTH, 0) or 0)
        height = int(self._tag(TAG_IMAGE_LENGTH, 0) or 0)
        if width <= 0 or height <= 0:
            raise GeoTiffError("TIFF is missing image dimensions")
        if width * height > _MAX_PIXELS:
            raise GeoTiffError(f"raster {width}x{height} exceeds the safety limit")

        samples = int(self._tag(TAG_SAMPLES_PER_PIXEL, 1) or 1)
        bits = self._tags.get(TAG_BITS_PER_SAMPLE, (8,))
        bits = tuple(int(b) for b in (bits if isinstance(bits, tuple) else (bits,)))
        if len(bits) < samples:
            bits = bits * samples if len(bits) == 1 else bits + (bits[-1],) * (samples - len(bits))
        formats = self._tags.get(TAG_SAMPLE_FORMAT, (SAMPLE_FORMAT_UINT,))
        formats = tuple(int(f) for f in (formats if isinstance(formats, tuple) else (formats,)))
        if len(formats) < samples:
            formats = formats * samples if len(formats) == 1 else formats + (formats[-1],) * (samples - len(formats))
        if len(set(bits[:samples])) != 1 or len(set(formats[:samples])) != 1:
            raise GeoTiffError("mixed sample widths/formats are not supported")

        compression = int(self._tag(TAG_COMPRESSION, COMPRESSION_NONE) or COMPRESSION_NONE)
        predictor = int(self._tag(TAG_PREDICTOR, 1) or 1)
        planar = int(self._tag(TAG_PLANAR_CONFIG, 1) or 1)
        photometric = self._tag(TAG_PHOTOMETRIC)
        tile_offsets = self._tags.get(TAG_TILE_OFFSETS)
        tiled = tile_offsets is not None
        n_blocks = len(tile_offsets if isinstance(tile_offsets, tuple) else (tile_offsets,)) if tiled else len(
            self._tags.get(TAG_STRIP_OFFSETS, ()) or ()
        )
        nodata_raw = self._tag(TAG_GDAL_NODATA)
        nodata: float | None = None
        if isinstance(nodata_raw, str):
            try:
                nodata = float(nodata_raw)
            except ValueError:
                nodata = float("nan") if nodata_raw.strip().lower() == "nan" else None
        extra = self._tags.get(TAG_EXTRA_SAMPLES, ()) or ()
        description = self._tag(TAG_DESCRIPTION)

        dtype = _numpy_dtype(bits[0], formats[0], self.big_endian)
        return TiffMeta(
            path=self.path,
            big_endian=self.big_endian,
            bigtiff=self.bigtiff,
            width=width,
            height=height,
            samples=samples,
            bits_per_sample=bits[:samples],
            sample_formats=formats[:samples],
            compression=compression,
            predictor=predictor,
            planar_config=planar,
            photometric=int(photometric) if photometric is not None else None,
            tiled=tiled,
            tile_width=int(self._tag(TAG_TILE_WIDTH)) if tiled else None,
            tile_height=int(self._tag(TAG_TILE_LENGTH)) if tiled else None,
            rows_per_strip=int(self._tag(TAG_ROWS_PER_STRIP, height) or height) if not tiled else None,
            n_blocks=n_blocks,
            nodata=nodata,
            description=description,
            extra_samples=tuple(int(e) for e in (extra if isinstance(extra, tuple) else (extra,))),
            geo=_build_geo_info(self._tags),
            dtype=str(np.dtype(dtype).str),
        )

    # -- pixel access ------------------------------------------------------
    def _block_table(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        if self.meta.tiled:
            offsets = self._tags.get(TAG_TILE_OFFSETS, ())
            counts = self._tags.get(TAG_TILE_BYTE_COUNTS, ())
        else:
            offsets = self._tags.get(TAG_STRIP_OFFSETS, ())
            counts = self._tags.get(TAG_STRIP_BYTE_COUNTS, ())
        offsets = offsets if isinstance(offsets, tuple) else (offsets,)
        counts = counts if isinstance(counts, tuple) else (counts,)
        if not offsets or not counts:
            raise GeoTiffError("TIFF has no strip/tile offset table")
        return tuple(int(o) for o in offsets), tuple(int(c) for c in counts)

    @staticmethod
    def _undo_predictor(block: np.ndarray, predictor: int) -> np.ndarray:
        if predictor == 2:  # horizontal differencing
            np.cumsum(block, axis=1, dtype=block.dtype, out=block)
        elif predictor == 3:  # floating-point predictor
            raise GeoTiffError("floating-point predictor (3) is not supported")
        return block

    def read(self, bands: Sequence[int] | None = None, step: int = 1) -> np.ndarray:
        """Read pixels as ``(bands, height, width)`` in native dtype order.

        ``step`` subsamples by taking every ``step``-th row and column, which
        keeps memory bounded for preview rendering. Blocks that fall entirely
        between sampled rows/columns are never decompressed, so ``step`` also
        cuts decode time substantially.
        """
        meta = self.meta
        if step < 1:
            raise ValueError("step must be >= 1")
        band_list = list(range(meta.samples)) if bands is None else [int(b) for b in bands]
        for b in band_list:
            if not 0 <= b < meta.samples:
                raise IndexError(f"band {b} out of range (file has {meta.samples})")

        dtype = np.dtype(meta.dtype)
        rows = np.arange(0, meta.height, step)
        cols = np.arange(0, meta.width, step)
        out = np.zeros((len(band_list), rows.size, cols.size), dtype=dtype)
        offsets, counts = self._block_table()

        if meta.tiled:
            tw, th = int(meta.tile_width or 0), int(meta.tile_height or 0)
            if tw <= 0 or th <= 0:
                raise GeoTiffError("tiled TIFF with invalid tile size")
            tiles_across = (meta.width + tw - 1) // tw
            tiles_down = (meta.height + th - 1) // th
            planes = meta.samples if meta.planar_config == 2 else 1
            per_plane_samples = 1 if meta.planar_config == 2 else meta.samples
            for plane in range(planes):
                if meta.planar_config == 2 and plane not in band_list:
                    continue
                for ty in range(tiles_down):
                    y0 = ty * th
                    row_sel = np.where((rows >= y0) & (rows < y0 + th))[0]
                    if row_sel.size == 0:
                        continue
                    for tx in range(tiles_across):
                        x0 = tx * tw
                        col_sel = np.where((cols >= x0) & (cols < x0 + tw))[0]
                        if col_sel.size == 0:
                            continue
                        index = plane * tiles_down * tiles_across + ty * tiles_across + tx
                        if index >= len(offsets):
                            continue
                        expected = tw * th * per_plane_samples * dtype.itemsize
                        block = self._read_block(offsets[index], counts[index], expected, dtype)
                        need = th * tw * per_plane_samples
                        if block.size < need:
                            block = np.pad(block, (0, need - block.size))
                        block = block[:need].reshape(th, tw, per_plane_samples)
                        if meta.predictor == 2:
                            block = self._undo_predictor(block, meta.predictor)
                        local_rows = rows[row_sel] - y0
                        local_cols = cols[col_sel] - x0
                        patch = block[np.ix_(local_rows, local_cols)]
                        if meta.planar_config == 2:
                            out[band_list.index(plane), row_sel[0] : row_sel[-1] + 1, col_sel[0] : col_sel[-1] + 1] = patch[:, :, 0]
                        else:
                            for oi, b in enumerate(band_list):
                                out[oi, row_sel[0] : row_sel[-1] + 1, col_sel[0] : col_sel[-1] + 1] = patch[:, :, b]
        else:
            rps = int(meta.rows_per_strip or meta.height)
            rps = min(rps, meta.height) if rps > 0 else meta.height
            strips_per_plane = (meta.height + rps - 1) // rps
            planes = meta.samples if meta.planar_config == 2 else 1
            per_plane_samples = 1 if meta.planar_config == 2 else meta.samples
            for plane in range(planes):
                if meta.planar_config == 2 and plane not in band_list:
                    continue
                for si in range(strips_per_plane):
                    y0 = si * rps
                    strip_rows = min(rps, meta.height - y0)
                    row_sel = np.where((rows >= y0) & (rows < y0 + strip_rows))[0]
                    if row_sel.size == 0:
                        continue
                    index = plane * strips_per_plane + si
                    if index >= len(offsets):
                        continue
                    expected = meta.width * strip_rows * per_plane_samples * dtype.itemsize
                    block = self._read_block(offsets[index], counts[index], expected, dtype)
                    need = strip_rows * meta.width * per_plane_samples
                    if block.size < need:
                        block = np.pad(block, (0, need - block.size))
                    block = block[:need].reshape(strip_rows, meta.width, per_plane_samples)
                    if meta.predictor == 2:
                        block = self._undo_predictor(block, meta.predictor)
                    local_rows = rows[row_sel] - y0
                    patch = block[np.ix_(local_rows, cols)]
                    if meta.planar_config == 2:
                        out[band_list.index(plane), row_sel[0] : row_sel[-1] + 1, :] = patch[:, :, 0]
                    else:
                        for oi, b in enumerate(band_list):
                            out[oi, row_sel[0] : row_sel[-1] + 1, :] = patch[:, :, b]
        return out

    def _read_block(self, offset: int, byte_count: int, expected: int, dtype: np.dtype) -> np.ndarray:
        self._fh.seek(offset)
        raw = self._fh.read(byte_count)
        data = _decompress(raw, self.meta.compression, expected)
        usable = (len(data) // dtype.itemsize) * dtype.itemsize
        return np.frombuffer(data[:usable], dtype=dtype).copy()

    # -- convenience -------------------------------------------------------
    def read_float32(self, bands: Sequence[int] | None = None, step: int = 1) -> np.ndarray:
        """Read pixels as native-endian float32 (``(bands, h, w)``)."""
        return np.ascontiguousarray(self.read(bands=bands, step=step), dtype=np.float32)


def read_geotiff(path: str, bands: Sequence[int] | None = None, step: int = 1) -> tuple[np.ndarray, TiffMeta]:
    """Read a GeoTIFF and return ``(array(bands, h, w), meta)``."""
    with GeoTiff(path) as src:
        return src.read_float32(bands=bands, step=step), src.meta


def read_meta(path: str) -> TiffMeta:
    """Read only the header of a GeoTIFF (cheap; no pixel decompression)."""
    with GeoTiff(path) as src:
        return src.meta
