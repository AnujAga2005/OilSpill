/** Screen 4: drift reconstruction.
 *
 * Two runs from one observation: backwards to where the oil probably came from, forwards to
 * where it is probably going. Both are Monte Carlo, so the honest output is a distribution,
 * not a line - the scrubber exists to make the spread visible as it grows rather than
 * presenting the final envelope as a fact.
 *
 * The forcing on this case is synthetic. That is stated in the shell, again on the forcing
 * card, and again in every export, because a drift result inherits all of its credibility
 * from the current field that drove it.
 */

import { h, icon } from "../dom.js";
import { ICONS } from "../icons.js";
import * as F from "../format.js";
import * as U from "../ui.js";
import * as X from "../exporters.js";
import { createMap, mapLegend, haversineKm } from "../mapview.js";
import { baseRasters, slickLayers, driftLayers, originLayers, C } from "../layers.js";
import { lineChart, stackBar } from "../chart.js";

export const LEDE =
  "A backward hindcast to an estimated release zone and a forward forecast from the " +
  "observed slick, both as particle clouds with an explicit uncertainty envelope.";

/** Scrubber frame interval. Slow enough to read the clock, fast enough to feel continuous. */
const FRAME_MS = 110;

export function render(ctx) {
  const { caseDoc, caseStatus } = ctx;

  if (caseStatus !== "ready" || !caseDoc) {
    return U.stateSwitch(caseStatus, caseDoc, () => null, {
      loadingTitle: "Loading drift",
      missing: {
        title: "No drift has been simulated yet",
        body: "The drift stage runs on a stored detection.",
        command: ".venv/bin/python scripts/run_api.py --build-demo",
      },
      failed: { title: "The case could not be loaded", body: "The API did not return a case." },
    });
  }

  if (!caseDoc.drift?.backward && !caseDoc.drift?.forward) {
    return U.missingState({
      title: "This case has no drift run",
      body:
        "A detection is stored but the drift stage has not been run against it. Submit a " +
        "drift job, or rebuild the demo case.",
      command: ".venv/bin/python scripts/run_api.py --build-demo",
      action: U.button("Run drift now", {
        kind: "primary",
        iconPath: ICONS.play,
        disabled: ctx.api.apiMode() === "offline",
        onClick: () => ctx.runAnalysis("drift", {}),
      }),
    });
  }

  const direction = ctx.route.get("direction") === "forward" ? "forward" : "backward";
  const run = caseDoc.drift[direction] || caseDoc.drift.backward;
  const origin = caseDoc.trajectories?.originEstimate || caseDoc.drift?.backward?.originEstimate;

  return h(
    "div",
    { class: "stack" },

    U.card(
      direction === "backward" ? "Hindcast summary" : "Forecast summary",
      {
        id: "summary",
        hint: run.label,
        note: run.uncertaintyBasis,
        actions: h(
          "div",
          { class: "inline" },
          U.segmented(
            [
              { value: "backward", label: "Backward", title: "Where the oil came from" },
              { value: "forward", label: "Forward", title: "Where the oil is going" },
            ],
            direction,
            (value) => ctx.setParams({ direction: value }),
            { ariaLabel: "Drift direction" },
          ),
          U.button("CSV", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.download,
            onClick: () => {
              X.downloadCsv(
                X.exportName(caseDoc, `drift-${direction}`, "csv"),
                X.driftCsv(caseDoc, direction),
              );
              ctx.announce("Drift timeline downloaded as CSV.", { kind: "success" });
            },
          }),
        ),
      },
      h(
        "div",
        { class: "grid grid--stats" },
        U.stat({
          label: "Horizon",
          value: F.hours(run.horizonHours),
          sub: `${F.int(run.numerics?.steps)} steps of ${F.int(run.numerics?.timeStepMinutes)} minutes`,
        }),
        U.stat({
          label: direction === "backward" ? "Origin spread, P90" : "Endpoint spread, P90",
          value: F.km(run.endpoints?.spreadP90Km, 1),
          unit: "km",
          tone: "drift",
          sub: `P50 ${F.km(run.endpoints?.spreadP50Km, 1)} km, ${F.int(run.endpoints?.count)} particles`,
        }),
        U.stat({
          label: "Mean displacement",
          value: F.km(run.endpoints?.displacementMeanKm, 1),
          unit: "km",
          sub: `P90 ${F.km(run.endpoints?.displacementP90Km, 1)} km over ${F.hours(run.horizonHours)}`,
        }),
        U.stat({
          label: direction === "backward" ? "Search corridor" : "Forecast corridor",
          value: F.km2(run.searchCorridor?.areaKm2),
          unit: "km²",
          sub: run.searchCorridor?.ringNote,
        }),
      ),
    ),

    mapCard(ctx, caseDoc, direction, run),

    direction === "backward" ? spillAgeCard(caseDoc.spillAge) : null,

    h(
      "div",
      { class: "grid grid--wide-left" },
      spreadCard(run, direction),
      h(
        "div",
        { class: "stack" },
        direction === "backward" ? originCard(caseDoc, origin, run) : forecastCard(run),
        outcomesCard(run),
      ),
    ),

    h(
      "div",
      { class: "grid grid--2" },
      forcingCard(caseDoc),
      numericsCard(run),
    ),

    caveatCard(run),
  );
}

