"""Persistence for computed cases.

A case takes roughly a minute of compute and is fully determined by its request key, so
it is cached on disk. That is what makes the offline demo work: the seeded demo case is
just a stored case, and the dashboard cannot tell a replayed case from a fresh one.

Two things are deliberate. Cases are written atomically via a temporary file and a rename,
so a browser reloading mid-write never reads half a document. And the store never invents
a case: a miss is a miss, and the caller decides whether to compute or to report that
nothing is available.
"""

from __future__ import annotations

import json
import os
import re
import threading
import zipfile
from pathlib import Path
from typing import Any, Iterator

from spilltrace_common import config as C

CASE_DIR = C.PROCESSED_DIR / "cases"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DEMO_KEY = "demo"


class StoreError(RuntimeError):
    pass


def _check(name: str) -> str:
    """Reject anything that could escape the case directory."""
    if not SAFE_NAME.match(name or ""):
        raise StoreError(f"unsafe case identifier {name!r}")
    return name


class CaseStore:
    """Disk-backed case cache with a small in-memory index."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or CASE_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- paths -------------------------------------------------------------
    def path_for(self, case_id: str) -> Path:
        return self.root / f"{_check(case_id)}.json"

    def exists(self, case_id: str) -> bool:
        return self.path_for(case_id).exists()

    # -- read --------------------------------------------------------------
    def load(self, case_id: str) -> dict[str, Any] | None:
        path = self.path_for(case_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError) as exc:
            raise StoreError(f"case {case_id} is unreadable: {exc}") from exc

    def load_raw(self, case_id: str) -> bytes | None:
        """The stored bytes, for serving without a parse-and-reserialise round trip."""
        path = self.path_for(case_id)
        if not path.exists():
            return None
        return path.read_bytes()

    # -- write -------------------------------------------------------------
    def save(self, case_id: str, payload: dict[str, Any]) -> Path:
        path = self.path_for(case_id)
        text = json.dumps(payload, separators=(",", ":"))
        with self._lock:
            tmp = path.with_suffix(".json.partial")
            tmp.write_text(text)
            os.replace(tmp, path)
        return path

    def delete(self, case_id: str) -> bool:
        path = self.path_for(case_id)
        mask_path = self.mask_path_for(case_id)
        existed = path.exists()
        if existed:
            path.unlink()
        if mask_path.exists():
            mask_path.unlink()
        return existed

    # -- detection mask cache ---------------------------------------------
    # A 2048x2048 mask is 4 MB of JSON booleans and 60 kB of compressed npz, so it lives
    # beside the case rather than inside it. Its only consumer is the drift endpoint,
    # which re-runs trajectories against a detection the client already accepted.

    def mask_path_for(self, case_id: str) -> Path:
        return self.root / f"{_check(case_id)}.mask.npz"

    def save_mask(self, case_id: str, mask: Any, detection: dict[str, Any]) -> Path:
        import numpy as np

        path = self.mask_path_for(case_id)
        meta = {k: v for k, v in detection.items() if k not in ("mask", "probability")}
        with self._lock:
            tmp = path.with_suffix(".npz.partial")
            with tmp.open("wb") as handle:
                np.savez_compressed(
                    handle,
                    mask=np.asarray(mask, dtype=np.uint8),
                    meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8),
                )
            os.replace(tmp, path)
        return path

    def load_mask(self, case_id: str) -> tuple[Any, dict[str, Any]] | None:
        import numpy as np

        path = self.mask_path_for(case_id)
        if not path.exists():
            return None
        try:
            with np.load(path) as bundle:
                mask = np.asarray(bundle["mask"], dtype=np.uint8)
                meta = json.loads(bytes(bundle["meta"]).decode())
        except (ValueError, OSError, KeyError, zipfile.BadZipFile):
            # A truncated cache is a cache miss, not an error: the caller recomputes.
            # `BadZipFile` is listed explicitly because an npz is a zip and `BadZipFile`
            # descends from `Exception`, not from `OSError` -- so a half-written file
            # would otherwise surface as a 500 on the drift endpoint.
            return None
        return mask, meta

    # -- index -------------------------------------------------------------
    def summaries(self) -> list[dict[str, Any]]:
        """A light index for the case list, so it never loads full case documents."""
        out: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except (ValueError, OSError):
                continue
            out.append(summarise(payload, path))
        out.sort(key=lambda entry: (entry.get("id") != DEMO_KEY, entry.get("id") or ""))
        return out

    def __iter__(self) -> Iterator[str]:
        return (path.stem for path in sorted(self.root.glob("*.json")))


def summarise(payload: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Reduce a case to the fields the case list and the header bar need."""
    scene = payload.get("scene") or {}
    slick = payload.get("slick") or {}
    provenance = payload.get("provenance") or {}
    attribution = payload.get("attribution") or {}
    top = (payload.get("vessels") or [{}])[0]
    return {
        "id": payload.get("id"),
        "caseId": payload.get("caseId") or payload.get("id"),
        "scene": scene.get("name"),
        "region": scene.get("region"),
        "mission": scene.get("mission"),
        "acquiredStartUtc": scene.get("acquiredStartUtc"),
        "bounds": scene.get("bounds"),
        "areaKm2": slick.get("areaKm2"),
        "totalAreaKm2": slick.get("totalAreaKm2"),
        "slickCount": slick.get("slickCount"),
        "confidence": slick.get("confidence"),
        "detectionSource": provenance.get("detectionSource"),
        "detectionLabel": provenance.get("detectionLabel"),
        "driftMode": provenance.get("driftMode"),
        "aisLabel": provenance.get("aisLabel"),
        "status": payload.get("status"),
        "candidateCount": attribution.get("candidateCount"),
        "topCandidate": (
            {
                "name": top.get("name"),
                "mmsi": top.get("mmsi"),
                "score": top.get("score"),
                "band": top.get("band"),
                "status": top.get("status"),
            }
            if top
            else None
        ),
        "generatedUtc": payload.get("generatedUtc"),
        "pipelineVersion": payload.get("pipelineVersion"),
        "isDemo": payload.get("id") == DEMO_KEY or payload.get("demo") is True,
        "sizeBytes": path.stat().st_size if path is not None else None,
    }
