"""Coarse offline region naming for map and case labels.

The supplied scenes span several parts of the world, so a case list of bare
coordinates is hard to read. This module maps a coordinate to a named marine
region using a small hand-written table of bounding boxes. It is a *labelling
convenience only* -- deliberately offline, deterministic and approximate. It is
never used for geometry, drift or scoring, and the UI presents these names as
approximate.

Approximate does not license being wrong about which ocean a scene is in. Every
box here is checked against the real scene centroids in ``data/processed/audit.json``
by ``tests/test_regions.py``, and a coordinate that matches nothing gets a vague
label rather than a confident guess.
"""

from __future__ import annotations

# (name, west, south, east, north). Order matters: the first match wins, so
# smaller, more specific boxes are listed before the basins that contain them.
REGIONS: tuple[tuple[str, float, float, float, float], ...] = (
    ("Gulf of Suez", 32.2, 27.2, 34.2, 30.2),
    ("Suez Canal approaches", 32.0, 30.2, 33.2, 32.0),
    ("Nile Delta shelf", 29.0, 30.8, 33.0, 33.2),
    # The Aegean sits north of the Eastern Mediterranean box and east of the
    # Central Mediterranean one, so without its own entry it fell past every
    # named region. The second box is the Thracian Sea, cut off at the Aegean
    # mouth of the Dardanelles so the Sea of Marmara keeps its own water.
    ("Aegean Sea", 23.0, 35.8, 27.6, 40.0),
    ("Aegean Sea", 23.0, 40.0, 26.3, 41.2),
    ("Eastern Mediterranean", 25.0, 31.0, 36.5, 37.0),
    # Also north of the Central Mediterranean box. Below 42 N these longitudes
    # are the Italian peninsula, so the box may start there without claiming
    # Tyrrhenian water.
    ("Adriatic Sea", 12.0, 42.0, 20.0, 45.9),
    ("Central Mediterranean", 10.0, 30.0, 25.0, 42.0),
    ("Western Mediterranean", -6.0, 34.0, 10.0, 44.5),
    # West of Gibraltar, so outside the Mediterranean boxes and previously left
    # to the open-Atlantic fallback.
    ("Gulf of Cadiz", -9.6, 34.8, -6.0, 37.4),
    ("Persian Gulf", 47.5, 23.5, 57.0, 30.5),
    ("Gulf of Oman", 56.5, 22.0, 62.0, 26.5),
    ("Red Sea", 32.0, 12.0, 44.0, 27.5),
    ("Arabian Sea", 55.0, 5.0, 78.0, 25.0),
    ("Bay of Bengal", 78.0, 5.0, 95.0, 23.0),
    # Before the Gulf of Mexico, whose box reaches east to 80.5 W and south to 18 N
    # and so claims both ends of the strait. The water south of the Florida Keys is
    # not the Gulf. The western edge stops east of the Dry Tortugas, which is where
    # the Gulf's own limit runs.
    ("Straits of Florida", -82.4, 22.6, -78.5, 25.6),
    ("Gulf of Mexico", -98.0, 18.0, -80.5, 31.0),
    ("Caribbean Sea", -88.0, 8.0, -59.0, 22.5),
    # Before the North Sea, whose box reaches west to 4.5 W and would otherwise
    # claim the water on the wrong side of Britain.
    ("Irish Sea", -6.6, 51.3, -2.7, 55.2),
    ("North Sea", -4.5, 51.0, 9.5, 61.5),
    ("Baltic Sea", 9.5, 53.5, 30.5, 66.0),
    ("English Channel", -6.0, 48.5, 2.5, 51.2),
    ("Bay of Biscay", -10.5, 43.0, -1.0, 48.6),
    ("Norwegian Sea", -5.0, 61.5, 20.0, 71.0),
    # Before the Black Sea, whose box dips south of the Bosphorus and so claimed the
    # whole Marmara. The Marmara's own box stops at the Bosphorus (41.2 N) and at the
    # head of the Gulf of Izmit (30 E), leaving the Black Sea its coast.
    ("Sea of Marmara", 26.0, 40.2, 30.0, 41.2),
    ("Black Sea", 27.0, 40.5, 42.0, 47.5),
    ("Caspian Sea", 46.5, 36.0, 55.0, 47.5),
    ("Gulf of Guinea", -5.0, -6.0, 12.0, 6.5),
    # South of the Gulf of Guinea box, off Cabinda and northern Angola.
    ("Angolan shelf", 8.0, -18.0, 14.2, -5.5),
    # Java Sea first: the South China Sea reaches south past the Java Sea's
    # northern limit, and a scene between Sumatra and Borneo belongs to the
    # latter even though it is south of the equator.
    ("Java Sea", 105.0, -8.0, 118.0, -2.0),
    ("South China Sea", 105.0, -3.2, 122.0, 23.5),
    ("East China Sea", 117.0, 23.5, 131.0, 33.5),
    ("Sea of Japan", 127.0, 33.5, 142.0, 52.0),
    ("Yellow Sea", 117.0, 33.5, 127.0, 41.0),
    ("Gulf of Thailand", 99.0, 5.0, 105.0, 14.0),
    ("Strait of Malacca", 96.0, -1.0, 105.0, 7.0),
    ("Gulf of Alaska", -160.0, 52.0, -132.0, 62.0),
    ("California coast", -126.0, 30.0, -116.0, 42.0),
    ("Brazilian shelf", -50.0, -34.0, -34.0, -1.0),
    ("Patagonian shelf", -70.0, -56.0, -52.0, -36.0),
    ("Gulf of Aden", 43.0, 10.0, 52.0, 15.5),
    ("Mozambique Channel", 33.0, -26.0, 49.0, -10.0),
    ("Great Australian Bight", 115.0, -40.0, 140.0, -30.0),
)

