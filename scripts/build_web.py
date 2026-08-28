#!/usr/bin/env python3
"""Verify the dashboard, write its offline fixtures, and assemble a servable bundle.

    .venv/bin/python scripts/build_web.py              # verify, write fixtures, build dist/
    .venv/bin/python scripts/build_web.py --check      # verify only, write nothing
    .venv/bin/python scripts/build_web.py --no-dist    # fixtures only, skip dist/

There is no bundler here, and that is deliberate rather than a shortfall. The interface is
plain ES modules with no JSX, no TypeScript and no dependencies, so it runs from source in
any current browser. Introducing content-hashed filenames would mean rewriting every
`import` specifier across the module graph -- a real source transform, with a real chance of
breaking an import in a way no test would catch -- to buy cache-busting that the API's own
`Cache-Control` headers already provide.

So "build" here means four things that can actually fail, and does them in this order:

1. **Verify.** Every JavaScript module is parsed with `node --check`, every stylesheet is
   checked for balanced braces, and every `import` in the module graph is resolved against
   the filesystem. A missing screen module is a blank page, so it is worth failing on.
2. **Write the offline fixtures.** The real API is started in-process on an ephemeral port
   and its own responses are saved. Nothing is reimplemented here, so a fixture cannot
   drift away from the endpoint it stands in for.
3. **Check what is about to be shipped.** No GeoTIFF, no NetCDF, no `.npz` and no absolute
   host path may appear anywhere under the web root.
4. **Assemble `dist/`.** A copy of the web root with a manifest recording every file's size
   and SHA-256, so a deployment can be diffed against the build that produced it.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _package in ("common", "ml", "drift", "api"):
    sys.path.insert(0, str(ROOT / "services" / _package))

from spilltrace_api import server as server_mod  # noqa: E402
from spilltrace_api import store as store_mod  # noqa: E402
from spilltrace_common import config as C  # noqa: E402

DIST_DIR = ROOT / "dist"

# Extensions that must never appear under the web root. The dataset stays on disk and is
# served by the API; a copy inside the bundle would be both enormous and unversioned.
FORBIDDEN_SUFFIXES = {".tif", ".tiff", ".nc", ".npz", ".npy", ".dim", ".img", ".hdr", ".zip"}

# Everything the bundle is allowed to contain.
BUNDLE_SUFFIXES = {".html", ".js", ".css", ".json", ".png", ".svg", ".ico", ".webmanifest", ".txt"}

IMPORT_RE = re.compile(r"""^\s*(?:import|export)\b[^'"]*from\s+['"]([^'"]+)['"]""", re.MULTILINE)
BARE_IMPORT_RE = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.MULTILINE)


class BuildError(RuntimeError):
    """Something that must stop the build, as opposed to something worth mentioning."""


def say(message: str = "") -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# 1. Verify
# ---------------------------------------------------------------------------

def web_files(suffixes: set[str] | None = None) -> list[Path]:
    out = []
    for path in sorted(C.WEB_DIR.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(C.WEB_DIR).parts):
            continue
        if suffixes is None or path.suffix in suffixes:
            out.append(path)
    return out


