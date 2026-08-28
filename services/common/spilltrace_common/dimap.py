"""Parser for the BEAM-DIMAP XML that SNAP embeds in TIFF tag 65000.

The supplied scenes carry a full 5.6 MB DIMAP document per file. Everything the
pipeline needs (product name, acquisition times, band names, no-data value,
transform, CRS) sits in the first few kilobytes, so :func:`read_header` reads a
bounded prefix and parses it with regular expressions -- that stays correct on a
truncated read, which ElementTree cannot do. :func:`read_full` parses the whole
document when the richer SAR metadata is wanted for a specific scene.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DIMAP_TAG = 65000

# Elements the pipeline relies on all appear well before this offset in the
# supplied files (Image_Interpretation closes at byte 7320).
HEADER_BYTES = 16384

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# subset_0_of_S1A_IW_GRDH_1SDV_20180803T172551_20180803T172608_023085_0281B1_DB30_...
_SUBSET_RE = re.compile(r"^subset_(\d+)_of_(.+)$")
_PRODUCT_RE = re.compile(
    r"(?P<mission>S1[AB])_(?P<mode>[A-Z0-9]{2})_(?P<ptype>[A-Z]{3})(?P<res>[FHM])"
    r"_(?P<proc>\d)(?P<class>[SA])(?P<pol>[SD][VH])"
    r"_(?P<start>\d{8}T\d{6})_(?P<stop>\d{8}T\d{6})"
    r"_(?P<abs_orbit>\d{6})_(?P<take>[0-9A-F]{6})_(?P<crc>[0-9A-F]{4})"
)

_POLARISATION = {
    "SV": ["VV"],
    "SH": ["HH"],
    "DV": ["VV", "VH"],
    "DH": ["HH", "HV"],
}


class DimapError(Exception):
    """Raised when the embedded DIMAP document cannot be understood."""


def parse_snap_utc(text: str | None) -> datetime | None:
    """Parse SNAP's ``03-AUG-2018 17:25:57.581481`` timestamps as UTC."""
    if not text:
        return None
    cleaned = " ".join(text.split())
    match = re.match(
        r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?",
        cleaned,
    )
    if not match:
        return None
    day, mon, year, hh, mm, ss, frac = match.groups()
    month = _MONTHS.get(mon.upper())
    if month is None:
        return None
    micro = int((frac or "0").ljust(6, "0")[:6])
    try:
        return datetime(
            int(year), month, int(day), int(hh), int(mm), int(ss), micro,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def parse_compact_utc(text: str) -> datetime | None:
    """Parse the ``20180803T172551`` form used inside product identifiers."""
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class ProductId:
    """Fields decoded from a Sentinel-1 product identifier."""

    raw: str
    mission: str | None = None
    mode: str | None = None
    product_type: str | None = None
    resolution: str | None = None
    polarisations: list[str] = field(default_factory=list)
    start: datetime | None = None
    stop: datetime | None = None
    absolute_orbit: int | None = None
    data_take: str | None = None

    @property
    def mission_name(self) -> str | None:
        if not self.mission:
            return None
        return f"Sentinel-{self.mission[1]}{self.mission[2]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "mission": self.mission,
            "missionName": self.mission_name,
            "mode": self.mode,
            "productType": self.product_type,
            "resolution": self.resolution,
            "polarisations": list(self.polarisations),
            "start": iso_utc(self.start),
            "stop": iso_utc(self.stop),
            "absoluteOrbit": self.absolute_orbit,
            "dataTake": self.data_take,
        }


def parse_product_id(name: str) -> ProductId:
    """Decode ``S1A_IW_GRDH_1SDV_20180803T172551_...`` into its components."""
    out = ProductId(raw=name)
    match = _PRODUCT_RE.search(name)
    if not match:
        return out
    g = match.groupdict()
    out.mission = g["mission"]
    out.mode = g["mode"]
    out.product_type = g["ptype"]
    out.resolution = g["res"]
    out.polarisations = list(_POLARISATION.get(g["pol"], []))
    out.start = parse_compact_utc(g["start"])
    out.stop = parse_compact_utc(g["stop"])
    out.absolute_orbit = int(g["abs_orbit"])
    out.data_take = g["take"]
    return out


