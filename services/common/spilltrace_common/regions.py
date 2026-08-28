"""Coarse offline region naming for map and case labels.

The supplied scenes span several parts of the world, so a case list of bare
coordinates is hard to read. This module maps a coordinate to a named marine
region using a small hand-written table of bounding boxes. It is a *labelling
convenience only* -- deliberately offline, deterministic and approximate. It is
never used for geometry, drift or scoring, and the UI presents these names as
approximate.
"""

from __future__ import annotations

# (name, west, south, east, north). Order matters: the first match wins, so
# smaller, more specific boxes are listed before the basins that contain them.
REGIONS: tuple[tuple[str, float, float, float, float], ...] = (
    ("Gulf of Suez", 32.2, 27.2, 34.2, 30.2),
    ("Suez Canal approaches", 32.0, 30.2, 33.2, 32.0),
    ("Nile Delta shelf", 29.0, 30.8, 33.0, 33.2),
    ("Eastern Mediterranean", 25.0, 31.0, 36.5, 37.0),
    ("Central Mediterranean", 10.0, 30.0, 25.0, 42.0),
    ("Western Mediterranean", -6.0, 34.0, 10.0, 44.5),
    ("Persian Gulf", 47.5, 23.5, 57.0, 30.5),
    ("Gulf of Oman", 56.5, 22.0, 62.0, 26.5),
    ("Red Sea", 32.0, 12.0, 44.0, 27.5),
    ("Arabian Sea", 55.0, 5.0, 78.0, 25.0),
    ("Bay of Bengal", 78.0, 5.0, 95.0, 23.0),
    ("Gulf of Mexico", -98.0, 18.0, -80.5, 31.0),
    ("Caribbean Sea", -88.0, 8.0, -59.0, 22.5),
    ("North Sea", -4.5, 51.0, 9.5, 61.5),
    ("Baltic Sea", 9.5, 53.5, 30.5, 66.0),
    ("English Channel", -6.0, 48.5, 2.5, 51.2),
    ("Bay of Biscay", -10.5, 43.0, -1.0, 48.6),
    ("Norwegian Sea", -5.0, 61.5, 20.0, 71.0),
    ("Black Sea", 27.0, 40.5, 42.0, 47.5),
    ("Sea of Marmara", 26.0, 40.0, 30.5, 41.5),
    ("Caspian Sea", 46.5, 36.0, 55.0, 47.5),
    ("Gulf of Guinea", -5.0, -6.0, 12.0, 6.5),
    ("South China Sea", 105.0, 2.0, 122.0, 23.5),
    ("East China Sea", 117.0, 23.5, 131.0, 33.5),
    ("Sea of Japan", 127.0, 33.5, 142.0, 52.0),
    ("Yellow Sea", 117.0, 33.5, 127.0, 41.0),
    ("Gulf of Thailand", 99.0, 5.0, 105.0, 14.0),
    ("Java Sea", 105.0, -8.0, 118.0, -2.0),
    ("Strait of Malacca", 96.0, -1.0, 105.0, 7.0),
    ("Gulf of Alaska", -160.0, 52.0, -132.0, 62.0),
    ("California coast", -126.0, 30.0, -116.0, 42.0),
    ("Brazilian shelf", -50.0, -34.0, -34.0, -1.0),
    ("Patagonian shelf", -70.0, -56.0, -52.0, -36.0),
    ("Gulf of Aden", 43.0, 10.0, 52.0, 15.5),
    ("Mozambique Channel", 33.0, -26.0, 49.0, -10.0),
    ("Great Australian Bight", 115.0, -40.0, 140.0, -30.0),
)

_OCEANS: tuple[tuple[str, float, float], ...] = (
    # (name, west, east) for the broad longitude bands, split by hemisphere.
    ("Atlantic Ocean", -80.0, 20.0),
    ("Indian Ocean", 20.0, 120.0),
    ("Pacific Ocean", 120.0, 180.0),
)


def region_label(lat: float | None, lon: float | None) -> str:
    """Return an approximate named region for a coordinate.

    Falls back to a hemisphere-qualified ocean basin, then to ``"Unknown
    region"`` when the coordinate is missing or nonsensical.
    """
    if lat is None or lon is None:
        return "Unknown region"
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return "Unknown region"
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return "Unknown region"

    for name, west, south, east, north in REGIONS:
        if west <= lon <= east and south <= lat <= north:
            return name

    hemisphere = "North" if lat >= 0 else "South"
    for name, west, east in _OCEANS:
        if west <= lon <= east:
            return f"{hemisphere} {name}"
    if lon < -80.0:
        return f"{hemisphere} Pacific Ocean"
    return f"{hemisphere} Atlantic Ocean"


def describe_location(lat: float | None, lon: float | None) -> str:
    """Human-readable ``55.242 N, 4.058 E`` style coordinate string."""
    if lat is None or lon is None:
        return "unknown"
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.3f} {ns}, {abs(lon):.3f} {ew}"
