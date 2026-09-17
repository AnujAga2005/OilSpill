/** Map layer builders: one place that turns a case document into map primitives.
 *
 * Four screens draw the same geometry at different levels of detail, and the colour of a
 * thing on the map is a claim about where it came from. Keeping the translation here means
 * the slick is amber on every screen, the supplied reference mask is blue on every screen,
 * and synthetic tracks are yellow on every screen, because there is exactly one function
 * that decides.
 *
 * Canvas needs literal colours - `strokeStyle = "var(--oil)"` silently draws black - so the
 * palette is duplicated from `tokens.css` here, and nowhere else.
 */

import * as api from "./api.js";

export const C = {
  oil: "#ff8a4c",
  oilFill: "rgba(255, 138, 76, 0.22)",
  oilFaint: "rgba(255, 138, 76, 0.10)",
  // A region the look-alike screen puts on the other side of the line. Neutral steel on
  // purpose: "not oil" should not read as a second kind of finding, and every other hue on
  // the locator already means something.
  lookalike: "#8ea2b8",
  lookalikeFill: "rgba(142, 162, 184, 0.13)",
  reference: "#5ec8ff",
  referenceFill: "rgba(94, 200, 255, 0.16)",
  agree: "#6ee7b7",
  drift: "#a78bfa",
  driftFill: "rgba(167, 139, 250, 0.11)",
  driftFaint: "rgba(167, 139, 250, 0.30)",
  synthetic: "#fcd34d",
  syntheticFaint: "rgba(252, 211, 77, 0.34)",
  danger: "#fb7185",
  land: "rgba(255, 255, 255, 0.11)",
  landLine: "rgba(255, 255, 255, 0.16)",
  neutral: "rgba(242, 244, 247, 0.55)",
};

/** Draw order, lowest first. Named so a screen can slot something between two layers. */
export const Z = {
  land: 0,
  particles: 1,
  corridor: 2,
  envelope: 3,
  vesselTrack: 4,
  slick: 5,
  driftPath: 6,
  origin: 7,
  selected: 8,
  marker: 9,
};

// -- rasters ----------------------------------------------------------------

/**
 * One georeferenced preview raster.
 * @param {object} caseDoc
 * @param {string} caseId
 * @param {"vv"|"vh"|"prediction"|"referenceMask"|"probability"|"comparison"} kind
 */
export function raster(caseDoc, caseId, kind, opacity = 1) {
  const previews = caseDoc?.previews;
  if (!previews?.files?.[kind] || !previews.bounds) return null;
  const url = api.imageUrl(caseId, kind, previews.files);
  return url ? { url, bounds: previews.bounds, opacity } : null;
}

/** The greyscale backdrop every map sits on. */
export function baseRasters(caseDoc, caseId, { kind = "vv", opacity = 0.9 } = {}) {
  return [raster(caseDoc, caseId, kind, opacity)].filter(Boolean);
}

// -- slick ------------------------------------------------------------------

/**
 * Rings for every published slick, exterior first then holes.
 * Prefers the GeoJSON the geometry stage wrote; falls back to `slick.outlines`, which is
 * what a `?lean=1` document carries.
 *
 * Each ring set carries its look-alike verdict when the document has one, so a caller can
 * draw the screen's own answer instead of painting every region the same colour. The
 * geojson path already has it in `properties.screening`; the lean path does not, so it is
 * joined back by id against `geometry.slicks`.
 */
export function slickRings(caseDoc) {
  const features = caseDoc?.geometry?.geojson?.features;
  if (Array.isArray(features) && features.length) {
    return features.map((feature) => ({
      id: feature.properties?.id,
      areaKm2: feature.properties?.areaKm2,
      confidence: feature.properties?.confidence,
      verdict: feature.properties?.screening?.label || null,
      rings: feature.geometry?.coordinates || [],
    }));
  }
  const outlines = caseDoc?.slick?.outlines;
  if (Array.isArray(outlines) && outlines.length) {
    const verdicts = new Map(
      (caseDoc?.geometry?.slicks || []).map((s) => [s.id, s.screening?.label || null]),
    );
    return outlines.map((outline) => ({
      id: outline.id,
      areaKm2: outline.areaKm2,
      confidence: outline.confidence,
      verdict: verdicts.get(outline.id) || null,
      rings: [outline.exterior, ...(outline.holes || [])].filter(
        (ring) => Array.isArray(ring) && ring.length > 2,
      ),
    }));
  }
  const polygon = caseDoc?.slick?.polygon;
  if (Array.isArray(polygon) && polygon.length > 2) {
    return [
      {
        id: caseDoc.slick.id,
        areaKm2: caseDoc.slick.areaKm2,
        verdict: caseDoc.slick.screening?.label || null,
        rings: [polygon],
      },
    ];
  }
  return [];
}

