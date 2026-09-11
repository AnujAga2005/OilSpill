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

    // -- the clause the statement was most prescriptive about -------------
    // "The irrelevant traffic is to be filtered out." So the funnel comes before the
    // shortlist: how ten vessels became two, then who the two are. It also means the
    // presenter's eye only ever travels down this screen.
    filterCard(attribution, caseDoc.provenance?.aisLabel || caseDoc.ais?.label),

    // -- the conclusion ---------------------------------------------------
    verdictCard(selected, attribution),

    mapCard(ctx, caseDoc, candidates, selectedMmsi),

    h(
      "div",
      { class: "grid grid--wide-left" },
      rankingCard(ctx, caseDoc, candidates, selectedMmsi),
      evidenceCard(selected, attribution),
    ),

    // -- the working, folded until someone wants to check it ---------------
    // Weights, rule text, track defects and the generator's own intent. All of it has to be
    // on the screen for the score to be auditable; none of it competes with the score.
    h(
      "div",
      { class: "stack stack--tight" },
      weightsCard(attribution),
      explanationCard(selected, attribution),
      trackQualityCard(caseDoc, selected),
      generatorCard(caseDoc, selected),
      feedCard(caseDoc),
    ),
  );
}

// -- the disclosure detail, under the sentence it supports -------------------

/**
 * How this feed was made and what its identifiers mean.
 *
 * The mandated one-line label is not here -- it is on the funnel card at the top of the
 * screen, unfolded, because a reader must not have to open anything to learn the traffic is
 * fabricated. What is here is everything that label implies and does not say: the
 * generator's disclaimer, the source string, the column count, and what each fabricated
 * field is and is not. Folded, because a reader who has taken the label at face value does
 * not need to be told four more times.
 */
