/** Screen 5: vessel attribution.
 *
 * The most dangerous screen in the product. It ranks vessels near an estimated release zone,
 * and a ranked list of named ships is read as an accusation whether or not it is labelled as
 * one. So three things are non-negotiable here:
 *
 *   1. Every vessel is synthetic and says so, on the row, in the detail panel and in exports.
 *   2. No vessel is ever called responsible. The only status is the one the pipeline stores:
 *      "Priority candidate for investigation".
 *   3. Every point of every score is traceable to a stated rule and a measured quantity.
 *      A score with no arithmetic behind it is worse than no score.
 */

import { h, icon } from "../dom.js";
import { ICONS } from "../icons.js";
import * as F from "../format.js";
import * as U from "../ui.js";
import * as X from "../exporters.js";
import { createMap, mapLegend } from "../mapview.js";
import { baseRasters, slickLayers, driftLayers, originLayers, vesselLayers, C } from "../layers.js";
import { stackBar } from "../chart.js";

export const LEDE =
  "Synthetic vessel tracks scored against the estimated release zone and window. A ranking " +
  "of who to ask first, not a finding of who did it.";

/**
 * Component colours, reused by the score bars and the weight budget.
 *
 * The scorer names its components one way in `componentDetail` and its weights another, so
 * both spellings are mapped to the same colour rather than silently falling through to grey.
 */
const COMPONENT_TONE = {
  Distance: C.oil,
  distance: C.oil,
  Time: C.reference,
  timeWindow: C.reference,
  Trajectory: C.drift,
  trajectory: C.drift,
  Behaviour: C.synthetic,
  behaviour: C.synthetic,
  Type: C.agree,
  vesselType: C.agree,
  "Data quality": C.neutral,
  dataCompleteness: C.neutral,
};

export function render(ctx) {
  const { caseDoc, caseStatus } = ctx;

  if (caseStatus !== "ready" || !caseDoc) {
    return U.stateSwitch(caseStatus, caseDoc, () => null, {
      loadingTitle: "Loading candidates",
      missing: {
        title: "No attribution has been run yet",
        body: "Scoring runs after drift, on synthetic AIS.",
        command: ".venv/bin/python scripts/run_api.py --build-demo",
      },
      failed: { title: "The case could not be loaded", body: "The API did not return a case." },
    });
  }

  const attribution = caseDoc.attribution || {};
  const candidates = attribution.candidates || caseDoc.vessels || [];

  if (!candidates.length) {
    return U.emptyState({
      title: "No candidate vessels",
      body:
        "No synthetic vessel reported a position near the estimated release zone inside the " +
        "release window. With synthetic traffic that is a property of the generator, not " +
        "evidence that no vessel was there.",
    });
  }

  const selectedMmsi = String(ctx.route.get("vessel") || candidates[0].mmsi);
  const selected = candidates.find((c) => String(c.mmsi) === selectedMmsi) || candidates[0];

  return h(
    "div",
    { class: "stack" },

    syntheticBanner(caseDoc),

    U.card(
      "Ranking basis",
      {
        id: "basis",
        hint: attribution.candidateLabel,
        note: attribution.caveat,
        actions: U.button("Candidates CSV", {
          kind: "quiet",
          small: true,
          iconPath: ICONS.download,
          onClick: () => {
            X.downloadCsv(X.exportName(caseDoc, "candidates", "csv"), X.candidatesCsv(caseDoc));
            ctx.announce("Candidate table downloaded as CSV.");
          },
        }),
      },
      h(
        "div",
        { class: "grid grid--stats" },
        U.stat({
          label: "Candidates scored",
          value: F.int(attribution.candidateCount ?? candidates.length),
          tone: "synthetic",
          sub: `of ${F.int(caseDoc.ais?.counts?.vessels)} synthetic vessels generated`,
        }),
        U.stat({
          label: "Release window",
          value: F.hours(attribution.releaseWindow?.hours),
          sub: `${F.utc(attribution.releaseWindow?.startUtc)} → ${F.utc(attribution.releaseWindow?.endUtc)}`,
        }),
        U.stat({
          label: "Origin zone radius",
          value: F.km(attribution.originZone?.radiusKm, 1),
          unit: "km",
          tone: "reference",
          sub: "P90 of the backward particle cloud",
        }),
        U.stat({
          label: "Top score",
          value: F.num(candidates[0].score, 1),
          sub: `of ${candidates[0].scoreMax} — ${candidates[0].name}`,
        }),
      ),
      weightsStrip(attribution.weights),
    ),

    mapCard(ctx, caseDoc, candidates, selectedMmsi),

    h(
      "div",
      { class: "grid grid--wide-left" },
      rankingCard(ctx, candidates, selectedMmsi),
      h(
        "div",
        { class: "stack" },
        scoreCard(selected),
        evidenceCard(selected, attribution),
      ),
    ),

    h(
      "div",
      { class: "grid grid--2" },
      explanationCard(selected, attribution),
      trackQualityCard(caseDoc, selected),
    ),

    generatorCard(caseDoc, selected),
  );
}