/**
 * How one region is drawn once the look-alike verdict is allowed to matter.
 *
 * `accepted` keeps the amber a reader already associates with oil. `uncertain` is the same
 * amber dashed, because it is still a published region and the screen declined to settle
 * it. `rejected` goes steel and unfilled: drawn, because it is part of what the network
 * published and hiding it would misstate the detection, but visibly not claimed as oil.
 */
const VERDICT_STYLE = {
  accepted: { stroke: C.oil, fill: C.oilFill, dash: null },
  uncertain: { stroke: C.oil, fill: C.oilFaint, dash: [6, 4] },
  rejected: { stroke: C.lookalike, fill: null, dash: [3, 4] },
};

export function slickLayers(
  caseDoc,
  { fill = true, width = 1.5, pickable = false, z = Z.slick, byVerdict = false } = {},
) {
  return slickRings(caseDoc).map((slick) => {
    // Default off, so the working screens keep drawing one detection in one colour.
    const style = byVerdict ? VERDICT_STYLE[slick.verdict] : null;
    return {
      type: "polygon",
      id: `slick:${slick.id}`,
      kind: "slick",
      rings: slick.rings,
      stroke: style ? style.stroke : C.oil,
      fill: style ? (fill ? style.fill : null) : fill ? C.oilFill : null,
      dash: style ? style.dash : null,
      width,
      z,
      pickable,
      label: `${slick.id} · ${fmt(slick.areaKm2)} km²`,
    };
  });
}

// -- drift -------------------------------------------------------------------

/**
 * Every drift overlay for one direction.
 *
 * `upTo` is an hour offset from the observation used by the timeline scrubber: particle
 * paths and the centroid track are clipped to it so the animation shows the run advancing
 * rather than the finished result fading in.
 */
export function driftLayers(caseDoc, {
  direction = "backward",
  particles = true,
  particleLimit = 38,
  corridor = true,
  envelopes = true,
  centroidPath = true,
  land = true,
  upTo = null,
} = {}) {
  const run = caseDoc?.drift?.[direction];
  if (!run) return [];
  const layers = [];
  const backward = direction === "backward";
  const colour = C.drift;

  if (land && run.landMaskCells?.available && run.landMaskCells.cells?.length) {
    layers.push({
      type: "polygon",
      id: "land",
      kind: "land",
      rings: run.landMaskCells.cells.map(([w, s, e, n]) => [
        [w, s], [e, s], [e, n], [w, n],
      ]),
      stroke: null,
      fill: C.land,
      z: Z.land,
      label: "land mask cell",
    });
  }

  if (particles && Array.isArray(run.tracks)) {
    for (const track of run.tracks.slice(0, particleLimit)) {
      const path = clipPath(track.path, run.timeline, upTo);
      if (path.length < 2) continue;
      layers.push({
        type: "line",
        id: `particle:${direction}:${track.particle}`,
        kind: "particle",
        path,
        stroke: track.beached ? C.danger : C.driftFaint,
        width: 0.9,
        opacity: track.beached ? 0.65 : 0.5,
        z: Z.particles,
      });
    }
  }

  if (corridor && run.searchCorridor?.ring?.length > 2) {
    layers.push({
      type: "polygon",
      id: `corridor:${direction}`,
      kind: "corridor",
      rings: [run.searchCorridor.ring],
      stroke: colour,
      fill: C.driftFill,
      width: 1.2,
      dash: [5, 4],
      z: Z.corridor,
      pickable: true,
      label: `${backward ? "Search corridor" : "Forecast corridor"} · ${fmt(run.searchCorridor.areaKm2)} km²`,
    });
  }

  if (envelopes && Array.isArray(run.envelopes)) {
    for (const envelope of run.envelopes) {
      if (upTo !== null && Math.abs(envelope.hoursFromObservation) > Math.abs(upTo) + 1e-6) {
        continue;
      }
      if (!envelope.ring || envelope.ring.length < 3) continue;
      layers.push({
        type: "polygon",
        id: `envelope:${direction}:${envelope.hoursFromObservation}`,
        kind: "envelope",
        rings: [envelope.ring],
        stroke: colour,
        fill: null,
        width: 1,
        dash: [2, 3],
        opacity: 0.34 + 0.4 * (envelope.fractionOfHorizon || 0),
        z: Z.envelope,
        pickable: true,
        label: `${signedHours(envelope.hoursFromObservation)} · ${fmt(envelope.areaKm2)} km²`,
      });
    }
  }

  if (centroidPath && Array.isArray(run.timeline) && run.timeline.length > 1) {
    const steps = run.timeline.filter(
      (step) => upTo === null || Math.abs(step.hoursFromObservation) <= Math.abs(upTo) + 1e-6,
    );
    const path = steps.map((step) => step.centroid).filter(Boolean);
    if (path.length > 1) {
      layers.push({
        type: "line",
        id: `centroid:${direction}`,
        kind: "driftPath",
        path,
        stroke: colour,
        width: 2.2,
        dash: backward ? [] : [7, 4],
        arrow: 9,
        endDot: 3.5,
        z: Z.driftPath,
        pickable: true,
        label: backward
          ? "Mean hindcast path, observation to estimated origin"
          : "Mean forecast path from the observed slick",
      });
    }
  }

  return layers;
}

