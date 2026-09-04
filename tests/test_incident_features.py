"""The incident report and its dispatch — the two artefacts a responder touches.

Three things are worth testing here and only three:

  * **The case number is a filename.** It arrives in a request body, so the
    traversal tests are the point of this file, not garnish.
  * **The PDF says what the case says.** Asserting `b"%PDF"` proves reportlab
    ran, not that the document is correct: the previous version of this file
    passed while the drift section rendered two blank rows. So the tests below
    inflate the PDF's own content streams and look for the numbers.
  * **Dispatch cannot be turned into an open relay.** `POST /api/cases/<id>/dispatch`
    has no authentication in front of it.

Everything that needs reportlab is skipped when reportlab is not importable,
because it is the one optional dependency in the project. Everything that does
not need it runs regardless — which is the whole reason the import is lazy.
"""

from __future__ import annotations

import base64
import json
import re
import time
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from spilltrace_api import dispatch as D
from spilltrace_api import jobs as jobs_mod
from spilltrace_api import reports as R
from spilltrace_api import server as S

from test_api import request  # the socket-free harness for the real handler

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "data" / "processed" / "cases" / "demo.json"


def load_case() -> dict[str, Any]:
    if not CASE_PATH.exists():
        pytest.skip(f"{CASE_PATH.relative_to(ROOT)} is absent; run scripts/run_case.py")
    return json.loads(CASE_PATH.read_text())


def reportlab_missing() -> bool:
    try:
        R._reportlab()
    except R.ReportUnavailable:
        return True
    return False


needs_reportlab = pytest.mark.skipif(
    reportlab_missing(),
    reason="reportlab (or its Pillow dependency) is not importable in this environment",
)


_PDF_ESCAPES = {ord("n"): 0x0A, ord("r"): 0x0D, ord("t"): 0x09, ord("b"): 0x08, ord("f"): 0x0C}


def pdf_literal(raw: bytes) -> str:
    """Decode the bytes between one pair of PDF string parentheses.

    Anything outside ASCII arrives as an octal escape -- `\\260` for the degree sign,
    `\\227` for the em dash -- so a straight `latin-1` decode of the literal leaves
    `\\227` sitting in the text as four characters and every assertion that quotes a
    coordinate or a dashed sentence fails for the wrong reason. The bytes are
    WinAnsi, which is reportlab's default encoding for the base-14 fonts.
    """
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        if raw[i] != 0x5C:  # not a backslash
            out.append(raw[i])
            i += 1
            continue
        i += 1
        if i >= n:
            break
        if 0x30 <= raw[i] <= 0x37:  # one to three octal digits
            digits = ""
            while i < n and len(digits) < 3 and 0x30 <= raw[i] <= 0x37:
                digits += chr(raw[i])
                i += 1
            out.append(int(digits, 8))
        elif raw[i] in (0x0A, 0x0D):  # a line continuation inside the literal
            i += 2 if raw[i : i + 2] == b"\r\n" else 1
        else:
            out.append(_PDF_ESCAPES.get(raw[i], raw[i]))
            i += 1
    return out.decode("cp1252", errors="replace")


def pdf_text(path: Path) -> str:
    """Recover the drawn text from a PDF by inflating its content streams.

    reportlab writes the base-14 fonts without subsetting, so the text operands are
    literal strings and no glyph mapping is needed. Good enough to assert that a
    number reached the page; not a general-purpose PDF parser.

    Two details cost real time to find. reportlab runs its page streams through
    **ASCII85 and then Flate**, so `zlib.decompress` alone returns nothing -- and a
    silent `except` around it turns every assertion below into `x in ""`, which
    passes for nothing and fails for everything. And spacing has to come from the
    text operators, not from the literals: reportlab emits one literal per *run*, so
    joining them all with a space puts one before the semicolon in "...is
    **synthetic**; read sections 7 and 8", while joining them with nothing welds the
    end of one wrapped line onto the start of the next. Only the operators that move
    the text position -- `Td`, `TD`, `T*`, and a fresh `BT` for the next table cell
    -- mean "new line", so only those contribute a space.
    """
    raw = path.read_bytes()
    chunks: list[str] = []
    # The content ends flush against `endstream`, with no separating newline.
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        body = match.group(1)
        stripped = body.strip()
        if stripped.endswith(b"~>"):
            try:
                body = base64.a85decode(stripped, adobe=True)
            except ValueError:
                pass
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        # The literal alternative comes first, so an operator name that happens to
        # appear inside a drawn string is consumed as text and never read as an operator.
        for token in re.finditer(
            rb"\((?:\\.|[^\\()])*\)|(?<![A-Za-z])(?:Td|TD|T\*|BT|ET)(?![A-Za-z])", body, re.S
        ):
            piece = token.group(0)
            if piece.startswith(b"("):
                chunks.append(pdf_literal(piece[1:-1]))
            elif chunks and chunks[-1] != " ":
                chunks.append(" ")
    return re.sub(r"\s+", " ", "".join(chunks))


# ---------------------------------------------------------------------------
# The case number, which becomes a filename
# ---------------------------------------------------------------------------