// -- the disclosure that has to come first ----------------------------------

function syntheticBanner(caseDoc) {
  const ais = caseDoc.ais || {};
  return U.card(
    "Before reading this ranking",
    { id: "disclosure" },
    h(
      "div",
      { class: "stack stack--tight" },
      U.notice(ais.disclaimer || "", { kind: "synthetic", strongPrefix: ais.label || "Synthetic AIS." }),
      h(
        "ul",
        { class: "bullets" },
        h("li", null, h("span", null, ais.identifierNote || "")),
        h("li", null, h("span", null, ais.nameNote || "")),
        h(
          "li",
          null,
          h(
            "span",
            null,
            "The only status this pipeline assigns is " +
              `"${caseDoc.attribution?.candidateLabel || "Priority candidate for investigation"}". ` +
              "Nothing here identifies a responsible party.",
          ),
        ),
      ),
    ),
  );
}

/** The weight budget, as a single bar. It sums to 100 by construction. */
function weightsStrip(weights) {
  if (!weights) return null;
  const parts = Object.entries(weights)
    .filter(([key]) => key !== "total")
    .map(([key, value]) => ({
      label: F.label(key),
      value,
      colour: COMPONENT_TONE[key] || C.neutral,
    }));
  return h(
    "div",
    { class: "stack stack--tight", style: { "margin-top": "var(--s4)" } },
    stackBar(parts, { height: 10 }),
    U.legend(
      parts.map((part) => ({ label: `${part.label} ${part.value}`, colour: part.colour })),
    ),
    h(
      "p",
      { class: "small muted" },
      `The weights are fixed before any case is scored and total ${F.int(weights.total)}. ` +
        "They were chosen by hand, not fitted to data, because there is no labelled " +
        "attribution ground truth to fit them to.",
    ),
  );
}

// -- map ---------------------------------------------------------------------

function mapCard(ctx, caseDoc, candidates, selectedMmsi) {
  const holder = h("div", { style: { height: "clamp(320px, 54vh, 600px)" } });
  const view = { limit: 10, drift: true, all: true };
  let map = null;
  const controlsHost = h("div", { class: "inline" });

  const paint = () => {
    if (!map) return;
    const vectors = [
      ...slickLayers(caseDoc, { fill: true }),
      ...(view.drift
        ? driftLayers(caseDoc, { direction: "backward", particles: false, envelopes: true })
        : []),
      ...originLayers(caseDoc),
      ...vesselLayers(caseDoc, {
        selected: selectedMmsi,
        limit: view.all ? null : 3,
      }),
    ];
    map.setVectors(vectors).redraw();
  };

  const paintControls = () => {
    controlsHost.replaceChildren(
      h(
        "label",
        { class: "switch" },
        h("input", {
          type: "checkbox",
          checked: view.all,
          onChange: (event) => {
            view.all = event.target.checked;
            paint();
          },
        }),
        h("span", null, `All ${candidates.length} tracks`),
      ),
      h(
        "label",
        { class: "switch" },
        h("input", {
          type: "checkbox",
          checked: view.drift,
          onChange: (event) => {
            view.drift = event.target.checked;
            paint();
          },
        }),
        h("span", null, "Drift envelopes"),
      ),
    );
  };
  paintControls();

  requestAnimationFrame(() => {
    if (!holder.isConnected) return;
    map = createMap(holder, {
      onSelect: (hit) => {
        const match = /^vessel(?:-mark)?:(\d+)$/.exec(hit?.id || "");
        if (match) ctx.setParams({ vessel: match[1] });
      },
    });
    map.setRasters(baseRasters(caseDoc, ctx.caseId, { kind: "vv", opacity: 0.75 }));
    paint();
    map.fitContent(0.1);
    ctx.onCleanup(() => map.destroy());
  });

  return U.card(
    "Tracks against the estimated release zone",
    {
      id: "map",
      hint: `${candidates.length} synthetic tracks`,
      note:
        "Every track on this map was generated by this repository. Click a track to select " +
        "the vessel; the selected one is drawn in amber over the others.",
      actions: controlsHost,
    },
    h(
      "div",
      { class: "stack stack--tight" },
      holder,
      mapLegend([
        { label: "Observed slick", colour: "var(--oil)" },
        { label: "Estimated origin zone", colour: "var(--reference)", shape: "dash" },
        { label: "Drift envelope", colour: "var(--drift)", shape: "dash" },
        { label: "Synthetic vessel track", colour: "var(--synthetic)", shape: "line" },
        { label: "Selected vessel", colour: "var(--oil)", shape: "line" },
      ]),
    ),
  );
}

