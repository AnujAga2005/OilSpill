"""Routing, the store, jobs, and the shapes the dashboard reads.

Every request here goes through the real `SpillTraceHandler` -- its routing, its handlers,
its serialisation -- driven over a pair of byte buffers instead of a socket, because binding
a listening port is not available in every environment this has to run in. What is exercised
is therefore the code that runs in production, not a reimplementation of it.

Two groups matter more than the rest. The path-traversal tests exist because three separate
endpoints turn a URL fragment into a filesystem path. And the 404 tests exist because "not
computed yet" is a routine state in this product, not an error: the response has to say what
would produce the missing thing, or the dashboard has nothing to tell the operator.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from spilltrace_api import case as case_mod
from spilltrace_api import jobs as jobs_mod
from spilltrace_api import server as server_mod
from spilltrace_api import store as store_mod
from spilltrace_common import config as C
from spilltrace_drift import marinecadastre as MC

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# A socket-free client for the real handler
# ---------------------------------------------------------------------------

class Response:
    def __init__(self, raw: bytes) -> None:
        head, _, self.body = raw.partition(b"\r\n\r\n")
        lines = head.decode(errors="replace").split("\r\n")
        self.status = int(lines[0].split(" ")[1]) if len(lines[0].split(" ")) > 1 else 0
        self.headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                self.headers[key.strip().lower()] = value.strip()

    def json(self) -> Any:
        return json.loads(self.body.decode())


def request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    api_only: bool = False,
    headers: dict[str, str] | None = None,
):
    """Drive one request through the real handler and return the parsed response."""
    base = server_mod.ApiOnlyHandler if api_only else server_mod.SpillTraceHandler

    class Loopback(base):  # type: ignore[valid-type, misc]
        def __init__(self, raw: bytes) -> None:
            self.rfile = io.BytesIO(raw)
            self.wfile = io.BytesIO()
            self.connection = None
            self.client_address = ("127.0.0.1", 0)
            self.server = None
            self.requestline = ""
            self.request_version = "HTTP/1.1"
            self.command = ""
            self.handle()

        def setup(self) -> None:
            pass

        def finish(self) -> None:
            pass

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            pass

    head = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
    for name, value in (headers or {}).items():
        head += f"{name}: {value}\r\n"
    payload = b"" if body is None else json.dumps(body).encode()
    if body is not None:
        head += f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
    raw = head.encode() + b"\r\n" + payload
    return Response(Loopback(raw).wfile.getvalue())


def get(path: str, **kwargs) -> Response:
    return request("GET", path, **kwargs)


def post(path: str, body: dict[str, Any] | None = None, **kwargs) -> Response:
    return request("POST", path, body if body is not None else {}, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def sample_case(case_id: str = "demo") -> dict[str, Any]:
    """A case document with one field from every section the API reads.

    The candidate list appears twice, under `vessels` and under `attribution.candidates`,
    because that is how the pipeline writes it: the screens read the former and the CSV
    exporter reads the ranking document it was built from.
    """
    candidates = [
        {"rank": 1, "name": "SYNTH-1", "mmsi": "999000001", "score": 0.81, "band": "high",
         "type": "tanker", "status": C.LABEL_CANDIDATE, "components": {}, "evidence": {}},
        {"rank": 2, "name": "SYNTH-2", "mmsi": "999000002", "score": 0.44, "band": "low",
         "type": "cargo", "status": C.LABEL_CANDIDATE, "components": {}, "evidence": {}},
    ]
    return {
        "id": case_id,
        "caseId": case_id,
        "status": C.LABEL_STATUS,
        "generatedUtc": "2024-01-01T00:00:00Z",
        "pipelineVersion": C.PIPELINE_VERSION,
        "scene": {
            "name": "scene_00053",
            "region": "Persian Gulf",
            "mission": "SENTINEL-1A",
            "acquiredStartUtc": "2020-05-01T02:11:00Z",
            "bounds": {"west": 54.6, "east": 54.78, "south": 25.5, "north": 25.69},
        },
        "slick": {"areaKm2": 12.5, "totalAreaKm2": 14.0, "slickCount": 2, "confidence": "medium"},
        "provenance": {
            "detectionSource": "model",
            "detectionLabel": C.LABEL_PREDICTION,
            "driftMode": "synthetic",
            "aisLabel": C.LABEL_AIS,
        },
        "attribution": {
            "candidateCount": 2,
            "candidates": candidates,
            "weights": {"proximity": 0.4},
            "method": "weighted sum of four explainable factors",
            "caveat": "synthetic AIS",
            "candidateLabel": C.LABEL_CANDIDATE,
            "releaseWindow": {"startUtc": "2020-04-30T20:00:00Z"},
            "originZone": {"lat": 25.6, "lon": 54.7},
            "driftLabel": C.LABEL_DRIFT_SYNTHETIC,
        },
        "vessels": candidates,
        "detection": {"source": "model", "threshold": 0.8},
        "forcing": {"mode": "synthetic"},
        "trajectories": {"originEstimate": {"lat": 25.6, "lon": 54.7}},
        "ais": {"releaseWindow": {"hours": 12}, "reports": [{"mmsi": "999000001"}]},
        "geometry": {"rings": [[[54.6, 25.5]]]},
        "drift": {"backward": {"particles": 2000}},
        "previews": {
            "directory": str(C.PREVIEW_DIR),
            "files": {"vv": "does_not_exist_vv.png"},
            "bounds": {"west": 54.6, "east": 54.78, "south": 25.5, "north": 25.69},
            "legend": {"oil": "model detection"},
            "previewSize": [512, 512],
        },
        "limits": ["synthetic AIS"],
        "timing": {"stages": [{"stage": "detect", "seconds": 4.2}]},
    }


def _case_with_ais(case_id: str = "demo") -> dict[str, Any]:
    """`sample_case` plus the vessel objects the drift stage adds.

    Only the fields the exporter reads are present; anything it does not read would make
    the fixture agree with the exporter for the wrong reason.
    """
    case = sample_case(case_id)
    case["ais"] = dict(case["ais"])
    case["ais"]["vessels"] = [
        {
            "mmsi": "999000001",
            "name": "SYNTH-1",
            "imo": None,
            "callSign": "QDEMO001",
            "vesselTypeCode": 80,
            "cargoCode": 80,
            "lengthM": 250,
            "widthM": 44,
            "draftM": 14.0,
            "transceiverClass": "A",
            "reports": [
                {"timeUtc": "2020-04-30T20:00:00Z", "lat": 25.60, "lon": 54.70,
                 "sogKn": 11.2, "cogDeg": 104.0, "headingDeg": 103.0, "statusCode": 0,
                 "navStatus": "under way using engine"},
                {"timeUtc": "2020-04-30T20:10:00Z", "lat": 25.61, "lon": 54.72,
                 "sogKn": 11.4, "cogDeg": None, "headingDeg": None, "statusCode": 0,
                 "navStatus": "under way using engine"},
            ],
        }
    ]
    return case


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the server's module-level store at a temporary directory.

    Without this the tests would write into `data/processed/cases`, i.e. into the artefacts
    the dashboard actually serves.
    """
    fresh = store_mod.CaseStore(tmp_path / "cases")
    monkeypatch.setattr(server_mod, "STORE", fresh)
    return fresh


