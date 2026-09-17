"""The SpillTrace HTTP API, on the standard library alone.

Written against ``http.server`` rather than a framework because nothing here needs one:
twenty-one routes, one content negotiation decision, and no ORM. The parts that do need
care are the parts a framework would not have solved anyway.

Four design points worth stating:

* **Long work is a job, not a request.** A full case is roughly a minute of NumPy. The
  POST endpoints return ``202`` with a job id and the client polls; the job carries its
  own progress log so the dashboard can name the current stage instead of spinning.
* **Reads never compute.** Every ``GET`` serves what the store already holds and returns
  ``404`` with the command to build it otherwise. That keeps a page refresh from queuing
  another minute of inference, and it is what makes the seeded demo case sufficient for
  the whole product to work with no live services.
* **Threaded, because HTTP/1.1 keep-alive plus a single thread deadlocks.** A browser
  holds its connection open, so a single-threaded server would stall every other request
  behind it.
* **One POST carries a file rather than JSON.** ``/api/uploads`` is dispatched before the
  body is parsed and streams to disk, because reading a 43 MB GeoTIFF into memory to
  discover it is not JSON would be the wrong order of operations. See
  :data:`BINARY_POST_ROUTES` and :mod:`spilltrace_api.uploads`.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
for _package in ("common", "ml", "drift", "api"):
    _path = str(ROOT / "services" / _package)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# These imports must happen after the service package paths are installed.  Otherwise
# importing this module directly (rather than through the helper script) fails before
# the server has a chance to configure its local package layout.
from .dispatch import (  # noqa: E402
    DispatchError,
    allowed_recipients,
    dispatch_case_email,
    dispatch_mode,
    parse_recipients,
)
from .reports import ReportUnavailable, build_case_number, generate_incident_report  # noqa: E402
from spilltrace_api import case as case_mod  # noqa: E402
from spilltrace_api import jobs as jobs_mod  # noqa: E402
from spilltrace_api import store as store_mod  # noqa: E402
from spilltrace_api import uploads as uploads_mod  # noqa: E402
from spilltrace_common import config as C  # noqa: E402
from spilltrace_drift import marinecadastre  # noqa: E402

WEB_ROOT = ROOT / "apps" / "web"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Caps for the two free-text fields a case can be named with. They arrive in a request
#: body and land in a <select> option and a card hint, so they are bounded here rather
#: than left to whatever a caller sends.
LABEL_MAX = 120
DESCRIPTION_MAX = 600

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("health", re.compile(r"^/api/health$")),
    ("metrics", re.compile(r"^/api/metrics$")),
    ("eval_image", re.compile(r"^/api/eval/([A-Za-z0-9_.-]+\.png)$")),
    ("scenes", re.compile(r"^/api/scenes$")),
    ("cases", re.compile(r"^/api/cases$")),
    ("case_images", re.compile(r"^/api/cases/([^/]+)/images$")),
    ("case_ais_csv", re.compile(r"^/api/cases/([^/]+)/ais\.csv$")),
    ("detect", re.compile(r"^/api/cases/([^/]+)/detect$")),
    ("slick", re.compile(r"^/api/cases/([^/]+)/slick$")),
    ("drift", re.compile(r"^/api/cases/([^/]+)/drift$")),
    ("trajectories", re.compile(r"^/api/cases/([^/]+)/trajectories$")),
    ("vessels", re.compile(r"^/api/cases/([^/]+)/vessels$")),
    ("report", re.compile(r"^/api/cases/([^/]+)/report$")),
    ("dispatch", re.compile(r"^/api/cases/([^/]+)/dispatch$")),
    ("case_label", re.compile(r"^/api/cases/([^/]+)/label$")),
    ("case", re.compile(r"^/api/cases/([^/]+)$")),
    ("job_result", re.compile(r"^/api/jobs/([^/]+)/result$")),
    ("job_cancel", re.compile(r"^/api/jobs/([^/]+)/cancel$")),
    ("jobs", re.compile(r"^/api/jobs$")),
    ("job", re.compile(r"^/api/jobs/([^/]+)$")),
    ("upload_clear", re.compile(r"^/api/uploads/clear$")),
    ("uploads", re.compile(r"^/api/uploads$")),
)

#: Routes whose request body is a file rather than JSON. ``do_POST`` must dispatch these
#: before it tries to parse the body, or a 43 MB GeoTIFF is read into memory and then
#: rejected as "not a JSON object". Kept as a set next to the table so the two cannot
#: drift apart unnoticed.
BINARY_POST_ROUTES = frozenset({"uploads"})


def route(path: str) -> tuple[str, str | None]:
    """Map a URL path to ``(endpoint, parameter)``, or ``("unknown", None)``."""
    candidate = path.rstrip("/") or "/"
    for name, pattern in ROUTES:
        match = pattern.match(candidate)
        if match:
            return name, (match.group(1) if match.groups() else None)
    return "unknown", None


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

RUNNER = jobs_mod.JobRunner(workers=1)
STORE = store_mod.CaseStore()

_PATCH_METRICS = C.PROCESSED_DIR / "metrics.json"
_SCENE_METRICS = C.PROCESSED_DIR / "scene_metrics.json"
_LOOKALIKE_METRICS = C.PROCESSED_DIR / "lookalike_metrics.json"
_AUDIT = C.PROCESSED_DIR / "audit.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def _detection_metrics() -> dict[str, Any]:
    """Patch-scale and scene-scale numbers, side by side and both labelled.

    They disagree -- markedly -- because the patch cache is sampled around labelled oil
    while a scene is mostly open water. Serving only the flattering one would be the
    easiest possible way to mislead an operator, so the endpoint serves both and says
    which is which.
    """
    patch = _read_json(_PATCH_METRICS) or {}
    scene = _read_json(_SCENE_METRICS) or {}
    out: dict[str, Any] = {
        "available": bool(patch) or bool(scene),
        "pipelineVersion": C.PIPELINE_VERSION,
    }
    if patch:
        out["patchScale"] = {
            "basis": "128 px patches sampled from the scenes, half of them oil-centred",
            "threshold": (patch.get("protocol") or {}).get("thresholdSelection"),
            "validation": patch.get("validation"),
            "test": patch.get("test"),
            "baseline": {
                "method": (patch.get("baseline") or {}).get("method"),
                "calibration": ((patch.get("baseline") or {}).get("calibration") or {}).get("config"),
                "test": (patch.get("baseline") or {}).get("test"),
            },
            "comparison": patch.get("comparison"),
            "model": patch.get("model"),
            "modelVersion": patch.get("modelVersion"),
            "trainConfig": patch.get("trainConfig"),
            "history": patch.get("history"),
            "thresholdSweep": patch.get("thresholdSweepValidation"),
            "samples": patch.get("samples"),
            "dataUsage": patch.get("dataUsage"),
            "protocol": patch.get("protocol"),
            "limitations": patch.get("limitations"),
            "trainingSeconds": patch.get("trainingSeconds"),
        }
    if scene:
        out["sceneScale"] = {
            "basis": scene.get("note"),
            "patchThreshold": scene.get("patchThreshold"),
            "sceneThreshold": scene.get("sceneThreshold"),
            "thresholdSelection": scene.get("thresholdSelection"),
            "validation": scene.get("validation"),
            "test": scene.get("test"),
            "perScene": scene.get("perScene"),
            "limitations": scene.get("limitations"),
            "generatedUtc": scene.get("generatedUtc"),
        }
    else:
        out["sceneScale"] = {
            "basis": None,
            "note": "not measured yet; run scripts/run_scene_eval.py",
        }
    out["lookAlike"] = _lookalike_metrics()
    return out


def _lookalike_metrics() -> dict[str, Any]:
    """The look-alike screen's own numbers, which answer a different question.

    Every IoU on this product is measured on scenes that contain oil, so it says how well
    the shape of a known slick is recovered -- not how often dark water that is not oil
    raises an alarm. That second number is the one an operator is actually asking for, and
    it comes from here: a screen fitted on this project's decibel scenes and then measured
    against a published look-alike archive it never trained on.
    """
    doc = _read_json(_LOOKALIKE_METRICS)
    if not doc:
        return {
            "available": False,
            "note": "not measured yet; run scripts/run_lookalike_eval.py",
        }
    return {
        "available": True,
        "question": doc.get("question"),
        "screen": doc.get("screen"),
        "features": doc.get("features"),
        "contextFields": doc.get("contextFields"),
        "sameDomain": doc.get("sameDomain"),
        "crossDomain": doc.get("crossDomain"),
        "limitations": doc.get("limitations"),
        "generatedUtc": doc.get("generatedUtc"),
        "elapsedSeconds": doc.get("elapsedSeconds"),
    }


def _screening_brief(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The look-alike verdict counts for one case, without the per-patch detail.

    The full block, with every patch and every feature it was measured on, is on the case
    document. What a brief needs is the count that was rejected and the sentence saying the
    screen does not name which look-alike it thinks each one is.
    """
    block = payload.get("screening")
    if not isinstance(block, dict):
        return None
    return {key: block.get(key) for key in ("headline", "fitted", "counts", "components", "thresholds", "limits")}