// -- map and scrubber --------------------------------------------------------

/**
 * The map, with a timeline scrubber that clips every drift layer to an hour offset.
 *
 * The clip is the honest part: at +2 h the envelope is small and at +24 h it is large, and
 * seeing that grow is the difference between "the origin is here" and "the origin is
 * somewhere in this disc, and here is how fast the uncertainty accumulated".
 */
function mapCard(ctx, caseDoc, direction, run) {
  const holder = h("div", { style: { height: "clamp(320px, 56vh, 620px)" } });
  const timeline = run.timeline || [];
  const maxHours = Math.abs(run.horizonHours || 0);

  const view = {
    hour: maxHours,
    playing: false,
    particles: true,
    envelopes: true,
    corridor: true,
    both: false,
  };
  let map = null;
  let timer = null;

  const clockHost = h("span", { class: "timeline__clock" });
  const playHost = h("span");
  const togglesHost = h("div", { class: "inline" });

  const stepAt = (hour) => {
    if (!timeline.length) return null;
    let best = timeline[0];
    for (const step of timeline) {
      if (Math.abs(step.hoursFromObservation) <= hour + 1e-6) best = step;
    }
    return best;
  };

  const paint = () => {
    if (!map) return;
    const upTo = view.hour >= maxHours - 1e-6 ? null : view.hour;
    const vectors = [
      ...slickLayers(caseDoc, { fill: true, pickable: true }),
      ...driftLayers(caseDoc, {
        direction,
        upTo,
        particles: view.particles,
        envelopes: view.envelopes,
        corridor: view.corridor && upTo === null,
      }),
    ];
    if (view.both) {
      // The other direction, dimmed, so the whole reconstruction is visible at once.
      const other = direction === "backward" ? "forward" : "backward";
      for (const layer of driftLayers(caseDoc, {
        direction: other,
        particles: false,
        envelopes: false,
        corridor: false,
      })) {
        vectors.push({ ...layer, opacity: 0.4, width: 1.4, pickable: false });
      }
    }
    if (direction === "backward" && upTo === null) vectors.push(...originLayers(caseDoc));

    const step = stepAt(view.hour);
    if (step?.centroid) {
      vectors.push({
        type: "point",
        id: "drift:head",
        kind: "head",
        at: step.centroid,
        r: 4,
        fill: C.drift,
        stroke: "#ffffff",
        z: 9,
        pickable: true,
        label: `Cloud centroid at ${F.utc(step.timeUtc)}`,
      });
      vectors.push({
        type: "ring",
        id: "drift:head-p90",
        kind: "head",
        at: step.centroid,
        radiusKm: step.spreadP90Km,
        stroke: C.drift,
        fill: null,
        width: 1,
        dash: [3, 3],
        opacity: 0.8,
        z: 8,
        label: `90% of particles within ${F.km(step.spreadP90Km, 1)} km`,
      });
    }
    map.setVectors(vectors).redraw();
    paintClock(step);
  };

  const paintClock = (step) => {
    const label = step
      ? `${F.utc(step.timeUtc, { seconds: false })} · ${signed(step.hoursFromObservation)} h`
      : `${signed(direction === "backward" ? -view.hour : view.hour)} h`;
    clockHost.textContent = label;
  };

  const stop = () => {
    view.playing = false;
    if (timer) clearInterval(timer);
    timer = null;
    paintPlay();
  };

  const play = () => {
    if (!timeline.length) return;
    view.playing = true;
    if (view.hour >= maxHours - 1e-6) view.hour = 0;
    timer = setInterval(() => {
      const stepHours = maxHours / Math.max(1, timeline.length - 1);
      view.hour = Math.min(maxHours, view.hour + stepHours);
      slider.value = String(view.hour);
      paint();
      if (view.hour >= maxHours - 1e-6) stop();
    }, FRAME_MS);
    paintPlay();
  };

  const paintPlay = () => {
    playHost.replaceChildren(
      h(
        "button",
        {
          class: "timeline__play",
          type: "button",
          "aria-label": view.playing ? "Pause the drift animation" : "Play the drift animation",
          onClick: () => (view.playing ? stop() : play()),
        },
        icon(view.playing ? ICONS.pause : ICONS.play, { size: 15 }),
      ),
    );
  };

  const slider = h("input", {
    class: "range timeline__track",
    type: "range",
    min: "0",
    max: String(maxHours || 1),
    step: String(maxHours ? maxHours / Math.max(1, timeline.length - 1) : 1),
    value: String(view.hour),
    "aria-label": "Hours from the observation",
    onInput: (event) => {
      stop();
      view.hour = Number(event.target.value);
      paint();
    },
  });

  const paintToggles = () => {
    togglesHost.replaceChildren(
      toggle("Particles", view.particles, (on) => {
        view.particles = on;
        paint();
      }),
      toggle("Envelopes", view.envelopes, (on) => {
        view.envelopes = on;
        paint();
      }),
      toggle("Corridor", view.corridor, (on) => {
        view.corridor = on;
        paint();
      }),
      toggle("Both directions", view.both, (on) => {
        view.both = on;
        paint();
      }),
    );
  };
  paintToggles();
  paintPlay();

  requestAnimationFrame(() => {
    if (!holder.isConnected) return;
    map = createMap(holder, {
      onSelect: (hit) => {
        // Spoken only: the map already highlights what was clicked, and a card per click
        // would bury the selection it is describing.
        if (hit?.label) ctx.announce(hit.label, { silent: true });
      },
    });
    map.setRasters(baseRasters(caseDoc, ctx.caseId, { kind: "vv", opacity: 0.8 }));
    paint();
    map.fitContent(0.1);
    ctx.onCleanup(() => {
      if (timer) clearInterval(timer);
      map.destroy();
    });
  });

  const first = timeline[0];
  const last = timeline.at(-1);

  return U.card(
    direction === "backward" ? "Hindcast" : "Forecast",
    {
      id: "map",
      hint: run.status,
      note:
        `Observation at ${F.utc(run.observedUtc, { seconds: true })}. ` +
        (direction === "backward"
          ? "Time runs backwards: the scrubber at zero is the observed slick and at the far " +
            "end is the estimated release."
          : "Time runs forwards from the observed slick."),
    },
    h(
      "div",
      { class: "stack stack--tight" },
      holder,
      h("div", { class: "timeline no-print" }, playHost, slider, clockHost),
      togglesHost,
      mapLegend([
        { label: "Observed slick", colour: "var(--oil)" },
        { label: direction === "backward" ? "Hindcast centroid path" : "Forecast centroid path", colour: "var(--drift)" },
        { label: "Particle paths", colour: "var(--drift)", shape: "line" },
        { label: "Uncertainty envelope", colour: "var(--drift)", shape: "dash" },
        direction === "backward" ? { label: "Estimated origin zone", colour: "var(--reference)", shape: "dash" } : null,
        (run.particleOutcomes?.beached || 0) > 0 ? { label: "Beached particle", colour: "#fb7185", shape: "line" } : null,
      ].filter(Boolean)),
      first && last
        ? h(
            "p",
            { class: "small muted" },
            `${F.int(run.tracks?.length)} of ${F.int(run.endpoints?.count)} particle paths are drawn. ` +
              `${run.trackNote || ""}`,
          )
        : null,
    ),
  );
}