class TestCaseNumber:
    def test_the_scheme_is_derived_from_the_case(self):
        case = load_case()
        number = R.build_case_number(case)
        acquired = (case.get("scene") or {}).get("acquiredStartUtc") or ""
        assert number.startswith("ST-" + re.sub(r"[^0-9]", "", acquired)[:8])
        assert (case["scene"]["name"]) in number
        assert R.SAFE_CASE_NUMBER.match(number)

    def test_the_same_case_always_gets_the_same_number(self):
        case = load_case()
        assert R.build_case_number(case) == R.build_case_number(case)

    def test_an_empty_case_still_yields_a_safe_number(self):
        assert R.SAFE_CASE_NUMBER.match(R.build_case_number({}))

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../etc/passwd",
            "..",
            "/absolute",
            "a/b",
            "case number with spaces",
            "-leading-dash",
            ".hidden",
            "x" * 65,
            "",
            "ST-2017;rm -rf /",
        ],
    )
    def test_a_hostile_case_number_is_rejected_not_scrubbed(self, hostile):
        # Rejected, so a traversal attempt fails loudly. Scrubbing would silently
        # write somewhere the caller did not ask for.
        with pytest.raises(ValueError):
            R.build_case_number({}, hostile)

    @pytest.mark.parametrize("ok", ["INC-DEMO-0001", "ST-20170311-00053-demo", "a", "A1._-"])
    def test_a_reasonable_override_is_kept_verbatim(self, ok):
        assert R.build_case_number({}, ok) == ok


# ---------------------------------------------------------------------------
# Field rendering, which needs no PDF library at all
# ---------------------------------------------------------------------------

class TestFieldRendering:
    def test_coordinates_are_printed_latitude_first(self):
        # Stored [lon, lat]; a watch officer reads lat, lon. Getting this backwards
        # puts an incident 29 degrees away and reads perfectly plausibly.
        assert R._coord([54.637553, 25.516094]) == "25.516094° N, 54.637553° E"
        assert R._coord({"lon": -70.5, "lat": -33.25}).startswith("33.250000° S, 70.500000° W")
        assert R._coord(None) == "Not available"
        assert R._coord(["nonsense", 1]) == "Not available"

    def test_bounds_render_as_two_named_corners(self):
        text = R._bounds([54.598108, 25.501553, 54.782083, 25.685528])
        assert text.startswith("SW 25.501553° N, 54.598108° E")
        assert "NE 25.685528° N, 54.782083° E" in text
        assert R._bounds([1, 2]) == "Not available"

    def test_zero_is_a_measurement_and_not_a_missing_value(self):
        assert R._value(0) == "0"
        assert R._value(0.0) == "0"
        assert R._value(False) == "No"
        assert R._value(None) == "Not available"
        assert R._value("") == "Not available"

    def test_the_forward_endpoint_falls_back_to_the_track(self):
        # trajectories.forwardEndpoint is null in every case the pipeline writes,
        # so reading only that key blanks the row.
        case = load_case()
        assert (case.get("trajectories") or {}).get("forwardEndpoint") is None
        endpoint = R._forward_endpoint(case)
        assert endpoint == case["trajectories"]["forward"][-1]
        assert R._coord(endpoint) != "Not available"

    def test_the_origin_radii_use_the_keys_the_pipeline_writes(self):
        origin = load_case()["trajectories"]["originEstimate"]
        assert origin.get("radiusKm") is None, "if this key appears, the report can use it"
        assert R._value(origin["radiusP50Km"]) != "Not available"
        assert R._value(origin["radiusP90Km"]) != "Not available"

    def test_the_release_window_renders_as_an_interval_and_a_basis(self):
        window = load_case()["attribution"]["releaseWindow"]
        text, basis = R._release_window(window)
        assert window["startUtc"] in text and window["endUtc"] in text
        assert basis == window["basis"]
        assert R._release_window(None) == ("Not available", "")


def squash(text: str) -> str:
    """Drop all whitespace, so assertions survive reportlab's line wrapping.

    A paragraph is broken into one text operand per line, at spaces. Removing
    whitespace from both sides of the comparison makes a sentence assertion
    insensitive to where the break landed.
    """
    return re.sub(r"\s+", "", text)


# ---------------------------------------------------------------------------
# The document's content, without reportlab
# ---------------------------------------------------------------------------
#
# reportlab is optional and its Pillow dependency breaks easily, so the content
# assertions below run through a recording stand-in for the handful of reportlab
# names `reports.py` uses. This verifies what the module puts *into* the
# document -- the keys it reads, the formatting, the caveats it carries through
# -- on any machine. It does not verify PDF rendering; TestIncidentPdf does that
# when the real library is installed.

class _Recorder:
    """Collects every string handed to a Paragraph or a table cell."""

    def __init__(self) -> None:
        self.text: list[str] = []

    def note(self, value: Any) -> None:
        if isinstance(value, str):
            self.text.append(value)
        elif isinstance(value, _Fake):
            self.text.extend(value.text)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.note(item)

    def joined(self) -> str:
        return " ".join(self.text)