// -- ranking -----------------------------------------------------------------

/**
 * The ranked list, twice: a table on desktop and cards below 780 px.
 *
 * Both are in the DOM and CSS decides which is visible, which keeps the print stylesheet
 * able to force the table regardless of the viewport it was printed from.
 */
function rankingCard(ctx, candidates, selectedMmsi) {
  const select = (mmsi) => ctx.setParams({ vessel: String(mmsi) });

  const rows = candidates.map((candidate) => {
    const isSelected = String(candidate.mmsi) === selectedMmsi;
    return h(
      "tr",
      {
        "aria-selected": String(isSelected),
        tabindex: "0",
        onClick: () => select(candidate.mmsi),
        onKeydown: (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select(candidate.mmsi);
          }
        },
      },
      h("td", { class: "mono" }, String(candidate.rank)),
      h(
        "td",
        { class: "wrap" },
        h("div", null, candidate.name),
        h("div", { class: "small muted mono" }, `MMSI ${candidate.mmsi}`),
      ),
      h("td", null, candidate.type),
      h("td", { class: "right mono" }, F.num(candidate.score, 1)),
      h("td", { class: "right mono" }, F.km(candidate.evidence?.closestApproachKm, 2)),
      h("td", { class: "right mono" }, F.utc(candidate.evidence?.closestApproachUtc)),
      h("td", { class: "wrap small" }, candidate.band),
    );
  });

  const cards = candidates.map((candidate) =>
    h(
      "button",
      {
        class: "candidate-card",
        type: "button",
        "aria-selected": String(String(candidate.mmsi) === selectedMmsi),
        onClick: () => select(candidate.mmsi),
      },
      h("span", { class: "candidate-card__rank" }, String(candidate.rank)),
      h(
        "span",
        { class: "candidate-card__main" },
        h("span", { class: "candidate-card__name" }, candidate.name),
        h(
          "span",
          { class: "candidate-card__meta" },
          `${candidate.type} · ${F.km(candidate.evidence?.closestApproachKm, 2)} km · ` +
            `${F.utc(candidate.evidence?.closestApproachUtc)}`,
        ),
        h("span", { class: "candidate-card__meta" }, candidate.band),
      ),
      h("span", { class: "candidate-card__score" }, F.num(candidate.score, 1)),
    ),
  );

  return U.card(
    "Priority candidates",
    {
      id: "ranking",
      hint: `${candidates.length} scored`,
      note:
        "Ranked by total score. Ties are broken by closest approach. Selecting a row updates " +
        "the map, the score breakdown and the evidence panel.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      h(
        "div",
        { class: "table-wrap candidates-table" },
        h(
          "table",
          { class: "table" },
          h(
            "thead",
            null,
            h(
              "tr",
              null,
              h("th", null, "#"),
              h("th", null, "Vessel (synthetic)"),
              h("th", null, "Type"),
              h("th", { class: "right" }, "Score"),
              h("th", { class: "right" }, "Closest km"),
              h("th", { class: "right" }, "At (UTC)"),
              h("th", null, "Band"),
            ),
          ),
          h("tbody", null, rows),
        ),
      ),
      h("div", { class: "candidates-cards" }, cards),
    ),
  );
}

// -- the selected vessel ----------------------------------------------------