@pytest.fixture
def runner(monkeypatch):
    fresh = jobs_mod.JobRunner(workers=1)
    monkeypatch.setattr(server_mod, "RUNNER", fresh)
    yield fresh
    fresh.shutdown()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class TestRouting:
    @pytest.mark.parametrize(
        ("path", "expected", "param"),
        [
            ("/api/health", "health", None),
            ("/api/metrics", "metrics", None),
            ("/api/scenes", "scenes", None),
            ("/api/cases", "cases", None),
            ("/api/cases/demo", "case", "demo"),
            ("/api/cases/demo/images", "case_images", "demo"),
            ("/api/cases/demo/detect", "detect", "demo"),
            ("/api/cases/demo/slick", "slick", "demo"),
            ("/api/cases/demo/drift", "drift", "demo"),
            ("/api/cases/demo/trajectories", "trajectories", "demo"),
            ("/api/cases/demo/vessels", "vessels", "demo"),
            ("/api/cases/demo/report", "report", "demo"),
            ("/api/cases/demo/ais.csv", "case_ais_csv", "demo"),
            ("/api/eval/test_0117_iou100.png", "eval_image", "test_0117_iou100.png"),
            ("/api/jobs", "jobs", None),
            ("/api/jobs/abc123", "job", "abc123"),
            ("/api/jobs/abc123/result", "job_result", "abc123"),
            ("/api/jobs/abc123/cancel", "job_cancel", "abc123"),
        ],
    )
    def test_every_route_resolves(self, path, expected, param):
        assert server_mod.route(path) == (expected, param)

    def test_a_trailing_slash_is_the_same_route(self):
        assert server_mod.route("/api/cases/")[0] == "cases"

    def test_an_unknown_path_is_not_guessed_at(self):
        assert server_mod.route("/api/nope") == ("unknown", None)
        assert server_mod.route("/api/cases/demo/nope") == ("unknown", None)

    def test_the_more_specific_route_wins(self):
        """`/api/cases/x/images` must not be read as case id `x/images`."""
        assert server_mod.route("/api/cases/x/images") == ("case_images", "x")

    def test_the_ais_csv_suffix_is_not_read_as_a_case_id(self):
        """`ais.csv` contains a dot, which `[^/]+` in the bare-case pattern happily eats."""
        assert server_mod.route("/api/cases/x/ais.csv") == ("case_ais_csv", "x")
        assert server_mod.route("/api/cases/x/ais.json") == ("unknown", None)

    @pytest.mark.parametrize(
        "path",
        [
            "/api/eval/../secret.png",
            "/api/eval/../../etc/passwd.png",
            "/api/eval/sub/dir.png",
            "/api/eval/%2e%2e%2fsecret.png",
            "/api/eval/notapng.txt",
        ],
    )
    def test_the_eval_route_does_not_match_a_traversal(self, path):
        """The pattern is the first line of defence; `_eval_image` re-checks on disk."""
        assert server_mod.route(path) == ("unknown", None)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestCaseReads:
    def test_a_missing_case_says_what_would_build_it(self, store, runner):
        response = get("/api/cases/nothing_here", api_only=True)
        assert response.status == 404
        payload = response.json()
        assert payload["scene"] == "nothing_here"
        assert "detect" in payload["hint"]
        assert payload["pendingJobs"] == []

    def test_a_stored_case_is_served_whole(self, store):
        store.save("demo", sample_case())
        payload = get("/api/cases/demo", api_only=True).json()
        assert payload["id"] == "demo"
        assert "geometry" in payload
        assert "drift" in payload
        assert "ais" in payload

    def test_the_lean_case_drops_the_three_heavy_sections(self, store):
        """The overview screen needs none of them and they are most of the document."""
        store.save("demo", sample_case())
        payload = get("/api/cases/demo?lean=1", api_only=True).json()
        assert "geometry" not in payload
        assert "drift" not in payload
        assert "ais" not in payload
        assert payload["slick"]["areaKm2"] == 12.5

    def test_the_lean_case_is_smaller_than_the_full_one(self, store):
        """With sections the size they actually reach, not the size a fixture makes them.

        `sample_case` carries one token entry per section, and against a document that
        small the saving is a few dozen bytes -- a margin that would survive the lean
        projection being reduced to a no-op. So the three heavy sections are filled here
        to roughly the order of magnitude a real case reaches: a ring per detected slick,
        two thousand particles, a few hundred AIS reports.
        """
        heavy = sample_case()
        heavy["geometry"] = {"rings": [[[54.6 + i * 1e-4, 25.5 + i * 1e-4] for i in range(500)]]}
        heavy["drift"] = {"backward": {"tracks": [[[54.6, 25.5], [54.61, 25.51]]] * 2000}}
        heavy["ais"] = {"reports": [{"mmsi": "999000001", "lat": 25.6, "lon": 54.7}] * 400}
        store.save("demo", heavy)
        full = get("/api/cases/demo", api_only=True)
        lean = get("/api/cases/demo?lean=1", api_only=True)
        assert len(lean.body) < len(full.body) / 10

    def test_the_summary_view_is_the_index_shape(self, store):
        store.save("demo", sample_case())
        payload = get("/api/cases/demo?summary=1", api_only=True).json()
        assert payload["scene"] == "scene_00053"
        assert payload["topCandidate"]["mmsi"] == "999000001"
        assert "geometry" not in payload

    def test_an_unsafe_case_id_is_rejected_before_the_disk_is_touched(self, store):
        for case_id in ("..", ".hidden", "a/b"):
            response = get(f"/api/cases/{case_id}", api_only=True)
            assert response.status in (400, 404), case_id

    def test_a_section_endpoint_carries_the_status_label(self, store):
        """Every response that names a vessel or a slick has to carry the disclaimer."""
        store.save("demo", sample_case())
        payload = get("/api/cases/demo/slick", api_only=True).json()
        assert payload["status"] == C.LABEL_STATUS
        assert payload["caseId"] == "demo"

    def test_the_vessels_endpoint_never_implies_guilt(self, store):
        store.save("demo", sample_case())
        payload = get("/api/cases/demo/vessels", api_only=True).json()
        assert payload["candidateLabel"] == C.LABEL_CANDIDATE
        assert "investigation" in payload["candidateLabel"].lower()
        assert payload["aisMode"] == C.LABEL_AIS
        assert "ynthetic" in payload["aisMode"]
        text = json.dumps(payload).lower()
        for word in ("guilty", "responsible for", "confirmed culprit"):
            assert word not in text

    def test_the_report_carries_its_own_caveats(self, store):
        store.save("demo", sample_case())
        payload = get("/api/cases/demo/report", api_only=True).json()
        assert payload["status"] == C.LABEL_STATUS
        assert payload["attributionCaveat"]
        assert payload["limits"] == ["synthetic AIS"]
        assert "detectionMetrics" in payload

    def test_the_report_summarises_the_screening_without_the_patch_detail(self, store):
        """The counts are the part a brief needs; the per-patch table is a download."""
        case = sample_case()
        case["screening"] = {
            "headline": "2 of 9 dark patches rejected as look-alikes",
            "fitted": True,
            "counts": {"proposed": 9, "rejected": 2, "accepted": 6, "uncertain": 1},
            "thresholds": {"rejectAtOrBelow": 0.35, "acceptAtOrAbove": 0.65},
            "limits": ["the screen cannot name the phenomenon"],
            "patches": [{"id": f"patch-{index:02d}"} for index in range(9)],
        }
        store.save("demo", case)
        block = get("/api/cases/demo/report", api_only=True).json()["screening"]
        assert block["counts"]["rejected"] == 2
        assert block["headline"].startswith("2 of 9")
        assert block["thresholds"]["acceptAtOrAbove"] == 0.65
        assert block["limits"]
        assert "patches" not in block

    def test_a_case_with_no_screening_says_so_rather_than_omitting_the_key(self, store):
        store.save("demo", sample_case())
        assert get("/api/cases/demo/report", api_only=True).json()["screening"] is None

    def test_the_csv_report_is_an_attachment(self, store):
        store.save("demo", sample_case())
        response = get("/api/cases/demo/report?format=csv", api_only=True)
        assert response.status == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert b"999000001" in response.body

    def test_the_ais_export_has_the_marinecadastre_header_byte_for_byte(self, store):
        """The point of the route is that the format claim can be checked by downloading
        the file and diffing line one against a real daily extract."""
        store.save("demo", _case_with_ais())
        response = get("/api/cases/demo/ais.csv", api_only=True)
        assert response.status == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert "marinecadastre" in response.headers["content-disposition"]
        lines = response.body.decode("utf-8").splitlines()
        assert lines[0] == (
            "MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,"
            "VesselType,Status,Length,Width,Draft,Cargo,TransceiverClass"
        )
        assert len(lines) == 3  # header plus one row per report
        for line in lines[1:]:
            assert len(line.split(",")) == len(MC.HEADER)

    def test_the_ais_export_is_readable_by_the_importer(self, store):
        """Written and read by the same pair of functions the real-file path uses, so a
        drift in either direction shows up here rather than in a demo."""
        store.save("demo", _case_with_ais())
        body = get("/api/cases/demo/ais.csv", api_only=True).body
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8"))))
        MC.check_header(list(rows[0].keys()))
        parsed = [MC.parse_row(row) for row in rows]
        assert all(record is not None for record in parsed)
        assert {record["mmsi"] for record in parsed} == {"999000001"}
        assert {record["vesselGroup"] for record in parsed} == {"Tanker"}

    def test_a_case_without_an_ais_feed_says_what_would_produce_one(self, store):
        """`sample_case` has AIS reports but no vessel objects -- the state a case is in
        before the drift stage has run."""
        store.save("demo", sample_case())
        response = get("/api/cases/demo/ais.csv", api_only=True)
        assert response.status == 404
        assert "drift" in response.json()["error"]

    def test_the_image_index_lists_what_exists_without_leaking_a_path(self, store):
        store.save("demo", sample_case())
        payload = get("/api/cases/demo/images", api_only=True).json()
        assert payload["available"] == ["vv"]
        assert payload["urls"]["vv"].endswith("kind=vv")
        assert payload["legend"] == {"oil": "model detection"}

    def test_an_unknown_preview_kind_says_which_kinds_exist(self, store):
        store.save("demo", sample_case())
        response = get("/api/cases/demo/images?kind=nope", api_only=True)
        assert response.status == 404
        assert response.json()["available"] == ["vv"]

    def test_a_preview_filename_that_escapes_the_directory_is_refused(self, store):
        """The filename comes from our own renderer, but a stored case is a file on disk
        and files on disk can be edited."""
        case = sample_case()
        case["previews"]["files"]["vv"] = "../../../etc/passwd"
        store.save("demo", case)
        response = get("/api/cases/demo/images?kind=vv", api_only=True)
        assert response.status == 404
        assert b"passwd" not in response.body or b"not on disk" in response.body