_BASINS: tuple[tuple[str, float, float, float, float], ...] = (
    # (name, west, south, east, north) for the open-ocean fallbacks, reached
    # only when no named region above matches. These carry a latitude range for
    # the same reason the regions do. This table used to be longitude bands
    # alone, and a longitude band is enough to put the Aegean Sea at 39 N, 25 E
    # in the "North Indian Ocean" and the Straits of Florida in the "North
    # Pacific Ocean" -- which is what it did, to 34 scenes.
    ("Arctic Ocean", -180.0, 66.5, 180.0, 90.0),
    ("Southern Ocean", -180.0, -90.0, 180.0, -60.0),
    # A catch-all for the Mediterranean basin, so a corner no named sea claims
    # still lands in the right body of water.
    ("Mediterranean Sea", -6.0, 30.0, 37.0, 47.0),
    # The Indian Ocean stops at the Asian landmass: its northernmost water is
    # the head of the Persian Gulf at about 30 N.
    ("Indian Ocean", 20.0, -60.0, 120.0, 30.0),
    ("Pacific Ocean", 120.0, -60.0, 180.0, 66.5),
    # The eastern Pacific in three steps, because the Americas run west as they
    # run north: Chile and Peru reach 70 W, Central America 86 W, Baja 105 W.
    # Everything east of those is Atlantic, including the Florida coast at 80 W.
    ("Pacific Ocean", -180.0, 25.0, -105.0, 66.5),
    ("Pacific Ocean", -180.0, 5.0, -86.0, 25.0),
    ("Pacific Ocean", -180.0, -60.0, -70.0, 5.0),
    ("Atlantic Ocean", -100.0, -60.0, 20.0, 66.5),
)

# The basins that span both hemispheres take a "North"/"South" prefix. The
# Arctic, the Southern Ocean and the Mediterranean already say where they are.
_HEMISPHERE_PREFIXED = frozenset({"Atlantic Ocean", "Indian Ocean", "Pacific Ocean"})


def region_label(lat: float | None, lon: float | None) -> str:
    """Return an approximate named region for a coordinate.

    Falls back to an ocean basin, then to ``"Unclassified waters"`` when the
    coordinate matches nothing and ``"Unknown region"`` when it is missing or
    nonsensical. The fallback is deliberately vague rather than confidently
    wrong: this string is shown to the user beside the scene, and a plausible
    but incorrect sea name is worse than an honest shrug.
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

    for name, west, south, east, north in _BASINS:
        if west <= lon <= east and south <= lat <= north:
            if name in _HEMISPHERE_PREFIXED:
                return f"{'North' if lat >= 0 else 'South'} {name}"
            return name
    return "Unclassified waters"


def describe_location(lat: float | None, lon: float | None) -> str:
    """Human-readable ``55.242 N, 4.058 E`` style coordinate string."""
    if lat is None or lon is None:
        return "unknown"
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.3f} {ns}, {abs(lon):.3f} {ew}"