function toggle(label, checked, onChange) {
  return h(
    "label",
    { class: "switch" },
    h("input", {
      type: "checkbox",
      checked,
      onChange: (event) => onChange(event.target.checked),
    }),
    h("span", null, label),
  );
}

function signed(hours) {
  const value = Math.round((hours || 0) * 10) / 10;
  return value > 0 ? `+${value}` : String(value);
}

// -- cards -------------------------------------------------------------------

/**
 * The spill's age, and — the part that matters — whether the hindcast can pin it down.
 *
 * The temptation on this card is to print the midpoint of the window and call it the age.
 * The pipeline refuses to, because the run itself says it cannot tell one end of the
 * window from the other: over the whole horizon the estimated position moves less than the
 * P90 radius of the uncertainty around it. So the card shows the bound, shows the
 * arithmetic behind the refusal, and lists what would actually narrow it.
 */
function spillAgeCard(age) {
  if (!age) return null;
  const resolution = age.resolution || {};
  const separation = resolution.bestSeparation || {};
  return U.card(
    "Estimated spill age",
    { id: "age", hint: age.label, note: age.basis },
    h(
      "div",
      { class: "stack stack--tight" },
      h(
        "div",
        { class: "grid grid--stats" },
        U.stat({
          label: "Upper bound",
          value: F.hours(age.maxHours),
          tone: "drift",
          sub: `oldest release the hindcast follows — ${F.utc(age.earliestReleaseUtc)}`,
        }),
        U.stat({
          label: "Lower bound",
          value: F.hours(age.minHours),
          sub: "nothing in one scene rules out a release minutes before the pass",
        }),
        U.stat({
          label: "Age resolvable?",
          value: resolution.resolvable ? "yes" : "no",
          tone: resolution.resolvable ? "reference" : undefined,
          sub: resolution.resolvable
            ? `from ${F.hours(resolution.fromHours)} back`
            : "the whole window fits inside its own error bar",
        }),
        U.stat({
          label: "Best separation",
          value: F.num(separation.ratio, 2),
          unit: "× radius",
          sub:
            `${F.km(separation.displacementKm, 1)} km of movement against a P90 radius of ` +
            `${F.km(separation.radiusKm, 1)} km, at ${F.hours(separation.hours)} back`,
        }),
      ),
      U.notice(resolution.note || "", { kind: "" }),
      h("p", { class: "small muted" }, resolution.test || ""),
      (age.narrowedBy || []).length
        ? h(
            "div",
            { class: "stack stack--tight" },
            h("p", { class: "small" }, "What would narrow it:"),
            h(
              "ul",
              { class: "bullets" },
              (age.narrowedBy || []).map((item) => h("li", null, h("span", null, item))),
            ),
          )
        : null,
      h("p", { class: "small muted" }, age.caveat || ""),
    ),
  );
}