# ---------------------------------------------------------------------------
# Naming a stored case
# ---------------------------------------------------------------------------

class TestCaseLabel:
    """`POST /api/cases/<id>/label` writes what a person calls a run, and nothing else.

    The whole point of the endpoint is that it is inert: the case is already on disk, it was
    computed from a request key that does not mention the name, and renaming it must not be
    able to move a single number. So these tests check two things in roughly equal measure --
    that the name arrives where the case list reads it, and that everything around it is
    untouched.
    """

    def test_a_name_reaches_the_summary_the_case_list_is_built_from(self, store):
        store.save("demo", sample_case())
        response = post(
            "/api/cases/demo/label",
            {"label": "Malta channel, morning pass"},
            api_only=True,
        )
        assert response.status == 200
        assert response.json()["label"] == "Malta channel, morning pass"
        assert store.summaries()[0]["label"] == "Malta channel, morning pass"

    def test_a_description_can_be_written_with_it_or_on_its_own(self, store):
        store.save("demo", sample_case())
        post("/api/cases/demo/label", {"label": "Morning pass"}, api_only=True)
        post(
            "/api/cases/demo/label",
            {"description": "Re-run after the ERA5 file arrived."},
            api_only=True,
        )
        payload = store.load("demo")
        assert payload["label"] == "Morning pass"
        assert payload["description"] == "Re-run after the ERA5 file arrived."

    def test_naming_a_case_changes_nothing_that_was_measured(self, store):
        """Every other key comes back byte-identical, because none of them are its business."""
        before = sample_case()
        store.save("demo", before)
        post("/api/cases/demo/label", {"label": "Whatever it is called"}, api_only=True)
        after = store.load("demo")
        assert {k: v for k, v in after.items() if k != "label"} == before

    def test_an_empty_string_clears_the_field_rather_than_storing_one(self, store):
        """The case list falls back to the id, so a cleared name has to be absent, not "".

        A stored empty string would render as a blank option in the picker -- present,
        selectable and saying nothing.
        """
        store.save("demo", sample_case())
        post("/api/cases/demo/label", {"label": "Temporary"}, api_only=True)
        post("/api/cases/demo/label", {"label": "  "}, api_only=True)
        assert "label" not in store.load("demo")
        assert store.summaries()[0]["label"] is None

    def test_newlines_are_collapsed_because_this_lands_in_one_line_of_a_dropdown(self, store):
        store.save("demo", sample_case())
        payload = post(
            "/api/cases/demo/label", {"label": "Malta\nchannel\t pass"}, api_only=True
        ).json()
        assert payload["label"] == "Malta channel pass"

    def test_a_name_longer_than_the_cap_is_refused_and_nothing_is_written(self, store):
        store.save("demo", sample_case())
        response = post(
            "/api/cases/demo/label",
            {"label": "x" * (server_mod.LABEL_MAX + 1)},
            api_only=True,
        )
        assert response.status == 400
        assert str(server_mod.LABEL_MAX) in response.json()["error"]
        assert "label" not in store.load("demo")

    def test_a_description_longer_than_the_cap_is_refused(self, store):
        store.save("demo", sample_case())
        response = post(
            "/api/cases/demo/label",
            {"description": "x" * (server_mod.DESCRIPTION_MAX + 1)},
            api_only=True,
        )
        assert response.status == 400
        assert "description" not in store.load("demo")

    @pytest.mark.parametrize("value", [7, ["a"], {"a": 1}, True])
    def test_a_name_that_is_not_a_string_is_refused(self, store, value):
        store.save("demo", sample_case())
        response = post("/api/cases/demo/label", {"label": value}, api_only=True)
        assert response.status == 400
        assert "must be a string" in response.json()["error"]

    def test_a_request_that_names_neither_field_is_refused(self, store):
        """Silently succeeding at nothing would show as a saved name that never appears."""
        store.save("demo", sample_case())
        response = post("/api/cases/demo/label", {"note": "hello"}, api_only=True)
        assert response.status == 400

    def test_an_unknown_case_is_a_404_and_creates_nothing(self, store, runner):
        response = post("/api/cases/nothing_here/label", {"label": "x"}, api_only=True)
        assert response.status == 404
        assert not store.exists("nothing_here")

    def test_an_unsafe_case_id_is_refused_by_the_route_before_the_handler(self):
        assert server_mod.route("/api/cases/../../etc/label") == ("unknown", None)

    def test_the_label_route_is_not_swallowed_by_the_case_route(self):
        """Both patterns match `/api/cases/<something>`; order is what separates them."""
        assert server_mod.route("/api/cases/demo/label") == ("case_label", "demo")
        assert server_mod.route("/api/cases/demo") == ("case", "demo")

    def test_a_name_is_not_part_of_the_request_key(self):
        """Renaming a run must not change its seed, its cache identity or its result.

        This is why `label` is excluded from `CaseRequest.key()`: the key is what decides
        whether a stored case can be reused, and two runs that differ only in what they are
        called are the same run.
        """
        plain = case_mod.CaseRequest(scene="scene_00053")
        named = case_mod.CaseRequest(scene="scene_00053", label="Morning pass")
        assert plain.key() == named.key()


