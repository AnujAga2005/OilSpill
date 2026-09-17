"""Operator uploads: the one door a file that was never audited can come through.

The claims this module's docstring makes are the ones these tests exist to defend, and each
of them is a claim about what *cannot* happen:

* an upload cannot reach the audited dataset, because it is written to a different tree --
  tested by asserting ``IMAGE_DIR`` and ``MASK_DIR`` stay empty while a file is accepted;
* a declared name cannot produce a path outside the upload tree, because it is never used
  to build one -- tested against separators, ``..`` and a leading dot, in both the stored
  stem and the id a later request resolves;
* a file is identified by its bytes and not its extension -- tested by naming a text file
  ``.tif`` and an HDF5 file ``.csv``;
* an oversized request costs the refusal and nothing else, because the cap is enforced from
  ``Content-Length`` before the body is read -- tested by declaring a size over the cap and
  sending no body at all, which can only pass if nothing tried to read one.

The HTTP tests drive the real ``SpillTraceHandler`` over byte buffers rather than a socket,
the same way ``test_api`` does, so what runs here is the routing and the handler that run in
production.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from spilltrace_api import case as case_mod
from spilltrace_api import server as server_mod
from spilltrace_api import uploads as U
from spilltrace_common import config as C

# What a TIFF starts with, little-endian classic and BigTIFF, and what NetCDF-4 does.
TIFF_MAGIC = b"II\x2a\x00"
BIGTIFF_MAGIC = b"II\x2b\x00"
HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
CDF3_MAGIC = b"CDF\x01"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def upload_dirs(tmp_path, monkeypatch):
    """Point every upload directory at a temporary tree.

    Autouse because a test that forgot it would write into ``data/uploads`` -- the real
    directory an operator's files land in -- and would then be testing against whatever a
    previous run happened to leave there.
    """
    root = tmp_path / "uploads"
    monkeypatch.setattr(C, "UPLOAD_DIR", root)
    monkeypatch.setattr(C, "UPLOAD_IMAGE_DIR", root / "scenes")
    monkeypatch.setattr(C, "UPLOAD_MASK_DIR", root / "masks")
    monkeypatch.setattr(C, "UPLOAD_FORCING_DIR", root / "forcing")
    return root


def stored(kind: str = "scene", body: bytes = TIFF_MAGIC + b"payload", name: str = "scene.tif") -> U.Upload:
    """Store one file through the real code path."""
    return U.store(kind, io.BytesIO(body), len(body), name)


# ---------------------------------------------------------------------------
# Size, checked before the body is read
# ---------------------------------------------------------------------------


class TestLengthCheck:
    def test_an_unknown_slot_is_refused(self):
        with pytest.raises(U.UploadError, match="unknown upload kind"):
            U.check_length("bogus", 10)

    def test_a_missing_length_is_refused(self):
        # A chunked upload with no Content-Length cannot be sized, and this module refuses
        # to guess: the cap has to be enforceable before the bytes arrive, or not at all.
        with pytest.raises(U.UploadError, match="Content-Length"):
            U.check_length("scene", None)

    @pytest.mark.parametrize("length", [0, -1])
    def test_an_empty_upload_is_refused(self, length):
        with pytest.raises(U.UploadError, match="empty"):
            U.check_length("scene", length)

    def test_a_size_at_the_cap_is_accepted(self):
        cap = C.UPLOAD_MAX_BYTES["scene"]
        assert U.check_length("scene", cap) == cap

    def test_a_size_one_byte_over_the_cap_is_refused(self):
        cap = C.UPLOAD_MAX_BYTES["scene"]
        with pytest.raises(U.UploadError, match="capped at"):
            U.check_length("scene", cap + 1)

    def test_the_overage_is_rounded_up_not_floored(self):
        """Flooring both halves reads as a bug in the refusal.

        A body of 512 MB + 1 byte is over a 512 MB cap. Flooring its size to MB prints
        "capped at 512 MB; this one declares 512 MB", which is nonsense on its face.
        """
        cap = C.UPLOAD_MAX_BYTES["scene"]
        with pytest.raises(U.UploadError) as caught:
            U.check_length("scene", cap + 1)
        assert "declares 513 MB" in str(caught.value)

    def test_every_slot_has_a_cap(self):
        assert set(C.UPLOAD_MAX_BYTES) == set(U.KINDS)
        assert all(size > 0 for size in C.UPLOAD_MAX_BYTES.values())


# ---------------------------------------------------------------------------
# The declared name, which is never used to build a path
# ---------------------------------------------------------------------------


class TestDeclaredName:
    @pytest.mark.parametrize(
        "sent",
        [
            "../../etc/passwd",
            "/etc/passwd",
            "..\\..\\windows\\system32",
            "....//....//x",
            ".hidden",
            "....",
        ],
    )
    def test_a_traversal_in_the_name_cannot_survive_into_the_stem(self, sent):
        stem = U._safe_stem(sent)
        assert "/" not in stem and "\\" not in stem and ".." not in stem
        assert not stem.startswith(".")

    def test_only_the_final_component_is_kept(self):
        assert U._safe_stem("/somewhere/else/scene.tif") == "scene"

    def test_unusual_characters_become_dashes(self):
        assert U._safe_stem("my scene (final)!.tif") == "my-scene-final"

    def test_a_name_that_sanitises_to_nothing_gets_a_placeholder(self):
        assert U._safe_stem("///") == "upload"
        assert U._safe_stem("") == "upload"
        assert U._safe_stem(None) == "upload"

    def test_the_stem_is_clipped(self):
        assert len(U._safe_stem("a" * 200 + ".tif")) == 48

    def test_the_extension_must_be_on_the_slots_allow_list(self):
        with pytest.raises(U.UploadError, match="must be"):
            U.suffix_for("scene", "scene.png")

    def test_a_name_with_no_extension_is_refused(self):
        with pytest.raises(U.UploadError, match="no extension"):
            U.suffix_for("scene", "scene")

    def test_the_extension_is_taken_from_the_allow_list(self):
        # Upper case is accepted but normalised, so the stored file has one form.
        assert U.suffix_for("scene", "SCENE.TIFF") == ".tiff"
        assert U.suffix_for("ais", "report.csv") == ".csv"


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


class TestStore:
    def test_a_file_lands_under_a_hashed_name(self):
        body = TIFF_MAGIC + b"payload"
        upload = stored(body=body, name="Sentinel1_GRD.tif")
        assert upload.path.exists()
        assert upload.path.read_bytes() == body
        assert upload.size_bytes == len(body)
        assert upload.sha256 == hashlib.sha256(body).hexdigest()
        assert upload.upload_id == f"sentinel1_grd-{upload.sha256[:12]}"

    def test_the_original_name_is_kept_as_a_label_only(self):
        upload = stored(name="My Scene.tif")
        assert upload.original_name == "My Scene.tif"
        # ... and the stored path does not contain it, because the hash names the file.
        assert "My Scene" not in upload.path.name

    def test_the_same_bytes_twice_are_one_file(self):
        first = stored(body=TIFF_MAGIC + b"same")
        second = stored(body=TIFF_MAGIC + b"same")
        assert first.upload_id == second.upload_id
        assert first.path == second.path
        assert len(list(first.path.parent.glob("*.tif"))) == 1

    def test_different_bytes_under_one_name_are_two_files(self):
        first = stored(body=TIFF_MAGIC + b"one")
        second = stored(body=TIFF_MAGIC + b"two")
        assert first.upload_id != second.upload_id
        assert len(list(first.path.parent.glob("*.tif"))) == 2

    def test_a_body_that_ends_early_is_refused_and_leaves_nothing_behind(self, upload_dirs):
        with pytest.raises(U.UploadError, match="ended early"):
            U.store("scene", io.BytesIO(TIFF_MAGIC), 1024, "scene.tif")
        assert list((upload_dirs / "scenes").glob("*")) == []

    def test_an_oversized_body_past_the_check_is_still_caught_by_the_declared_length(self):
        # `check_length` gates the request; this is the second line of defence, for a caller
        # that skips it. Reading stops at the declared length, so the extra bytes are not
        # written and the hash describes the declared span only.
        body = TIFF_MAGIC + b"x" * 100
        upload = U.store("scene", io.BytesIO(body), 20, "scene.tif")
        assert upload.size_bytes == 20
        assert upload.path.read_bytes() == body[:20]

    def test_an_unknown_kind_cannot_be_stored(self):
        with pytest.raises(U.UploadError, match="unknown upload kind"):
            U.store("bogus", io.BytesIO(b"x"), 1, "x.tif")


# ---------------------------------------------------------------------------
# Identity by content, not by extension
# ---------------------------------------------------------------------------


class TestMagicBytes:
    def test_a_tiff_named_scene_is_accepted(self):
        assert stored(body=TIFF_MAGIC + b"x", name="a.tif").path.exists()

    def test_a_bigtiff_is_accepted(self):
        assert stored(body=BIGTIFF_MAGIC + b"x", name="a.tif").path.exists()

    def test_a_text_file_named_tif_is_refused(self):
        with pytest.raises(U.UploadError, match="not a TIFF"):
            stored(body=b"this is not a raster at all", name="a.tif")

    def test_a_png_named_tif_is_refused(self):
        with pytest.raises(U.UploadError, match="not a TIFF"):
            stored(body=b"\x89PNG\r\n\x1a\n" + b"x", name="a.tif")

    def test_a_refused_file_is_not_left_on_disk(self, upload_dirs):
        with pytest.raises(U.UploadError):
            stored(body=b"junk", name="a.tif")
        assert list((upload_dirs / "scenes").glob("*.tif")) == []

    def test_an_hdf5_file_named_nc_is_accepted(self):
        assert stored("era5", HDF5_MAGIC + b"x", "wind.nc").path.exists()

    def test_a_classic_netcdf3_file_gets_its_own_message(self):
        """The advice differs: re-save it, rather than it is not what you think it is.

        Every other magic-byte failure means the file is not the format at all. This one
        means it *is* NetCDF and the reader in this repository is the limitation, so the
        operator is told to convert rather than told their file is wrong.
        """
        with pytest.raises(U.UploadError, match="NetCDF-4"):
            stored("cmems", CDF3_MAGIC + b"x", "currents.nc")

    def test_a_csv_named_nc_is_refused(self):
        with pytest.raises(U.UploadError, match="not NetCDF-4"):
            stored("era5", b"time,wind\n", "wind.nc")

    def test_ais_is_exempt_from_the_prefix_check(self):
        # A CSV has no magic number. Its header is checked by the importer when it is read,
        # which is a stronger test than a prefix would be -- so the upload slot accepts it
        # and the later stage is what rejects a malformed one.
        upload = stored("ais", b"MMSI,BaseDateTime\n999000001,2020-01-01T00:00:00\n", "ais.csv")
        assert upload.path.exists()

    def test_the_mask_slot_requires_a_tiff_too(self):
        with pytest.raises(U.UploadError, match="not a TIFF"):
            stored("mask", b"not a raster", "mask.tif")


# ---------------------------------------------------------------------------
# Resolution by id
# ---------------------------------------------------------------------------


class TestResolve:
    def test_a_stored_id_resolves_to_its_file(self):
        upload = stored()
        assert U.resolve("scene", upload.upload_id) == upload.path.resolve()

    def test_an_absent_id_resolves_to_nothing(self):
        assert U.resolve("scene", "nothing-here") is None

    def test_an_empty_id_resolves_to_nothing(self):
        assert U.resolve("scene", None) is None
        assert U.resolve("scene", "") is None

    @pytest.mark.parametrize(
        "bad",
        [
            "../scene-abc123",
            "..",
            "../../etc/passwd",
            "/etc/passwd",
            ".hidden",
            "a/b",
            "a\\b",
            "scene-abc123/../..",
            "scene-abc123\x00",
        ],
    )
    def test_an_id_that_could_escape_is_refused_outright(self, bad):
        with pytest.raises(U.UploadError, match="invalid upload id"):
            U.resolve("scene", bad)

    def test_a_scene_id_does_not_resolve_against_the_mask_slot(self):
        scene = stored("scene")
        assert U.resolve("mask", scene.upload_id) is None


# ---------------------------------------------------------------------------
# What the interface is told
# ---------------------------------------------------------------------------


class TestListingAndDescribe:
    def test_a_stored_upload_is_described(self):
        upload = stored(name="Scene One.tif")
        described = U.describe("scene", upload.upload_id)
        assert described["originalName"] == "Scene One.tif"
        assert described["kind"] == "scene"
        assert described["receivedUtc"]

    def test_the_description_says_where_the_file_sits_the_way_the_rest_of_the_product_does(self):
        """A case document holds this and is bundled for the browser.

        ``build_web.py`` refuses a bundle containing an absolute host path, because it would
        pin the artefacts to one machine. The rule is ``config.display_path``, and the point
        of this test is that the upload report goes through it rather than calling
        ``str(path)`` -- which is what makes a dataset kept outside the repository safe to
        name absolutely while one inside it stays relative.
        """
        upload = stored()
        described = U.describe("scene", upload.upload_id)
        assert described["storedAs"] == C.display_path(upload.path)

    def test_a_path_inside_the_repository_is_made_relative(self):
        inside = C.REPO_ROOT / "data" / "uploads" / "scenes" / "x.tif"
        assert C.display_path(inside) == "data/uploads/scenes/x.tif"

    def test_a_path_outside_the_repository_is_left_absolute(self, tmp_path):
        outside = tmp_path / "elsewhere.tif"
        assert C.display_path(outside) == str(outside.resolve())

    def test_an_unknown_id_describes_nothing(self):
        assert U.describe("scene", "ghost") is None

    def test_listing_is_grouped_by_slot(self):
        stored("scene")
        stored("ais", b"MMSI\n", "ais.csv")
        found = U.listing()
        assert set(found) == set(U.KINDS)
        assert len(found["scene"]) == 1
        assert len(found["ais"]) == 1
        assert found["cmems"] == []

    def test_a_sidecar_without_its_file_describes_nothing(self):
        upload = stored()
        upload.path.unlink()
        assert U.listing()["scene"] == []

    def test_clear_removes_every_file_and_reports_the_count(self):
        stored("scene")
        stored("ais", b"MMSI\n", "ais.csv")
        assert U.clear() == 4  # two files and their two sidecars
        assert U.listing()["scene"] == []
        assert U.listing()["ais"] == []

    def test_clear_on_an_empty_tree_is_zero_and_not_an_error(self):
        assert U.clear() == 0


# ---------------------------------------------------------------------------
# The layout claim: an upload never reaches the audited dataset
# ---------------------------------------------------------------------------


class TestSeparationFromTheDataset:
    def test_an_upload_is_not_written_into_the_dataset_trees(self, tmp_path, monkeypatch):
        """The strongest claim in the module, and it is structural rather than checked.

        ``IMAGE_DIR`` and ``MASK_DIR`` hold the scenes the model was trained and scored on.
        Pointing them at empty directories and then accepting an upload is the test: if any
        code path wrote there, the directories would stop being empty.
        """
        images = tmp_path / "audited" / "images"
        masks = tmp_path / "audited" / "masks"
        images.mkdir(parents=True)
        masks.mkdir(parents=True)
        monkeypatch.setattr(C, "IMAGE_DIR", images)
        monkeypatch.setattr(C, "MASK_DIR", masks)

        stored("scene", name="00270.tif")
        stored("mask", name="00270.tif")

        assert list(images.iterdir()) == []
        assert list(masks.iterdir()) == []

    def test_the_upload_tree_is_a_sibling_of_the_dataset_and_not_inside_it(self):
        for directory in (C.UPLOAD_IMAGE_DIR, C.UPLOAD_MASK_DIR, C.UPLOAD_FORCING_DIR):
            assert C.IMAGE_DIR not in directory.parents
            assert C.MASK_DIR not in directory.parents


# ---------------------------------------------------------------------------
# The HTTP endpoints
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


def send(
    method: str,
    path: str,
    body: bytes = b"",
    *,
    content_type: str | None = None,
    content_length: int | str | None = None,
    host: str = "127.0.0.1",
) -> Response:
    """Drive one request -- with a raw body -- through the real handler, no socket.

    ``content_length`` is passed explicitly so a test can declare a size it does not send.
    That is the only way to show the cap is enforced before the body is read: a request
    whose declared length is over the cap and whose body is empty can only produce the
    right answer if nothing tried to read a body. A string is passed through verbatim, so a
    header that is not a number can be tested too.
    """
    base = server_mod.SpillTraceHandler

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

    head = f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n"
    if content_type:
        head += f"Content-Type: {content_type}\r\n"
    length = content_length if content_length is not None else len(body)
    head += f"Content-Length: {length}\r\n"
    return Response(Loopback(head.encode() + b"\r\n" + body).wfile.getvalue())


def upload_request(kind: str, body: bytes, name: str, **kwargs) -> Response:
    return send(
        "POST",
        f"/api/uploads?kind={kind}&name={name}",
        body,
        content_type="application/octet-stream",
        **kwargs,
    )


def send_pipeline(
    requests: list[tuple], *, content_type: str = "application/octet-stream"
) -> list[Response]:
    """Feed several requests down one keep-alive connection, and split the responses.

    Each request is ``(method, path, body)`` or ``(method, path, body, declared_length)`` --
    the fourth element lets a test declare a size it does not send, the same escape ``send``
    offers.

    Deliberately no ``Connection: close``: the handler loops on the same stream while the
    connection stays open, which is what a browser does and what makes an unread request
    body dangerous. ``send`` sends one request and closes, so it cannot see this class of
    bug at all -- there is no second request for the leftover bytes to corrupt.
    """
    base = server_mod.SpillTraceHandler

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

    raw = b""
    for request in requests:
        method, path, body = request[0], request[1], request[2]
        declared = request[3] if len(request) > 3 else len(body)
        raw += (
            f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Content-Type: {content_type}\r\nContent-Length: {declared}\r\n\r\n"
        ).encode() + body

    stream = Loopback(raw).wfile.getvalue()
    return _split_responses(stream)


def _split_responses(stream: bytes) -> list[Response]:
    """Cut a concatenated response stream into one Response per request."""
    out: list[Response] = []
    rest = stream
    while rest.startswith(b"HTTP/1.1 "):
        head, _, remainder = rest.partition(b"\r\n\r\n")
        length = 0
        for line in head.decode(errors="replace").split("\r\n"):
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        out.append(Response(head + b"\r\n\r\n" + remainder[:length]))
        rest = remainder[length:]
    return out


class TestUploadEndpoint:
    def test_a_good_file_is_accepted_and_described(self):
        response = upload_request("scene", TIFF_MAGIC + b"pixels", "my-scene.tif")
        assert response.status == 201
        payload = response.json()
        assert payload["upload"]["kind"] == "scene"
        assert payload["upload"]["originalName"] == "my-scene.tif"
        assert payload["upload"]["sizeBytes"] == len(TIFF_MAGIC + b"pixels")

    def test_the_slots_and_their_limits_travel_with_the_response(self):
        """The client renders its caps from this rather than hardcoding them.

        A form that disagreed with the server would refuse a file the server would have
        taken, or accept one it would not.
        """
        payload = upload_request("scene", TIFF_MAGIC + b"x", "a.tif").json()
        assert set(payload["slots"]) == set(U.KINDS)
        assert payload["slots"]["scene"]["required"] is True
        assert payload["slots"]["mask"]["required"] is False
        assert payload["slots"]["scene"]["maxBytes"] == C.UPLOAD_MAX_BYTES["scene"]
        assert payload["slots"]["scene"]["suffixes"] == [".tif", ".tiff"]

    def test_the_upload_is_then_visible_in_the_listing(self):
        upload_request("scene", TIFF_MAGIC + b"x", "a.tif")
        state = send("GET", "/api/uploads").json()
        assert len(state["uploads"]["scene"]) == 1
        assert state["uploads"]["scene"][0]["originalName"] == "a.tif"

    def test_the_slot_note_states_where_the_files_go(self):
        note = send("GET", "/api/uploads").json()["note"]
        assert "outside the evaluated dataset" in note

    def test_an_unknown_slot_is_refused(self):
        assert upload_request("bogus", b"x", "a.tif").status == 400

    def test_an_oversized_request_is_refused_before_the_body_is_read(self):
        """No body is sent. The 413 can only be produced from the header."""
        cap = C.UPLOAD_MAX_BYTES["scene"]
        response = upload_request("scene", b"", "a.tif", content_length=cap + 1)
        assert response.status == 413
        assert "capped at" in response.json()["error"]

    def test_a_file_that_is_not_what_it_claims_is_a_400(self):
        response = upload_request("scene", b"just text", "a.tif")
        assert response.status == 400
        assert "not a TIFF" in response.json()["error"]

    def test_an_empty_upload_is_a_400_and_not_a_413(self):
        """Size is not the problem, so the status must not say it is.

        The two refusals travel down the same path, and it is easy to write the branch so
        that any refusal carrying a Content-Length reads as "too large". An empty body and
        an oversized one are different answers, and the client renders them differently.
        """
        assert upload_request("scene", b"x", "a.tif", content_length=0).status == 400

    def test_an_unknown_slot_is_a_400_and_not_a_413(self):
        assert upload_request("bogus", b"x", "a.tif").status == 400

    def test_a_wrong_extension_is_a_400(self):
        assert upload_request("scene", TIFF_MAGIC + b"x", "a.png").status == 400

    def test_a_declared_length_that_is_not_a_number_is_refused(self):
        response = upload_request("scene", b"x", "a.tif", content_length="not-a-number")
        assert response.status == 400
        assert "is not a number" in response.json()["error"]

    def test_clear_empties_the_tree_and_reports_the_count(self):
        upload_request("scene", TIFF_MAGIC + b"x", "a.tif")
        response = send("POST", "/api/uploads/clear", b"{}", content_type="application/json")
        assert response.status == 200
        assert response.json()["removed"] >= 1
        assert send("GET", "/api/uploads").json()["uploads"]["scene"] == []


class TestARefusalLeavesTheConnectionUsable:
    """A refused upload must not corrupt the requests behind it on the same connection.

    A browser reuses one keep-alive connection for every call the page makes. If a refusal
    returns while the request body is still unread, the handler's next ``readline`` starts
    inside those bytes: the "request line" is garbage, no route matches it, and
    ``BaseHTTPRequestHandler`` answers with its own HTML ``Error code: 501`` page. The error
    the operator sees then names neither the upload nor the reason -- the real refusal came
    back a moment earlier and was thrown away with it.

    This is invisible to a one-request-per-test suite. ``send`` closes the connection, so
    there is no next request to corrupt.
    """

    def test_a_wrong_extension_does_not_desync_the_next_request(self):
        responses = send_pipeline(
            [
                ("POST", "/api/uploads?kind=scene&name=a.png", TIFF_MAGIC + b"pixels"),
                ("GET", "/api/uploads", b""),
            ]
        )
        assert [r.status for r in responses] == [400, 200]
        assert "must be .tif" in responses[0].json()["error"]

    def test_an_unknown_slot_does_not_desync_the_next_request(self):
        responses = send_pipeline(
            [
                ("POST", "/api/uploads?kind=bogus&name=a.tif", TIFF_MAGIC + b"pixels"),
                ("GET", "/api/health", b""),
            ]
        )
        assert [r.status for r in responses] == [400, 200]

    def test_an_oversized_declaration_does_not_desync_the_next_request(self, monkeypatch):
        """A 413 returns before a byte is read, so the body behind it must be drained.

        The cap is lowered so the request can be over it *and* still send a body worth
        draining: the real cap is 512 MB, and ``_drain`` closes the connection past 4 MB
        rather than read a body it has already refused. Both of those are correct, so the
        test has to arrive at the drain with something small enough to be drained.

        The declared length is the body's true length. Declaring more than is sent would
        leave the drain eating into the next request -- but that request is malformed in a
        way no client produces, and the desync it causes is the declaration's fault, not the
        handler's.
        """
        monkeypatch.setitem(C.UPLOAD_MAX_BYTES, "scene", 8)
        body = TIFF_MAGIC + b"pixels!!"
        responses = send_pipeline(
            [
                ("POST", "/api/uploads?kind=scene&name=a.tif", body),
                ("GET", "/api/health", b""),
            ]
        )
        assert [r.status for r in responses] == [413, 200]
        assert "capped at" in responses[0].json()["error"]

    def test_a_refusal_after_the_body_is_read_closes_rather_than_guesses(self):
        """A TIFF that is not a TIFF fails inside ``store``, after the body is consumed.

        There is nothing left to drain there and the stream state is unknown, so the handler
        closes instead of pretending to be reusable. ``store`` can also fail having read
        fewer bytes than were declared, and a connection in that state cannot be recovered by
        any amount of draining -- so the honest answer is one response and a closed
        connection, not a 400 followed by a 501 out of whatever the next parse finds.
        """
        responses = send_pipeline(
            [
                ("POST", "/api/uploads?kind=scene&name=a.tif", b"just text"),
                ("GET", "/api/health", b""),
            ]
        )
        assert len(responses) == 1
        assert responses[0].status == 400
        assert "not a TIFF" in responses[0].json()["error"]

    def test_the_second_request_is_a_real_response_not_a_501_page(self):
        """``BaseHTTPRequestHandler``'s own 501 page is HTML and names nothing."""
        responses = send_pipeline(
            [
                ("POST", "/api/uploads?kind=bogus&name=a.tif", b"x"),
                ("GET", "/api/health", b""),
            ]
        )
        assert len(responses) == 2
        for response in responses:
            assert b"<html" not in response.body.lower()
            assert b"Error code" not in response.body