def _public_result(result: Any) -> Any:
    """Strip the host's filesystem layout out of a dispatch result.

    `dispatch_case_email` returns absolute paths because its callers -- this server and
    `scripts/make_report.py` -- need to open the files. Over HTTP they are somebody
    else's machine layout, and no endpoint here is authenticated, so the response
    carries the repository-relative path and nothing above the checkout.
    """
    if not isinstance(result, dict):
        return result
    out = dict(result)
    for key in ("reportPath", "emailPath"):
        value = out.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            out[key] = str(Path(value).resolve().relative_to(C.REPO_ROOT))
        except ValueError:
            out[key] = Path(value).name
    return out


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class SpillTraceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"SpillTrace/{C.PIPELINE_VERSION}"
    serve_frontend = True

    # -- plumbing ----------------------------------------------------------

    def _split(self) -> tuple[str, dict[str, str]]:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        return urllib.parse.unquote(parsed.path), {k: v[0] for k, v in query.items()}

    def _body(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return {}
        try:
            payload = json.loads(self.rfile.read(int(raw_length)) or b"{}")
        except (ValueError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def _send(self, code: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The dashboard is same-origin in normal use; the permissive header is here so a
        # developer can point a separately-served frontend at this API.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        # Compact, matching how the store writes cases. The default separators pad every
        # key and every element, which on a case document is a few hundred kilobytes of
        # whitespace -- enough that `?lean=1` could return more bytes than the full case
        # it is meant to shrink.
        self._send(code, json.dumps(payload, separators=(",", ":")).encode(), "application/json")

    def _fail(self, code: int, message: str, **extra: Any) -> None:
        self._json(code, {"error": message, "status": code, **extra})

    def _case_or_404(self, case_id: str | None) -> dict[str, Any] | None:
        if not case_id or not SAFE_ID.match(case_id):
            self._fail(400, f"invalid case id {case_id!r}")
            return None
        try:
            payload = STORE.load(case_id)
        except store_mod.StoreError as exc:
            self._fail(500, str(exc))
            return None
        if payload is None:
            pending = [
                job.to_dict()
                for job in RUNNER.list(scene=case_id)
                if job.state in ("queued", "running")
            ]
            self._fail(
                404,
                f"case {case_id!r} has not been computed",
                scene=case_id,
                pendingJobs=pending,
                hint=f"POST /api/cases/{case_id}/detect to build it",
            )
            return None
        return payload

    # -- verbs -------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path, query = self._split()
        if self.serve_frontend and not path.startswith("/api/"):
            self._static(path)
            return
        name, param = route(path)
        handlers = {
            "health": lambda: self._health(),
            "metrics": lambda: self._json(200, _detection_metrics()),
            "eval_image": lambda: self._eval_image(param),
            "scenes": lambda: self._scenes(),
            "cases": lambda: self._cases(),
            "case": lambda: self._case(param, query),
            "case_images": lambda: self._images(param, query),
            "case_ais_csv": lambda: self._ais_csv(param),
            "slick": lambda: self._section(param, "slick"),
            "trajectories": lambda: self._section(param, "trajectories"),
            "vessels": lambda: self._vessels(param),
            "report": lambda: self._report(param, query),
            "jobs": lambda: self._json(200, {"jobs": [j.to_dict() for j in RUNNER.list()]}),
            "job": lambda: self._job(param),
            "job_result": lambda: self._job_result(param),
            "uploads": lambda: self._json(200, self._upload_state()),
        }
        try:
            handler = handlers.get(name)
            if handler is None:
                self._fail(404, f"no GET route for {path!r}")
            else:
                handler()
        except BrokenPipeError:
            pass  # the browser navigated away mid-response
        except Exception as exc:  # noqa: BLE001
            self._fail(500, f"{type(exc).__name__}: {exc}", detail=traceback.format_exc(limit=4))

    def do_POST(self) -> None:  # noqa: N802
        path, query = self._split()
        name, param = route(path)

        # Dispatched before the body is touched. These routes carry a file, not JSON, and
        # `_body` would read the whole thing into memory only to fail to parse it.
        if name in BINARY_POST_ROUTES:
            try:
                if name == "uploads":
                    self._upload(query)
            except BrokenPipeError:
                pass
            except Exception as exc:  # noqa: BLE001
                self._fail(
                    500, f"{type(exc).__name__}: {exc}", detail=traceback.format_exc(limit=4)
                )
            return

        body = self._body()
        if body is None:
            self._fail(400, "request body is not a JSON object")
            return
        try:
            if name == "detect":
                self._submit(param, body, kind="detect")
            elif name == "drift":
                self._submit(param, body, kind="drift")
            elif name == "dispatch":
                self._dispatch(param, body)
            elif name == "case_label":
                self._label(param, body)
            elif name == "upload_clear":
                removed = uploads_mod.clear()
                self._json(200, {"removed": removed, "uploads": uploads_mod.listing()})
            elif name == "job_cancel":
                ok = RUNNER.cancel(param or "")
                self._json(200 if ok else 409, {"cancelled": ok, "jobId": param})
            else:
                self._fail(405, f"no POST route for {path!r}")
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._fail(500, f"{type(exc).__name__}: {exc}", detail=traceback.format_exc(limit=4))

    # -- endpoints ---------------------------------------------------------

    def _health(self) -> None:
        loaded = case_mod._load_checkpoint()
        patch = _read_json(_PATCH_METRICS) or {}
        scene = _read_json(_SCENE_METRICS) or {}
        audit = _read_json(_AUDIT) or {}
        cases = STORE.summaries()
        self._json(200, {
            "status": "ok",
            "pipelineVersion": C.PIPELINE_VERSION,
            "timestampUtc": C.utc_now_iso(),
            "model": {
                "available": loaded is not None,
                "checkpoint": C.CHECKPOINT_PATH.name if loaded is not None else None,
                "parameters": int(loaded[0].parameter_count()) if loaded is not None else None,
                "version": patch.get("modelVersion"),
                "threshold": (patch.get("protocol") or {}).get("thresholdSelection"),
                "patchTestIou": (patch.get("test") or {}).get("iou"),
                "patchTestDice": (patch.get("test") or {}).get("dice"),
                "baselineTestIou": ((patch.get("baseline") or {}).get("test") or {}).get("iou"),
                "iouDeltaOverBaseline": (patch.get("comparison") or {}).get("iouDelta"),
                "sceneTestIou": (
                    ((scene.get("test") or {}).get("atSceneThreshold") or {}).get("pooled") or {}
                ).get("iou"),
                "sceneThreshold": scene.get("sceneThreshold"),
            },
            "dataset": {
                "verdict": audit.get("verdict"),
                "pairs": (audit.get("summary") or {}).get("pairs"),
                "scenesAvailable": len(case_mod.available_scenes()),
            },
            "cases": {
                "count": len(cases),
                "ids": [entry["id"] for entry in cases],
                "demoAvailable": STORE.exists(store_mod.DEMO_KEY),
            },
            "jobs": {"active": sum(1 for j in RUNNER.list() if j.state in ("queued", "running"))},
            "labels": {
                "ais": C.LABEL_AIS,
                "satellite": C.LABEL_SATELLITE,
                "prediction": C.LABEL_PREDICTION,
                "reference": C.LABEL_REFERENCE,
                "driftSynthetic": C.LABEL_DRIFT_SYNTHETIC,
                "driftCmems": C.LABEL_DRIFT_CMEMS,
                "driftHybrid": C.LABEL_DRIFT_HYBRID,
                "driftReal": C.LABEL_DRIFT_REAL,
                "candidate": C.LABEL_CANDIDATE,
                "status": C.LABEL_STATUS,
            },
        })

    def _scenes(self) -> None:
        names = case_mod.available_scenes()
        splits = _read_json(C.PROCESSED_DIR / "splits.json") or {}
        split_of = {
            name: split
            for split, members in (splits.get("scenes") or {}).items()
            for name in members
        }
        manifest = _read_json(C.PROCESSED_DIR / "preview_manifest.json") or {}
        previews = {entry["scene"]: entry for entry in manifest.get("scenes") or []}
        stored = set(STORE)
        self._json(200, {
            "count": len(names),
            "scenes": [
                {
                    "name": name,
                    "split": split_of.get(name),
                    "region": (previews.get(name) or {}).get("region"),
                    "acquiredStartUtc": (previews.get(name) or {}).get("acquiredStart"),
                    "bounds": (previews.get(name) or {}).get("bounds"),
                    "referenceOilFraction": (previews.get(name) or {}).get("referenceOilFraction"),
                    "hasPreview": name in previews,
                    "hasCase": name in stored,
                }
                for name in names
            ],
        })

    def _cases(self) -> None:
        summaries = STORE.summaries()
        self._json(200, {"count": len(summaries), "cases": summaries})

    def _case(self, case_id: str | None, query: dict[str, str]) -> None:
        lean = query.get("lean") in ("1", "true")
        if query.get("summary") in ("1", "true"):
            payload = self._case_or_404(case_id)
            if payload is not None:
                self._json(200, store_mod.summarise(payload))
            return
        if lean:
            payload = self._case_or_404(case_id)
            if payload is None:
                return
            # `geometry`, `drift` and `ais` hold the full raster diagnostics, every
            # particle track and every AIS report; the overview screen needs none of them.
            self._json(200, {k: v for k, v in payload.items() if k not in ("geometry", "drift", "ais")})
            return
        # Serve the stored bytes rather than parse-and-reserialise: the document is a few
        # megabytes and a round trip through Python objects gains nothing.
        if not case_id or not SAFE_ID.match(case_id):
            self._fail(400, f"invalid case id {case_id!r}")
            return
        raw = STORE.load_raw(case_id)
        if raw is None:
            self._case_or_404(case_id)
            return
        self._send(200, raw, "application/json")

    def _eval_image(self, filename: str | None) -> None:
        """One qualitative evaluation strip, from the evaluation run's own output.

        These are the example patches the methodology screen shows, including the failures.
        They live beside the case previews rather than in the web bundle, so the interface
        stays free of dataset-derived imagery.
        """
        directory = C.EVAL_PREVIEW_DIR.resolve()
        path = (directory / (filename or "")).resolve()
        if directory not in path.parents or not path.is_file():
            self._fail(
                404,
                f"no evaluation image named {filename!r}",
                hint=".venv/bin/python scripts/run_train.py",
            )
            return
        self._send(200, path.read_bytes(), "image/png", {"Cache-Control": "public, max-age=3600"})

    @staticmethod
    def _preview_dir(previews: dict[str, Any]) -> Path:
        """Where this case's preview PNGs actually live.

        Newer cases record the directory relative to the repository. Older ones recorded an
        absolute path from whichever machine rendered them; that is honoured while it still
        exists and otherwise ignored, so a case document that arrived with a clone falls
        back to this checkout's preview directory instead of 404ing.
        """
        stored = previews.get("directory")
        if isinstance(stored, str) and stored:
            candidate = Path(stored)
            if not candidate.is_absolute():
                candidate = C.REPO_ROOT / candidate
            if candidate.is_dir():
                return candidate
        return C.PREVIEW_DIR

    def _images(self, case_id: str | None, query: dict[str, str]) -> None:
        payload = self._case_or_404(case_id)
        if payload is None:
            return
        previews = payload.get("previews") or {}
        files: dict[str, str] = previews.get("files") or {}
        directory = self._preview_dir(previews)
        kind = query.get("kind")
        if not kind:
            self._json(200, {
                "directory": str(directory),
                "available": sorted(files),
                "previewSize": previews.get("previewSize"),
                "sourceSize": previews.get("sourceSize"),
                "bounds": previews.get("bounds"),
                "epsg": previews.get("epsg"),
                "downsampleFactor": previews.get("downsampleFactor"),
                "contrastStretch": previews.get("contrastStretch"),
                "legend": previews.get("legend"),
                "label": previews.get("label"),
                "urls": {
                    name: f"/api/cases/{case_id}/images?kind={name}" for name in sorted(files)
                },
            })
            return
        filename = files.get(kind)
        if filename is None:
            self._fail(404, f"no preview named {kind!r}", available=sorted(files))
            return
        # `filename` came from our own renderer, but resolve-and-check anyway: a stored
        # case is a file on disk and files on disk can be edited.
        path = (directory / filename).resolve()
        if directory.resolve() not in path.parents or not path.is_file():
            self._fail(404, f"preview {kind!r} is not on disk")
            return
        self._send(200, path.read_bytes(), "image/png", {"Cache-Control": "public, max-age=3600"})

    def _section(self, case_id: str | None, key: str) -> None:
        payload = self._case_or_404(case_id)
        if payload is None:
            return
        self._json(200, {
            "caseId": payload.get("id"),
            "status": C.LABEL_STATUS,
            key: payload.get(key) or {},
            "provenance": payload.get("provenance"),
        })

    def _vessels(self, case_id: str | None) -> None:
        payload = self._case_or_404(case_id)
        if payload is None:
            return
        attribution = payload.get("attribution") or {}
        provenance = payload.get("provenance") or {}
        self._json(200, {
            "caseId": payload.get("id"),
            "aisMode": provenance.get("aisLabel") or C.LABEL_AIS,
            "aisSource": provenance.get("aisSource"),
            "aisSchema": provenance.get("aisSchema"),
            "candidateCount": attribution.get("candidateCount"),
            "relevantCount": attribution.get("relevantCount"),
            "excludedCount": attribution.get("excludedCount"),
            "filtering": attribution.get("filtering"),
            "candidates": payload.get("vessels") or [],
            "weights": attribution.get("weights"),
            "method": attribution.get("method"),
            "caveat": attribution.get("caveat"),
            "candidateLabel": attribution.get("candidateLabel") or C.LABEL_CANDIDATE,
            "releaseWindow": attribution.get("releaseWindow"),
            "originZone": attribution.get("originZone"),
            "driftLabel": attribution.get("driftLabel"),
            "status": C.LABEL_STATUS,
        })

    def _ais_csv(self, case_id: str | None) -> None:
        """The case's AIS feed as a MarineCadastre-format CSV.

        This route exists to make the schema claim checkable rather than assertable: download
        it, diff its header against a real daily extract, and the two are identical. It also
        gives the pipeline a round trip -- what `marinecadastre.read_csv` imports is exactly
        what this exports.
        """
        payload = self._case_or_404(case_id)
        if payload is None:
            return
        vessels = ((payload.get("ais") or {}).get("vessels")) or []
        if not vessels:
            self._fail(404, f"case {case_id!r} has no AIS feed; POST /api/cases/{case_id}/drift first")
            return
        lines = [marinecadastre.HEADER_LINE]
        lines.extend(",".join(row) for row in marinecadastre.iter_rows(vessels))
        body = ("\n".join(lines) + "\n").encode("utf-8")
        self._send(
            200,
            body,
            "text/csv; charset=utf-8",
            {"Content-Disposition": f'attachment; filename="ais_{case_id}_marinecadastre.csv"'},
        )

    def _dispatch(self, case_id: str | None, body: dict[str, Any]) -> None:
        """Queue an incident-report email for a stored case.

        The PDF and email are generated in the background because report generation and
        SMTP can both perform blocking I/O. The API returns a normal Job record so the
        existing frontend job/polling machinery can be reused.
        """
        if not case_id or not SAFE_ID.match(case_id):
            self._fail(400, f"invalid case id {case_id!r}")
            return
        if not STORE.exists(case_id):
            self._fail(404, f"case {case_id!r} has not been computed")
            return

        recipients = body.get("recipients", body.get("to"))
        if recipients in (None, "", []):
            recipients = None
        subject = body.get("subject")
        message = body.get("message")

        # The case number reaches a filename, so reject a bad one here — with a
        # 400 the caller can read — rather than inside a background job.
        try:
            case_number = build_case_number(STORE.load(case_id) or {}, body.get("caseNumber"))
        except ValueError as exc:
            self._fail(400, str(exc))
            return

        # Validate recipient syntax before creating a job so obvious client errors are
        # returned immediately rather than becoming a failed background job. An empty
        # request falls back to SPILLTRACE_ALERT_RECIPIENTS when that is set, which is
        # also what the dispatcher does; with it unset, an empty request is an error.
        try:
            parse_recipients(recipients if recipients else allowed_recipients())
        except DispatchError as exc:
            self._fail(400, str(exc))
            return

        def work(say: Any) -> dict[str, Any]:
            payload = STORE.load(case_id)
            if payload is None:
                raise DispatchError(f"case {case_id!r} disappeared before dispatch")
            say("generating incident PDF")
            result = dispatch_case_email(
                payload,
                recipients,
                case_number=case_number,
                subject=subject,
                message=message,
            )
            say("incident email prepared" if result.get("dryRun") else "incident email sent")
            return result

        job = RUNNER.submit("dispatch", case_id, work)
        self._json(202, {
            "jobId": job.id,
            "kind": "dispatch",
            "caseId": case_id,
            "state": job.state,
            "pollUrl": f"/api/jobs/{job.id}",
            "resultUrl": f"/api/jobs/{job.id}/result",
            "reportUrl": f"/api/cases/{case_id}/report?format=pdf",
        })

    def _label(self, case_id: str | None, body: dict[str, Any]) -> None:
        """Name a stored case, and optionally describe it.

        Presentation only. The run is already on disk -- this writes what a person calls
        it so the case list can show that instead of an id, and touches nothing the
        pipeline measured. An empty string clears the field rather than storing "".
        """
        payload = self._case_or_404(case_id)
        if payload is None:
            return

        updates: dict[str, Any] = {}
        for field, limit in (("label", LABEL_MAX), ("description", DESCRIPTION_MAX)):
            if field not in body:
                continue
            value = body[field]
            if value is None:
                updates[field] = None
                continue
            if not isinstance(value, str):
                self._fail(400, f"{field} must be a string")
                return
            # Collapse newlines: this lands in a one-line <option> and a card hint.
            cleaned = " ".join(value.split())
            if len(cleaned) > limit:
                self._fail(400, f"{field} is longer than {limit} characters")
                return
            updates[field] = cleaned or None
        if not updates:
            self._fail(400, "send a label and/or a description")
            return

        for field, value in updates.items():
            if value is None:
                payload.pop(field, None)
            else:
                payload[field] = value
        try:
            STORE.save(case_id or "", payload)
        except store_mod.StoreError as exc:
            self._fail(500, str(exc))
            return
        self._json(200, store_mod.summarise(payload))

    def _report(self, case_id: str | None, query: dict[str, str]) -> None:
        payload = self._case_or_404(case_id)
        if payload is None:
            return
        if query.get("format") == "pdf":
            # 503, not 500: the server is fine, the optional PDF dependency is not.
            try:
                path = generate_incident_report(payload)
                body = path.read_bytes()
            except ReportUnavailable as exc:
                self._fail(503, str(exc))
                return
            except OSError as exc:
                self._fail(500, f"could not read generated incident report: {exc}")
                return
            self._send(
                200,
                body,
                "application/pdf",
                {"Content-Disposition": f'attachment; filename="{path.name}"'},
            )
            return
        if query.get("format") == "csv":
            from spilltrace_drift import scoring as scoring_mod

            text = scoring_mod.candidates_csv(payload.get("attribution") or {})
            self._send(
                200,
                text.encode(),
                "text/csv; charset=utf-8",
                {"Content-Disposition": f'attachment; filename="spilltrace-{case_id}-candidates.csv"'},
            )
            return
        detection = {k: v for k, v in (payload.get("detection") or {}).items()}
        self._json(200, {
            "caseId": payload.get("id"),
            "generatedUtc": payload.get("generatedUtc"),
            "pipelineVersion": payload.get("pipelineVersion"),
            "status": C.LABEL_STATUS,
            "summary": store_mod.summarise(payload),
            "provenance": payload.get("provenance"),
            "detection": detection,
            "slick": payload.get("slick"),
            "screening": _screening_brief(payload),
            "forcing": payload.get("forcing"),
            "releaseWindow": (payload.get("ais") or {}).get("releaseWindow"),
            "spillAge": payload.get("spillAge"),
            "originEstimate": (payload.get("trajectories") or {}).get("originEstimate"),
            "candidates": payload.get("vessels") or [],
            "trafficFiltering": (payload.get("attribution") or {}).get("filtering"),
            "attributionMethod": (payload.get("attribution") or {}).get("method"),
            "attributionCaveat": (payload.get("attribution") or {}).get("caveat"),
            "weights": (payload.get("attribution") or {}).get("weights"),
            "detectionMetrics": _detection_metrics(),
            "limits": payload.get("limits"),
            "timing": payload.get("timing"),
            "pdfUrl": f"/api/cases/{case_id}/report?format=pdf",
            "dispatchUrl": f"/api/cases/{case_id}/dispatch",
            "dispatch": dispatch_mode(),
        })

    def _job(self, job_id: str | None) -> None:
        job = RUNNER.get(job_id or "")
        if job is None:
            self._fail(404, f"job {job_id!r} not found")
            return
        # A dispatch result is four short strings and two booleans, so it rides along on
        # the poll rather than forcing a second request. A detect or drift result is the
        # whole case document, which is what `/result` is for.
        payload = job.to_dict(include_result=job.kind == "dispatch")
        if "result" in payload:
            payload["result"] = _public_result(payload["result"])
        self._json(200, payload)

    def _job_result(self, job_id: str | None) -> None:
        job = RUNNER.get(job_id or "")
        if job is None:
            self._fail(404, f"job {job_id!r} not found")
            return
        if job.state != "done":
            self._json(202 if job.state in ("queued", "running") else 409, job.to_dict())
            return
        if job.kind == "dispatch":
            self._json(200, {"jobId": job.id, "kind": job.kind, "result": _public_result(job.result)})
            return
        raw = STORE.load_raw(job.scene)
        if raw is None:
            self._fail(500, f"job {job.id} finished but case {job.scene!r} is not in the store")
            return
        self._send(200, raw, "application/json")

    # -- uploads -----------------------------------------------------------

    def _upload_state(self) -> dict[str, Any]:
        """What is on the server, and what each slot is for.

        The rules travel with the listing rather than being duplicated in the client: the
        caps and the accepted extensions are decided in ``config`` and enforced in
        ``uploads``, and a form that disagreed with either would refuse a file the server
        would have taken, or accept one it would not.
        """
        return {
            "uploads": uploads_mod.listing(),
            "slots": {
                kind: {
                    "maxBytes": C.UPLOAD_MAX_BYTES[kind],
                    "suffixes": list(C.UPLOAD_SUFFIXES[kind]),
                    "required": kind == "scene",
                }
                for kind in uploads_mod.KINDS
            },
            "note": (
                "Uploaded files are stored outside the evaluated dataset and are never "
                "added to it. Each one is checked against the scene on its own terms."
            ),
        }

    def _upload(self, query: dict[str, str]) -> None:
        """Receive one file into one slot.

        The size is refused from ``Content-Length`` before a byte is read. If it passes,
        the body is streamed straight to disk -- a CMEMS product is several hundred
        megabytes and there is no reason to hold it in memory.
        """
        kind = str(query.get("kind") or "").strip().lower()
        name = str(query.get("name") or "")
        raw_length = self.headers.get("Content-Length")
        try:
            length = uploads_mod.check_length(
                kind, int(raw_length) if raw_length else None
            )
            # Checked here rather than inside `store` so a wrong extension is refused before
            # a byte of the body is read. `_drain` below can then leave the connection
            # usable; a refusal discovered after the read could not.
            uploads_mod.suffix_for(kind, name)
        except uploads_mod.UploadTooLarge as exc:
            # The one refusal that is about size, and so the one that earns a 413: the
            # client can tell that the same file will never fit, rather than reading it as
            # a complaint about the file's contents.
            self._drain()
            self._fail(413, str(exc))
            return
        except uploads_mod.UploadError as exc:
            self._drain()
            self._fail(400, str(exc))
            return
        except ValueError:
            self._fail(400, f"Content-Length {raw_length!r} is not a number")
            return

        try:
            upload = uploads_mod.store(kind, self.rfile, length, name)
        except uploads_mod.UploadError as exc:
            # `store` fails only after consuming the body, so there is nothing left to
            # drain and the connection state is unknown. Closing it costs one handshake and
            # guarantees the next request cannot be parsed out of a half-read stream -- the
            # failure mode being a 501 from the base handler, which names nothing useful.
            self.close_connection = True
            self._fail(400, str(exc))
            return
        self._json(201, {"upload": upload.to_dict(), **self._upload_state()})

    def _drain(self) -> None:
        """Read and discard a rejected body so the connection stays usable.

        ``protocol_version`` is HTTP/1.1, so the socket is kept alive between requests. An
        unread body would be parsed as the start of the next request line. Capped: a body
        that was refused for being too large is exactly the one not worth reading in full,
        so past the cap the connection is closed instead.
        """
        try:
            remaining = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            remaining = 0
        if remaining > 4 * 1024 * 1024:
            self.close_connection = True
            return
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _submit(self, scene: str | None, body: dict[str, Any], kind: str) -> None:
        if not scene or not SAFE_ID.match(scene):
            self._fail(400, f"invalid scene name {scene!r}")
            return

        # Resolve every upload id the request carries into a path on disk, before anything
        # else, so a stale id fails immediately with the slot named rather than a minute
        # into a job.
        try:
            supplied = {
                f"{kind_name}_upload": uploads_mod.resolve(
                    kind_name, body.get(f"{kind_name}Upload")
                )
                for kind_name in uploads_mod.KINDS
            }
        except uploads_mod.UploadError as exc:
            self._fail(400, str(exc))
            return
        for kind_name in uploads_mod.KINDS:
            requested = body.get(f"{kind_name}Upload")
            if requested and supplied[f"{kind_name}_upload"] is None:
                self._fail(
                    404,
                    f"no {kind_name} upload with id {requested!r} is on the server",
                    hint="re-upload the file; the server may have been restarted",
                )
                return

        # A case built on an uploaded scene does not need a dataset scene of that name --
        # the id is only the label the case is stored under. Every other case does.
        if supplied["scene_upload"] is None:
            try:
                case_mod.scene_paths(scene)
            except (case_mod.CaseError, ValueError) as exc:
                self._fail(404, str(exc))
                return
        elif STORE.exists(scene) and kind == "detect":
            # Storing under an id that already holds a dataset case would overwrite it
            # with a case built from different pixels under the same name. The store is
            # keyed by id alone, so the only place to catch this is here.
            self._fail(
                409,
                f"case {scene!r} already exists; choose another id for the uploaded scene",
                hint="the case id is the label this result is stored under, so it has to be free",
            )
            return

        defaults = C.DriftConfig()
        try:
            request = case_mod.CaseRequest(
                scene=scene,
                detector=str(body.get("detector", "auto")),
                particles=max(50, min(20000, int(body.get("particles", defaults.particle_count)))),
                horizon_hours=(
                    float(body["horizonHours"]) if body.get("horizonHours") not in (None, "") else None
                ),
                threshold=(
                    float(body["threshold"]) if body.get("threshold") not in (None, "") else None
                ),
                previews=bool(body.get("previews", True)),
                seed=int(body.get("seed", defaults.seed)),
                acquired_utc=(str(body["acquiredUtc"]) if body.get("acquiredUtc") else None),
                band_order=(str(body["bandOrder"]) if body.get("bandOrder") else None),
                # Presentation only, and capped here because it arrives in a request body.
                # Deliberately absent from `request.key()`: a name must not change a seed.
                label=(" ".join(str(body["label"]).split())[:LABEL_MAX] or None
                       if body.get("label") else None),
                **supplied,
            )
        except (TypeError, ValueError) as exc:
            self._fail(400, f"bad request parameters: {exc}")
            return

        reuse = kind == "drift"
        if reuse and not STORE.exists(scene):
            self._fail(404, f"case {scene!r} has no stored detection; POST /detect first")
            return

        def work(say: Any) -> dict[str, Any]:
            cached: dict[str, Any] | None = None
            if reuse:
                bundle = STORE.load_mask(scene)
                if bundle is not None:
                    mask, meta = bundle
                    cached = {**meta, "mask": mask, "probability": None}
                    say("loaded the stored detection mask")
                else:
                    say("no cached mask; re-running detection")
            payload = case_mod.build_case(request, progress=say, detection=cached)
            mask = payload.pop("_mask", None)
            STORE.save(scene, payload)
            if mask is not None:
                STORE.save_mask(scene, mask, payload.get("detection") or {})
            say(f"stored case {scene}")
            return {"caseId": scene, "candidates": len(payload.get("vessels") or [])}

        job = RUNNER.submit(kind, scene, work)
        self._json(202, {
            "jobId": job.id,
            "kind": kind,
            "scene": scene,
            "state": job.state,
            "requestKey": request.key(),
            "pollUrl": f"/api/jobs/{job.id}",
            "resultUrl": f"/api/jobs/{job.id}/result",
            "caseUrl": f"/api/cases/{scene}",
            "note": "poll pollUrl until state is done, failed or cancelled",
        })

    # -- static ------------------------------------------------------------

    def _static(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        target = (WEB_ROOT / relative).resolve()
        try:
            inside = target == WEB_ROOT.resolve() or WEB_ROOT.resolve() in target.parents
        except OSError:
            inside = False
        if not inside:
            self._fail(400, "path escapes the web root")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # Single-page app: unknown paths fall back to the shell, which then routes
            # client-side. A missing shell is a build problem, so say so plainly.
            target = WEB_ROOT / "index.html"
            if not target.is_file():
                self._fail(404, "apps/web/index.html is missing", path=path)
                return
        body = target.read_bytes()
        # This server is the development server: `apps/web` is served straight off disk,
        # so a cache that outlives an edit means looking at code that is no longer there.
        # Source revalidates on every request; the rasters and fonts under the same root
        # are content that does not change, and keep the long cache.
        source = target.suffix in (".html", ".js", ".css", ".json", ".map")
        cache = "no-cache" if source else "public, max-age=300"
        headers = {"Cache-Control": cache}
        if source:
            headers["ETag"] = f'W/"{target.stat().st_mtime_ns:x}-{len(body):x}"'
            if self.headers.get("If-None-Match") == headers["ETag"]:
                self._send(304, b"", MIME.get(target.suffix, "text/plain"), headers)
                return
        self._send(200, body, MIME.get(target.suffix, "application/octet-stream"), headers)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        print(f"  {self.log_date_time_string()}  {fmt % args}", flush=True)


class ApiOnlyHandler(SpillTraceHandler):
    """The same routes without the static file server, for API-only deployments."""

    serve_frontend = False


# ---------------------------------------------------------------------------
# Demo case
# ---------------------------------------------------------------------------

def pick_demo_scene() -> str | None:
    """A test-split scene, so the demo shows the model on data it never trained on."""
    names = case_mod.available_scenes()
    if not names:
        return None
    splits = _read_json(C.PROCESSED_DIR / "splits.json") or {}
    test = splits.get("scenes", {}).get("test") or []
    manifest = _read_json(C.PROCESSED_DIR / "preview_manifest.json") or {}
    # Prefer a test scene that already has previews rendered and a decent amount of oil,
    # so the demo is representative rather than a hairline streak.
    ranked = sorted(
        (entry for entry in manifest.get("scenes") or [] if entry.get("split") == "test"),
        key=lambda entry: -(entry.get("referenceOilFraction") or 0.0),
    )
    for entry in ranked:
        if entry["scene"] in names:
            return entry["scene"]
    for name in test:
        if name in names:
            return name
    return names[0]


def build_demo(force: bool = False, particles: int | None = None) -> str | None:
    """Compute the offline demo case if it is absent. Returns the scene used."""
    if STORE.exists(store_mod.DEMO_KEY) and not force:
        return None
    scene = pick_demo_scene()
    if scene is None:
        print("no scenes on disk; skipping the demo case", flush=True)
        return None
    defaults = C.DriftConfig()
    request = case_mod.CaseRequest(
        scene=scene,
        particles=particles or defaults.particle_count,
        previews=True,
        seed=defaults.seed,
    )
    print(f"building the offline demo case from scene {scene}", flush=True)
    try:
        payload = case_mod.build_case(request, progress=lambda m: print(f"    {m}", flush=True))
    except Exception as exc:  # noqa: BLE001 - the API must still start without a demo
        print(f"demo case failed: {type(exc).__name__}: {exc}", flush=True)
        return None
    mask = payload.pop("_mask", None)
    payload["demo"] = True
    payload["id"] = store_mod.DEMO_KEY
    payload["demoScene"] = scene
    payload["demoNote"] = (
        "Seeded offline demonstration case. Every stage ran on the supplied scene with "
        "fixed seeds, so it reproduces byte for byte and needs no live service."
    )
    STORE.save(store_mod.DEMO_KEY, payload)
    if mask is not None:
        STORE.save_mask(store_mod.DEMO_KEY, mask, payload.get("detection") or {})
    # Also store it under the scene name so the scene list shows it as computed.
    payload_scene = dict(payload)
    payload_scene["id"] = scene
    payload_scene.pop("demo", None)
    STORE.save(scene, payload_scene)
    if mask is not None:
        STORE.save_mask(scene, mask, payload.get("detection") or {})
    print(f"demo case stored as {store_mod.DEMO_KEY!r} (scene {scene})", flush=True)
    return scene


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    host: str = "127.0.0.1",
    port: int = 8765,
    serve_frontend: bool = True,
    demo: bool = True,
) -> None:
    handler = SpillTraceHandler if serve_frontend else ApiOnlyHandler
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True

    if demo:
        # In a thread so the port is listening immediately: the demo takes about a
        # minute and there is no reason for the health endpoint to wait for it.
        threading.Thread(target=build_demo, name="demo-builder", daemon=True).start()

    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"SpillTrace API {C.PIPELINE_VERSION} on http://{shown}:{port}", flush=True)
    if serve_frontend:
        print(f"  dashboard  http://{shown}:{port}/", flush=True)
    print(f"  health     http://{shown}:{port}/api/health", flush=True)
    print(f"  cases      http://{shown}:{port}/api/cases", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        RUNNER.shutdown()
        server.server_close()