# ---------------------------------------------------------------------------
# Deleting a stored case
# ---------------------------------------------------------------------------

def _case_with_previews(directory: Path, case_id: str = "demo") -> dict[str, Any]:
    """`sample_case` with its preview manifest pointing at a directory of real files.

    The fixture's own manifest names the repository's preview directory, which is where the
    dashboard's actual PNGs live. A delete test that used it unchanged would unlink them.
    """
    case = sample_case(case_id)
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for kind in ("vv", "prediction", "probability"):
        name = f"{case_id}_{kind}.png"
        (directory / name).write_bytes(b"\x89PNG\r\n\x1a\n")
        files[kind] = name
    case["previews"] = dict(case["previews"], directory=str(directory), files=files)
    return case


class TestCaseDelete:
    """`POST /api/cases/<id>/delete` removes a run, and only what the run produced.

    The endpoint is the counterpart to a run, so the tests are paired the same way as the
    naming ones: half check that everything the pipeline wrote is actually gone, and half
    check that nothing the pipeline merely *read* is -- a deleted case must free its id
    without costing the operator the scene it was built from.
    """

    def test_the_document_and_its_cached_mask_both_go(self, store, tmp_path):
        store.save("demo", _case_with_previews(tmp_path / "previews"))
        store.save_mask("demo", np.zeros((4, 4), dtype=np.uint8), {"threshold": 0.5})
        assert store.mask_path_for("demo").exists()

        response = post("/api/cases/demo/delete", {}, api_only=True)

        assert response.status == 200
        assert response.json()["deleted"] == "demo"
        assert not store.exists("demo")
        assert not store.mask_path_for("demo").exists()

    def test_the_previews_it_rendered_go_with_it(self, store, tmp_path):
        directory = tmp_path / "previews"
        store.save("demo", _case_with_previews(directory))

        payload = post("/api/cases/demo/delete", {}, api_only=True).json()

        assert payload["removedPreviews"] == 3
        assert sorted(p.name for p in directory.iterdir()) == []

    def test_a_preview_path_pointing_outside_its_directory_is_not_followed(
        self, store, tmp_path
    ):
        """The manifest is data on disk, so it is treated as a name, never as a path.

        `_case_or_404` will happily load a case document that was edited by hand, and the
        only thing between a `../` in it and a deleted file elsewhere is that the name is
        reduced to its basename and the result is required to resolve inside the directory.
        """
        directory = tmp_path / "previews"
        outsider = tmp_path / "keep_me.png"
        outsider.write_bytes(b"\x89PNG\r\n\x1a\n")
        case = _case_with_previews(directory)
        case["previews"]["files"]["vv"] = "../keep_me.png"
        store.save("demo", case)

        payload = post("/api/cases/demo/delete", {}, api_only=True).json()

        assert outsider.exists()
        # The two well-formed previews still went; only the escaping name was skipped.
        assert payload["removedPreviews"] == 2

    def test_another_case_is_left_whole(self, store, tmp_path):
        """One id in, one id out. The store is a shelf, not a transaction log."""
        store.save("demo", _case_with_previews(tmp_path / "a", "demo"))
        keep = _case_with_previews(tmp_path / "b", "other")
        store.save("other", keep)
        store.save_mask("other", np.zeros((4, 4), dtype=np.uint8), {"threshold": 0.5})

        post("/api/cases/demo/delete", {}, api_only=True)

        assert store.load("other") == keep
        assert store.mask_path_for("other").exists()
        assert sorted(p.name for p in (tmp_path / "b").iterdir()) == [
            "other_prediction.png",
            "other_probability.png",
            "other_vv.png",
        ]

    def test_the_response_carries_the_list_the_picker_rebuilds_from(self, store, tmp_path):
        """The client replaces its case list from this, rather than re-fetching it.

        A delete that returned only `{deleted}` would leave the picker showing the case it
        just removed until the next load.
        """
        store.save("demo", _case_with_previews(tmp_path / "a", "demo"))
        store.save("other", _case_with_previews(tmp_path / "b", "other"))

        payload = post("/api/cases/demo/delete", {}, api_only=True).json()

        assert [entry["id"] for entry in payload["cases"]] == ["other"]

    def test_the_id_is_free_afterwards(self, store, tmp_path):
        store.save("demo", _case_with_previews(tmp_path / "a", "demo"))
        post("/api/cases/demo/delete", {}, api_only=True)
        store.save("demo", _case_with_previews(tmp_path / "c", "demo"))
        assert store.exists("demo")

    def test_an_unknown_case_is_a_404(self, store):
        response = post("/api/cases/nothing_here/delete", {}, api_only=True)
        assert response.status == 404

    def test_an_unsafe_case_id_is_refused_by_the_route_before_the_handler(self):
        assert server_mod.route("/api/cases/../../etc/delete") == ("unknown", None)

    def test_the_delete_route_is_not_swallowed_by_the_case_route(self):
        """Three patterns now match `/api/cases/<something>`; order is what separates them."""
        assert server_mod.route("/api/cases/demo/delete") == ("case_delete", "demo")
        assert server_mod.route("/api/cases/demo/label") == ("case_label", "demo")
        assert server_mod.route("/api/cases/demo") == ("case", "demo")

    def test_a_case_with_no_previews_on_disk_still_deletes(self, store):
        """A case built with `previews: false` has a manifest naming files that were
        never rendered. It is still a case, and it still has to be removable."""
        store.save("demo", sample_case())
        payload = post("/api/cases/demo/delete", {}, api_only=True).json()
        assert payload["deleted"] == "demo"
        assert payload["removedPreviews"] == 0