function spreadCard(run, direction) {
  const timeline = run.timeline || [];
  const points = (key) =>
    timeline
      .map((step) => [Math.abs(step.hoursFromObservation), step[key]])
      .filter((p) => Number.isFinite(p[1]));

  return U.card(
    "How the uncertainty grows",
    {
      id: "spread",
      hint: `${F.int(timeline.length)} steps`,
      note:
        "Spread is the distance from the particle cloud's own centroid, so it measures " +
        "disagreement between particles rather than distance travelled. It grows because " +
        "of the random walk, not because the current field is uncertain — that uncertainty " +
        "is not quantified here at all.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      lineChart(
        [
          { label: "P50 spread", colour: C.drift, points: points("spreadP50Km"), dots: false },
          { label: "P90 spread", colour: C.drift, points: points("spreadP90Km"), dash: "5 4" },
          { label: "Maximum", colour: C.synthetic, points: points("spreadMaxKm"), dash: "2 3" },
          { label: "Mean displacement", colour: C.reference, points: points("displacementMeanKm") },
        ],
        {
          xLabel: "hours from observation",
          yLabel: "km",
          yZero: true,
          formatX: (v) => `${Math.round(v)}`,
          formatY: (v) => `${Math.round(v * 10) / 10}`,
          ariaLabel: `Particle spread in kilometres against hours from the observation, ${direction} run`,
        },
      ),
      U.legend([
        { label: "P50 spread", colour: "var(--drift)", shape: "line" },
        { label: "P90 spread", colour: "var(--drift)", shape: "dash" },
        { label: "Maximum spread", colour: "var(--synthetic)", shape: "dash" },
        { label: "Mean displacement from release", colour: "var(--reference)", shape: "line" },
      ]),
      h(
        "p",
        { class: "small muted" },
        `Expected diffusive spread from the random walk alone is ` +
          `${F.km(run.numerics?.expectedDiffusiveSpreadKm, 2)} km over the full horizon ` +
          `(${F.num(run.numerics?.randomWalkStdPerStepM, 1)} m per step). Anything beyond that ` +
          "is the current field pulling particles apart.",
      ),
    ),
  );
}

