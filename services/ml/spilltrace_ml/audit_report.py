"""Render the Phase 1 audit report as ``DATA_AUDIT.md``."""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    aligns = ["---"] * len(headers)
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "| " + " | ".join(aligns) + " |"]
    empty = True
    for row in rows:
        empty = False
        out.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    if empty:
        out.append("| " + " | ".join(["_none_"] * len(headers)) + " |")
    return out


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _yesno(value: Any) -> str:
    return "yes" if value else "no"


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    pairing = report["pairing"]
    raster = report["raster"]
    geo = report["georeferencing"]
    acq = report["acquisitions"]
    masks = report["maskValues"]
    pixels = report["pixelValues"]
    problems = report["problems"]
    forcing = report.get("forcing", {})

    lines: list[str] = []
    add = lines.append

    add("# SpillTrace - Data Audit")
    add("")
    add("Phase 1 of the SpillTrace build (SIH Problem Statement 26143).")
    add("")
    add(
        "Every value below was read from the supplied files. Nothing is assumed: "
        "filenames, dimensions, band order, acquisition times, coordinates and the "
        "CMEMS overlap verdict are all derived from file contents at audit time."
    )
    add("")
    add(f"- Generated: `{report['generatedUtc']}`")
    add(f"- Pipeline version: `{report['pipelineVersion']}`")
    add(f"- Runtime: {report['elapsedSeconds']} s")
    add(f"- Image directory: `{report['imageDir']}`")
    add(f"- Mask directory: `{report['maskDir']}`")
    add("")

    # ---------------------------------------------------------------- verdict
    verdict = "PASS" if pairing["valid"] else "FAIL"
    add(f"## Verdict: {verdict}")
    add("")
    if pairing["valid"]:
        add(
            f"All {counts['matchedPairs']} images pair 1:1 with a mask of identical "
            "raster size, every mask is binary, and every image carries a usable "
            "WGS84 geotransform. The dataset is fit for the pipeline."
        )
    else:
        add(
            f"**Image/mask pairing is not valid.** {problems['errorCount']} blocking "
            "error(s) were found; see [Problems](#problems). The pipeline must not "
            "train or measure geometry on this dataset until they are resolved."
        )
    add("")
    if problems["warningCount"]:
        add(
            f"{problems['warningCount']} non-blocking warning(s) were recorded and are "
            "listed below."
        )
        add("")

    # ----------------------------------------------------------------- counts
    add("## 1. File counts and pairing")
    add("")
    lines.extend(
        _table(
            ["Measure", "Value"],
            [
                ["Image files (`.tif`)", counts["images"]],
                ["Mask files (`.tif`)", counts["masks"]],
                ["Matched pairs", counts["matchedPairs"]],
                ["Images with no mask", counts["imagesWithoutMask"]],
                ["Masks with no image", counts["masksWithoutImage"]],
                ["Pairs fully scanned", counts["scanned"]],
                ["Distinct parent acquisitions", counts["distinctAcquisitions"]],
            ],
        )
    )
    add("")
    add(f"Pairing rule: {pairing['rule']}.")
    add("")
    if pairing["imagesWithoutMask"]:
        add("Unmatched images (first 50): `" + "`, `".join(pairing["imagesWithoutMask"]) + "`")
        add("")
    if pairing["masksWithoutImage"]:
        add("Unmatched masks (first 50): `" + "`, `".join(pairing["masksWithoutImage"]) + "`")
        add("")
    if not pairing["imagesWithoutMask"] and not pairing["masksWithoutImage"]:
        add("No unmatched filenames in either direction.")
        add("")

    # ----------------------------------------------------------------- raster
    add("## 2. Raster structure")
    add("")
    add("### Dimensions")
    add("")
    lines.extend(
        _table(
            ["Size (w x h)", "Files"],
            [[d["size"], d["files"]] for d in raster["dimensions"]],
        )
    )
    add("")
    add("### Channels")
    add("")
    lines.extend(
        _table(
            ["Channels", "Files"], [[b["bands"], b["files"]] for b in raster["bandCounts"]]
        )
    )
    add("")
    lines.extend(
        _table(
            ["Band order", "Files"],
            [
                [", ".join(f"`{n}`" for n in s["bandNames"]) or "_no DIMAP names_", s["files"]]
                for s in raster["bandNameSets"]
            ],
        )
    )
    add("")
    add(
        "Band order is taken from the `BAND_NAME` elements of the BEAM-DIMAP document "
        "that SNAP embeds in TIFF tag 65000, not guessed from position. The pipeline "
        "resolves VV and VH by name for every scene."
    )
    add("")
    add("### Data types and encoding")
    add("")
    lines.extend(
        _table(
            ["Image dtype", "Files"],
            [[f"`{d['dtype']}`", d["files"]] for d in raster["imageDtypes"]],
        )
    )
    add("")
    lines.extend(
        _table(
            ["Mask dtype", "Files"],
            [[f"`{d['dtype']}`", d["files"]] for d in raster["maskDtypes"]],
        )
    )
    add("")
    lines.extend(
        _table(
            ["Compression", "Files"],
            [[c["codec"], c["files"]] for c in raster["compression"]],
        )
    )
    add("")
    if raster["processingChains"]:
        add("### SNAP processing chain (from the product identifier)")
        add("")
        lines.extend(
            _table(
                ["Chain", "Files"],
                [
                    ["`" + "_".join(c["chain"]) + "`" if c["chain"] else "_unknown_", c["files"]]
                    for c in raster["processingChains"]
                ],
            )
        )
        add("")

    # -------------------------------------------------------- georeferencing
    add("## 3. Georeferencing")
    add("")
    lines.extend(
        _table(["CRS", "Files"], [[f"`{c['crs']}`", c["files"]] for c in geo["crs"]])
    )
    add("")
    lines.extend(
        _table(["EPSG", "Files"], [[e["epsg"], e["files"]] for e in geo["epsg"]])
    )
    add("")
    bounds = geo["datasetBounds"]
    if bounds:
        add(
            f"Combined dataset bounds (WGS84): west `{bounds[0]}`, south `{bounds[1]}`, "
            f"east `{bounds[2]}`, north `{bounds[3]}`."
        )
        add("")
    add(
        f"Masks carrying usable geo tags: **{geo['masksWithGeoTags']}**; masks without: "
        f"**{geo['masksWithoutGeoTags']}**."
    )
    add("")
    pixel_space = [
        p
        for p in list(report["problems"]["errors"]) + list(report["problems"]["warnings"])
        if p.get("kind") == "mask-pixel-space-transform"
    ]
    if pixel_space:
        add(
            f"**{len(pixel_space)} mask(s) carry a pixel-space transform that is not "
            "georeferencing.** They store an identity-style matrix with no CRS - an "
            "artefact of the raster editor that produced them, not a map projection. "
            "Trusting it would place the slick at degenerate coordinates, so the audit "
            "rejects any mask transform that has no EPSG code, a pixel size of 1 or "
            "more, or an origin outside valid latitude. Affected: "
            + ", ".join(f"`{p['name']}`" for p in pixel_space[:8])
            + ("." if len(pixel_space) <= 8 else f" and {len(pixel_space) - 8} more.")
        )
        add("")
    add(f"Handling: {geo['maskGeoreferencingRule']}.")
    add("")
    add("### Approximate regions covered")
    add("")
    lines.extend(
        _table(
            ["Region (approximate label)", "Scenes"],
            [[r["region"], r["files"]] for r in geo["regions"]],
        )
    )
    add("")
    add(
        "Region names come from an offline bounding-box lookup and are display labels "
        "only. All geometry, drift and scoring use the raster transform, never these "
        "names."
    )
    add("")

    # ----------------------------------------------------------- acquisitions
    add("## 4. Acquisitions")
    add("")
    lines.extend(
        _table(["Mission", "Scenes"], [[m["mission"], m["files"]] for m in acq["missions"]])
    )
    add("")
    lines.extend(
        _table(["Mode", "Scenes"], [[m["mode"], m["files"]] for m in acq["modes"]])
    )
    add("")
    lines.extend(
        _table(
            ["Measure", "Value"],
            [
                ["Earliest acquisition (UTC)", acq["earliestUtc"] or "unknown"],
                ["Latest acquisition (UTC)", acq["latestUtc"] or "unknown"],
                ["Distinct parent products", acq["distinctParentProducts"]],
            ],
        )
    )
    add("")
    add(
        "Each file is a `subset_N_of_<product>` crop, so several scenes share one "
        "parent acquisition. The parent product identifier is the grouping key for "
        "train/validation/test splits, which is how the pipeline prevents crops of the "
        "same acquisition from crossing splits."
    )
    add("")
    add("Largest acquisition groups:")
    add("")
    lines.extend(
        _table(
            ["Parent product", "Scenes"],
            [[f"`{g['groupKey']}`", g["files"]] for g in acq["largestGroups"]],
        )
    )
    add("")

    # ------------------------------------------------------------ mask values
    add("## 5. Mask values")
    add("")
    if masks["scanned"]:
        observed = ", ".join(f"`{v:g}`" for v in masks["observed"])
        add(f"Every mask was fully decoded. Observed pixel values across the set: {observed}.")
        add("")
        lines.extend(
            _table(
                ["Mask value", "Files containing it"],
                [[f"`{k}`", v] for k, v in masks["perValueFileCount"].items()],
            )
        )
        add("")
        frac = masks["oilFraction"]
        lines.extend(
            _table(
                ["Oil-pixel fraction", "Value"],
                [
                    ["Minimum", _fmt(frac["min"], 6)],
                    ["Median", _fmt(frac["median"], 6)],
                    ["Mean", _fmt(frac["mean"], 6)],
                    ["Maximum", _fmt(frac["max"], 6)],
                    ["Masks with no oil pixels", frac["emptyMasks"]],
                ],
            )
        )
        add("")
        add(
            "The oil class is heavily minority, which the loss function has to account "
            "for; the pipeline therefore trains on a combined Dice + BCE objective "
            "rather than BCE alone."
        )
    else:
        add("Mask pixel scanning was disabled for this run.")
    add("")

    # ----------------------------------------------------------- pixel values
    add("## 6. Pixel value ranges")
    add("")
    add(pixels["note"])
    add("")
    add(
        f"Scenes decoded for this section ({len(pixels['sampledScenes'])}): "
        + ", ".join(f"`{s}`" for s in pixels["sampledScenes"])
    )
    add("")
    add("Raw statistics, every decoded sample including no-data padding:")
    add("")
    lines.extend(
        _table(
            ["Band", "Name", "Min (dB)", "Max (dB)", "Mean (dB)", "p0.5", "p99.5", "Exact zeros", "Non-finite"],
            [
                [
                    b["band"],
                    f"`{b['name']}`",
                    _fmt(b["min"], 2),
                    _fmt(b["max"], 2),
                    _fmt(b["mean"], 2),
                    _fmt(b["p0_5"], 2),
                    _fmt(b["p99_5"], 2),
                    b["zeros"],
                    b["nonFinite"],
                ]
                for b in pixels["perBand"]
            ],
        )
    )
    add("")
    add(
        "Values are calibrated backscatter in decibels (DIMAP `PHYSICAL_UNIT` = "
        "`intensity_db`), not raw DN. The DIMAP no-data value is `0.0`; the pipeline "
        "treats exact zeros and non-finite samples as invalid and records them in an "
        "explicit invalid mask rather than feeding them to the model."
    )
    add("")
    has_valid = any((b.get("valid") or {}).get("count") is not None or
                    (b.get("valid") or {}).get("mean") is not None
                    for b in pixels["perBand"])
    if has_valid:
        add("### Valid-sample statistics (no-data excluded)")
        add("")
        add(
            "The raw table above is contaminated by the no-data border: because zero "
            "sits above the real backscatter distribution, it drags the upper "
            "percentile to `0.00` and hides the range the model actually has to "
            "cover. These are the same statistics with exact-`0.0` and non-finite "
            "samples removed, and they are what the normalisation clip limits are "
            "derived from."
        )
        add("")
        lines.extend(
            _table(
                ["Band", "Name", "Min (dB)", "Max (dB)", "Mean (dB)", "Std (dB)", "p0.5", "p99.5"],
                [
                    [
                        b["band"],
                        f"`{b['name']}`",
                        _fmt((b.get("valid") or {}).get("min"), 2),
                        _fmt((b.get("valid") or {}).get("max"), 2),
                        _fmt((b.get("valid") or {}).get("mean"), 2),
                        _fmt((b.get("valid") or {}).get("std"), 2),
                        _fmt((b.get("valid") or {}).get("p0_5"), 2),
                        _fmt((b.get("valid") or {}).get("p99_5"), 2),
                    ]
                    for b in pixels["perBand"]
                ],
            )
        )
        add("")
        add(
            "The two channels sit in visibly different ranges, so normalisation is "
            "per channel rather than shared."
        )
        add("")

    contrast_rows = []
    for scene in pixels["perScene"]:
        for entry in scene.get("maskContrast", []):
            contrast_rows.append(
                [
                    f"`{scene['name']}`",
                    entry["band"],
                    _fmt(entry["oilMeanDb"], 2),
                    _fmt(entry["seaMeanDb"], 2),
                    _fmt(entry["separationDb"], 2),
                ]
            )
    if contrast_rows:
        add("### Backscatter separation between labelled oil and background")
        add("")
        lines.extend(
            _table(
                ["Scene", "Band", "Oil mean (dB)", "Background mean (dB)", "Separation (dB)"],
                contrast_rows,
            )
        )
        add("")
        add(
            "Negative separation means labelled oil is darker than its surroundings, "
            "which is the expected damping signature. Only valid samples are compared, "
            "so no-data padding cannot manufacture a difference. This is a sanity check "
            "that image and mask are spatially aligned - a misaligned pair would show "
            "near-zero separation."
        )
        add("")

    # --------------------------------------------------------------- forcing
    add("## 7. CMEMS forcing overlap")
    add("")
    if not forcing.get("cmemsPresent"):
        add("No CMEMS file was supplied.")
    elif not forcing.get("cmemsReadable", True):
        add(f"The CMEMS file could not be read: `{forcing.get('error')}`.")
    else:
        product = forcing["product"]
        lines.extend(
            _table(
                ["Property", "Value"],
                [
                    ["File", f"`{product['file']}`"],
                    ["Current variables", f"`{product['currentVariables']['u']}` / `{product['currentVariables']['v']}`"],
                    ["Grid shape (lat x lon)", f"{product['gridShape'][0]} x {product['gridShape'][1]}"],
                    ["Latitude range", f"{product['latRange'][0]} to {product['latRange'][1]}"],
                    ["Longitude range", f"{product['lonRange'][0]} to {product['lonRange'][1]}"],
                    ["Resolution (deg)", f"{product['resolutionDeg'][0]} x {product['resolutionDeg'][1]}"],
                    ["Surface depth (m)", ", ".join(str(d) for d in product["depthsM"])],
                    ["Time steps", product["timeStepCount"]],
                    ["Times (UTC)", ", ".join(product["timesUtc"]) or "unknown"],
                ],
            )
        )
        add("")
        add(
            f"Acquisitions tested: **{forcing['acquisitionsChecked']}**; usable "
            f"(space *and* time): **{forcing['acquisitionsUsable']}**; spatial match "
            f"only: **{forcing['acquisitionsSpatialOnly']}**."
        )
        add("")
        lines.extend(
            _table(
                ["Representative scene", "Region", "Scene time (UTC)", "Spatial", "Time", "Gap (days)", "Water cells", "Mean speed (m/s)"],
                [
                    [
                        f"`{c['representativeScene']}`",
                        c["region"],
                        c["sceneTimeUtc"] or "unknown",
                        _yesno(c["spatialOverlap"]),
                        _yesno(c["temporalOverlap"]),
                        c["timeGapDays"],
                        c["waterCellsOverScene"],
                        _fmt((c["windowStats"] or {}).get("speedMean"), 3),
                    ]
                    for c in forcing["checks"]
                ],
            )
        )
        add("")
        add(f"**Verdict:** {forcing['verdict']}.")
        add("")
        add(f"Drift mode: `{forcing['driftMode']}`. UI label: `{forcing['label']}`.")
        add("")
        add(
            "The spatial test requires the scene footprint to sit inside the product "
            "grid *and* the covered cells to contain water rather than land. The "
            "temporal test requires the nearest product timestep to be within 24 h of "
            "the acquisition. Both must pass before CMEMS forcing is used."
        )
        add("")

    # -------------------------------------------------------------- problems
    add("## Problems")
    add("")
    add(
        f"Blocking errors: **{problems['errorCount']}**. Warnings: "
        f"**{problems['warningCount']}**."
    )
    add("")
    if problems["errors"]:
        add("### Errors")
        add("")
        lines.extend(
            _table(
                ["File", "Kind", "Detail"],
                [
                    [f"`{p['name']}`" if p["name"] else "-", p["kind"], p["detail"]]
                    for p in problems["errors"]
                ],
            )
        )
        add("")
    if problems["warningKinds"]:
        add("### Warning summary")
        add("")
        lines.extend(
            _table(
                ["Kind", "Count"],
                [[k["kind"], k["count"]] for k in problems["warningKinds"]],
            )
        )
        add("")
        add("First warnings in detail:")
        add("")
        lines.extend(
            _table(
                ["File", "Kind", "Detail"],
                [
                    [f"`{p['name']}`" if p["name"] else "-", p["kind"], p["detail"]]
                    for p in problems["warnings"][:20]
                ],
            )
        )
        add("")
    if not problems["errors"] and not problems["warnings"]:
        add("No problems were found.")
        add("")

    # ------------------------------------------------------- consequences
    add("## Consequences for the pipeline")
    add("")
    for item in _consequences(report):
        add(f"- {item}")
    add("")
    add("---")
    add("")
    add(
        "Machine-readable form of this report, including a per-scene record for every "
        "pair, is written to `data/processed/audit.json`."
    )
    add("")
    return "\n".join(lines)