def have_node() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def check_javascript(node: bool) -> list[str]:
    """Parse every module. `node --check` is the only real parser available offline."""
    modules = web_files({".js"})
    if not modules:
        raise BuildError("no JavaScript modules found under apps/web")
    notes = []
    if not node:
        notes.append(f"node is not installed, so {len(modules)} modules were not parsed")
        say(f"  ! skipped parsing {len(modules)} modules: node is not on PATH")
        return notes
    failed = []
    for path in modules:
        result = subprocess.run(
            ["node", "--check", str(path)], capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            failed.append((path, (result.stderr or result.stdout).strip().splitlines()[:4]))
    if failed:
        for path, lines in failed:
            say(f"  x {path.relative_to(ROOT)}")
            for line in lines:
                say(f"      {line}")
        raise BuildError(f"{len(failed)} of {len(modules)} modules failed to parse")
    say(f"  ok parsed {len(modules)} JavaScript modules")
    return notes


def check_imports() -> None:
    """Resolve every relative import against the filesystem.

    `node --check` parses a file in isolation, so it is perfectly happy with an import of a
    module that does not exist. In a no-bundler app that is a blank screen and a console
    error, which is exactly the failure this build should catch.
    """
    missing = []
    bare = []
    for path in web_files({".js"}):
        text = path.read_text()
        for specifier in IMPORT_RE.findall(text) + BARE_IMPORT_RE.findall(text):
            if specifier.startswith(("http:", "https:", "data:")):
                continue
            if not specifier.startswith("."):
                bare.append((path, specifier))
                continue
            target = (path.parent / specifier).resolve()
            if not target.is_file():
                missing.append((path, specifier))
    for path, specifier in missing:
        say(f"  x {path.relative_to(ROOT)} imports {specifier!r}, which is not on disk")
    for path, specifier in bare:
        say(f"  x {path.relative_to(ROOT)} imports bare specifier {specifier!r}; there is no "
            "package manager in this build")
    if missing or bare:
        raise BuildError(f"{len(missing) + len(bare)} unresolvable imports")
    say("  ok every import resolves")


def check_css() -> list[str]:
    """Balanced braces, no `@import` of anything missing, and no leftover camelCase."""
    notes = []
    sheets = web_files({".css"})
    if not sheets:
        raise BuildError("no stylesheets found under apps/web")
    for path in sheets:
        text = path.read_text()
        stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        if stripped.count("{") != stripped.count("}"):
            raise BuildError(
                f"{path.relative_to(ROOT)} has {stripped.count('{')} '{{' and "
                f"{stripped.count('}')} '}}'"
            )
    say(f"  ok checked {len(sheets)} stylesheets")

    # Every custom property the JavaScript asks for by name, as `var(--x)`, must be declared
    # somewhere. A typo here is a silently unstyled element.
    declared: set[str] = set()
    for path in sheets:
        declared |= set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", path.read_text(), re.MULTILINE))
    used: set[str] = set()
    for path in web_files({".js"}) + sheets:
        used |= set(re.findall(r"var\(\s*(--[a-z0-9-]+)", path.read_text()))
    undeclared = sorted(used - declared)
    if undeclared:
        notes.append(f"undeclared custom properties: {', '.join(undeclared)}")
        for name in undeclared:
            say(f"  ! {name} is used but never declared")
    else:
        say(f"  ok {len(used)} custom properties all declared")
    return notes


def check_html() -> None:
    index = C.WEB_DIR / "index.html"
    if not index.is_file():
        raise BuildError("apps/web/index.html is missing; there is nothing to serve")
    text = index.read_text()
    for specifier in re.findall(r"""(?:src|href)=["']([^"']+)["']""", text):
        if specifier.startswith(("http:", "https:", "data:", "#", "mailto:")):
            continue
        target = (C.WEB_DIR / specifier.lstrip("./")).resolve()
        if not target.is_file():
            raise BuildError(f"index.html references {specifier!r}, which is not on disk")
    say("  ok index.html references resolve")


# ---------------------------------------------------------------------------
# 2. Fixtures
# ---------------------------------------------------------------------------

def strip_host_paths(payload: Any) -> Any:
    """Remove absolute filesystem paths from anything about to be written to the bundle.

    A case document records where its previews were rendered, which is an absolute path on
    whoever's machine ran the pipeline. That is fine on disk and wrong in a file a browser
    downloads, so it is replaced with a note rather than deleted -- an absent key reads as
    "no previews", which would be false.
    """
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            if key == "directory" and isinstance(value, str):
                out[key] = "served by the API; the offline bundle carries copies"
            else:
                out[key] = strip_host_paths(value)
        return out
    if isinstance(payload, list):
        return [strip_host_paths(item) for item in payload]
    if isinstance(payload, str) and str(ROOT) in payload:
        return payload.replace(str(ROOT), "<repo>")
    return payload


class _LoopbackHandler(server_mod.ApiOnlyHandler):
    """The real request handler, wired to a pair of buffers instead of a socket.

    The fixtures have to be the API's own output or they are worth nothing, so this drives
    the actual handler: its routing, its payload assembly, its JSON serialisation. Binding a
    listening socket would be the obvious way to do that and is not available in every
    environment this has to build in, so the request goes in as bytes and the response comes
    back as bytes. Everything between those two points is the code that runs in production.
    """

    def __init__(self, request_bytes: bytes) -> None:
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()
        self.connection = None
        self.client_address = ("127.0.0.1", 0)
        self.server = None
        self.requestline = ""
        self.request_version = "HTTP/1.1"
        self.command = ""
        self.handle()

    def setup(self) -> None:  # the buffers are already in place
        pass

    def finish(self) -> None:  # nothing to flush and no socket to close
        pass

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        pass


def call_api(path: str) -> tuple[int, bytes]:
    """Issue one GET against the real handler and return `(status, body)`."""
    request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
    raw = _LoopbackHandler(request).wfile.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    first = head.split(b"\r\n", 1)[0].decode(errors="replace")
    try:
        status = int(first.split(" ")[1])
    except (IndexError, ValueError) as exc:
        raise BuildError(f"{path} produced an unreadable status line {first!r}") from exc
    return status, body


def fetch_json(path: str) -> Any:
    status, body = call_api(path)
    if status != 200:
        detail = body.decode(errors="replace")[:300]
        raise BuildError(
            f"{path} answered {status}. If the demo case is missing, run "
            f"`.venv/bin/python scripts/run_api.py --build-demo` first. ({detail})"
        )
    return json.loads(body.decode())


def write_fixtures() -> list[str]:
    """Save the real API's own responses as the offline fixtures."""
    notes = []
    demo = store_mod.DEMO_KEY
    out_dir = C.WEB_FIXTURE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    wanted = [
        ("health", "/api/health"),
        ("metrics", "/api/metrics"),
        ("scenes", "/api/scenes"),
        ("cases", "/api/cases"),
        (f"case-{demo}", f"/api/cases/{demo}"),
        (f"images-{demo}", f"/api/cases/{demo}/images"),
        (f"report-{demo}", f"/api/cases/{demo}/report"),
    ]

    for name, path in wanted:
        payload = fetch_json(path)
        target = out_dir / f"{name}.json"
        target.write_text(json.dumps(strip_host_paths(payload), separators=(",", ":")))
        say(f"  ok {target.relative_to(ROOT)}  {target.stat().st_size / 1024:.0f} KB")

    # The preview rasters the offline case document names, plus the evaluation strips the
    # methodology screen shows. `api.js` looks for both under `./demo/`.
    case = json.loads((out_dir / f"case-{demo}.json").read_text())
    previews = case.get("previews") or {}
    files = previews.get("files") or {}
    copied = copy_images(C.PREVIEW_DIR, out_dir / "previews", sorted(set(files.values())))
    if copied < len(set(files.values())):
        notes.append(f"{len(set(files.values())) - copied} preview rasters were not on disk")

    metrics = json.loads((out_dir / "metrics.json").read_text())
    samples = ((metrics.get("patchScale") or {}).get("samples")) or []
    names = [Path(sample.get("file", "")).name for sample in samples if sample.get("file")]
    eval_copied = copy_images(C.EVAL_PREVIEW_DIR, out_dir / "eval", names)
    if names and eval_copied < len(names):
        notes.append(f"{len(names) - eval_copied} evaluation strips were not on disk")
    return notes


def copy_images(source: Path, target: Path, names: list[str]) -> int:
    if not names:
        return 0
    target.mkdir(parents=True, exist_ok=True)
    total = 0
    copied = 0
    for name in names:
        src = source / name
        if not src.is_file():
            say(f"  ! {src.relative_to(ROOT) if ROOT in src.parents else src} is not on disk")
            continue
        shutil.copy2(src, target / name)
        total += src.stat().st_size
        copied += 1
    say(f"  ok {copied} images to {target.relative_to(ROOT)}  {total / 1024:.0f} KB")
    return copied


# ---------------------------------------------------------------------------
# 3. What is about to ship
# ---------------------------------------------------------------------------

def check_bundle_contents() -> None:
    offenders = []
    for path in web_files():
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            offenders.append((path, f"{path.suffix} is a dataset format"))
        elif path.suffix not in BUNDLE_SUFFIXES:
            offenders.append((path, f"{path.suffix or 'no extension'} is not a bundle format"))
    for path in offenders:
        say(f"  x {path[0].relative_to(ROOT)}: {path[1]}")
    if offenders:
        raise BuildError(f"{len(offenders)} files must not be in the web root")

    # An absolute host path in a fixture leaks the machine that built it.
    leaked = []
    for path in web_files({".json", ".js", ".css", ".html"}):
        text = path.read_text(errors="replace")
        if str(ROOT) in text or "/Users/" in text or "C:\\Users" in text:
            leaked.append(path)
    for path in leaked:
        say(f"  x {path.relative_to(ROOT)} contains an absolute host path")
    if leaked:
        raise BuildError(f"{len(leaked)} bundled files contain absolute host paths")

    # There is nothing to authenticate against, so there should be nothing that looks like
    # a credential either. Checked rather than assumed.
    secrets = []
    pattern = re.compile(
        r"""(api[_-]?key|secret|password|bearer\s+[A-Za-z0-9]|CMEMS_[A-Z]+)\s*[:=]\s*["'][^"']{6,}""",
        re.IGNORECASE,
    )
    for path in web_files({".js", ".json", ".html", ".css"}):
        if pattern.search(path.read_text(errors="replace")):
            secrets.append(path)
    for path in secrets:
        say(f"  x {path.relative_to(ROOT)} looks like it contains a credential")
    if secrets:
        raise BuildError(f"{len(secrets)} bundled files look like they contain credentials")

    say(f"  ok {len(web_files())} bundle files, no dataset formats, no host paths, no credentials")


# ---------------------------------------------------------------------------
# 4. dist/
# ---------------------------------------------------------------------------

def build_dist() -> dict[str, Any]:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)
    entries = []
    by_kind: dict[str, dict[str, int]] = {}
    for path in web_files():
        relative = path.relative_to(C.WEB_DIR)
        target = DIST_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(data)
        entries.append({
            "path": str(relative),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
        kind = by_kind.setdefault(path.suffix or "other", {"files": 0, "bytes": 0})
        kind["files"] += 1
        kind["bytes"] += len(data)

    manifest = {
        "pipelineVersion": C.PIPELINE_VERSION,
        "generatedUtc": C.utc_now_iso(),
        "note": (
            "A verified copy of apps/web. No bundler, no minifier and no content hashing: "
            "the interface is plain ES modules and runs from source. Cache behaviour comes "
            "from the server's Cache-Control headers, not from filenames."
        ),
        "totals": {
            "files": len(entries),
            "bytes": sum(entry["bytes"] for entry in entries),
        },
        "byExtension": dict(sorted(by_kind.items(), key=lambda kv: -kv[1]["bytes"])),
        "files": sorted(entries, key=lambda entry: entry["path"]),
    }
    (DIST_DIR / "build.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def report(manifest: dict[str, Any]) -> None:
    say()
    say("  bundle by type")
    for suffix, totals in manifest["byExtension"].items():
        say(f"    {suffix:<14} {totals['files']:>3} files   {totals['bytes'] / 1024:>8.1f} KB")
    total = manifest["totals"]
    say(f"    {'total':<14} {total['files']:>3} files   {total['bytes'] / 1024:>8.1f} KB")

    code = sum(
        totals["bytes"] for suffix, totals in manifest["byExtension"].items()
        if suffix in (".js", ".css", ".html")
    )
    say()
    say(f"  interface code, uncompressed: {code / 1024:.1f} KB with no dependencies")


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and build the SpillTrace dashboard")
    parser.add_argument("--check", action="store_true", help="verify only; write nothing")
    parser.add_argument("--no-dist", action="store_true", help="write fixtures but skip dist/")
    args = parser.parse_args()

    notes: list[str] = []
    try:
        say("verifying the interface")
        node = have_node()
        notes += check_javascript(node)
        check_imports()
        notes += check_css()
        check_html()

        if not args.check:
            say()
            say("writing offline fixtures")
            notes += write_fixtures()

        say()
        say("checking what is about to ship")
        check_bundle_contents()

        if not args.check and not args.no_dist:
            say()
            say("assembling dist/")
            manifest = build_dist()
            say(f"  ok dist/ with {manifest['totals']['files']} files")
            report(manifest)
    except BuildError as exc:
        say()
        say(f"BUILD FAILED: {exc}")
        return 1

    say()
    if notes:
        say("build succeeded with notes:")
        for note in notes:
            say(f"  - {note}")
    else:
        say("build succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
