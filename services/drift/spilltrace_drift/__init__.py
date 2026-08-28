"""SpillTrace drift simulation.

``forcing`` decides what velocity field is legitimate for a given scene and time;
``engine`` integrates particles through it, backward to a plausible origin region
or forward to a forecast envelope.
"""

from .engine import (
    BACKWARD_CAVEAT,
    convex_hull_indices,
    distances_m,
    hull_ring,
    seed_from_mask,
    seed_from_point,
    simulate,
    to_geojson,
)
from .forcing import (
    FALLBACK_SPEED_MS,
    CmemsForcing,
    Forcing,
    GridLandMask,
    LatLonGrid,
    NoLandMask,
    SyntheticForcing,
    SyntheticSpec,
    resolve_forcing,
)

__all__ = [
    "BACKWARD_CAVEAT",
    "CmemsForcing",
    "FALLBACK_SPEED_MS",
    "Forcing",
    "GridLandMask",
    "LatLonGrid",
    "NoLandMask",
    "SyntheticForcing",
    "SyntheticSpec",
    "convex_hull_indices",
    "distances_m",
    "hull_ring",
    "resolve_forcing",
    "seed_from_mask",
    "seed_from_point",
    "simulate",
    "to_geojson",
]