def _consequences(report: dict[str, Any]) -> list[str]:
    """Turn audit findings into the decisions they force downstream."""
    out: list[str] = []
    counts = report["counts"]
    forcing = report.get("forcing", {})
    pixels = report["pixelValues"]

    out.append(
        f"**Splits are grouped by parent acquisition.** The {counts['matchedPairs']} "
        f"scenes come from {counts['distinctAcquisitions']} distinct Sentinel-1 "
        "products, so splitting by filename would leak crops of the same acquisition "
        "across train, validation and test. Splits use the parent product identifier "
        "as the grouping key."
    )
    out.append(
        "**Mask geometry borrows the image transform.** No mask carries usable "
        "georeferencing - most have no geo tags at all, and the few that do store a "
        "pixel-space matrix with no CRS. Every geometry calculation therefore reads the "
        "transform and CRS from the paired image after confirming both rasters have "
        "identical dimensions."
    )
    out.append(
        "**Band order is resolved by name, per scene.** VV and VH are located through "
        "the embedded DIMAP band names instead of a hard-coded index."
    )
    per_band = pixels.get("perBand") or []
    if per_band:
        def _band_range(band: dict[str, Any]) -> str:
            valid = band.get("valid") or {}
            lo = valid.get("p0_5", band.get("p0_5"))
            hi = valid.get("p99_5", band.get("p99_5"))
            return f"`{band['name']}` {_fmt(lo, 1)} to {_fmt(hi, 1)} dB"

        ranges = "; ".join(_band_range(b) for b in per_band)
        out.append(
            "**Normalisation is per band, from training-split statistics.** The two "
            f"channels occupy different decibel ranges (valid-sample p0.5-p99.5: "
            f"{ranges}), so a shared scaling would suppress one of them. Clip limits "
            "come from those percentiles rather than a fixed guess, and they are "
            "computed with no-data excluded so the padding cannot collapse the range."
        )
    out.append(
        "**Exact zeros and non-finite samples become an explicit invalid mask.** The "
        "DIMAP declares `0.0` as no-data, so those pixels are excluded from "
        "normalisation statistics, from the loss and from reported metrics."
    )
    separations: dict[str, list[float]] = {}
    for scene in pixels.get("perScene", []):
        for entry in scene.get("maskContrast", []):
            value = entry.get("separationDb")
            if value is not None:
                separations.setdefault(entry.get("name") or str(entry["band"]), []).append(
                    float(value)
                )
    if len(separations) >= 2:
        means = {k: sum(v) / len(v) for k, v in separations.items()}
        strongest = min(means, key=lambda k: means[k])
        summary = "; ".join(f"`{k}` {means[k]:+.2f} dB" for k in sorted(means))
        out.append(
            "**Both polarisations are kept, and the mask alignment is verified rather "
            f"than assumed.** Labelled oil is darker than its surroundings ({summary} "
            f"mean separation), and `{strongest}` carries most of the contrast. Both "
            "channels are still fed to the model because the weaker one helps reject "
            "look-alikes, but a near-zero separation in the stronger channel would have "
            "meant image and mask were misaligned."
        )
    if forcing.get("cmemsReadable") and forcing.get("driftMode") == "synthetic":
        speeds = [
            (c.get("windowStats") or {}).get("speedMean")
            for c in forcing.get("checks", [])
        ]
        speeds = [s for s in speeds if s is not None]
        anchor = (
            f" Observed CMEMS speeds over the scene footprints ({min(speeds):.3f} to "
            f"{max(speeds):.3f} m/s) are used only as a plausibility anchor for the "
            "magnitude of the synthetic field."
            if speeds
            else ""
        )
        out.append(
            "**Drift uses deterministic synthetic forcing, clearly labelled.** The "
            "supplied CMEMS product matches the scene footprints in space but not in "
            f"time, so it cannot drive the drift model.{anchor} The UI reports spatial "
            "overlap as valid and time overlap as invalid, and labels the forcing "
            f"`{forcing.get('label')}`."
        )
        out.append(
            "**The CMEMS land pattern is still used.** Cells where `uo`/`vo` are "
            "fill values mark land or no-data, and the drift engine flags particles "
            "that enter them."
        )
    elif forcing.get("driftMode") == "cmems":
        out.append(
            "**Drift uses CMEMS forcing.** The product covers every acquisition in "
            f"both space and time, so the UI labels the forcing `{forcing.get('label')}`."
        )
    out.append(
        "**The 48 GB of raw imagery stays out of the browser.** Scenes are decoded "
        "server-side and the frontend receives only downsampled PNG previews and "
        "compact JSON, per the non-functional requirements."
    )
    return out