function scoreCard(candidate) {
  const detail = candidate.componentDetail || [];
  return U.card(
    "Score breakdown",
    { id: "score", hint: `rank ${candidate.rank}` },
    h(
      "div",
      { class: "stack stack--tight" },
      h(
        "div",
        { class: "score" },
        U.scoreRing(candidate.score, candidate.scoreMax),
        h(
          "div",
          { style: { "min-width": 0 } },
          h("div", { style: { "font-weight": "600" } }, candidate.name),
          h("div", { class: "small muted mono" }, `MMSI ${candidate.mmsi} · ${candidate.type}`),
          h(
            "div",
            { class: "inline", style: { "margin-top": "var(--s2)" } },
            U.badge(candidate.status, "warn", {
              title:
                "This is the only status the pipeline assigns. It is a request to look, not a finding.",
            }),
            candidate.synthetic ? U.badge("Synthetic", "synthetic") : null,
          ),
        ),
      ),
      h(
        "div",
        { class: "bars" },
        detail.map((part) =>
          h(
            "div",
            { class: "bar" },
            h("div", { class: "bar__label", title: part.reason }, part.component),
            h(
              "div",
              { class: "bar__track", role: "img", "aria-label": `${part.component}: ${part.score} of ${part.max}` },
              h("div", {
                class: "bar__fill",
                style: {
                  width: `${part.max > 0 ? (part.score / part.max) * 100 : 0}%`,
                  background: COMPONENT_TONE[part.component] || C.neutral,
                },
              }),
            ),
            h("div", { class: "bar__value" }, `${F.num(part.score, 1)}/${part.max}`),
          ),
        ),
      ),
      h(
        "p",
        { class: "small muted" },
        candidate.band || "",
      ),
    ),
  );
}

function evidenceCard(candidate, attribution) {
  const evidence = candidate.evidence || {};
  return U.card(
    "Measured quantities",
    {
      id: "evidence",
      note:
        "Each of these is computed from the synthetic track and the drift output. They are " +
        "the inputs to the score, not a summary of it.",
    },
    U.rows(
      U.row("Closest approach", `${F.km(evidence.closestApproachKm, 3)} km`, { mono: true }),
      U.row(
        "As a fraction of the envelope",
        `${F.num(evidence.closestApproachRadii, 3)} × the ${F.km(evidence.envelopeRadiusKm, 2)} km radius`,
        { stack: true, mono: true },
      ),
      U.row("At", F.utc(evidence.closestApproachUtc, { seconds: true }), { mono: true }),
      U.row("Course at approach", F.bearing(evidence.courseAtApproachDeg), { mono: true }),
      U.row("Speed at approach", `${F.num(evidence.sogAtApproachKn, 1)} kn`, { mono: true }),
      U.row(
        "Reports inside the window",
        `${F.int(evidence.reportsInWindow)} of ${F.int(evidence.reportsTotal)}`,
        { mono: true },
      ),
      U.row("Reports near the envelope", F.int(evidence.reportsNearEnvelope), { mono: true }),
      U.row("Time near the envelope", `${F.num(evidence.minutesNearEnvelope, 1)} min`, { mono: true }),
      U.row(
        "Nearest pass outside the window",
        evidence.nearestPassOutsideWindowKm === null
          ? "none — the track does not leave the window"
          : `${F.km(evidence.nearestPassOutsideWindowKm, 2)} km`,
        { stack: true },
      ),
      U.row("First report", F.utc(evidence.firstReportUtc, { seconds: true }), { mono: true }),
      U.row("Last report", F.utc(evidence.lastReportUtc, { seconds: true }), { mono: true }),
      U.row("Window basis", attribution.releaseWindow?.basis, { stack: true }),
    ),
  );
}

/** The rule text behind every point, straight from the scorer. Nothing paraphrased. */
function explanationCard(candidate, attribution) {
  const lines = candidate.explanation || [];
  const method = attribution.method || {};
  return U.card(
    "Why this score",
    { id: "why", hint: `${lines.length} rules applied` },
    h(
      "div",
      { class: "stack stack--tight" },
      lines.length
        ? h(
            "ul",
            { class: "bullets" },
            lines.map((line) => h("li", null, h("span", null, line))),
          )
        : U.emptyState({ title: "No explanation stored" }),
      h("div", { class: "divider" }),
      h("div", { class: "small muted" }, "The rules themselves, independent of this vessel:"),
      U.rows(
        ...Object.entries(method).map(([key, text]) => U.row(F.label(key), text, { stack: true })),
      ),
    ),
  );
}