# ---------------------------------------------------------------------------
# Submitting a case built on uploads
# ---------------------------------------------------------------------------


def sample_case(case_id: str = "demo") -> dict[str, Any]:
    """A minimal stored case, for the collision path."""
    return {"id": case_id, "caseId": case_id, "scene": {}, "slick": {}, "provenance": {}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from spilltrace_api import store as store_mod

    fresh = store_mod.CaseStore(tmp_path / "cases")
    monkeypatch.setattr(server_mod, "STORE", fresh)
    return fresh


class TestSubmitWithUploads:
    def test_an_unknown_upload_id_says_which_slot_is_missing(self, store):
        response = send(
            "POST",
            "/api/cases/fresh-scene/detect",
            json.dumps({"sceneUpload": "nothing-here"}).encode(),
            content_type="application/json",
        )
        assert response.status == 404
        error = response.json()["error"]
        assert "scene upload" in error

    def test_a_traversal_in_an_upload_id_is_a_400(self, store):
        response = send(
            "POST",
            "/api/cases/fresh-scene/detect",
            json.dumps({"sceneUpload": "../etc/passwd"}).encode(),
            content_type="application/json",
        )
        assert response.status == 400

    def test_an_uploaded_scene_must_not_overwrite_an_existing_case(self, store):
        """The store is keyed by id alone, so this is the only place the clash is catchable.

        Without it, a case built from an operator's pixels would silently replace a stored
        case of the same name -- and the dashboard would show one case's header over
        another's numbers.
        """
        upload = stored()
        store.save("taken", sample_case("taken"))
        response = send(
            "POST",
            "/api/cases/taken/detect",
            json.dumps({"sceneUpload": upload.upload_id}).encode(),
            content_type="application/json",
        )
        assert response.status == 409
        assert "already exists" in response.json()["error"]

    def test_an_unfree_id_is_only_refused_for_an_uploaded_scene(self, store):
        """A dataset scene under an id that already holds a case is a re-run, not a clash.

        The two are different: re-running the supplied scene is the ordinary way to rebuild
        a case, and only an upload can put different pixels under a name already in use.
        """
        store.save("taken", sample_case("taken"))
        response = send(
            "POST",
            "/api/cases/taken/detect",
            json.dumps({}).encode(),
            content_type="application/json",
        )
        # The scene does not exist in the audited dataset either, so this is a 404 -- but a
        # 404 about the *scene*, not a 409 about the id.
        assert response.status == 404
        assert "already exists" not in response.json()["error"]


# ---------------------------------------------------------------------------
# The interactive parts: limits, the acquisition instant, band order
# ---------------------------------------------------------------------------


class TestCaseLimits:
    def test_a_synthetic_feed_keeps_the_synthetic_warning(self):
        limits = case_mod.case_limits(feed={"mode": "synthetic"}, uploaded_scene=False)
        assert limits[0] == case_mod.LIMITS[0]

    def test_a_real_feed_replaces_the_synthetic_warning(self):
        """A case built on real AIS must not carry the sentence saying its AIS is synthetic.

        Leaving it would put a false statement on the Vessels screen of the one case whose
        whole point is that its traffic is real.
        """
        limits = case_mod.case_limits(feed={"mode": "real"}, uploaded_scene=False)
        assert limits[0] == case_mod.LIMIT_AIS_REAL
        assert case_mod.LIMITS[0] not in limits

    def test_an_uploaded_scene_adds_the_out_of_dataset_warning(self):
        limits = case_mod.case_limits(feed={"mode": "synthetic"}, uploaded_scene=True)
        assert limits[0] == case_mod.LIMIT_UPLOADED_SCENE
        assert len(limits) == len(case_mod.LIMITS) + 1

    def test_both_at_once(self):
        limits = case_mod.case_limits(feed={"mode": "real"}, uploaded_scene=True)
        assert limits[0] == case_mod.LIMIT_UPLOADED_SCENE
        assert limits[1] == case_mod.LIMIT_AIS_REAL

    def test_the_dataset_case_is_unchanged(self):
        assert case_mod.case_limits(feed={}, uploaded_scene=False) == case_mod.LIMITS


class TestOperatorAcquisitionTime:
    @pytest.mark.parametrize(
        "typed",
        [
            "2020-05-01T02:11:00Z",
            "2020-05-01T02:11:00",
            "2020-05-01T02:11",
            "2020-05-01T04:11:00+02:00",
        ],
    )
    def test_the_forms_a_browser_can_send_all_land_on_the_same_instant(self, typed):
        assert case_mod._parse_operator_time(typed) == "2020-05-01T02:11:00Z"

    def test_an_offset_is_converted_rather_than_kept(self):
        assert case_mod._parse_operator_time("2020-05-01T00:11:00-02:00") == "2020-05-01T02:11:00Z"

    def test_an_empty_value_is_refused(self):
        with pytest.raises(case_mod.CaseError, match="empty"):
            case_mod._parse_operator_time("   ")

    @pytest.mark.parametrize("bad", ["yesterday", "2020-13-45T99:99:99Z", "05/01/2020"])
    def test_a_value_that_is_not_a_time_is_refused_with_an_example(self, bad):
        with pytest.raises(case_mod.CaseError, match="2020-05-01T02:11:00Z"):
            case_mod._parse_operator_time(bad)


class TestCaseRequestKey:
    def test_two_requests_differing_only_in_an_upload_do_not_share_a_key(self):
        """The key decides whether a job is reused; an upload has to be part of it.

        Two uploads hash to different ids, so a key that ignored them would let a second
        request be answered from the first one's cached job -- the operator would get their
        own scene's geometry back under someone else's pixels.
        """
        base = dict(scene="s", acquired_utc="2020-05-01T02:11:00Z")
        first = case_mod.CaseRequest(**base, scene_upload=Path("/a/one.tif"))
        second = case_mod.CaseRequest(**base, scene_upload=Path("/a/two.tif"))
        assert first.key() != second.key()

    def test_the_same_upload_and_settings_share_a_key(self):
        base = dict(scene="s", acquired_utc="2020-05-01T02:11:00Z")
        first = case_mod.CaseRequest(**base, scene_upload=Path("/a/one.tif"))
        second = case_mod.CaseRequest(**base, scene_upload=Path("/a/one.tif"))
        assert first.key() == second.key()

    def test_band_order_is_part_of_the_key(self):
        base = dict(scene="s")
        assert (
            case_mod.CaseRequest(**base, band_order="vh-vv").key()
            != case_mod.CaseRequest(**base, band_order="vv-vh").key()
        )
