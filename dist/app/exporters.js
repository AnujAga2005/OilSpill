/** Downloads and printing.
 *
 * The CSV builder deliberately mirrors `spilltrace_drift.scoring.candidates_csv` column
 * for column. Offline there is no server to generate it, and a download that changed shape
 * depending on whether the API happened to be up would be worse than no download at all.
 *
 * Every export carries the provenance labels - synthetic AIS, drift forcing mode, the
 * candidate wording - because a file that leaves the interface loses the banners that
 * qualify it, and the qualification is the part that must not get lost.
 */

import * as F from "./format.js";

/** Column order copied from the API's CSV writer. */
const CSV_HEADER = [
  "rank",
  "name",
  "mmsi",
  "vessel_type",
  "total_score",
  "score_max",
  "distance",
  "time",
  "trajectory",
  "behaviour",
  "type",
  "data_quality",
  "closest_approach_km",
  "closest_approach_utc",
  "reports_in_window",
  "reports_near_envelope",
  "status",
  "ais_mode",
];

function cell(value) {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Build the candidates CSV from a case document, byte-compatible with the API's. */
export function candidatesCsv(caseDoc) {
  const attribution = caseDoc?.attribution || {};
  const candidates = attribution.candidates || caseDoc?.vessels || [];
  const aisLabel = attribution.aisLabel || caseDoc?.ais?.label || "";
  const lines = [CSV_HEADER.join(",")];
  for (const entry of candidates) {
    const components = entry.components || {};
    const evidence = entry.evidence || {};
    lines.push(
      [
        entry.rank,
        entry.name,
        entry.mmsi,
        entry.type,
        entry.score,
        entry.scoreMax,
        components.Distance,
        components.Time,
        components.Trajectory,
        components.Behaviour,
        components.Type,
        components["Data quality"],
        evidence.closestApproachKm,
        evidence.closestApproachUtc,
        evidence.reportsInWindow,
        evidence.reportsNearEnvelope,
        entry.status,
        aisLabel,
      ]
        .map(cell)
        .join(","),
    );
  }
  return `${lines.join("\n")}\n`;
}

/** The slick table as CSV: one row per detected feature. */
export function slicksCsv(caseDoc) {
  const header = [
    "id",
    "area_km2",
    "perimeter_m",
    "length_m",
    "width_m",
    "elongation",
    "compactness",
    "orientation_deg_from_north",
    "centroid_lon",
    "centroid_lat",
    "mean_probability",
    "median_probability",
    "confidence",
    "touches_scene_edge",
    "geometry_valid",
    "quality_flags",
    "detection_source",
  ];
  const source = caseDoc?.provenance?.detectionLabel || caseDoc?.detection?.source || "";
  const lines = [header.join(",")];
  for (const slick of caseDoc?.geometry?.slicks || []) {
    lines.push(
      [
        slick.id,
        slick.areaKm2,
        slick.perimeterM,
        slick.lengthM,
        slick.widthM,
        slick.elongation,
        slick.compactness,
        slick.orientationDegFromNorth,
        slick.centroid?.[0],
        slick.centroid?.[1],
        slick.meanProbability,
        slick.medianProbability,
        slick.confidence,
        slick.touchesSceneEdge,
        slick.geometryValid,
        (slick.qualityFlags || []).join("; "),
        source,
      ]
        .map(cell)
        .join(","),
    );
  }
  return `${lines.join("\n")}\n`;
}

/** The drift timeline as CSV: one row per integration step. */
export function driftCsv(caseDoc, direction = "backward") {
  const run = caseDoc?.drift?.[direction];
  const header = [
    "step",
    "hours_from_observation",
    "time_utc",
    "centroid_lon",
    "centroid_lat",
    "spread_p50_km",
    "spread_p90_km",
    "spread_max_km",
    "displacement_mean_km",
    "displacement_max_km",
    "active_particles",
    "beached_particles",
    "exited_particles",
    "forcing_mode",
  ];
  const mode = run?.forcing?.label || caseDoc?.forcing?.label || "";
  const lines = [header.join(",")];
  for (const step of run?.timeline || []) {
    lines.push(
      [
        step.stepIndex,
        step.hoursFromObservation,
        step.timeUtc,
        step.centroid?.[0],
        step.centroid?.[1],
        step.spreadP50Km,
        step.spreadP90Km,
        step.spreadMaxKm,
        step.displacementMeanKm,
        step.displacementMaxKm,
        step.activeParticles,
        step.beachedParticles,
        step.exitedParticles,
        mode,
      ]
        .map(cell)
        .join(","),
    );
  }
  return `${lines.join("\n")}\n`;
}

// -- delivery ---------------------------------------------------------------

function deliver(filename, text, mime) {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke on the next frame: revoking synchronously races the download in Safari.
  requestAnimationFrame(() => URL.revokeObjectURL(url));
}

export function downloadCsv(filename, text) {
  deliver(filename, text, "text/csv");
}

export function downloadJson(filename, value) {
  deliver(filename, JSON.stringify(value, null, 2), "application/json");
}

/** Name a download after the case and the day it was computed. */
export function exportName(caseDoc, kind, extension) {
  const scene = caseDoc?.scene?.name || caseDoc?.id || "case";
  const day = F.utcDate(caseDoc?.generatedUtc) || "";
  return `spilltrace-${F.slug(scene)}-${kind}-${day}.${extension}`;
}

export function printPage() {
  window.print();
}