/** The estimated origin: a P50 disc, a P90 ring and a crosshair on the centroid. */
export function originLayers(caseDoc, { crosshair = true } = {}) {
  const origin = caseDoc?.trajectories?.originEstimate
    || caseDoc?.drift?.backward?.originEstimate
    || caseDoc?.attribution?.originZone;
  if (!origin?.centroid) return [];
  const p50 = origin.radiusP50Km ?? origin.radiusKm;
  const p90 = origin.radiusP90Km ?? origin.radiusKm;
  const layers = [];
  if (p50 > 0) {
    layers.push({
      type: "ring",
      id: "origin:p50",
      kind: "origin",
      at: origin.centroid,
      radiusKm: p50,
      stroke: C.reference,
      fill: C.referenceFill,
      width: 1.3,
      z: Z.origin,
      label: `Estimated origin, 50% of particles within ${fmt(p50)} km`,
    });
  }
  if (p90 > 0 && p90 !== p50) {
    layers.push({
      type: "ring",
      id: "origin:p90",
      kind: "origin",
      at: origin.centroid,
      radiusKm: p90,
      stroke: C.reference,
      fill: null,
      width: 1,
      dash: [4, 4],
      opacity: 0.7,
      z: Z.origin,
      label: `90% of particles within ${fmt(p90)} km`,
    });
  }
  if (crosshair) {
    layers.push({
      type: "point",
      id: "origin:centre",
      kind: "origin",
      at: origin.centroid,
      r: 2.6,
      fill: C.reference,
      stroke: C.reference,
      cross: true,
      z: Z.marker,
      pickable: true,
      label: "Estimated origin centre",
    });
  }
  return layers;
}

// -- vessels -----------------------------------------------------------------

/**
 * Synthetic AIS tracks, plus a marker at each vessel's closest approach.
 *
 * The selected vessel is drawn in amber on top; every other track stays yellow, which is
 * the synthetic-data colour. Nothing here promotes a track to "evidence" by colouring it
 * like a measurement.
 */
