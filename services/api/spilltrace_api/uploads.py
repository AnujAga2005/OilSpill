"""Operator-supplied files: where they land, and what is checked before they are used.

Everything the pipeline consumes normally comes from the audited dataset on local disk.
This module is the one door through which a file that was *not* audited can get in, so
the checks are here rather than spread across the endpoints that call it.

Four rules shape the whole module:

* **Uploads never join the dataset.** They land under :data:`config.UPLOAD_DIR`, which is
  a different tree from ``IMAGE_DIR``/``MASK_DIR`` and is gitignored. An upload cannot
  add to, shadow, or overwrite a supplied scene, because it is never written where one
  would be looked for. That is a property of the layout, not of a check that could be
  forgotten.
* **The declared name is never trusted.** The stored filename is derived from a content
  hash and a sanitised stem; the client's string is kept only as a label to show back to
  the operator. A name containing a separator, a ``..`` or a leading dot cannot produce a
  path outside the upload tree because it is not used to build the path at all.
* **The file is identified by what it is.** A ``.tif`` that is not a TIFF and a ``.nc``
  that is not an HDF5 container are rejected on their magic bytes after landing, with the
  partial file removed. The readers downstream would raise anyway; failing here means the
  operator gets told which file was wrong instead of a decode error from three layers in.
* **Same bytes, same id.** The id is the content hash, so re-uploading a file the server
  already holds is idempotent rather than a second copy under a second name.

Size is enforced from ``Content-Length`` *before* the body is read, so an oversized
request costs the refusal and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from spilltrace_common import config as C

#: Characters allowed to survive from the operator's filename into the stored one. The
#: stem is only ever a readability aid -- the hash is what makes the name unique -- so an
#: aggressive filter costs nothing and removes a whole class of path questions.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: How much of the content hash goes into the id. 12 hex characters is 48 bits: for the
#: handful of files one operator uploads in a session, a collision is not a real risk, and
#: a shorter id is one a person can read back off the screen.
_HASH_CHARS = 12

#: Read the body in chunks rather than into one buffer: a CMEMS product is ~370 MB and
#: there is no reason for the server to hold all of it in memory to write it to disk.
_CHUNK = 1024 * 1024

#: What each slot's bytes must start with for the file to be what it claims to be.
#: AIS is absent on purpose -- a CSV has no magic number, and its header is checked by
#: ``marinecadastre.check_header`` when it is read, which is a stronger test than a
#: prefix would be.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    # Byte-order marker plus 42 (classic) or 43 (BigTIFF), little- and big-endian. This is
    # exactly what spilltrace_common.geotiff accepts.
    "scene": (b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"),
    "mask": (b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"),
    # NetCDF-4 is HDF5 underneath, and the HDF5 reader in this repository checks the same
    # eight bytes. A classic NetCDF-3 file ("CDF\x01") is refused here rather than later:
    # the reader genuinely cannot decode it, and saying so at upload is more use.
    "era5": (b"\x89HDF\r\n\x1a\n",),
    "cmems": (b"\x89HDF\r\n\x1a\n",),
}

#: The slots an operator can fill, and which directory each one lands in.
KINDS: tuple[str, ...] = ("scene", "mask", "era5", "cmems", "ais")


class UploadError(RuntimeError):
    """An upload was refused. Carries a message fit to show an operator."""


class UploadTooLarge(UploadError):
    """The declared body is over its slot's cap.

    Separated from :class:`UploadError` because it is the only refusal that is about size,
    and the status code says so: 413 tells the client the same file will never fit, where
    400 would read as though the file were malformed. Every other refusal -- an unknown
    slot, an empty body, a wrong extension -- is a 400, and collapsing them into one branch
    is how a text file named ``.tif`` ends up reported as too large.
    """


class ChunkOutOfOrder(UploadError):
    """A chunk of a chunked upload did not continue the staged file.

    Its own class because it is recoverable and the others are not: the body was not touched
    (the offset is checked before a byte is read), so the request can be answered 409 and the
    client can restart the file, rather than the connection being torn down as for a body
    that failed mid-write. Carries the offset the staged file actually sits at.
    """

    def __init__(self, message: str, *, expected_offset: int) -> None:
        super().__init__(message)
        self.expected_offset = expected_offset


@dataclass(frozen=True)
class Upload:
    """One stored file and what is known about it."""

    kind: str
    upload_id: str
    path: Path
    size_bytes: int
    sha256: str
    original_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "uploadId": self.upload_id,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            # The name the operator's browser sent, shown back so they can tell which of
            # several files this is. Never used to build a path.
            "originalName": self.original_name,
            # Relative to the repository, never absolute: a case document carrying this
            # is written to disk and bundled, and an absolute path would pin it to one
            # machine. `build_web.py` refuses a bundle containing one.
            "storedAs": C.display_path(self.path),
        }


def directory_for(kind: str) -> Path:
    """Where files of this kind live."""
    if kind == "scene":
        return C.UPLOAD_IMAGE_DIR
    if kind == "mask":
        return C.UPLOAD_MASK_DIR
    if kind in ("era5", "cmems", "ais"):
        return C.UPLOAD_FORCING_DIR
    raise UploadError(f"unknown upload kind {kind!r}")


def _safe_stem(name: str) -> str:
    """A readable, path-free stem from whatever the client called the file."""
    stem = Path(str(name or "")).name  # drop any directory component the client sent
    stem = Path(stem).stem
    stem = _UNSAFE.sub("-", stem).strip("-._")
    # A stem that sanitised away to nothing, or to something a shell or a glob would treat
    # specially, is replaced rather than patched: the hash already makes the name unique.
    return (stem[:48] or "upload").lower()


def suffix_for(kind: str, name: str) -> str:
    """The stored extension, taken from the allow-list rather than from the client.

    Public so a caller can reject a wrong-extension upload *before* reading its body. The
    HTTP handler does exactly that: a request refused here has had nothing read from it
    yet, so the body can be drained and the connection left usable. Discovering the same
    problem inside :func:`store` would be too late to drain, because by then the caller has
    a body half-written and unread bytes still sitting in the socket.
    """
    if kind not in KINDS:
        raise UploadError(f"unknown upload kind {kind!r}; expected one of {', '.join(KINDS)}")
    allowed = C.UPLOAD_SUFFIXES[kind]
    got = Path(str(name or "")).suffix.lower()
    if got not in allowed:
        raise UploadError(
            f"a {kind} upload must be {' or '.join(allowed)}; got "
            f"{got or 'a name with no extension'}"
        )
    return got


def check_length(kind: str, content_length: int | None) -> int:
    """Validate a declared body size, or raise. Returns the size.

    Called before the body is read so an oversized request is refused without the server
    reading, or writing, a single byte of it.
    """
    if kind not in KINDS:
        raise UploadError(f"unknown upload kind {kind!r}; expected one of {', '.join(KINDS)}")
    if content_length is None:
        raise UploadError("an upload needs a Content-Length header")
    if content_length <= 0:
        raise UploadError("the upload is empty")
    cap = C.UPLOAD_MAX_BYTES[kind]
    if content_length > cap:
        # Rounded up, not down: flooring both halves turns "512 MB + 1 byte" into the
        # message "capped at 512 MB; this one declares 512 MB", which reads as a bug.
        over = -(-content_length // (1024 * 1024))
        raise UploadTooLarge(
            f"a {kind} upload is capped at {cap // (1024 * 1024)} MB; this one declares "
            f"{over} MB"
        )
    return content_length


def _verify_magic(kind: str, path: Path) -> None:
    """Confirm the landed file is the format its slot requires."""
    expected = _MAGIC.get(kind)
    if not expected:
        return
    with path.open("rb") as handle:
        head = handle.read(8)
    if not any(head.startswith(prefix) for prefix in expected):
        if kind in ("era5", "cmems") and head.startswith(b"CDF"):
            raise UploadError(
                f"this is a classic NetCDF-3 file; the {kind} reader in this project "
                "decodes NetCDF-4 (HDF5) only. Re-save it as NetCDF-4 and upload again."
            )
        raise UploadError(
            f"the bytes in this file are not {'a TIFF' if kind in ('scene', 'mask') else 'NetCDF-4'}, "
            "whatever it is named"
        )


def store(kind: str, body: BinaryIO, declared_length: int, original_name: str) -> Upload:
    """Stream one upload to disk, verify it, and return where it landed.

    The body is written to a temporary file in the destination directory and hashed as it
    goes, then renamed once the content hash is known. Writing beside the destination
    rather than in the system temp directory keeps the rename on one filesystem, so a
    half-written file is never visible under its final name.
    """
    if kind not in KINDS:
        raise UploadError(f"unknown upload kind {kind!r}; expected one of {', '.join(KINDS)}")
    suffix = suffix_for(kind, original_name)
    target_dir = directory_for(kind)
    target_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    written = 0
    # The name cannot collide: it is replaced by the hashed name before this function
    # returns, and two concurrent uploads of different bytes differ in `id(body)`.
    staging = target_dir / f".incoming-{id(body):x}{suffix}"
    try:
        with staging.open("wb") as handle:
            while written < declared_length:
                chunk = body.read(min(_CHUNK, declared_length - written))
                if not chunk:
                    break
                written += len(chunk)
                digest.update(chunk)
                handle.write(chunk)
        if written != declared_length:
            raise UploadError(
                f"the upload ended early: {written} bytes arrived of {declared_length} declared"
            )
    except UploadError:
        staging.unlink(missing_ok=True)
        raise
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise UploadError(f"the upload could not be written: {exc}") from exc

    return _finalize(kind, staging, suffix, digest.hexdigest(), written, original_name)


def _finalize(
    kind: str, staging: Path, suffix: str, sha: str, size: int, original_name: str
) -> Upload:
    """Rename a fully-written staging file to its content-hash id, verify it, record it.

    The tail shared by the single-shot :func:`store` and the chunked :func:`store_chunk`, so
    a file that arrived in one request and one that arrived in ten land in exactly the same
    way: the id is the content hash (same bytes, same name), the magic bytes are checked on
    the assembled file, and a file that is not what it claims is removed rather than kept
    under a name that implies it is valid.
    """
    upload_id = f"{_safe_stem(original_name)}-{sha[:_HASH_CHARS]}"
    # Same bytes, same name: a repeated upload replaces its own identical file rather than
    # accumulating copies. `.with_name` keeps the rename inside the staging file's own
    # directory, so it stays on one filesystem and a half-written file is never seen final.
    final = staging.with_name(f"{upload_id}{suffix}")
    try:
        staging.replace(final)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise UploadError(f"the upload could not be written: {exc}") from exc

    try:
        _verify_magic(kind, final)
    except UploadError:
        # A file that is not what it claims to be is not kept. Leaving it would mean the
        # upload directory holds things nothing can read, under names that imply otherwise.
        final.unlink(missing_ok=True)
        raise

    upload = Upload(
        kind=kind,
        upload_id=upload_id,
        path=final,
        size_bytes=size,
        sha256=sha,
        original_name=Path(str(original_name or "")).name[:128],
    )
    _write_sidecar(upload)
    return upload


#: A chunked upload's staging file is keyed by a client-chosen token so the pieces of one
#: upload append to one file and two uploads in flight never cross. Constrained to lowercase
#: hex, exactly as :func:`resolve` constrains an id, so the token cannot build a path outside
#: the slot's directory however the client mangles it.
_CHUNK_TOKEN = re.compile(r"[0-9a-f]{8,64}")


def _chunk_staging(kind: str, token: str, suffix: str) -> Path:
    if not _CHUNK_TOKEN.fullmatch(str(token or "")):
        raise UploadError("invalid upload token")
    return directory_for(kind) / f".chunk-{token}{suffix}"


def store_chunk(
    kind: str,
    body: BinaryIO,
    chunk_length: int,
    *,
    offset: int,
    total: int,
    token: str,
    original_name: str,
) -> Upload | None:
    """Append one chunk of a large upload; finalise and return it on the last one.

    A file too big for a single request -- a client behind a proxy that caps request size,
    as Cloud Run caps an HTTP/1 request at 32 MiB -- arrives in order as several sub-cap
    requests that share one ``token``. Each is appended to one staging file at the ``offset``
    it declares. The declared ``total`` is checked against the slot cap on every chunk, so an
    oversized file is refused as early as a single-shot one is, not after gigabytes have
    landed. The completed file then goes through the *same* :func:`_finalize` -- the same
    hash-rename and the same magic-byte check -- as every other upload: the assembled bytes
    are validated, never trusted for having arrived in pieces.

    Returns the finished :class:`Upload` on the chunk that completes the file, else ``None``.
    Raises :class:`ChunkOutOfOrder` (before reading the body) if the chunk does not continue
    the staged file, so the caller can answer 409 and leave the connection usable.
    """
    if kind not in KINDS:
        raise UploadError(f"unknown upload kind {kind!r}; expected one of {', '.join(KINDS)}")
    suffix = suffix_for(kind, original_name)
    if total <= 0:
        raise UploadError("the upload is empty")
    cap = C.UPLOAD_MAX_BYTES[kind]
    if total > cap:
        over = -(-total // (1024 * 1024))
        raise UploadTooLarge(
            f"a {kind} upload is capped at {cap // (1024 * 1024)} MB; this one declares "
            f"{over} MB"
        )
    if chunk_length <= 0:
        raise UploadError("the chunk is empty")
    if offset < 0 or offset + chunk_length > total:
        raise UploadError("the chunk runs past the declared total size")

    target_dir = directory_for(kind)
    target_dir.mkdir(parents=True, exist_ok=True)
    staging = _chunk_staging(kind, token, suffix)
    current = staging.stat().st_size if staging.exists() else 0
    if offset != current:
        # Out of order, a duplicate, or a gap. The body is still unread, so this is the one
        # chunk failure the connection survives: name where the staged file actually sits and
        # let the client resume or restart, rather than silently leaving a hole in the file.
        raise ChunkOutOfOrder(
            f"chunk offset {offset} does not continue the staged file at {current}",
            expected_offset=current,
        )

    written = 0
    try:
        with staging.open("ab") as handle:
            while written < chunk_length:
                buf = body.read(min(_CHUNK, chunk_length - written))
                if not buf:
                    break
                written += len(buf)
                handle.write(buf)
        if written != chunk_length:
            raise UploadError(
                f"the chunk ended early: {written} bytes arrived of {chunk_length} declared"
            )
    except UploadError:
        staging.unlink(missing_ok=True)
        raise
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise UploadError(f"the upload could not be written: {exc}") from exc

    size = current + written
    if size < total:
        return None  # more chunks to come

    # The last chunk landed. Hash the assembled file in one pass -- it was written across
    # many requests, so unlike `store` there is no running digest to reuse -- then finalise.
    digest = hashlib.sha256()
    try:
        with staging.open("rb") as handle:
            for block in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(block)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise UploadError(f"the assembled upload could not be read back: {exc}") from exc
    return _finalize(kind, staging, suffix, digest.hexdigest(), size, original_name)


def _sidecar(kind: str, upload_id: str) -> Path:
    return directory_for(kind) / f"{upload_id}.json"


def _write_sidecar(upload: Upload) -> None:
    """Record what arrived, so a later request can resolve an id without a directory scan.

    The operator's own filename is the only thing here that cannot be recovered from the
    stored file itself, and it is what the interface shows them.
    """
    payload = {**upload.to_dict(), "receivedUtc": C.utc_now_iso()}
    try:
        _sidecar(upload.kind, upload.upload_id).write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
    except OSError:
        # The sidecar is a convenience. Losing it costs the original filename in the UI,
        # which is not worth failing an upload that is otherwise on disk and verified.
        pass


def resolve(kind: str, upload_id: str | None) -> Path | None:
    """Find a stored upload by id, or ``None``.

    The id is matched against the allow-listed pattern and then joined to the slot's own
    directory. It cannot escape: a value containing a separator or a leading dot fails the
    pattern, and the result is confirmed to sit inside the expected directory regardless.
    """
    if not upload_id:
        return None
    name = str(upload_id).strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) or ".." in name:
        raise UploadError(f"invalid upload id {upload_id!r}")
    directory = directory_for(kind)
    for suffix in C.UPLOAD_SUFFIXES[kind]:
        candidate = directory / f"{name}{suffix}"
        if not candidate.exists():
            continue
        # Belt and braces: the pattern above already excludes a traversal, and this
        # confirms the resolved path against the directory it must be in.
        resolved = candidate.resolve()
        if resolved.parent != directory.resolve():
            raise UploadError(f"invalid upload id {upload_id!r}")
        return resolved
    return None


def describe(kind: str, upload_id: str) -> dict[str, Any] | None:
    """What was recorded when this upload arrived, if the sidecar survives."""
    path = _sidecar(kind, upload_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def listing() -> dict[str, list[dict[str, Any]]]:
    """Every upload currently on disk, by kind, newest first."""
    out: dict[str, list[dict[str, Any]]] = {}
    for kind in KINDS:
        directory = directory_for(kind)
        found: list[dict[str, Any]] = []
        if directory.exists():
            for path in directory.glob("*.json"):
                payload = describe(kind, path.stem)
                # A sidecar whose file has been removed by hand describes nothing.
                if payload and payload.get("kind") == kind and resolve(kind, path.stem):
                    found.append(payload)
        found.sort(key=lambda item: str(item.get("receivedUtc") or ""), reverse=True)
        out[kind] = found
    return out


def clear() -> int:
    """Delete every stored upload. Returns how many files were removed.

    Exists so an operator can take their own data off the machine after a demonstration
    without needing a shell, and so the tests can start from an empty tree.
    """
    removed = 0
    for directory in (C.UPLOAD_IMAGE_DIR, C.UPLOAD_MASK_DIR, C.UPLOAD_FORCING_DIR):
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
    return removed