class TestEvalImages:
    def test_an_unknown_name_says_what_would_produce_it(self):
        response = get("/api/eval/no_such_strip.png", api_only=True)
        assert response.status == 404
        assert "run_train" in response.json()["hint"]

    def test_a_real_strip_is_served_as_a_png(self):
        directory = C.EVAL_PREVIEW_DIR
        candidates = sorted(directory.glob("*.png")) if directory.is_dir() else []
        if not candidates:
            pytest.skip("no evaluation strips on disk; run scripts/run_train.py")
        response = get(f"/api/eval/{candidates[0].name}", api_only=True)
        assert response.status == 200
        assert response.headers["content-type"] == "image/png"
        assert response.body.startswith(b"\x89PNG")
        assert "max-age" in response.headers["cache-control"]


class TestMetricsAndHealth:
    def test_metrics_labels_both_scales(self):
        """Serving only the flattering figure would be the easiest way to mislead."""
        payload = get("/api/metrics", api_only=True).json()
        assert payload["available"] is True
        assert "patch" in payload["patchScale"]["basis"]
        assert payload["sceneScale"] is not None

    def test_the_patch_and_scene_figures_are_kept_apart(self):
        payload = get("/api/metrics", api_only=True).json()
        patch = payload["patchScale"]["test"]["iou"]
        scene = ((payload["sceneScale"]["test"] or {}).get("atSceneThreshold") or {})
        if scene:
            assert scene["pooled"]["iou"] != patch  # they genuinely disagree

    def test_the_look_alike_screen_answers_a_question_iou_cannot(self):
        """Every IoU on this product is measured on scenes that contain oil, so none of them
        says how often dark water that is not oil raises an alarm. That figure lives here, and
        when it has not been measured the endpoint has to say so rather than stay silent."""
        block = get("/api/metrics", api_only=True).json()["lookAlike"]
        if block["available"]:
            assert set(block) >= {
                "question",
                "screen",
                "features",
                "sameDomain",
                "crossDomain",
                "limitations",
            }
            assert block["limitations"]
        else:
            assert "run_lookalike_eval" in block["note"]

    def test_health_publishes_the_disclaimers_the_interface_renders(self):
        payload = get("/api/health", api_only=True).json()
        assert payload["status"] == "ok"
        labels = payload["labels"]
        assert labels["ais"] == C.LABEL_AIS
        assert labels["candidate"] == C.LABEL_CANDIDATE
        assert labels["status"] == C.LABEL_STATUS

    def test_health_reports_the_model_it_actually_loaded(self):
        payload = get("/api/health", api_only=True).json()
        model = payload["model"]
        if model["available"]:
            assert model["parameters"] > 0
            assert model["checkpoint"].endswith(".npz")
        else:
            assert model["parameters"] is None

    def test_the_scene_list_marks_which_split_each_scene_is_in(self):
        payload = get("/api/scenes", api_only=True).json()
        assert payload["count"] == len(payload["scenes"])
        if payload["scenes"]:
            entry = payload["scenes"][0]
            assert set(entry) >= {"name", "split", "hasPreview", "hasCase"}


class TestProtocol:
    def test_options_is_answered_without_a_body(self):
        response = request("OPTIONS", "/api/health", api_only=True)
        assert response.status == 204
        assert response.headers["access-control-allow-origin"] == "*"

    def test_a_head_request_sends_headers_and_no_body(self):
        response = request("HEAD", "/api/health", api_only=True)
        assert response.status == 200
        assert int(response.headers["content-length"]) > 0
        assert response.body == b""

    def test_an_unknown_get_route_is_a_404_not_a_crash(self):
        response = get("/api/does-not-exist", api_only=True)
        assert response.status == 404
        assert "no GET route" in response.json()["error"]

    def test_an_unknown_post_route_is_a_405(self):
        response = post("/api/health", {}, api_only=True)
        assert response.status == 405

    def test_a_body_that_is_not_a_json_object_is_refused(self):
        raw = (
            b"POST /api/cases/x/detect HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
            b"Content-Type: application/json\r\nContent-Length: 7\r\n\r\n[1,2,3]"
        )

        class Loopback(server_mod.ApiOnlyHandler):
            def __init__(self) -> None:
                self.rfile = io.BytesIO(raw)
                self.wfile = io.BytesIO()
                self.connection = None
                self.client_address = ("127.0.0.1", 0)
                self.server = None
                self.requestline = ""
                self.request_version = "HTTP/1.1"
                self.command = ""
                self.handle()

            def setup(self) -> None:
                pass

            def finish(self) -> None:
                pass

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                pass

        response = Response(Loopback().wfile.getvalue())
        assert response.status == 400
        assert "JSON object" in response.json()["error"]

    def test_content_length_is_always_set(self):
        """HTTP/1.1 keep-alive without a length is how a browser hangs forever."""
        for path in ("/api/health", "/api/metrics", "/api/cases"):
            response = get(path, api_only=True)
            assert int(response.headers["content-length"]) == len(response.body), path

    def test_a_drift_request_for_an_uncomputed_case_is_refused(self, store, runner):
        response = post("/api/cases/scene_00053/drift", {}, api_only=True)
        assert response.status == 404

    def test_a_detect_request_for_an_unknown_scene_is_refused(self, store, runner):
        response = post("/api/cases/not_a_scene/detect", {}, api_only=True)
        assert response.status == 404
        assert not response.json().get("jobId")

    def test_an_unsafe_scene_name_never_reaches_the_pipeline(self, store, runner):
        response = post("/api/cases/..%2F..%2Fetc/detect", {}, api_only=True)
        assert response.status in (400, 404, 405)