function originCard(caseDoc, origin, run) {
  if (!origin) {
    return U.card(
      "Estimated origin",
      { id: "origin" },
      U.missingState({ title: "No origin estimate", body: "The backward run stored no origin block." }),
    );
  }
  const window_ = caseDoc.attribution?.releaseWindow || caseDoc.ais?.releaseWindow;
  const slickCentroid = caseDoc.slick?.centroid;
  const straight =
    slickCentroid && origin.centroid ? haversineKm(slickCentroid, origin.centroid) : null;

  return U.card(
    "Estimated origin",
    { id: "origin", hint: `${F.hours(origin.hoursBeforeObservation)} before the observation` },
    h(
      "div",
      { class: "stack stack--tight" },
      U.rows(
        U.row("Centre", F.latLon(origin.centroid), { mono: true }),
        U.row("50% of particles within", `${F.km(origin.radiusP50Km, 2)} km`, { mono: true }),
        U.row("90% of particles within", `${F.km(origin.radiusP90Km, 2)} km`, { mono: true }),
        U.row("Straight-line from slick centroid", straight === null ? null : `${F.km(straight, 2)} km`, { mono: true }),
        window_ ? U.row("Release window", `${F.utc(window_.startUtc)} → ${F.utc(window_.endUtc)}`, { stack: true, mono: true }) : null,
        U.row("At", F.utc(run.timeline?.at(-1)?.timeUtc, { seconds: true }), { mono: true }),
      ),
      U.notice(origin.interpretation || "", { strongPrefix: "Read this as a zone." }),
    ),
  );
}

function forecastCard(run) {
  const last = run.timeline?.at(-1);
  return U.card(
    "Forecast endpoint",
    { id: "forecast", hint: last ? F.utc(last.timeUtc) : null },
    U.rows(
      U.row("Cloud centroid", F.latLon(last?.centroid), { mono: true }),
      U.row("50% of particles within", `${F.km(run.endpoints?.spreadP50Km, 2)} km`, { mono: true }),
      U.row("90% of particles within", `${F.km(run.endpoints?.spreadP90Km, 2)} km`, { mono: true }),
      U.row("Mean displacement", `${F.km(run.endpoints?.displacementMeanKm, 2)} km`, { mono: true }),
      U.row("P90 displacement", `${F.km(run.endpoints?.displacementP90Km, 2)} km`, { mono: true }),
      U.row("Corridor area", `${F.km2(run.searchCorridor?.areaKm2)} km²`, { mono: true }),
      U.row("Meaning", run.searchCorridor?.meaning, { stack: true }),
    ),
  );
}

function outcomesCard(run) {
  const outcomes = run.particleOutcomes || {};
  const parts = [
    { label: "Still drifting", value: outcomes.stillDrifting || 0, colour: C.drift },
    { label: "Beached", value: outcomes.beached || 0, colour: C.danger },
    { label: "Left the forcing domain", value: outcomes.leftForcingDomain || 0, colour: C.synthetic },
  ];
  return U.card(
    "Particle outcomes",
    { id: "outcomes", hint: `${F.int(outcomes.released)} released` },
    h(
      "div",
      { class: "stack stack--tight" },
      stackBar(parts),
      U.rows(
        ...parts.map((part) =>
          U.row(
            part.label,
            `${F.int(part.value)} · ${F.pct((part.value || 0) / (outcomes.released || 1))}`,
            { mono: true },
          ),
        ),
      ),
      h(
        "p",
        { class: "small muted" },
        outcomes.beached === 0
          ? "No particle reached a land cell, so the whole cloud stayed at sea for the full horizon."
          : `${F.int(outcomes.beached)} particle(s) reached a land cell and were stopped there.`,
      ),
      U.rows(
        U.row("Seeding", run.seeding?.method),
        U.row("Mask pixels available", F.int(run.seeding?.maskPixels), { mono: true }),
        U.row("Particles drawn", F.int(run.seeding?.particles), { mono: true }),
        U.row("With replacement", run.seeding?.withReplacement ? "yes" : "no"),
      ),
    ),
  );
}