@dataclass
class DimapHeader:
    """The subset of DIMAP a case needs, safe to read from a prefix."""

    dataset_name: str | None = None
    comments: str | None = None
    product_type: str | None = None
    scene_start: datetime | None = None
    scene_stop: datetime | None = None
    wkt: str | None = None
    image_to_model: list[float] = field(default_factory=list)
    ncols: int | None = None
    nrows: int | None = None
    nbands: int | None = None
    band_names: list[str] = field(default_factory=list)
    band_units: list[str] = field(default_factory=list)
    nodata_used: bool = False
    nodata_value: float | None = None
    # Parent acquisition, shared by every ``subset_N_of_<product>`` file.
    subset_index: int | None = None
    product_name: str | None = None
    product: ProductId | None = None
    processing_chain: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def group_key(self) -> str:
        """Split-grouping key: the parent acquisition, not the subset."""
        if self.product and self.product.raw:
            match = _PRODUCT_RE.search(self.product.raw)
            if match:
                return match.group(0)
        return self.product_name or self.dataset_name or "unknown"

    def band_index(self, polarisation: str) -> int | None:
        """Locate a band by polarisation, e.g. ``VV`` -> index in the raster."""
        needle = polarisation.upper()
        for i, name in enumerate(self.band_names):
            parts = re.split(r"[_\W]+", name.upper())
            if needle in parts:
                return i
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "datasetName": self.dataset_name,
            "comments": self.comments,
            "productType": self.product_type,
            "sceneStartUtc": iso_utc(self.scene_start),
            "sceneStopUtc": iso_utc(self.scene_stop),
            "crsWkt": self.wkt,
            "imageToModel": list(self.image_to_model),
            "width": self.ncols,
            "height": self.nrows,
            "bandCount": self.nbands,
            "bandNames": list(self.band_names),
            "bandUnits": list(self.band_units),
            "noDataUsed": self.nodata_used,
            "noDataValue": self.nodata_value,
            "subsetIndex": self.subset_index,
            "productName": self.product_name,
            "product": self.product.to_dict() if self.product else None,
            "processingChain": list(self.processing_chain),
            "groupKey": self.group_key,
            "truncated": self.truncated,
        }


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.S)
    if not match:
        return None
    return " ".join(match.group(1).split())


def _all(pattern: str, text: str) -> list[str]:
    return [" ".join(m.split()) for m in re.findall(pattern, text, re.S)]