export function vesselLayers(caseDoc, { selected = null, ranked = null, limit = null, upToUtc = null } = {}) {
  const list = ranked || caseDoc?.attribution?.candidates || caseDoc?.vessels || [];
  const reportsByMmsi = new Map(
    (caseDoc?.ais?.vessels || []).map((vessel) => [String(vessel.mmsi), vessel.reports || []]),
  );
  const layers = [];

  for (const vessel of limit ? list.slice(0, limit) : list) {
    const mmsi = String(vessel.mmsi);
    const isSelected = selected !== null && mmsi === String(selected);
    const reports = reportsByMmsi.get(mmsi) || [];
    const path = upToUtc
      ? reports.filter((r) => r.timeUtc <= upToUtc).map((r) => [r.lon, r.lat])
      : vessel.track || reports.map((r) => [r.lon, r.lat]);

    if (path.length >= 2) {
      layers.push({
        type: "line",
        id: `vessel:${mmsi}`,
        kind: "vessel",
        path,
        stroke: isSelected ? C.oil : C.synthetic,
        width: isSelected ? 2.4 : 1.2,
        opacity: isSelected ? 1 : selected ? 0.4 : 0.7,
        arrow: isSelected ? 9 : 0,
        z: isSelected ? Z.selected : Z.vesselTrack,
        pickable: true,
        label: `${vessel.name} · rank ${vessel.rank} · score ${fmt(vessel.score, 1)}`,
      });
    }

    const approach = closestApproachPoint(vessel, reports);
    if (approach) {
      layers.push({
        type: "point",
        id: `vessel-mark:${mmsi}`,
        kind: "vessel",
        at: approach,
        r: isSelected ? 5.5 : 3.6,
        fill: isSelected ? C.oil : C.synthetic,
        stroke: isSelected ? C.oil : C.synthetic,
        z: isSelected ? Z.selected : Z.marker,
        pickable: true,
        alwaysLabel: isSelected,
        label: `${vessel.name} · closest approach ${fmt(vessel.evidence?.closestApproachKm)} km`,
      });
    }
  }
  return layers;
}

/** Where the vessel came closest to the time-matched drift envelope. */
function closestApproachPoint(vessel, reports) {
  const at = vessel?.evidence?.closestApproachUtc;
  if (at) {
    const report = reports.find((r) => r.timeUtc === at);
    if (report) return [report.lon, report.lat];
  }
  const track = vessel?.track;
  if (Array.isArray(track) && track.length) return track[Math.floor(track.length / 2)];
  return null;
}

// -- composition -------------------------------------------------------------

/**
 * The default stack for an overview map: the VV backdrop, the reference mask when the
 * dataset supplied one, the predicted slick, and optionally drift and vessels.
 */
export function sceneVectors(caseDoc, ctx, {
  baseKind = "vv",
  baseOpacity = 0.92,
  includeReference = false,
  includeDrift = false,
  includeVessels = false,
  driftDirection = "backward",
  selectedVessel = null,
  byVerdict = false,
} = {}) {
  const caseId = ctx?.caseId || caseDoc?.id;
  const rasters = baseRasters(caseDoc, caseId, { kind: baseKind, opacity: baseOpacity });
  if (includeReference && caseDoc?.scene?.hasReferenceMask) {
    const mask = raster(caseDoc, caseId, "referenceMask", 0.55);
    if (mask) rasters.push(mask);
  }

  const vectors = [];
  if (includeDrift) {
    vectors.push(
      ...driftLayers(caseDoc, {
        direction: driftDirection,
        particles: false,
        envelopes: false,
        land: true,
      }),
      ...originLayers(caseDoc),
    );
  }
  vectors.push(...slickLayers(caseDoc, { byVerdict }));
  if (includeVessels) {
    vectors.push(...vesselLayers(caseDoc, { selected: selectedVessel }));
  }
  return { rasters, vectors };
}

// -- helpers -----------------------------------------------------------------

/** Clip a particle path to the timeline steps at or before `upTo` hours. */
function clipPath(path, timeline, upTo) {
  if (!Array.isArray(path)) return [];
  if (upTo === null || !Array.isArray(timeline)) return path;
  let count = 0;
  for (const step of timeline) {
    if (Math.abs(step.hoursFromObservation) > Math.abs(upTo) + 1e-6) break;
    count += 1;
  }
  return path.slice(0, Math.max(2, count));
}

function signedHours(value) {
  if (value === null || value === undefined) return "";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${Number(n.toFixed(1))} h`;
}

function fmt(value, decimals = 2) {
  return Number.isFinite(Number(value)) ? Number(Number(value).toFixed(decimals)) : "—";
}