function feedCard(caseDoc) {
  const ais = caseDoc.ais || {};
  const schema = ais.schema || {};
  const source = caseDoc.provenance?.aisSource || schema.label;
  const aisLabel = caseDoc.provenance?.aisLabel || ais.label;
  return U.foldout(
    "About this synthetic feed",
    { id: "disclosure", hint: source },
    h(
      "div",
      { class: "stack stack--tight" },
      // Two separate facts, deliberately shown as two rows: this feed is fabricated, and it
      // is fabricated in the format the problem statement names. The second is what makes
      // the first replaceable -- a real MarineCadastre extract loads through the same reader.
      source || aisLabel
        ? U.rows(
            // The label is stated in full on the funnel card at the top of this screen, so
            // the row here carries only the part the key does not already say -- a row
            // reading "AIS mode / AIS mode: ..." says it twice.
            aisLabel ? U.row("AIS mode", aisLabel.replace(/^AIS mode:\s*/i, "")) : null,
            source ? U.row("AIS source", source, { mono: true, title: schema.note || "" }) : null,
            schema.header
              ? U.row("Schema", `${schema.header.length} columns · ${schema.format || "AIS"}`, {
                  title: schema.header.join(", "),
                })
              : null,
          )
        : null,
      h(
        "ul",
        { class: "bullets" },
        // The generator's own disclaimer, in full. The row above carries the mandated
        // one-line label; this is the sentence that says what the label means.
        ais.disclaimer ? h("li", null, h("span", null, ais.disclaimer)) : null,
        h("li", null, h("span", null, ais.identifierNote || "")),
        h("li", null, h("span", null, ais.nameNote || "")),
        ais.imoNote ? h("li", null, h("span", null, ais.imoNote)) : null,
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
    { class: "stack stack--tight" },
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

/**
 * The weight budget on its own. The bars in the verdict card show what this vessel scored;
 * this shows the ceiling each component could have contributed, which is a different
 * question and only asked once.
 */
function weightsCard(attribution) {
  const weights = attribution.weights;
  if (!weights) return null;
  const count = Object.keys(weights).filter((key) => key !== "total").length;
  return U.foldout(
    "How the score is weighted",
    { id: "weights", hint: `${count} components · total ${F.int(weights.total)}`, note: attribution.caveat },
    weightsStrip(weights),
  );
}

/**
 * The traffic filter, as a funnel whose numbers add up.
 *
 * The problem statement asks for irrelevant traffic to be filtered out. A shortlist alone
 * cannot show that this happened, so the counts on both sides of the filter are published
 * here: what came in, what survived each test, and what was set aside. The excluded
 * vessels stay in the table below rather than disappearing, so the filter itself can be
 * checked against the rule stated on this card.
 */
function filterCard(attribution, aisLabel) {
  const funnel = attribution.filtering;
  if (!funnel) return null;
  const steps = [
    {
      label: "AIS reports ingested",
      value: F.int(funnel.aisReports),
      // The mandated one-line label, verbatim, on the stat that counts the reports it
      // describes. It used to ride in the badge strip at the top of every screen; with that
      // strip gone this is where it has to be, because a disclosure folded behind a
      // disclosure triangle is not a disclosure.
      sub: [`${F.int(funnel.vesselsSeen)} distinct vessels in the feed`, aisLabel]
        .filter(Boolean)
        .join(" · "),
    },
    {
      label: "In the release window",
      // The window itself, stated on the step that tests against it. "In the window" is an
      // unfalsifiable claim until the reader can see which window, and putting it here
      // costs nothing -- a separate card of context above the funnel cost 90 px and pushed
      // the shortlist off the first screenful.
      value: F.int(funnel.vesselsInWindow),
      sub:
        `${F.int(funnel.reportsInWindow)} reports inside the ${F.hours(attribution.releaseWindow?.hours)} ` +
        `window, ${F.utc(attribution.releaseWindow?.startUtc)} → ${F.utc(attribution.releaseWindow?.endUtc)}`,
    },
    {
      label: "Intersect the origin envelope",
      value: F.int(funnel.vesselsRelevant),
      sub:
        `within ${F.num(funnel.irrelevantRadii, 0)} × the ${F.km(attribution.originZone?.radiusKm, 1)} km ` +
        "origin radius, at their own timestamps",
    },
    {
      label: "Excluded as irrelevant",
      value: F.int(funnel.excludedTotal),
      sub:
        `${F.int(funnel.excludedOutsideWindow)} never in the window · ` +
        `${F.int(funnel.excludedTooFar)} in the window but too far`,
    },
  ];
  return U.card(
    "Traffic filtering",
    {
      id: "filtering",
      hint: `${F.int(funnel.vesselsRelevant)} of ${F.int(funnel.vesselsSeen)} relevant`,
      note: funnel.rule,
    },
    h(
      "div",
      { class: "stack stack--tight" },
      h(
        "div",
        { class: "grid grid--stats" },
        ...steps.map((step, index) =>
          U.stat({
            label: step.label,
            value: step.value,
            sub: step.sub,
            tone: index === 2 ? "reference" : index === 3 ? undefined : "synthetic",
          }),
        ),
      ),
      // `funnel.summary` used to be printed here as a mono line. It reads "1112 AIS reports ·
      // 10 vessels -> 9 with reports in the release window -> 2 intersect the origin envelope
      // -> 8 excluded", which is the four figures above restated as a sentence. Saying the
      // same thing twice, once as numbers and once as prose, is what makes a screen feel
      // padded. The figures stay; the restatement goes. `funnel.rule` is the card's note and
      // `retentionNote` is the auditability argument, so neither is a restatement.
      h("p", { class: "small muted" }, funnel.retentionNote),
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
    // The tracks and the slick, not the whole acquisition around them.
    map.fitFindings();
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
function rankingCard(ctx, caseDoc, candidates, selectedMmsi) {
  const select = (mmsi) => ctx.setParams({ vessel: String(mmsi) });

  const rows = candidates.map((candidate) => {
    const isSelected = String(candidate.mmsi) === selectedMmsi;
    const excluded = candidate.relevant === false;
    return h(
      "tr",
      {
        "aria-selected": String(isSelected),
        class: [excluded ? "is-excluded" : null],
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
      h(
        "td",
        { class: "wrap small" },
        // The filter verdict sits next to the band because the two answer different
        // questions: the band is how well the vessel scored, the tag is whether it was
        // near the oil at all. A high band on excluded traffic would otherwise mislead.
        excluded
          ? U.badge("Excluded — irrelevant traffic", "", { title: candidate.relevanceReason || "" })
          : null,
        excluded ? h("div", { class: "small muted", style: { "margin-top": "var(--s1)" } }, candidate.band) : candidate.band,
      ),
    );
  });

  const cards = candidates.map((candidate) =>
    h(
      "button",
      {
        class: ["candidate-card", candidate.relevant === false ? "is-excluded" : null],
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
        h(
          "span",
          { class: "candidate-card__meta" },
          candidate.relevant === false ? "Excluded — irrelevant traffic" : candidate.band,
        ),
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
        "Relevant traffic first, then total score; ties are broken by closest approach. " +
        "Vessels the filter set aside are kept at the bottom, dimmed and tagged, so the " +
        "filter can be audited. Selecting a row updates the map, the score breakdown and " +
        "the evidence panel.",
      // The export belongs on the table it exports, not on a summary card three positions up.
      actions: U.button("Candidates CSV", {
        kind: "quiet",
        small: true,
        iconPath: ICONS.download,
        onClick: () => {
          X.downloadCsv(X.exportName(caseDoc, "candidates", "csv"), X.candidatesCsv(caseDoc));
          ctx.announce("Candidate table downloaded as CSV.", { kind: "success" });
        },
      }),
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

/**
 * The screen's conclusion, promoted to the top.
 *
 * This used to be the fifth card down, titled "Score breakdown", underneath a "Ranking
 * basis" card that repeated the same score and a map that repeated the same shortlist. A
 * reader had to assemble one answer out of three cards. Now the vessel, its arithmetic and
 * the one status this pipeline is allowed to assign are a single block, and everything that
 * explains them is a foldout below.
 */
function verdictCard(candidate, attribution) {
  const detail = candidate.componentDetail || [];
  const excluded = candidate.relevant === false;
  return U.card(
    excluded ? "Selected vessel" : "First in the queue",
    {
      id: "score",
      hint: `rank ${candidate.rank} of ${F.int(attribution.candidateCount)} scored`,
    },
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
            excluded
              ? U.badge("Excluded — irrelevant traffic", "", {
                  title: candidate.relevanceReason || "",
                })
              : null,
          ),
          candidate.band
            ? h("p", { class: "small muted", style: { "margin-top": "var(--s2)" } }, candidate.band)
            : null,
        ),
      ),
      h(
        "div",
        { class: "bars" },
        detail.map((part) =>
          h(
            "div",
            { class: "bar" },
            h("div", { class: "bar__label" }, part.component),
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
            // The measured reason for the points, under its own bar. It was a `title`
            // tooltip on the label, which no keyboard and no touch screen can reach -- and
            // this sentence is the entire justification for the number beside it.
            part.reason ? h("div", { class: "bar__note" }, part.reason) : null,
          ),
        ),
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
      // The filter verdict comes first because it decides whether the rest of this panel
      // describes a candidate or a passer-by.
      candidate.relevant === undefined
        ? null
        : U.row(
            "Relevant traffic",
            candidate.relevant ? "yes" : "no — excluded as irrelevant traffic",
            { stack: true },
          ),
      candidate.relevanceReason
        ? U.row("On the basis that", candidate.relevanceReason, { stack: true })
        : null,
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
  return U.foldout(
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
    return U.foldout(
      "Track quality",
      { id: "quality", open: true },
      U.missingState({
        title: "No raw track stored",
        body: "This candidate has a score but the case did not store its underlying reports.",
      }),
    );
  }
  const cleaning = vessel.cleaning || {};
  const rejected = cleaning.rejected || {};

  return U.foldout(
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

  return U.foldout(
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