/**
 * The wind half of the forcing, which is resolved from a different product than the
 * currents and can be real when they are not. Oil moves at about 3% of the wind, and at
 * the current speeds here that term is the same size as the current, so "which wind" is
 * a substantive claim rather than a footnote.
 */
function windRows(forcing, spec) {
  // A case stored before the wind became a product of its own has no `forcing.wind`, and
  // its wind was the synthetic rotation the spec describes. Reconstructing that is honest;
  // reporting "no wind" for a run that had one would not be.
  const wind =
    forcing.wind ||
    (spec.windSpeedMs == null
      ? {}
      : {
          source: "synthetic",
          available: true,
          speedMs: spec.windSpeedMs,
          directionDeg: spec.windDirectionDeg,
        });
  const overlap = wind.overlap || {};
  const coverage = overlap.horizonCoverageFraction;
  const era5 = wind.source === "era5";
  // `available: false` on a synthetic wind means the run was configured without windage,
  // which is a third state: the field exists but never entered the velocity.
  const off = wind.available === false && wind.source === "synthetic";
  const synthetic = wind.source === "synthetic" && !off;
  const sourceText = era5
    ? `ERA5 reanalysis · ${wind.product || "operator-supplied file"}`
    : off
      ? "Synthetic scenario wind · disabled for this run"
      : synthetic
        ? "Synthetic scenario wind"
        : "None — currents only";
  return h(
    "div",
    { class: "stack stack--tight" },
    U.rows(
      U.row("Wind source", sourceText, { stack: true, muted: !era5 }),
      era5
        ? U.row("Mean wind over the footprint", `${F.num(wind.speedMs, 2)} m/s`, { mono: true })
        : synthetic
          ? U.row("Wind", `${F.num(wind.speedMs, 2)} m/s from ${F.bearing(wind.directionDeg)}`, {
              mono: true,
            })
          : null,
      era5
        ? U.row(
            "Hours read",
            `${F.int(wind.frameCount)} at ${F.num(wind.cadenceHours, 0)} h cadence`,
            { mono: true, title: wind.interpolation },
          )
        : null,
      era5 && coverage != null
        ? U.row("Horizon covered by the wind file", F.pct(coverage), {
            mono: true,
            title: (overlap.notes || []).join(" "),
          })
        : null,
      era5
        ? U.row("Nearest wind hour", F.utc(overlap.nearestProductTimeUtc), { stack: true })
        : null,
      U.row("Wind drift contribution", `${F.num(forcing.windDriftMs, 4)} m/s`, {
        mono: true,
        title: forcing.windNote,
      }),
    ),
    h("p", { class: "small muted" }, wind.note || forcing.windNote || ""),
  );
}

/**
 * The forcing card. This is the one card on the screen that determines whether anything
 * else on it means anything, so it states the mode first and the numbers second.
 */