function trackQualityCard(caseDoc, candidate) {
  const vessel = (caseDoc.ais?.vessels || []).find(
    (entry) => String(entry.mmsi) === String(candidate.mmsi),
  );
  if (!vessel) {
    return U.card(
      "Track quality",
      { id: "quality" },
      U.missingState({
        title: "No raw track stored",
        body: "This candidate has a score but the case did not store its underlying reports.",
      }),
    );
  }
  const cleaning = vessel.cleaning || {};
  const rejected = cleaning.rejected || {};

  return U.card(
    "Track quality",
    {
      id: "quality",
      hint: `${F.int(cleaning.accepted)} of ${F.int(vessel.rawReportCount)} reports kept`,
      note:
        "The generator injects the defects real AIS has — dropped reports, duplicate " +
        "timestamps, impossible jumps, missing fields — and the same cleaning stage that " +
        "would run on real data runs on it.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      stackBar(
        [
          { label: "Accepted", value: cleaning.accepted || 0, colour: C.agree },
          { label: "Rejected", value: cleaning.rejectedTotal || 0, colour: C.danger },
        ],
        { height: 8 },
      ),
      U.rows(
        U.row("Reports accepted", F.int(cleaning.accepted), { mono: true }),
        U.row("Bad position", F.int(rejected.badPosition), { mono: true }),
        U.row("Duplicate timestamp", F.int(rejected.duplicateTimestamp), { mono: true }),
        U.row("Impossible speed", F.int(rejected.impossibleSpeed), { mono: true }),
        U.row("Unparsable time", F.int(rejected.unparsableTime), { mono: true }),
        U.row(
          "Completeness",
          `${F.pct(cleaning.reportCompleteness)} of the ${F.int(cleaning.expectedReports)} expected at ` +
            `${F.int(cleaning.expectedIntervalS)} s intervals`,
          { stack: true },
        ),
        U.row("Gaps", F.int(cleaning.gaps), { mono: true }),
        U.row("Largest gap", `${F.num(cleaning.largestGapMinutes, 1)} min`, { mono: true }),
        U.row("Missing field values", F.int(cleaning.missingFieldValues), { mono: true }),
        U.row("Field completeness", F.pct(cleaning.fieldCompleteness), { mono: true }),
        U.row("Speed range", `${F.num(vessel.minSogKn, 1)} – ${F.num(vessel.maxSogKn, 1)} kn, median ${F.num(vessel.medianSogKn, 1)}`, { stack: true }),
        U.row("Reports on a land cell", F.int(vessel.reportsOnLandMask), { mono: true }),
      ),
    ),
  );
}

/**
 * How this vessel was invented. Stating the generator's own intent is the strongest
 * possible reminder that the ranking above is a demonstration of a method, not a finding:
 * the pipeline ranked a track that this repository wrote on purpose.
 */
function generatorCard(caseDoc, candidate) {
  const vessel = (caseDoc.ais?.vessels || []).find(
    (entry) => String(entry.mmsi) === String(candidate.mmsi),
  );
  const repro = caseDoc.ais?.reproducibility || {};

  return U.card(
    "How this track was generated",
    { id: "generator", hint: vessel?.pattern ? F.label(vessel.pattern) : null },
    h(
      "div",
      { class: "stack stack--tight" },
      U.notice(
        vessel?.generatorStory ||
          "This vessel was generated by this repository from a fixed seed. It does not " +
            "correspond to any real ship.",
        { kind: "synthetic", strongPrefix: "Generated on purpose." },
      ),
      U.rows(
        U.row("Behaviour pattern", vessel?.pattern ? F.label(vessel.pattern) : null),
        U.row("Type relevance", candidate.typeKey ? F.label(candidate.typeKey) : null),
        U.row("Type rationale", vessel?.typeRationale, { stack: true }),
        U.row("Length", vessel?.lengthM ? `${F.num(vessel.lengthM, 0)} m` : null, { mono: true }),
        U.row("Reports", F.int(vessel?.reportCount), { mono: true }),
        U.row("Reporting interval", `${F.int(caseDoc.ais?.config?.reportIntervalS)} s`, { mono: true }),
        U.row("Seed", F.int(repro.seed), { mono: true }),
        U.row("Derivation", repro.derivation, { stack: true, mono: true }),
      ),
      h(
        "div",
        { class: "inline" },
        icon(ICONS.info, { size: 14 }),
        h("span", { class: "small muted" }, repro.note || ""),
      ),
      h(
        "p",
        { class: "small muted" },
        `The generator produced ${F.int(caseDoc.ais?.counts?.vessels)} vessels and ` +
          `${F.int(caseDoc.ais?.counts?.reports)} reports across ` +
          `${F.int((caseDoc.ais?.counts?.patterns || []).length)} behaviour patterns: ` +
          `${(caseDoc.ais?.counts?.patterns || []).map(F.label).join(", ")}.`,
      ),
    ),
  );
}