class TestStaticFiles:
    def test_the_shell_is_served_at_the_root(self):
        response = get("/")
        assert response.status == 200
        assert b"<html" in response.body.lower()
        assert response.headers["cache-control"] == "no-cache"

    def test_a_module_is_served_as_javascript(self):
        response = get("/app/main.js")
        assert response.status == 200
        assert response.headers["content-type"].startswith("text/javascript")

    def test_source_revalidates_instead_of_being_cached_for_five_minutes(self):
        """`apps/web` is served off disk, so a stale module is code that is not there.

        This cost an hour once: an edit to `ui.js` was invisible in the browser, the
        served bytes were correct, and the page was running the copy it had cached under
        `max-age=300` with no validator to revalidate against.
        """
        for path in ("/app/main.js", "/styles/components.css", "/"):
            response = get(path)
            assert response.headers["cache-control"] == "no-cache", path
            assert response.headers.get("etag"), path

    def test_an_unchanged_module_answers_304(self):
        first = get("/app/main.js")
        again = get("/app/main.js", headers={"If-None-Match": first.headers["etag"]})
        assert again.status == 304
        assert again.body == b""

    def test_a_raster_keeps_the_long_cache(self):
        """Previews are content-addressed by case; they do not change under a running server."""
        response = get("/demo/previews/does-not-exist.png")
        # Absent files fall back to the shell, so assert on the rule instead of a fixture.
        assert response.status == 200
        png = ROOT / "apps" / "web" / "demo" / "previews"
        names = sorted(p.name for p in png.glob("*.png")) if png.is_dir() else []
        if not names:
            pytest.skip("no bundled previews on disk; run scripts/build_web.py")
        served = get(f"/demo/previews/{names[0]}")
        assert served.headers["cache-control"] == "public, max-age=300"

    def test_an_unknown_path_falls_back_to_the_shell(self):
        """Client-side routing: `/vessels` is a screen, not a file."""
        response = get("/vessels")
        assert response.status == 200
        assert b"<html" in response.body.lower()

    def test_a_path_escaping_the_web_root_is_refused(self):
        for path in ("/../../etc/passwd", "/../services/api/spilltrace_api/server.py"):
            response = get(path)
            assert response.status == 400, path
            assert "escapes" in response.json()["error"]

    def test_the_api_only_handler_serves_no_files(self):
        response = get("/", api_only=True)
        assert response.status == 404


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class TestCaseStore:
    def test_a_saved_case_comes_back_unchanged(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        payload = sample_case()
        store.save("demo", payload)
        assert store.load("demo") == payload

    def test_a_miss_is_none_and_not_an_invention(self, tmp_path):
        assert store_mod.CaseStore(tmp_path).load("nothing") is None

    def test_the_write_is_atomic(self, tmp_path):
        """A browser reloading mid-write must never read half a document, so the file is
        renamed into place rather than written in place."""
        store = store_mod.CaseStore(tmp_path)
        store.save("demo", sample_case())
        assert not list(tmp_path.glob("*.partial"))
        assert json.loads(store.path_for("demo").read_text())["id"] == "demo"

    def test_an_unreadable_case_raises_rather_than_returning_a_partial(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        store.path_for("broken").write_text("{not json")
        with pytest.raises(store_mod.StoreError):
            store.load("broken")

    @pytest.mark.parametrize("name", ["..", "../escape", "a/b", "", ".hidden", "x" * 200])
    def test_an_unsafe_identifier_cannot_reach_the_filesystem(self, tmp_path, name):
        store = store_mod.CaseStore(tmp_path)
        with pytest.raises(store_mod.StoreError):
            store.path_for(name)

    def test_deleting_a_case_removes_its_mask_too(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        store.save("demo", sample_case())
        store.save_mask("demo", np.zeros((8, 8), dtype=np.uint8), {"source": "model"})
        assert store.delete("demo") is True
        assert not store.path_for("demo").exists()
        assert not store.mask_path_for("demo").exists()

    def test_deleting_a_case_that_is_not_there_is_not_an_error(self, tmp_path):
        assert store_mod.CaseStore(tmp_path).delete("ghost") is False

    def test_a_mask_survives_the_round_trip(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        mask = (np.arange(64).reshape(8, 8) % 2).astype(np.uint8)
        store.save_mask("demo", mask, {"source": "model", "threshold": 0.8})
        loaded, meta = store.load_mask("demo")
        assert loaded.tolist() == mask.tolist()
        assert meta["threshold"] == 0.8

    def test_the_mask_cache_does_not_carry_the_arrays_it_was_given(self, tmp_path):
        """`mask` and `probability` are the megabytes; storing them in the metadata blob
        would defeat the point of having a separate cache."""
        store = store_mod.CaseStore(tmp_path)
        store.save_mask(
            "demo", np.zeros((4, 4), dtype=np.uint8),
            {"source": "model", "mask": [[1]], "probability": [[0.5]]},
        )
        _, meta = store.load_mask("demo")
        assert "mask" not in meta
        assert "probability" not in meta

    def test_a_truncated_mask_is_a_cache_miss_and_not_a_failure(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        store.mask_path_for("demo").write_bytes(b"PK\x03\x04 truncated")
        assert store.load_mask("demo") is None

    def test_the_index_puts_the_demo_case_first(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        store.save("zzz_scene", sample_case("zzz_scene"))
        store.save("demo", sample_case("demo"))
        store.save("aaa_scene", sample_case("aaa_scene"))
        assert [entry["id"] for entry in store.summaries()][0] == "demo"

    def test_the_index_skips_a_corrupt_file_rather_than_failing(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        store.save("good", sample_case("good"))
        store.path_for("bad").write_text("{oops")
        assert [entry["id"] for entry in store.summaries()] == ["good"]

    def test_the_index_never_loads_a_whole_document(self, tmp_path):
        store = store_mod.CaseStore(tmp_path)
        store.save("demo", sample_case())
        entry = store.summaries()[0]
        assert "geometry" not in entry
        assert "vessels" not in entry
        assert entry["sizeBytes"] > 0


class TestSummarise:
    def test_the_summary_carries_what_the_header_bar_needs(self):
        entry = store_mod.summarise(sample_case())
        assert entry["scene"] == "scene_00053"
        assert entry["areaKm2"] == 12.5
        assert entry["aisLabel"] == C.LABEL_AIS
        assert entry["isDemo"] is True

    def test_a_case_under_a_scene_name_is_not_marked_as_the_demo(self):
        entry = store_mod.summarise(sample_case("scene_00053"))
        assert entry["isDemo"] is False

    def test_an_explicit_demo_flag_is_honoured(self):
        payload = sample_case("scene_00053")
        payload["demo"] = True
        assert store_mod.summarise(payload)["isDemo"] is True

    def test_the_top_candidate_is_the_first_vessel(self):
        entry = store_mod.summarise(sample_case())
        assert entry["topCandidate"]["score"] == 0.81
        assert entry["topCandidate"]["status"] == C.LABEL_CANDIDATE

    def test_an_empty_document_summarises_without_raising(self):
        """The index must not fall over on a case written by an older pipeline version."""
        entry = store_mod.summarise({})
        assert entry["id"] is None
        assert entry["topCandidate"] is None


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def wait_for(job: jobs_mod.Job, states=("done", "failed", "cancelled"), timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.state in states:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job stayed in {job.state!r}")


class TestJobRunner:
    def test_a_job_runs_and_reports_its_result(self):
        runner = jobs_mod.JobRunner(workers=1)
        try:
            job = runner.submit("detect", "scene_1", lambda say: {"ok": True})
            wait_for(job)
            assert job.state == "done"
            assert job.to_dict(include_result=True)["result"] == {"ok": True}
        finally:
            runner.shutdown()

    def test_progress_messages_are_kept_for_the_client(self):
        """The dashboard names the current stage from this log rather than spinning.

        `log` holds every line in the order the stages reported them and `message` is
        always its tail, which is what the header reads. On a finished job that tail is
        the runner's own closing line, so what matters is the ordering: the stage lines
        appear between `started` and `finished`, and a client polling mid-run therefore
        sees the stage that is actually running.
        """
        runner = jobs_mod.JobRunner(workers=1)
        try:
            def work(say):
                say("decoding")
                say("running inference")
                return {}
            job = runner.submit("detect", "scene_1", work)
            wait_for(job)
            record = job.to_dict()
            assert record["log"] == ["started", "decoding", "running inference", "finished"]
            assert record["message"] == record["log"][-1]
        finally:
            runner.shutdown()

    def test_the_message_is_the_newest_line_while_the_job_runs(self):
        """Read mid-run, which is the only time the header actually shows it.

        The worker is parked after reporting one line so the assertion happens while the
        job is still running, rather than racing the runner's closing `finished`.
        """
        reported = threading.Event()
        release = threading.Event()
        runner = jobs_mod.JobRunner(workers=1)
        try:
            def work(say):
                say("decoding")
                reported.set()
                release.wait(5.0)
                return {}
            job = runner.submit("detect", "scene_1", work)
            assert reported.wait(5.0)
            record = job.to_dict()
            assert record["state"] == "running"
            assert record["message"] == "decoding"
            release.set()
            wait_for(job)
        finally:
            release.set()
            runner.shutdown()

    def test_a_failing_job_records_the_error_instead_of_taking_the_server_down(self):
        runner = jobs_mod.JobRunner(workers=1)
        try:
            def work(say):
                raise ValueError("no checkpoint")
            job = runner.submit("detect", "scene_1", work)
            wait_for(job)
            assert job.state == "failed"
            assert "no checkpoint" in job.to_dict()["error"]
        finally:
            runner.shutdown()

    def test_a_queued_job_can_be_cancelled(self):
        runner = jobs_mod.JobRunner(workers=1)
        release = threading.Event()
        try:
            first = runner.submit("detect", "a", lambda say: release.wait(5) and {})
            second = runner.submit("detect", "b", lambda say: {})
            assert runner.cancel(second.id) is True
            release.set()
            wait_for(first)
            wait_for(second)
            assert second.state == "cancelled"
        finally:
            release.set()
            runner.shutdown()

    def test_cancelling_an_unknown_job_is_false_and_not_an_error(self):
        runner = jobs_mod.JobRunner(workers=1)
        try:
            assert runner.cancel("no_such_job") is False
        finally:
            runner.shutdown()

    def test_jobs_can_be_listed_by_scene(self):
        runner = jobs_mod.JobRunner(workers=1)
        try:
            runner.submit("detect", "scene_a", lambda say: {})
            runner.submit("detect", "scene_b", lambda say: {})
            assert [j.scene for j in runner.list(scene="scene_a")] == ["scene_a"]
            assert len(runner.list()) == 2
        finally:
            runner.shutdown()

    def test_a_job_id_is_unique(self):
        runner = jobs_mod.JobRunner(workers=1)
        try:
            ids = {runner.submit("detect", "s", lambda say: {}).id for _ in range(20)}
            assert len(ids) == 20
        finally:
            runner.shutdown()

    def test_the_public_record_hides_the_result_by_default(self):
        """A finished case is megabytes; the poll response must stay small."""
        runner = jobs_mod.JobRunner(workers=1)
        try:
            job = runner.submit("detect", "s", lambda say: {"big": "x" * 1000})
            wait_for(job)
            assert "result" not in job.to_dict()
            assert "result" in job.to_dict(include_result=True)
        finally:
            runner.shutdown()

    def test_an_unknown_job_is_a_404_over_http(self, runner):
        assert get("/api/jobs/nope", api_only=True).status == 404
        assert get("/api/jobs/nope/result", api_only=True).status == 404

    def test_a_finished_job_serves_the_stored_case(self, store, runner):
        store.save("scene_x", sample_case("scene_x"))
        job = runner.submit("detect", "scene_x", lambda say: {"caseId": "scene_x"})
        wait_for(job)
        response = get(f"/api/jobs/{job.id}/result", api_only=True)
        assert response.status == 200
        assert response.json()["id"] == "scene_x"

    def test_an_unfinished_job_answers_202_rather_than_blocking(self, store, runner):
        release = threading.Event()
        job = runner.submit("detect", "scene_y", lambda say: release.wait(5) and {})
        try:
            response = get(f"/api/jobs/{job.id}/result", api_only=True)
            assert response.status == 202
            assert response.json()["state"] in ("queued", "running")
        finally:
            release.set()
            wait_for(job)

    def test_the_client_reads_the_field_the_server_writes(self):
        """A cross-language shape check, because the failure mode is silent.

        The progress bar reads `message`, `log`, `state` and `elapsedSeconds`. When it read
        a `progress` array instead -- a field the server has never sent -- the line simply
        said "working" for the whole minute, with nothing anywhere to indicate why.
        """
        published = set(jobs_mod.Job(id="j", kind="detect", scene="s").to_dict())
        assert {"message", "log", "state", "error", "elapsedSeconds"} <= published

        client = (ROOT / "apps" / "web" / "app" / "progress.js").read_text()
        referenced = set(re.findall(r"job\??\.([A-Za-z]+)", client))
        assert referenced <= published, f"progress.js reads {referenced - published} from a job"

    def test_the_bar_recognises_every_line_the_pipeline_prints(self):
        """The same silent failure, one level up.

        `progress.js` turns a job's progress line into a position on the bar by matching
        the text the pipeline writes. Rename a stage on the Python side and nothing breaks
        -- the bar simply stops advancing and sits at whatever it last recognised. So the
        lines are asserted from both ends: each one is still printed by the server, and
        each one is still matched by a prefix in the client's stage table.
        """
        case_src = (ROOT / "services" / "api" / "spilltrace_api" / "case.py").read_text()
        server_src = (ROOT / "services" / "api" / "spilltrace_api" / "server.py").read_text()
        client = (ROOT / "apps" / "web" / "app" / "progress.js").read_text()

        # (the line the job logs, the source that prints it). An f-string line is given as
        # the literal prefix the server writes before the interpolated value.
        lines = [
            ('f"decoding {request.scene}"', "decoding scene_x", case_src),
            ('"detecting oil"', "detecting oil", case_src),
            ('"reusing the stored detection"', "reusing the stored detection", case_src),
            ('"measuring slick geometry"', "measuring slick geometry", case_src),
            (
                '"screening dark patches for look-alikes"',
                "screening dark patches for look-alikes",
                case_src,
            ),
            ('"resolving drift forcing"', "resolving drift forcing", case_src),
            ('"reconstructing backward drift"', "reconstructing backward drift", case_src),
            ('"projecting forward drift"', "projecting forward drift", case_src),
            (
                '"reading the supplied AIS extract"',
                "reading the supplied AIS extract",
                case_src,
            ),
            ('"generating synthetic AIS"', "generating synthetic AIS", case_src),
            ('"scoring vessels"', "scoring vessels", case_src),
            ('"rendering previews"', "rendering previews", case_src),
            (
                '"loaded the stored detection mask"',
                "loaded the stored detection mask",
                server_src,
            ),
            (
                '"no cached mask; re-running detection"',
                "no cached mask; re-running detection",
                server_src,
            ),
        ]

        # Every prefix the client's stage table matches on.
        prefixes = re.findall(r'line\.startsWith\("([^"]+)"\)', client)
        assert prefixes, "progress.js publishes no stage prefixes to match against"

        for literal, logged, source in lines:
            assert f"say({literal})" in source, f"the server no longer logs {literal}"
            assert any(logged.startswith(prefix) for prefix in prefixes), (
                f"progress.js matches no stage for the line {logged!r}; "
                "the bar will stop advancing there"
            )

    def test_the_tile_count_the_bar_reads_is_the_one_inference_writes(self):
        """The one part of the bar that is a measurement rather than an estimate.

        Tiled inference prints `inference 64/361 tiles` as it goes, and the bar parses the
        two numbers out of it to place itself inside the detection stage. Change the wording
        on the Python side and the bar silently falls back to the clock -- no error, just a
        less truthful bar -- so the format is pinned from both ends.
        """
        case_src = (ROOT / "services" / "api" / "spilltrace_api" / "case.py").read_text()
        client = (ROOT / "apps" / "web" / "app" / "progress.js").read_text()

        assert 'progress(f"inference {done}/{total} tiles")' in case_src

        pattern = re.search(r"/\^inference \(\\d\+\)\\/\(\\d\+\) tiles/", client)
        assert pattern, "progress.js no longer parses the inference tile line"

        # The literal the server would emit, matched with Python's equivalent of the
        # client's regex, so a change to either side fails here rather than in a browser.
        emitted = "inference 64/361 tiles"
        match = re.match(r"^inference (\d+)/(\d+) tiles", emitted)
        assert match and match.group(1) == "64" and match.group(2) == "361"


# ---------------------------------------------------------------------------
# The build's own path scrubbing
# ---------------------------------------------------------------------------

def load_build_web():
    spec = importlib.util.spec_from_file_location(
        "spilltrace_build_web", ROOT / "scripts" / "build_web.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBundleScrubbing:
    """The fixtures are the API's own output, and the API's output names local paths.

    A bundled file is downloaded by a browser, so an absolute host path in one leaks the
    machine that built it. This is checked here as well as in the build because the build
    fails loudly and a test explains why.
    """

    def test_a_preview_directory_becomes_a_note_and_not_a_deletion(self):
        module = load_build_web()
        out = module.strip_host_paths({"previews": {"directory": "/Users/someone/repo/previews"}})
        assert "/Users/" not in json.dumps(out)
        # Removing the key would read as "no previews", which would be false.
        assert "directory" in out["previews"]

    def test_the_repository_root_is_replaced_wherever_it_appears(self):
        module = load_build_web()
        out = module.strip_host_paths({"hint": f"run {ROOT}/scripts/run_api.py"})
        assert str(ROOT) not in out["hint"]
        assert "<repo>" in out["hint"]

    def test_nested_lists_and_dicts_are_reached(self):
        module = load_build_web()
        out = module.strip_host_paths(
            {"a": [{"b": {"directory": "/Users/x"}}, {"c": f"{ROOT}/y"}]}
        )
        assert "/Users/x" not in json.dumps(out)
        assert str(ROOT) not in json.dumps(out)

    def test_everything_else_is_left_exactly_as_it_was(self):
        module = load_build_web()
        payload = {"iou": 0.771076, "count": 36, "ok": True, "nothing": None, "name": "demo"}
        assert module.strip_host_paths(payload) == payload

    def test_the_real_demo_fixture_carries_no_host_path(self):
        fixture = C.WEB_FIXTURE_DIR / "case-demo.json"
        if not fixture.is_file():
            pytest.skip("fixtures not built; run scripts/build_web.py")
        text = fixture.read_text()
        assert str(ROOT) not in text
        assert "/Users/" not in text