function forcingCard(caseDoc) {
  const block = caseDoc.forcing || {};
  const forcing = block.forcing || {};
  const overlap = block.overlap || {};
  const spec = forcing.spec || {};
  const anchor = forcing.amplitudeAnchor || {};
  const synthetic = forcing.isSynthetic !== false;

  return U.card(
    "Forcing",
    { id: "forcing", hint: block.label, note: forcing.method },
    h(
      "div",
      { class: "stack stack--tight" },
      U.notice(
        synthetic
          ? forcing.warning ||
              "The current field is a deterministic synthetic construction, not a measurement. " +
                "Drift distances and directions are illustrative."
          : "Currents come from the supplied reanalysis for this acquisition time.",
        { kind: synthetic ? "synthetic" : "", strongPrefix: block.label || "" },
      ),
      (block.reasons || []).length
        ? h(
            "ul",
            { class: "bullets" },
            (block.reasons || []).map((reason) => h("li", null, h("span", null, reason))),
          )
        : null,
      U.rows(
        U.row("CMEMS product present", block.cmemsAvailable ? "yes" : "no"),
        U.row("Spatial overlap", overlap.spatialOverlap ? "yes" : "no"),
        U.row(
          "Temporal overlap",
          overlap.temporalOverlap
            ? "yes"
            : `no · nearest ${F.utc(overlap.nearestProductTimeUtc)}, ${F.num(overlap.timeGapHours / 24 / 365.25, 1)} years away`,
          { stack: true },
        ),
        U.row("Tolerance", `${F.num(overlap.timeToleranceHours, 0)} h`, { mono: true }),
        U.row("Water cells over the scene", F.int(overlap.waterCellsOverScene), { mono: true }),
      ),
      synthetic
        ? U.rows(
            U.row("Origin", forcing.origin, { stack: true }),
            U.row("Amplitude anchored to", `${F.num(anchor.speedMs, 4)} m/s`, { mono: true, title: anchor.note }),
            U.row("Anchor source", anchor.source, { stack: true }),
            U.row("Target speed", `${F.num(spec.targetSpeedMs, 4)} m/s`, { mono: true }),
            U.row("Current RMS speed", `${F.num(spec.currentRmsSpeedMs, 4)} m/s`, { mono: true }),
            U.row("Modes", F.int((spec.modes || []).length), { mono: true }),
            U.row("Seed", F.int(spec.seed), { mono: true }),
          )
        : null,
      // The wind is reported outside the synthetic branch because it is a separate
      // product: a real ERA5 wind can force a synthetic current field, and a CMEMS run
      // can have no wind at all. Reading it off `spec` would mislabel both.
      windRows(forcing, spec),
      forcing.landMask?.available
        ? h(
            "div",
            { class: "stack stack--tight" },
            U.rows(
              U.row("Land mask", forcing.landMask.source, { stack: true }),
              U.row(
                "Resolution",
                `${F.num(forcing.landMask.resolutionDeg, 6)}° · ${F.num(forcing.landMask.resolutionKm, 2)} km`,
                { mono: true },
              ),
              U.row("Water fraction over the window", F.pct(forcing.landMask.waterFractionOverWindow), { mono: true }),
            ),
            h("p", { class: "small muted" }, forcing.landMask.caveat || ""),
          )
        : U.notice("No land mask was available, so beaching was never tested.", { kind: "synthetic" }),
      h("p", { class: "small muted" }, forcing.reproducibility || ""),
    ),
  );
}

function numericsCard(run) {
  const numerics = run.numerics || {};
  const config = run.config || {};
  return U.card(
    "Numerics",
    { id: "numerics", note: numerics.note },
    U.rows(
      U.row("Scheme", numerics.scheme, { stack: true }),
      U.row("Time step", `${F.int(numerics.timeStepMinutes)} min`, { mono: true }),
      U.row("Steps", F.int(numerics.steps), { mono: true }),
      U.row("Horizon", F.hours(config.horizon_hours), { mono: true }),
      U.row("Particles", F.int(config.particle_count), { mono: true }),
      U.row("Diffusivity", `${F.num(numerics.diffusivityM2S, 1)} m²/s`, { mono: true }),
      U.row("Random walk per step", `${F.num(numerics.randomWalkStdPerStepM, 2)} m`, { mono: true }),
      U.row("Expected diffusive spread", `${F.km(numerics.expectedDiffusiveSpreadKm, 3)} km`, { mono: true }),
      U.row("Windage factor", F.num(config.windage_factor, 3), { mono: true }),
      U.row("Wind included", config.use_wind ? "yes" : "no"),
      U.row("Seed", F.int(config.seed), { mono: true }),
      U.row("Generated", F.utc(run.generatedUtc, { seconds: true }), { mono: true }),
      U.row("Pipeline", run.pipelineVersion, { mono: true }),
    ),
  );
}

function caveatCard(run) {
  if (!run.caveat) return null;
  return U.card(
    "What this drift run does not tell you",
    { id: "caveat" },
    h(
      "div",
      { class: "stack stack--tight" },
      U.notice(run.caveat, { kind: "synthetic", strongPrefix: "Caveat." }),
      h(
        "p",
        { class: "small muted" },
        "No oil weathering is modelled: no evaporation, no emulsification, no dispersion and " +
          "no change in slick thickness. Every particle is a passive tracer, so the cloud " +
          "represents where surface water went, not how much oil is still there.",
      ),
    ),
  );
}