class _Fake:
    """A stand-in flowable that remembers the text it was given."""

    def __init__(self, recorder: _Recorder, *args: Any, **kwargs: Any) -> None:
        self.text: list[str] = []
        for arg in args:
            local = _Recorder()
            local.note(arg)
            self.text.extend(local.text)
        recorder.note(self.text)

    def setStyle(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def wrap(self, *_args: Any):
        return (0, 0)


def fake_reportlab(recorder: _Recorder):
    """A SimpleNamespace shaped like the reportlab subset `reports.py` imports."""

    class _Doc:
        def __init__(self, path: str, **_kwargs: Any) -> None:
            self.path = Path(path)

        def build(self, story: list[Any], **kwargs: Any) -> None:
            recorder.note(story)
            # Exercise the page callbacks too; a broken footer is a real failure.
            canvas = _Canvas()
            for key in ("onFirstPage", "onLaterPages"):
                if kwargs.get(key):
                    kwargs[key](canvas, SimpleNamespace(page=1))
            recorder.text.extend(canvas.text)
            self.path.write_bytes(b"%PDF-1.4 recorded-by-test\n")

    class _Canvas:
        def __init__(self) -> None:
            self.text: list[str] = []

        def __getattr__(self, _name: str):
            def sink(*args: Any, **_kwargs: Any) -> None:
                for arg in args:
                    if isinstance(arg, str):
                        self.text.append(arg)
            return sink

    def flowable(*args: Any, **kwargs: Any) -> _Fake:
        return _Fake(recorder, *args, **kwargs)

    styles = {name: SimpleNamespace(name=name) for name in ("Title", "BodyText", "Heading2")}
    return SimpleNamespace(
        colors=SimpleNamespace(HexColor=lambda value: value, grey="grey", white="white"),
        TA_CENTER=1,
        A4=(595.0, 842.0),
        ParagraphStyle=lambda name, **kwargs: SimpleNamespace(name=name, **kwargs),
        getSampleStyleSheet=lambda: styles,
        mm=2.834,
        PageBreak=flowable,
        Paragraph=flowable,
        SimpleDocTemplate=_Doc,
        Spacer=flowable,
        Table=flowable,
        TableStyle=flowable,
    )


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    """Render the demo case through the recorder and return (case, text)."""
    recorder = _Recorder()
    monkeypatch.setattr(R, "_RL", fake_reportlab(recorder))
    case = load_case()
    path = R.generate_incident_report(case, output_dir=tmp_path)
    assert path.exists()
    return case, recorder.joined()


class TestDocumentContent:
    def test_the_case_number_scene_and_product_are_present(self, recorded):
        case, text = recorded
        assert R.build_case_number(case) in text
        assert case["scene"]["productId"] in text
        assert case["scene"]["region"] in text

    def test_the_drift_rows_that_used_to_be_blank_now_carry_numbers(self, recorded):
        case, text = recorded
        origin = case["trajectories"]["originEstimate"]
        assert R._value(origin["radiusP50Km"]) in text
        assert R._value(origin["radiusP90Km"]) in text
        assert R._coord(origin["centroid"]) in text
        assert R._coord(R._forward_endpoint(case)) in text
        assert origin["interpretation"] in text

    def test_the_release_window_is_an_interval_not_a_dict_repr(self, recorded):
        case, text = recorded
        window = case["attribution"]["releaseWindow"]
        assert window["startUtc"] in text and window["endUtc"] in text
        assert "'startUtc'" not in text, "the raw dict must never be printed"

    def test_the_spill_age_reasoning_is_present(self, recorded):
        case, text = recorded
        age = case["spillAge"]
        for key in ("label", "basis", "caveat"):
            assert age[key] in text
        assert age["resolution"]["note"] in text
        for item in age["narrowedBy"]:
            assert item in text

    def test_every_score_component_appears_with_its_reason(self, recorded):
        case, text = recorded
        for item in case["vessels"][0]["componentDetail"]:
            assert str(item["component"]) in text
            assert str(item["reason"]) in text

    def test_the_traffic_filtering_funnel_is_present(self, recorded):
        case, text = recorded
        filtering = case["attribution"]["filtering"]
        assert filtering["rule"] in text
        assert R._value(filtering["aisReports"]) in text
        assert R._value(filtering["reportsNearEnvelope"]) in text

    def test_the_limitations_and_provenance_labels_are_verbatim(self, recorded):
        case, text = recorded
        assert case["limits"]
        for limit in case["limits"]:
            assert limit in text
        for key in ("aisLabel", "driftLabel", "detectionLabel"):
            assert case["provenance"][key] in text

    def test_the_detection_method_note_is_present(self, recorded):
        case, text = recorded
        assert case["detection"]["note"] in text
        assert case["geometry"]["areaMethod"] in text

    def test_nothing_in_the_document_accuses_a_vessel(self, recorded):
        _case, text = recorded
        lowered = text.lower()
        for word in ("guilty", "culprit", "confirmed responsible", "responsible party"):
            assert word not in lowered
        assert "priority candidate for investigation" in lowered
        assert "does not establish responsibility" in lowered

    def test_every_page_is_stamped_as_a_proof_of_concept(self, recorded):
        case, text = recorded
        assert f"SpillTrace case {R.build_case_number(case)}" in text
        assert "Research proof of concept" in text

    def test_a_sparse_case_renders_without_inventing_anything(self, monkeypatch, tmp_path):
        recorder = _Recorder()
        monkeypatch.setattr(R, "_RL", fake_reportlab(recorder))
        R.generate_incident_report({"id": "sparse"}, output_dir=tmp_path)
        text = recorder.joined()
        assert "Not available" in text
        assert "carries no limitations block" in text

    def test_a_case_with_no_relevant_vessel_names_nobody(self, monkeypatch, tmp_path):
        recorder = _Recorder()
        monkeypatch.setattr(R, "_RL", fake_reportlab(recorder))
        case = load_case()
        R.generate_incident_report({**case, "vessels": []}, output_dir=tmp_path)
        text = recorder.joined()
        assert "No vessel satisfied both halves" in text
        assert case["vessels"][0]["name"] not in text


# ---------------------------------------------------------------------------
# Look-alike screening, section 3.1
# ---------------------------------------------------------------------------
# The screening block is built here rather than taken from the demo case on disk: the
# fixture predates the screen, and a test that only passes once someone re-runs the
# pipeline is a test that will be deleted the first time it goes red.


def _patch(
    index: int,
    label: str,
    likelihood: float | None,
    area: float,
    reason: str,
    on_slick: str | None = None,
) -> dict[str, Any]:
    """One screened dark patch, shaped like `case.screen_scene` writes them."""
    return {
        "id": f"patch-{index:02d}",
        "pixels": int(area * 2500),
        "areaKm2": area,
        "centroid": [54.7, 25.6],
        "overlapsSlick": on_slick,
        "overlapFraction": 0.0 if on_slick is None else 0.62,
        "label": label,
        "oilLikelihood": likelihood,
        "headline": f"{label}: oil-likelihood {likelihood}",
        "reasons": [reason],
        "measured": {"darknessZ": 2.4, "textureRatio": 0.81, "elongation": 3.2},
    }


def _screening_block(patches: list[dict[str, Any]], *, fitted: bool = True) -> dict[str, Any]:
    """A screening block whose counts agree with the patches it carries."""
    counts = {
        "proposed": len(patches),
        "overlappingPublishedSlick": sum(1 for patch in patches if patch["overlapsSlick"]),
    }
    for label in ("accepted", "uncertain", "rejected", "unscreened"):
        counts[label] = sum(1 for patch in patches if patch["label"] == label)
    return {
        "question": "is this dark water oil, or something that only looks like it?",
        "headline": (
            f"{counts['rejected']} of {len(patches)} dark patches rejected as look-alikes"
        ),
        "fitted": fitted,
        "calibration": "logistic screen fitted on regions grouped by scene, 5 folds",
        "thresholds": {"rejectAtOrBelow": 0.35, "acceptAtOrAbove": 0.65},
        "counts": counts,
        "patches": patches,
        "limits": [
            "the screen separates oil-like from not-oil-like and cannot name the phenomenon",
        ],
    }


class TestLookAlikeSection:
    """Section 3.1 is the only place the document says what it declined to call oil."""

    def render(self, monkeypatch, tmp_path, case: dict[str, Any]) -> str:
        recorder = _Recorder()
        monkeypatch.setattr(R, "_RL", fake_reportlab(recorder))
        R.generate_incident_report(case, output_dir=tmp_path)
        return recorder.joined()

    def test_the_row_labels_headline_and_thresholds_are_printed(self, monkeypatch, tmp_path):
        """Asserting on the row labels rather than the counts is deliberate: the counts are
        one- and two-digit numbers that appear all over a nine-section document, so matching
        them proves nothing about this table."""
        block = _screening_block(
            [
                _patch(1, "rejected", 0.11, 4.4, "interior as rough as the water around it"),
                _patch(2, "accepted", 0.88, 9.1, "dark and smooth", on_slick="slick-01"),
            ]
        )
        text = self.render(monkeypatch, tmp_path, {**load_case(), "screening": block})
        assert "3.1 Look-alike screening" in text
        for label in (
            "Screen fitted",
            "Dark patches examined",
            "Rejected as look-alikes",
            "Kept for human review",
            "Consistent with oil",
            "Patches on a published slick",
            "Decision thresholds",
        ):
            assert label in text
        assert block["headline"] in text
        assert block["calibration"] in text
        assert block["limits"][0] in text
        assert "reject at or below 0.35" in text
        assert "accept at or above 0.65" in text

    def test_the_patch_table_carries_each_id_verdict_and_leading_reason(
        self, monkeypatch, tmp_path
    ):
        block = _screening_block(
            [
                _patch(1, "rejected", 0.11, 4.4, "interior as rough as the water around it"),
                _patch(2, "accepted", 0.88, 9.1, "dark and smooth", on_slick="slick-01"),
            ]
        )
        text = self.render(monkeypatch, tmp_path, {**load_case(), "screening": block})
        for column in ("Patch", "Oil-like", "Verdict", "On slick", "Leading reason"):
            assert column in text
        for patch in block["patches"]:
            assert patch["id"] in text
            assert patch["label"] in text
            assert patch["reasons"][0] in text
        assert "slick-01" in text

    def test_rejected_patches_come_first_and_the_table_is_capped(self, monkeypatch, tmp_path):
        """Six rows, rejected first. The accepted patches are the ones a reader can already
        see in the slick table above, so they are what falls off the end."""
        patches = [
            _patch(index, "accepted", 0.9, 12.0 - index, "dark and smooth", on_slick="slick-01")
            for index in range(3)
        ]
        patches += [_patch(index, "rejected", 0.1, 3.0, f"leading reason {index}") for index in range(3, 7)]
        patches.append(_patch(7, "uncertain", 0.5, 2.0, "between the two thresholds"))
        text = self.render(
            monkeypatch, tmp_path, {**load_case(), "screening": _screening_block(patches)}
        )
        for index in (3, 4, 5, 6, 7):
            assert f"patch-{index:02d}" in text
        assert text.index("patch-03") < text.index("patch-00"), "rejected must sort first"
        assert text.index("patch-07") < text.index("patch-00"), "uncertain sorts above accepted"
        # Eight proposals, six rows: the two smallest accepted patches are dropped.
        assert "patch-01" not in text
        assert "patch-02" not in text
        assert "leading reason 3" in text

    def test_no_patch_is_labelled_with_a_phenomenon(self, monkeypatch, tmp_path):
        """The screen answers oil-like or not-oil-like, so the document may not name algae,
        low wind or a wake anywhere except in the sentence that says it cannot."""
        patches = [_patch(1, "rejected", 0.08, 5.5, "interior roughness matches the background")]
        text = self.render(
            monkeypatch, tmp_path, {**load_case(), "screening": _screening_block(patches)}
        )
        assert "cannot name the phenomenon" in text
        disclaimer = "called algae, low wind or a wake"
        assert disclaimer in text
        remainder = text.replace(disclaimer, "").lower()
        for phenomenon in ("algae", "low wind", "wake"):
            assert phenomenon not in remainder
        assert "not detections this report is making" in text

    def test_the_largest_slicks_verdict_is_in_the_detection_table(self, monkeypatch, tmp_path):
        case = load_case()
        verdict = {"label": "accepted", "oilLikelihood": 0.91}
        case = {**case, "slick": {**case["slick"], "screening": verdict}}
        text = self.render(monkeypatch, tmp_path, case)
        assert "Look-alike verdict, largest slick" in text
        assert "accepted (oil-likelihood 0.91)" in text

    def test_a_case_with_no_screening_omits_the_subsection(self, monkeypatch, tmp_path):
        case = load_case()
        slick = {key: value for key, value in case["slick"].items() if key != "screening"}
        text = self.render(monkeypatch, tmp_path, {**case, "slick": slick, "screening": None})
        assert "3.1 Look-alike screening" not in text
        assert "cannot name the phenomenon" not in text
        # The summary row stays, saying plainly that nothing screened it.
        assert "not screened (oil-likelihood Not available)" in text

    def test_an_unfitted_screen_still_reports_what_it_examined(self, monkeypatch, tmp_path):
        block = _screening_block(
            [_patch(1, "unscreened", None, 6.0, "no fitted screen on disk")], fitted=False
        )
        text = self.render(monkeypatch, tmp_path, {**load_case(), "screening": block})
        assert "Screen fitted No" in text
        assert "patch-01" in text
        assert "unscreened" in text
        assert block["limits"][0] in text


# ---------------------------------------------------------------------------
# The PDF, which needs reportlab
# ---------------------------------------------------------------------------

@needs_reportlab
class TestIncidentPdf:
    @pytest.fixture(scope="class")
    def rendered(self, tmp_path_factory):
        case = load_case()
        directory = tmp_path_factory.mktemp("reports")
        path = R.generate_incident_report(case, output_dir=directory)
        return case, path, pdf_text(path)

    def test_it_writes_a_pdf_named_after_the_case_number(self, rendered):
        case, path, _ = rendered
        assert path.exists() and path.stat().st_size > 8000
        assert path.read_bytes().startswith(b"%PDF")
        assert path.name == f"{R.build_case_number(case)}_incident_report.pdf"

    def test_the_case_number_and_scene_appear(self, rendered):
        case, _, text = rendered
        assert R.build_case_number(case) in text
        assert case["scene"]["productId"] in squash(text)

    def test_the_drift_section_carries_real_numbers(self, rendered):
        # The row that used to be blank: the pipeline writes radiusP50Km and
        # radiusP90Km, never radiusKm.
        case, _, text = rendered
        origin = case["trajectories"]["originEstimate"]
        assert R._value(origin["radiusP50Km"]) in text
        assert R._value(origin["radiusP90Km"]) in text
        assert R._coord(origin["centroid"]) in text
        assert R._coord(R._forward_endpoint(case)) in text
        assert squash(origin["interpretation"]) in squash(text)

    def test_the_spill_age_reasoning_is_printed_not_just_the_label(self, rendered):
        case, _, text = rendered
        age = case["spillAge"]
        squashed = squash(text)
        assert squash(age["label"]) in squashed
        assert squash(age["basis"]) in squashed
        assert squash(age["caveat"]) in squashed
        assert squash(age["resolution"]["note"]) in squashed

    def test_every_score_component_is_shown_with_its_reason(self, rendered):
        case, _, text = rendered
        squashed = squash(text)
        for item in case["vessels"][0]["componentDetail"]:
            assert squash(str(item["component"])) in squashed
            assert squash(str(item["reason"])) in squashed

    def test_the_stated_limitations_are_printed_verbatim(self, rendered):
        case, _, text = rendered
        squashed = squash(text)
        assert case["limits"], "the case should carry a limitations block"
        for limit in case["limits"]:
            assert squash(limit) in squashed

    def test_the_provenance_labels_are_printed_verbatim(self, rendered):
        case, _, text = rendered
        squashed = squash(text)
        for key in ("aisLabel", "driftLabel", "detectionLabel"):
            assert squash(case["provenance"][key]) in squashed

    def test_no_vessel_is_called_responsible(self, rendered):
        _case, _, text = rendered
        lowered = text.lower()
        for word in ("guilty", "responsible party", "confirmed responsible", "culprit"):
            assert word not in lowered
        assert "priority candidate for investigation" in lowered

    def test_the_attribution_caveat_survives_into_the_document(self, rendered):
        case, _, text = rendered
        assert squash(case["attribution"]["caveat"]) in squash(text)

    def test_no_character_falls_back_to_the_notdef_glyph(self, rendered):
        # reportlab does not warn when a character is outside the WinAnsi table it uses
        # for the base-14 fonts: `unicode2T1` substitutes byte 0x7f, and the reader draws
        # nothing at all in its place. That is how the "what would narrow it" list shipped
        # with invisible bullets. The document is ASCII plus a small set of typographic
        # characters, so any notdef here means a glyph silently disappeared off the page.
        _case, _, text = rendered
        assert "\x7f" not in text

    def test_a_case_with_no_vessels_still_renders(self, tmp_path):
        case = load_case()
        case = {**case, "vessels": [], "attribution": {**case["attribution"], "relevantCount": 0}}
        path = R.generate_incident_report(case, case_number="ST-EMPTY-0001", output_dir=tmp_path)
        assert "Novesselsatisfiedbothhalves" in squash(pdf_text(path))

    def test_an_almost_empty_case_does_not_crash(self, tmp_path):
        path = R.generate_incident_report({"id": "sparse"}, output_dir=tmp_path)
        assert path.read_bytes().startswith(b"%PDF")
        assert "Notavailable" in squash(pdf_text(path))


# ---------------------------------------------------------------------------
# Dispatch: recipients, the allowlist, and the dry run
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    """Start from no mail configuration at all, whatever the developer's .env holds."""
    for name in (
        "SPILLTRACE_ALERT_RECIPIENTS", "SPILLTRACE_EMAIL_DRY_RUN", "SPILLTRACE_SMTP_HOST",
        "SPILLTRACE_SMTP_PORT", "SPILLTRACE_SMTP_USERNAME", "SPILLTRACE_SMTP_USER",
        "SPILLTRACE_SMTP_PASSWORD", "SPILLTRACE_EMAIL_FROM", "SPILLTRACE_ALERT_FROM",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestRecipients:
    def test_a_string_a_list_and_a_semicolon_all_parse(self):
        expected = ["a@x.gov", "b@y.gov"]
        assert D.parse_recipients("a@x.gov, b@y.gov") == expected
        assert D.parse_recipients(["a@x.gov", "b@y.gov"]) == expected
        assert D.parse_recipients("a@x.gov; b@y.gov") == expected
        assert D.parse_recipients("Ops Desk <a@x.gov>") == ["a@x.gov"]

    def test_duplicates_collapse_case_insensitively_in_order(self):
        assert D.parse_recipients("B@x.gov, a@x.gov, b@X.GOV") == ["B@x.gov", "a@x.gov"]

    @pytest.mark.parametrize("bad", ["not-an-email", "a@b", "@x.gov", "a b@x.gov", "", None, 7])
    def test_anything_that_is_not_an_address_is_refused(self, bad):
        with pytest.raises(D.DispatchError):
            D.parse_recipients(bad)


class TestAllowlist:
    def test_an_unset_allowlist_permits_nothing(self, clean_env):
        # The dispatch endpoint is unauthenticated. Without this, an open port is
        # an open relay for whatever SMTP account the operator configured.
        with pytest.raises(D.DispatchError, match="SPILLTRACE_ALERT_RECIPIENTS"):
            D._check_allowlist(["anyone@example.com"])

    def test_an_exact_entry_permits_only_that_address(self, clean_env):
        clean_env.setenv("SPILLTRACE_ALERT_RECIPIENTS", "ops@coastguard.gov.in")
        D._check_allowlist(["OPS@CoastGuard.gov.in"])
        with pytest.raises(D.DispatchError, match="not in SPILLTRACE_ALERT_RECIPIENTS"):
            D._check_allowlist(["someone@coastguard.gov.in"])

    def test_a_domain_entry_permits_the_whole_domain(self, clean_env):
        clean_env.setenv("SPILLTRACE_ALERT_RECIPIENTS", "@coastguard.gov.in, named@dgs.gov.in")
        D._check_allowlist(["watch@coastguard.gov.in", "named@dgs.gov.in"])
        with pytest.raises(D.DispatchError):
            D._check_allowlist(["watch@coastguard.gov.in", "attacker@evil.example"])


class TestDispatchMode:
    def test_no_smtp_host_means_dry_run(self, clean_env):
        mode = D.dispatch_mode()
        assert mode["mode"] == "dryRun"
        assert mode["smtpConfigured"] is False
        assert "disk" in mode["note"]

    def test_a_configured_host_means_send(self, clean_env):
        clean_env.setenv("SPILLTRACE_SMTP_HOST", "smtp.example.gov")
        assert D.dispatch_mode()["mode"] == "send"

    def test_the_dry_run_flag_wins_over_a_configured_host(self, clean_env):
        clean_env.setenv("SPILLTRACE_SMTP_HOST", "smtp.example.gov")
        clean_env.setenv("SPILLTRACE_EMAIL_DRY_RUN", "1")
        assert D.dispatch_mode()["mode"] == "dryRun"


class TestCaseSummary:
    def test_a_zero_area_detection_is_reported_as_zero(self):
        summary = D._case_summary({"slick": {"totalAreaKm2": 0.0, "areaKm2": 12.5}})
        assert summary["area_km2"] == 0.0

    def test_the_body_carries_the_caveats_and_never_accuses(self):
        case = load_case()
        body = D._default_body(D._case_summary(case), "ST-TEST-0001")
        assert "ST-TEST-0001" in body
        assert case["provenance"]["aisLabel"] in body
        assert case["provenance"]["driftLabel"] in body
        assert "Priority candidate for investigation" in body
        assert "does not establish" in body
        lowered = body.lower()
        assert "guilty" not in lowered and "responsible for" not in lowered


@needs_reportlab
class TestDryRunDispatch:
    def test_it_writes_an_eml_and_reports_that_nothing_was_sent(self, clean_env, tmp_path):
        clean_env.setattr(R, "REPORT_DIR", tmp_path)
        result = D.dispatch_case_email(load_case(), "watch@coastguard.gov.in")
        assert result["sent"] is False and result["dryRun"] is True
        assert result["reason"] == "no SMTP host configured"
        assert result["recipients"] == ["watch@coastguard.gov.in"]
        assert result["caseNumber"] in result["subject"]
        eml = Path(result["emailPath"])
        assert eml.exists() and eml.suffix == ".eml"
        raw = eml.read_text(errors="replace")
        assert "Content-Type: application/pdf" in raw
        assert result["caseNumber"] in raw
        assert "From: spilltrace-dry-run@localhost" in raw

    def test_an_empty_request_falls_back_to_the_allowlist(self, clean_env, tmp_path):
        clean_env.setattr(R, "REPORT_DIR", tmp_path)
        clean_env.setenv("SPILLTRACE_ALERT_RECIPIENTS", "@coastguard.gov.in")
        with pytest.raises(D.DispatchError):
            D.dispatch_case_email(load_case(), None)
        clean_env.setenv("SPILLTRACE_ALERT_RECIPIENTS", "watch@coastguard.gov.in")
        result = D.dispatch_case_email(load_case(), None)
        assert result["recipients"] == ["watch@coastguard.gov.in"]

    def test_a_configured_host_with_no_allowlist_refuses_before_sending(self, clean_env, tmp_path):
        clean_env.setattr(R, "REPORT_DIR", tmp_path)
        clean_env.setenv("SPILLTRACE_SMTP_HOST", "smtp.example.gov")
        with pytest.raises(D.DispatchError, match="SPILLTRACE_ALERT_RECIPIENTS"):
            D.dispatch_case_email(load_case(), "attacker@evil.example")
        assert not list(tmp_path.glob("*.eml")), "nothing should have been written"

    def test_a_hostile_case_number_never_reaches_the_filesystem(self, clean_env, tmp_path):
        clean_env.setattr(R, "REPORT_DIR", tmp_path)
        with pytest.raises(ValueError):
            D.dispatch_case_email(load_case(), "a@x.gov", case_number="../../../../etc/passwd")
        assert not list(tmp_path.iterdir())

    def test_a_long_subject_and_body_are_capped(self, clean_env, tmp_path):
        # Both arrive in a request body; the subject becomes a mail header.
        case = load_case()
        path = R.generate_incident_report(case, output_dir=tmp_path)
        msg = D.build_email(case, ["a@x.gov"], path, subject="S" * 5000, message="M" * 50000)
        assert len(msg["Subject"]) <= D.MAX_SUBJECT
        # The PDF attachment makes this multipart/mixed, so the text has to be asked
        # for by part; `msg.get_content()` raises KeyError on a multipart container.
        body = msg.get_body(preferencelist=("plain",))
        assert body is not None, "the message should still carry a readable text part"
        assert len(body.get_content().strip()) == D.MAX_MESSAGE
        assert "\n" not in msg["Subject"]
        assert msg.get_content_type() == "multipart/mixed"


# ---------------------------------------------------------------------------
# The HTTP surface, through the real handler
# ---------------------------------------------------------------------------

class TestDispatchEndpoint:
    def test_a_hostile_case_number_is_a_400_not_a_background_failure(self):
        response = request(
            "POST", "/api/cases/demo/dispatch",
            {"recipients": "a@x.gov", "caseNumber": "../../etc/passwd"},
        )
        assert response.status == 400
        assert "case number" in response.json()["error"].lower()

    def test_a_bad_recipient_is_rejected_before_a_job_is_created(self):
        response = request("POST", "/api/cases/demo/dispatch", {"recipients": "nope"})
        assert response.status == 400
        assert "invalid recipient" in response.json()["error"].lower()

    def test_no_recipients_and_no_allowlist_is_a_400(self, clean_env):
        response = request("POST", "/api/cases/demo/dispatch", {})
        assert response.status == 400

    def test_an_unknown_case_is_a_404(self):
        assert request("POST", "/api/cases/no-such-case/dispatch", {"recipients": "a@x.gov"}).status == 404

    def test_a_traversal_case_id_never_reaches_the_store(self):
        # 405 rather than 400: %2F is decoded before routing, so the extra slash
        # means no route matches at all. Refused either way, which is the point.
        for path in ("/api/cases/..%2F..%2Fetc/dispatch", "/api/cases/..../dispatch"):
            assert request("POST", path, {"recipients": "a@x.gov"}).status in (400, 404, 405)

    def test_the_report_payload_says_what_a_dispatch_would_do(self, clean_env):
        payload = request("GET", "/api/cases/demo/report").json()
        assert payload["dispatchUrl"] == "/api/cases/demo/dispatch"
        assert payload["dispatch"]["mode"] == "dryRun"
        assert payload["dispatch"]["smtpConfigured"] is False
        # No addresses: the endpoint is unauthenticated, so the allowlist itself
        # is reported only as a boolean.
        assert set(payload["dispatch"]) == {"mode", "smtpConfigured", "recipientsConfigured", "note"}

    def test_the_pdf_route_answers_503_rather_than_500_when_reportlab_is_absent(self):
        response = request("GET", "/api/cases/demo/report?format=pdf")
        if reportlab_missing():
            assert response.status == 503
            assert "reportlab" in response.json()["error"]
        else:
            assert response.status == 200
            assert response.headers["content-type"] == "application/pdf"
            assert response.body.startswith(b"%PDF")
            assert "attachment" in response.headers["content-disposition"]


# ---------------------------------------------------------------------------
# What crosses the HTTP boundary
# ---------------------------------------------------------------------------

class TestPublicResult:
    """`_public_result` is the only thing between the host's paths and the network."""

    def test_a_path_inside_the_checkout_becomes_relative(self):
        absolute = ROOT / "data" / "processed" / "reports" / "ST-DEMO-0001.pdf"
        out = S._public_result({"reportPath": str(absolute), "sent": False})
        assert out["reportPath"] == "data/processed/reports/ST-DEMO-0001.pdf"
        assert not Path(out["reportPath"]).is_absolute()
        assert out["sent"] is False, "everything else passes through untouched"

    def test_a_path_outside_the_checkout_is_reduced_to_its_name(self):
        out = S._public_result({"emailPath": "/var/folders/xyz/ST-DEMO-0001.eml"})
        assert out["emailPath"] == "ST-DEMO-0001.eml"

    def test_the_original_is_not_mutated(self):
        result = {"reportPath": str(ROOT / "data" / "x.pdf")}
        S._public_result(result)
        assert Path(result["reportPath"]).is_absolute(), "the caller still needs to open it"

    def test_missing_and_odd_values_survive(self):
        assert S._public_result({}) == {}
        assert S._public_result({"reportPath": None}) == {"reportPath": None}
        assert S._public_result("not a dict") == "not a dict"


class TestJobPolling:
    """The frontend polls `/api/jobs/<id>`; a dispatch outcome has to be visible there.

    Before this, the poll response carried no `result`, so the browser read
    `job.result?.dryRun` off `undefined` and announced "email sent" for a dry run that
    had sent nothing. The result rides along on the poll for dispatch jobs only --
    a detect result is the whole case document.
    """

    def test_a_dispatch_job_reports_its_outcome_on_the_poll_route(self, clean_env):
        accepted = request(
            "POST", "/api/cases/demo/dispatch", {"recipients": "ops@example.gov"}
        ).json()
        payload = accepted
        for _ in range(200):
            payload = request("GET", accepted["pollUrl"]).json()
            if payload["state"] in ("done", "failed"):
                break
            time.sleep(0.05)

        assert payload["id"] == accepted["jobId"]
        if payload["state"] == "failed":
            pytest.skip(f"dispatch could not run here: {payload['error']}")
        result = payload["result"]
        assert result["dryRun"] is True
        assert result["sent"] is False
        assert result["reason"]
        # Written into the checkout, so the response says where relative to it.
        for key in ("reportPath", "emailPath"):
            assert not Path(result[key]).is_absolute(), f"{key} leaked a host path"
            assert result[key].startswith("data/processed/reports/")

    def test_a_detect_job_does_not_carry_a_case_document_on_the_poll_route(self):
        job = jobs_mod.Job(id="j1", kind="detect", scene="demo")
        job.state, job.result = "done", {"id": "demo"}
        assert "result" not in job.to_dict()


class TestClientServerShape:
    """Cross-language shape checks, because these failures are silent.

    `main.js` reading a field the server never sends does not raise; it renders
    `undefined` and the reader is told nothing. Same failure mode as the job-progress
    line in test_api.py, same style of guard.
    """

    CLIENT = ROOT / "apps" / "web" / "app" / "main.js"

    def test_the_client_reads_only_dispatch_result_fields_the_server_sends(self):
        source = self.CLIENT.read_text()
        body = source.split("async function dispatchIncidentEmail(")[1].split(
            "/** Why a dispatch"
        )[0]
        referenced = set(re.findall(r"result\.([A-Za-z]+)", body))
        assert referenced, "the split found no body, so this test would pass vacuously"
        published = DISPATCH_RESULT_KEYS
        assert referenced <= published, f"main.js reads {referenced - published} from a dispatch"

    def test_the_dispatch_result_keys_this_file_asserts_against_are_the_real_ones(self, clean_env):
        """Keeps DISPATCH_RESULT_KEYS honest without needing reportlab to enumerate it."""
        source = (
            ROOT / "services" / "api" / "spilltrace_api" / "dispatch.py"
        ).read_text()
        body = source.split("def dispatch_case_email(")[1]
        keys = set(re.findall(r'^\s+"([A-Za-z]+)":', body, re.M))
        assert keys == DISPATCH_RESULT_KEYS, "dispatch_case_email's result shape changed"

    def test_the_client_reads_only_dispatch_mode_fields_the_server_sends(self):
        source = self.CLIENT.read_text()
        referenced = set(re.findall(r"dispatch\?\.([A-Za-z]+)|dispatch\.([A-Za-z]+)", source))
        names = {name for pair in referenced for name in pair if name}
        published = set(D.dispatch_mode())
        assert names <= published, f"main.js reads {names - published} from the dispatch block"

    def test_the_recipient_prompt_is_no_longer_a_window_prompt(self):
        """A blocking, unstyleable dialog cannot say whether anything will be sent.

        Some browsers also suppress `window.prompt` outright, which turned the button
        into a no-op with no message anywhere.
        """
        source = self.CLIENT.read_text()
        assert "window.prompt" not in source
        assert "U.dialog(" in source

    def test_the_client_polls_through_the_api_module(self):
        """One place decides how long to wait and what a failure means."""
        source = self.CLIENT.read_text()
        assert "api.awaitJob(" in source
        assert "fetch(" not in source, "every request goes through api.js"


#: Every key `dispatch_case_email` can return. Spelled out rather than derived, because
#: deriving it needs reportlab; `test_the_dispatch_result_keys_...` checks it still matches.
DISPATCH_RESULT_KEYS = {
    "caseNumber", "recipients", "subject", "reportPath", "messageId",
    "sent", "dryRun", "emailPath", "reason",
}
