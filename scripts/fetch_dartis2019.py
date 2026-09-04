"""Fetch the DARTIS 2019 oil-slick / look-alike patch dataset from PANGAEA.

PANGAEA publishes doi:10.1594/PANGAEA.980773 as a tab-delimited index plus one
JPEG and one XML per patch, each served individually — there is no archive to
pull. This script turns that index into a few thousand polite sequential
requests, skipping anything already on disk so an interrupted run resumes
where it stopped rather than starting over.

The dataset earns its place here as a *negative* set: 2290 of its patches are
confusable phenomena with no oil in them, against 1365 patches holding 3225
annotated oil objects. SpillTrace has never been shown a labelled look-alike,
so this is what a measured false-positive rate would be computed from.

Two properties matter before using it:

  * Labels are per-object bounding boxes, not pixel masks. This cannot retrain
    the U-Net. It can score look-alike rejection and train a patch classifier.
  * The imagery is 8-bit JPEG, so calibrated dB backscatter is gone. Fine for a
    CNN on normalised input; not usable for quantitative radiometry.

Licence CC-BY-4.0 — cite Yang & Singha (2025), Earth Syst. Sci. Data 17,
6807-6837, doi:10.5194/essd-17-6807-2025.

Usage:
    # 0. Save the index first. On the PANGAEA page, "Download dataset as
    #    tab-delimited text" with UTF-8. It arrives named DARTIS_2019.tab —
    #    that is tab-delimited *text*, not a MapInfo TAB. Move it into
    #      data/raw/dartis2019/
    #    and this script will find it whatever it is called.
    #
    # 1. Read the index without downloading anything, and check its counts
    #    against the paper's 1365 / 2290:
    .venv/bin/python scripts/fetch_dartis2019.py --manifest
    #
    # 2. Smoke-test the transfer on ten patches before committing to all of it:
    .venv/bin/python scripts/fetch_dartis2019.py --limit 10
    #
    # 3. The full run. Expect tens of minutes; safe to interrupt and re-run.
    .venv/bin/python scripts/fetch_dartis2019.py
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "dartis2019"
BASE_URL = "https://download.pangaea.de/dataset/980773/files/"

# The README defines the XML schema and the subset semantics; fetch both, they are
# small and the dataset is hard to interpret without them.
REFERENCE_FILES = {
    "README.pdf": "https://download.pangaea.de/reference/132854/attachments/README.pdf",
    "Metadata.txt": "https://download.pangaea.de/reference/132855/attachments/Metadata.txt",
}

SUBSETS = {
    "oc": "oil, coastal",
    "ow": "oil, open water",
    "nc": "no oil, coastal",
    "nw": "no oil, open water",
}

USER_AGENT = "SpillTrace-dataset-fetch/1.0 (academic use; CC-BY-4.0 attribution honoured)"


def resolve_index(path: Path) -> Path:
    """Accept the file PANGAEA actually hands you.

    Its export arrives as `DARTIS_2019.tab` — tab-delimited *text*, despite an
    extension that collides with MapInfo TAB, a GIS vector format. Do not try to
    open it with geopandas or ogr; it is a spreadsheet, not geometry. When the
    requested path is absent, fall back to a single .tab/.tsv in the data dir.
    """
    if path.is_file():
        return path
    candidates = sorted(RAW_DIR.glob("*.tab")) + sorted(RAW_DIR.glob("*.tsv"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        listing = "\n".join(f"  {c.name}" for c in candidates)
        raise SystemExit(f"several candidate index files in {RAW_DIR}:\n{listing}\n"
                         "Pass one explicitly with --index.")
    raise SystemExit(
        f"index not found: {path}\n"
        f"Download it from the PANGAEA page — 'Download dataset as tab-delimited\n"
        f"text', encoding UTF-8 — and move it into {RAW_DIR}\n"
        f"(the file arrives named DARTIS_2019.tab; the name does not matter)."
    )


def read_index(path: Path) -> tuple[list[str], list[list[str]]]:
    """Parse a PANGAEA tab-delimited export into (header, rows).

    PANGAEA prefixes the real header line with a citation and parameter block
    delimited by `/*` and `*/`. Skip to the first line after the closing
    delimiter. Some exports omit the block, so fall back to line 0.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.strip() == "*/":
            start = i + 1
            break
    header = lines[start].split("\t")
    rows = []
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        cells = line.split("\t")
        cells += [""] * (len(header) - len(cells))
        rows.append(cells)
    return header, rows


def find_column(header: list[str], needle: str) -> int:
    """Locate a column by substring, because the headers are verbose.

    The JPEG column is published as `IMAGE (jpg_file)` and the annotation column
    as `Binary (xml_file)`, so matching on the bare name would fail.
    """
    for i, name in enumerate(header):
        if needle in name:
            return i
    raise SystemExit(f"column containing {needle!r} not found in: {header}")