def parse_header(blob: bytes | str) -> DimapHeader:
    """Parse the DIMAP prefix. Tolerates a truncated document."""
    if isinstance(blob, bytes):
        text = blob.rstrip(b"\x00").decode("utf-8", "replace")
    else:
        text = blob
    if "<Dimap_Document" not in text:
        raise DimapError("not a BEAM-DIMAP document")

    out = DimapHeader()
    out.truncated = "</Dimap_Document>" not in text
    out.dataset_name = _first(r"<DATASET_NAME>(.*?)</DATASET_NAME>", text)
    out.comments = _first(r"<DATASET_COMMENTS>(.*?)</DATASET_COMMENTS>", text)
    out.product_type = _first(r"<PRODUCT_TYPE>(.*?)</PRODUCT_TYPE>", text)
    out.scene_start = parse_snap_utc(
        _first(r"<PRODUCT_SCENE_RASTER_START_TIME>(.*?)</", text)
    )
    out.scene_stop = parse_snap_utc(
        _first(r"<PRODUCT_SCENE_RASTER_STOP_TIME>(.*?)</", text)
    )
    out.wkt = _first(r"<WKT>(.*?)</WKT>", text)

    transform = _first(r"<IMAGE_TO_MODEL_TRANSFORM>(.*?)</IMAGE_TO_MODEL_TRANSFORM>", text)
    if transform:
        values = []
        for token in transform.replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                values.append(float(token))
            except ValueError:
                values = []
                break
        out.image_to_model = values

    for tag, attr in (("NCOLS", "ncols"), ("NROWS", "nrows"), ("NBANDS", "nbands")):
        raw = _first(rf"<{tag}>(.*?)</{tag}>", text)
        if raw and raw.isdigit():
            setattr(out, attr, int(raw))

    out.band_names = _all(r"<BAND_NAME>(.*?)</BAND_NAME>", text)
    out.band_units = _all(r"<PHYSICAL_UNIT>(.*?)</PHYSICAL_UNIT>", text)
    used = _all(r"<NO_DATA_VALUE_USED>(.*?)</NO_DATA_VALUE_USED>", text)
    out.nodata_used = bool(used) and used[0].strip().lower() == "true"
    nodata = _all(r"<NO_DATA_VALUE>(.*?)</NO_DATA_VALUE>", text)
    if nodata:
        try:
            out.nodata_value = float(nodata[0])
        except ValueError:
            out.nodata_value = None

    if out.dataset_name:
        match = _SUBSET_RE.match(out.dataset_name)
        if match:
            out.subset_index = int(match.group(1))
            out.product_name = match.group(2)
        else:
            out.product_name = out.dataset_name
        out.product = parse_product_id(out.product_name)
        # Trailing SNAP operator suffixes, e.g. ``Orb_NR_Cal_Spk_TC_dB``.
        product_match = _PRODUCT_RE.search(out.product_name)
        if product_match:
            tail = out.product_name[product_match.end():].strip("_")
            out.processing_chain = [p for p in tail.split("_") if p]
    return out


def read_header(path: str, limit: int = HEADER_BYTES) -> DimapHeader:
    """Read and parse the DIMAP prefix of a SNAP-written GeoTIFF."""
    from .geotiff import GeoTiff

    with GeoTiff(path) as tif:
        blob = tif.read_bulk_tag(DIMAP_TAG, limit)
    if not blob:
        raise DimapError(f"no DIMAP metadata (tag {DIMAP_TAG}) in {path}")
    return parse_header(blob)


def read_full(path: str) -> dict[str, Any]:
    """Parse the entire DIMAP document, including SAR abstracted metadata."""
    from .geotiff import GeoTiff

    with GeoTiff(path) as tif:
        blob = tif.read_bulk_tag(DIMAP_TAG)
    if not blob:
        raise DimapError(f"no DIMAP metadata (tag {DIMAP_TAG}) in {path}")
    header = parse_header(blob)
    payload: dict[str, Any] = {"header": header.to_dict()}
    try:
        root = ET.fromstring(blob.rstrip(b"\x00"))
    except ET.ParseError as exc:  # keep the header even if the tail is damaged
        payload["abstractedMetadata"] = {}
        payload["parseError"] = str(exc)
        return payload

    abstracted: dict[str, Any] = {}
    for element in root.iter("MDElem"):
        if element.get("name") != "Abstracted_Metadata":
            continue
        for attr in element.findall("MDATTR"):
            name = attr.get("name")
            if not name:
                continue
            abstracted[name] = _coerce_mdattr(attr.get("type"), attr.text)
        break
    payload["abstractedMetadata"] = abstracted

    stats = []
    for band_stats in root.iter("Band_Statistics"):
        entry = {
            child.tag: (child.text or "").strip()
            for child in band_stats
            if child.tag.startswith("STX") or child.tag == "BAND_INDEX"
        }
        if len(entry) > 1:
            stats.append(entry)
    payload["snapBandStatistics"] = stats
    return payload


def _coerce_mdattr(kind: str | None, text: str | None) -> Any:
    value = " ".join((text or "").split())
    if not value:
        return None
    if kind in ("float64", "float32"):
        try:
            return float(value)
        except ValueError:
            return value
    if kind in ("int32", "int16", "uint8", "int8", "uint16", "uint32"):
        try:
            return int(float(value))
        except ValueError:
            return value
    if kind == "utc":
        return iso_utc(parse_snap_utc(value)) or value
    return value