def collapse_to_patches(header: list[str], rows: list[list[str]]) -> list[tuple[str, str, str]]:
    """Collapse the per-object index into one entry per patch.

    A patch holding three annotated oil objects appears as three consecutive
    rows sharing one jpg/xml pair, so the row count is the *object* count, not
    the file count. Dedupe on the JPEG name and keep the index's own order.

    Returns (subset, jpg_name, xml_name) triples.
    """
    subset_i = find_column(header, "subset")
    jpg_i = find_column(header, "jpg_file")
    xml_i = find_column(header, "xml_file")

    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for cells in rows:
        jpg = cells[jpg_i].strip()
        if not jpg or jpg in seen:
            continue
        seen.add(jpg)
        subset = cells[subset_i].strip().split(":")[0].strip() or jpg.split("-")[0]
        out.append((subset, jpg, cells[xml_i].strip()))
    return out


def fetch(url: str, dest: Path, retries: int = 3, timeout: float = 60.0) -> int:
    """Download one file. Returns bytes written, or 0 if it was already present.

    Writes to a `.part` file and renames on success so an interrupted transfer
    never leaves a truncated JPEG that a later run would mistake for complete.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            if not payload:
                raise OSError("empty response body")
            tmp.write_bytes(payload)
            tmp.replace(dest)
            return len(payload)
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            time.sleep(2.0 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    raise OSError(f"{url}: {last}")


def human(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024.0
    return f"{nbytes:.1f} GB"


def report_manifest(entries: list[tuple[str, str, str]], rows: int) -> None:
    counts: dict[str, int] = {}
    for subset, _, _ in entries:
        counts[subset] = counts.get(subset, 0) + 1
    print(f"index rows (annotated objects): {rows}")
    print(f"unique patches:                 {len(entries)}")
    for key, label in SUBSETS.items():
        print(f"  {key}  {label:<18} {counts.get(key, 0):>5}")
    unknown = sorted(set(counts) - set(SUBSETS))
    for key in unknown:
        print(f"  {key}  (unrecognised)     {counts[key]:>5}")
    oil = counts.get("oc", 0) + counts.get("ow", 0)
    no_oil = counts.get("nc", 0) + counts.get("nw", 0)
    print(f"oil patches: {oil}   no-oil (look-alike) patches: {no_oil}")
    print("paper states 1365 oil / 2290 no-oil — a mismatch means the index is partial.")
    print(f"files to transfer: {len(entries) * 2} (one JPEG and one XML per patch)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", type=Path, default=RAW_DIR / "index.tsv",
                        help="the tab-delimited export from PANGAEA")
    parser.add_argument("--out", type=Path, default=RAW_DIR, help="destination directory")
    parser.add_argument("--manifest", action="store_true",
                        help="report what the index contains and exit without downloading")
    parser.add_argument("--limit", type=int, default=0, help="stop after N patches (0 = all)")
    parser.add_argument("--subset", default="", help="comma-separated subsets, e.g. nc,nw")
    parser.add_argument("--delay", type=float, default=0.25,
                        help="seconds between requests; PANGAEA is a public archive, stay polite")
    parser.add_argument("--skip-xml", action="store_true", help="images only")
    args = parser.parse_args(argv)

    header, rows = read_index(resolve_index(args.index))
    entries = collapse_to_patches(header, rows)

    if args.subset:
        wanted = {s.strip() for s in args.subset.split(",") if s.strip()}
        entries = [e for e in entries if e[0] in wanted]

    if args.manifest:
        report_manifest(entries, len(rows))
        return 0

    if args.limit > 0:
        entries = entries[: args.limit]

    images = args.out / "images"
    annotations = args.out / "annotations"
    downloaded = skipped = failed = 0
    total_bytes = 0
    failures: list[str] = []

    for name, url in REFERENCE_FILES.items():
        try:
            total_bytes += fetch(url, args.out / name)
        except OSError as exc:
            print(f"  reference file {name} failed: {exc}", file=sys.stderr)

    for i, (_subset, jpg, xml) in enumerate(entries, start=1):
        targets = [(jpg, images / jpg)]
        if xml and not args.skip_xml:
            targets.append((xml, annotations / xml))
        for remote, dest in targets:
            try:
                written = fetch(BASE_URL + remote, dest)
            except OSError as exc:
                failed += 1
                failures.append(remote)
                print(f"  FAILED {remote}: {exc}", file=sys.stderr)
                continue
            if written:
                downloaded += 1
                total_bytes += written
            else:
                skipped += 1
        if i % 100 == 0 or i == len(entries):
            print(f"  {i}/{len(entries)} patches — {downloaded} new, {skipped} already present, "
                  f"{failed} failed, {human(total_bytes)} transferred", flush=True)
        time.sleep(args.delay)

    print(f"\ndone: {downloaded} files downloaded, {skipped} skipped, {failed} failed, "
          f"{human(total_bytes)} transferred")
    print(f"images:      {images}")
    print(f"annotations: {annotations}")
    if failures:
        print(f"\n{len(failures)} transfers failed. Re-run the same command to retry only those.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
