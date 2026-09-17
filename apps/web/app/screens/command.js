/** Screen 1: Command centre.
 *
 * The question this screen answers is "is there a spill, how big, roughly where did it come
 * from, and how many vessels are worth looking at" - in that order, above the fold, before
 * any control. Everything else on it is provenance: which numbers came from the supplied
 * data, which came from the model, and which are synthetic.
 */

import { h, icon } from "../dom.js";
import { ICONS } from "../icons.js";
import * as F from "../format.js";
import * as U from "../ui.js";
import { createMap, mapLegend } from "../mapview.js";
import { sceneVectors } from "../layers.js";

export function render(ctx) {
  const { caseDoc, caseStatus, state } = ctx;

  if (caseStatus !== "ready" || !caseDoc) {
    return U.stateSwitch(caseStatus, caseDoc, () => null, {
      loadingTitle: "Loading the case",
      loadingBody: "Reading the stored case document.",
      missing: {
        title: "No case has been computed yet",
        body: "Build the seeded demo case, then reload. It runs the whole pipeline on one supplied scene with fixed seeds.",
        command: ".venv/bin/python scripts/run_api.py --build-demo",
      },
      failed: {
        title: "The case could not be loaded",
        body: "The API answered, but not with a case document.",
        detail: state.caseError ? String(state.caseError.message) : undefined,
      },
    });
  }

  const slick = caseDoc.slick || {};
  const window_ = caseDoc.attribution?.releaseWindow || caseDoc.ais?.releaseWindow;
  const age = caseDoc.spillAge;
  const candidates = caseDoc.attribution?.candidates || caseDoc.vessels || [];
  const top = candidates[0];
  const scene = caseDoc.scene || {};
  // How many of the screened vessels survived both halves of the relevance test. This, not
  // the raw candidate count, is the number a duty officer acts on: the shortlist worth a look.
  const relevant = caseDoc.attribution?.relevantCount;
  const screened = caseDoc.attribution?.candidateCount ?? candidates.length;

  // Each numbered answer links to the screen that shows its working, so the summary is a
  // table of contents for the investigation rather than a dead end.
  const jump = (label, path, extra) =>
    U.button(label, {
      kind: "quiet",
      small: true,
      iconAfter: ICONS.chevronRight,
      onClick: () => ctx.navigate(path, extra),
    });

  return h(
    "div",
    { class: "stack" },

    // -- the headline finding ---------------------------------------------
    // One number, inverted, above everything: is there a spill and how big. The aside
    // carries the payoff of the whole pipeline -- the one vessel to look at first -- so the
    // first glance already spans detection to triage. It is a score, not a verdict, and it
    // keeps the "priority candidate for investigation" wording that the brief requires.
    U.hero({
      label: "Total detected slick area",
      value: F.km2(slick.totalAreaKm2),
      unit: "km²",
      sub: slick.slickCount
        ? `Across ${F.int(slick.slickCount)} disconnected region${slick.slickCount === 1 ? "" : "s"} in the supplied ${scene.region || "scene"}, acquired ${F.utc(scene.acquiredUtc || scene.acquiredStartUtc)}.`
        : "No slick was measured in this scene.",
      chips: [
        scene.mission
          ? U.chip(`${scene.mission} ${scene.mode || ""}`.trim(), { iconPath: ICONS.satellite })
          : null,
        // A scene the operator supplied is outside the evaluated dataset, which is the one
        // qualification that applies to every number on this screen at once.
        scene.isUpload
          ? U.chip("Operator-supplied scene", { tone: "synthetic", iconPath: ICONS.upload })
          : null,
        U.chip(
          caseDoc.detection?.source === "model" ? "U-Net prediction" : "Supplied reference mask",
          { tone: caseDoc.detection?.source === "model" ? "oil" : "reference" },
        ),
        // Read off the feed rather than hardcoded: an uploaded AIS extract makes the word
        // "synthetic" false, and a chip that stayed put would be the first thing a reader
        // saw and the one thing on the screen that was wrong.
        caseDoc.provenance?.aisMode === "real"
          ? U.chip("Real AIS extract", { tone: "reference", iconPath: ICONS.ship })
          : U.chip("Synthetic AIS", { tone: "synthetic" }),
        caseDoc.demo ? U.chip("Seeded offline case", { tone: "reference" }) : null,
      ].filter(Boolean),
      aside: [
        top
          ? U.heroStat({
              value: F.num(top.score, 1),
              unit: `/ ${top.scoreMax}`,
              label: `Priority candidate · ${top.name}`,
              title: top.band || caseDoc.attribution?.candidateLabel || "",
            })
          : null,
        U.button("Open investigation", {
          kind: "primary",
          iconAfter: ICONS.arrowRight,
          onClick: () => ctx.navigate("/imagery"),
          title: "Start at the imagery and work through to the candidate ranking",
        }),
      ].filter(Boolean),
    }),

    // -- bring your own scene ---------------------------------------------
    // One quiet row, directly under the headline, because this is the first question a
    // visitor asks of a detection product -- "does it work on my data?" -- and an intake
    // with no mention on the front page reads as something the product does not really
    // offer. The form itself lives on its own screen: it has five slots and four fields,
    // and unfolding all of that here would bury the four answers below it.
    h(
      "div",
      { class: "inline no-print", style: { gap: "var(--s3)" } },
      U.button("Analyse your own scene", {
        kind: "quiet",
        small: true,
        iconPath: ICONS.upload,
        iconAfter: ICONS.arrowRight,
        onClick: () => ctx.navigate("/new"),
        title: "Upload a Sentinel-1 scene and run this pipeline on it",
      }),
      h(
        "span",
        { class: "small muted" },
        "SAR GeoTIFF required · wind, currents and AIS optional",
      ),
    ),

    // -- the investigation, in four numbered answers ----------------------
    // The same four figures as before, but read as a sequence a person could say out loud:
    // what is in the water, when it started, how old it is, who to look at. The step number
    // gives the row an order so the eye is not asked to weigh four equal boxes at once.
    h(
      "div",
      { class: "grid grid--stats", id: "summary" },
      U.answer({
        step: 1,
        tone: "oil",
        question: "How large is the main slick?",
        value: F.km2(slick.areaKm2),
        unit: "km²",
        sub: slick.touchesSceneEdge
          ? "the largest connected region; it touches the scene edge, so it may continue beyond the image"
          : "the largest connected region, fully inside the scene footprint",
        missing: "no slick measured",
        action: jump("Imagery", "/imagery"),
      }),
      U.answer({
        step: 2,
        tone: "drift",
        question: "When could it have been released?",
        value: window_ ? F.hours(window_.hours) : null,
        unit: window_ ? "window" : undefined,
        sub: window_ ? `${F.utc(window_.startUtc)} → ${F.utc(window_.endUtc)}` : undefined,
        missing: "drift not run",
        action: jump("Drift", "/drift"),
      }),
      U.answer({
        step: 3,
        tone: "reference",
        question: "How old is the oil?",
        value: age ? `≤ ${F.hours(age.maxHours)}` : null,
        // The upper bound is the honest headline: one acquisition bounds the age by the
        // hindcast horizon and cannot narrow it further. The sub-line says which it is.
        sub: age
          ? age.resolution?.resolvable
            ? `resolvable from ${F.hours(age.resolution.fromHours)} back`
            : "bounded by the hindcast horizon, not narrowed by one image"
          : undefined,
        missing: "drift not run",
        action: jump("Drift", "/drift"),
      }),
      U.answer({
        step: 4,
        tone: "synthetic",
        question: "How many vessels warrant a look?",
        value: F.int(relevant != null ? relevant : candidates.length),
        sub: candidates.length
          ? relevant != null
            ? `of ${F.int(screened)} screened near the origin${top ? ` · top ${F.num(top.score, 1)} — ${top.name}` : ""}`
            : top
              ? `top score ${F.num(top.score, 1)} of ${top.scoreMax} — ${top.name}`
              : undefined
          : undefined,
        missing: "attribution not run",
        action: jump("Ranking", "/vessels"),
      }),
    ),

    slick.areaMethod
      ? h("p", { class: "small muted", style: { "max-width": "92ch", padding: "0 4px" } }, slick.areaMethod)
      : null,

    // -- the two things a presenter actually opens: where, and who --------
    h(
      "div",
      { class: "grid grid--wide-left" },
      locatorCard(ctx, caseDoc),
      shortlistCard(ctx, caseDoc, candidates),
    ),

    // -- the audit trail, folded away until someone wants to check it -----
    // Product id, checkpoint, threshold, stage timings: on the screen so the findings above
    // are verifiable, closed so they do not compete with them.
    h(
      "div",
      { class: "stack stack--tight" },
      acquisitionCard(caseDoc),
      inputsCard(caseDoc),
      provenanceCard(caseDoc),
      processingCard(ctx, caseDoc),
    ),

    limitsCard(caseDoc),
  );
}

// -- cards ------------------------------------------------------------------

function locatorCard(ctx, caseDoc) {
  const holder = h("div", { style: { height: "clamp(340px, 54vh, 620px)" } });

  // The map has a lifetime, so it is built after mount and torn down on navigation.
  requestAnimationFrame(() => {
    if (!holder.isConnected) return;
    const map = createMap(holder, { readout: false, onSelect: null });
    const { rasters, vectors } = sceneVectors(caseDoc, ctx, {
      includeDrift: true,
      includeVessels: false,
      // The one map in the app that colours regions by the look-alike verdict. It is the
      // first thing anyone looks at, and "twelve orange rings" overstates what the
      // pipeline actually concluded about six of them.
      byVerdict: true,
    });
    // A locator keeps a little context around the finding, but only a little: `fitFindings`
    // frames the slick, the drift path and the origin zone together, and a larger pad than
    // this spends the canvas on empty water rather than on the thing being located.
    map.setRasters(rasters).setVectors(vectors).fitFindings(0.16);
    ctx.onCleanup(() => map.destroy());
  });

  return U.card(
    "Where",
    {
      id: "locator",
      hint: caseDoc.scene?.region,
      note:
        "The backdrop is the supplied VV backscatter for this scene, drawn at its own " +
        "corner coordinates. There is no third-party basemap, so nothing here implies an " +
        "alignment that was not measured.",
    },
    holder,
    mapLegend([
      { label: "Consistent with oil", colour: "var(--oil)" },
      { label: "Uncertain — kept for review", colour: "var(--oil)", shape: "dash" },
      { label: "Screened out as a look-alike", colour: "var(--lookalike)", shape: "dash" },
      caseDoc.scene?.hasReferenceMask
        ? { label: "Supplied reference mask", colour: "var(--reference)", shape: "dash" }
        : null,
      { label: "Backward drift to origin", colour: "var(--drift)" },
      { label: "Estimated origin zone", colour: "var(--reference)", shape: "dash" },
    ]),
    verdictPanel(ctx, caseDoc),
  );
}

/** Verdict -> the colour it is drawn in above, and what the verdict means in one line. */
const VERDICT_ROWS = [
  {
    key: "accepted",
    label: "Consistent with oil",
    colour: "var(--oil)",
    blurb: "dark, sharp-edged and elongated the way a film is",
  },
  {
    key: "uncertain",
    label: "Uncertain",
    colour: "var(--oil)",
    blurb: "between the two thresholds, so kept for a human to settle",
  },
  {
    key: "rejected",
    label: "Screened out as a look-alike",
    colour: "var(--lookalike)",
    blurb: "more consistent with something else dark on the water",
  },
  {
    key: "unscreened",
    label: "Not screened",
    colour: "var(--text-tertiary)",
    blurb: "no clear water around it to measure against, so no verdict",
  },
];

/**
 * Which of the drawn regions the look-alike screen calls oil, and which it does not.
 *
 * The screen has already run -- every published region carries a verdict in the case
 * document, and the Slick screen shows the working. This is the one-glance version, sitting
 * under the map so the colours have a key that carries counts and area rather than only a
 * name. Area is summed from the regions themselves, not from `slick.totalAreaKm2`, because
 * that total spans every published region regardless of verdict.
 */
function verdictPanel(ctx, caseDoc) {
  const regions = caseDoc.geometry?.slicks || [];
  const screening = caseDoc.screening;
  if (!regions.length) return null;

  const tally = new Map();
  for (const region of regions) {
    const key = region.screening?.label || "unscreened";
    const entry = tally.get(key) || { count: 0, areaKm2: 0 };
    entry.count += 1;
    entry.areaKm2 += Number(region.areaKm2) || 0;
    tally.set(key, entry);
  }

  // Nothing was screened: say that, rather than drawing a split that was never computed.
  if (screening?.fitted === false || (tally.size === 1 && tally.has("unscreened"))) {
    return h(
      "p",
      { class: "card__note" },
      `The look-alike screen did not run on this case, so all ${F.int(regions.length)} ` +
        "regions are drawn the same and none of them carries a verdict.",
    );
  }

  const rows = VERDICT_ROWS.filter((row) => tally.has(row.key)).map((row) => {
    const { count, areaKm2 } = tally.get(row.key);
    return h(
      "div",
      { class: "verdict__row" },
      h("span", { class: "verdict__swatch", style: { background: row.colour } }),
      h(
        "div",
        { class: "verdict__text" },
        h(
          "div",
          null,
          h("span", { class: "verdict__count mono" }, F.int(count)),
          h("span", null, ` ${count === 1 ? "region" : "regions"} · ${row.label}`),
        ),
        h("div", { class: "small muted" }, row.blurb),
      ),
      h("div", { class: "verdict__area mono" }, `${F.km2(areaKm2)} km²`),
    );
  });

  return h(
    "div",
    { class: "verdict" },
    h(
      "div",
      { class: "verdict__head small muted" },
      `Of the ${F.int(regions.length)} regions drawn above, by the look-alike screen:`,
    ),
    rows,
    screening?.components?.note
      ? h("p", { class: "card__note" }, screening.components.note)
      : null,
    U.button("See the screen's working", {
      kind: "quiet",
      small: true,
      iconAfter: ICONS.chevronRight,
      onClick: () => ctx.navigate("/slick"),
    }),
  );
}

function acquisitionCard(caseDoc) {
  const scene = caseDoc.scene;
  if (!scene) {
    // Left open: a missing scene block is a fault, and a fault should not be behind a fold.
    return U.foldout(
      "Acquisition",
      { id: "acq", open: true },
      U.missingState({
        title: "No acquisition metadata",
        body: "This case has no scene block, which should not happen for a stored case.",
      }),
    );
  }
  // The mission and the timestamp are the one line worth reading without opening the card:
  // they say which satellite pass every figure above was measured from. `scene.name` is a
  // patch index in this dataset ("00053") and would tell a reader nothing.
  //
  // `acquiredUtc` rather than `acquiredStartUtc`, because that is the instant the pipeline
  // actually ran against: for a dataset scene the two are the same, and for an uploaded one
  // with no product header only the first exists.
  const acquired = scene.acquiredUtc || scene.acquiredStartUtc;
  const typed = scene.acquiredSource === "operator-supplied";
  return U.foldout(
    "Acquisition",
    {
      id: "acq",
      hint: `${scene.mission || (scene.isUpload ? "Operator-supplied" : F.DASH)} · ${F.utc(acquired)}`,
      note: scene.regionNote,
    },
    U.rows(
      U.row("Scene", scene.name, { mono: true }),
      U.row("Mission", `${scene.mission || F.DASH} · ${scene.mode || F.DASH} ${scene.productType || ""}`.trim()),
      U.row("Polarisations", (scene.polarisations || []).join(" / ")),
      U.row("Acquired", F.utc(acquired, { seconds: true }), { mono: true }),
      // Which of the two it was. A time typed into a form and one read off the product both
      // produce a working case and they are not equally trustworthy, so the row is always
      // present rather than appearing only when something is wrong with it.
      scene.acquiredSource
        ? U.row(
            "Time taken from",
            typed ? "typed by the operator, read as UTC" : "the product's own header",
            { stack: true, muted: !typed },
          )
        : null,
      U.row("Centre", F.latLon([
        (scene.bounds?.[0] + scene.bounds?.[2]) / 2,
        (scene.bounds?.[1] + scene.bounds?.[3]) / 2,
      ]), { mono: true }),
      U.row("Extent", `${scene.width} × ${scene.height} px`, { mono: true }),
      U.row("CRS", scene.crs, { mono: true }),
      // Only worth a row when the bands were not named by a header: for every dataset scene
      // it says the same thing, and a row that never varies is noise.
      scene.bandBasis && !scene.bandBasis.startsWith("band names read")
        ? U.row("Band order", scene.bandBasis, { stack: true })
        : null,
      U.row("Reference mask", scene.hasReferenceMask ? "supplied" : "not supplied"),
      U.row("Product", F.clip(scene.productId, 44), { mono: true, stack: true, title: scene.productId }),
    ),
  );
}

/**
 * Where each of the five inputs came from.
 *
 * Only rendered for a case that has any operator-supplied input: for a dataset case every
 * row would read "from the audited dataset", which the Provenance card below already says
 * in one line. When something *was* supplied, the five rows are the whole point — they say
 * which figures on this screen rest on the audited data and which rest on a file the
 * server had never seen before, and they say it slot by slot rather than as one verdict
 * over the case.
 */
function inputsCard(caseDoc) {
  const inputs = caseDoc.inputs;
  if (!inputs) return null;
  const slots = [
    ["scene", "SAR scene"],
    ["mask", "Reference mask"],
    ["era5", "Wind"],
    ["cmems", "Currents"],
    ["ais", "AIS"],
  ];
  const supplied = slots.filter(([key]) => inputs[key]?.source === "operator");
  if (!supplied.length) return null;

  // The forcing stage re-checks each supplied file against this scene and records what it
  // decided. A file that was uploaded but not used says so here, with the reason, rather
  // than looking identical to one that was.
  const decisions = caseDoc.forcing?.supplied || {};

  return U.foldout(
    "Inputs",
    {
      id: "inputs",
      hint: `${supplied.length} of 5 operator-supplied`,
      note: inputs.note,
      open: true,
    },
    U.rows(
      ...slots.map(([key, label]) => {
        const slot = inputs[key] || {};
        const operator = slot.source === "operator";
        const decision = decisions[key];
        // `used === false` is a real answer and `undefined` is "no decision was recorded
        // for this slot", so the check is explicit rather than falsy.
        const rejected = operator && decision && decision.used === false;
        const detail = rejected
          ? `${slot.file} — not used: ${decision.error || decision.note || "it does not apply to this scene"}`
          : operator
            ? slot.file
            : slot.note;
        return U.row(label, detail, {
          stack: true,
          muted: !operator,
          mono: operator && !rejected,
          title: operator ? slot.note : undefined,
        });
      }),
    ),
  );
}

function provenanceCard(caseDoc) {
  const detection = caseDoc.detection || {};
  // Closed, but the hint still says whether the slick above came from the model or from the
  // supplied mask -- the single fact that decides how much of this case is our own work.
  return U.foldout(
    "Provenance",
    {
      id: "prov",
      hint:
        detection.source === "model"
          ? `U-Net · threshold ${F.num(detection.threshold, 2)}`
          : F.label(detection.source),
      note: detection.note,
    },
    h(
      "div",
      { class: "stack stack--tight" },
      U.rows(
        U.row("Detection source", detection.source === "model" ? "U-Net prediction" : F.label(detection.source)),
        U.row("Scene threshold", F.num(detection.threshold, 2), { mono: true }),
        U.row("Threshold chosen on", detection.thresholdSelectedOn),
        U.row("Model parameters", F.int(detection.modelParameters), { mono: true }),
        U.row("Checkpoint", detection.checkpoint, { mono: true }),
        U.row("Pipeline", caseDoc.pipelineVersion, { mono: true }),
      ),
    ),
  );
}

function processingCard(ctx, caseDoc) {
  const timing = caseDoc.timing;
  const job = ctx.state.job;
  const stages = timing?.stages || [];
  const slowest = stages.reduce((acc, s) => Math.max(acc, s.seconds || 0), 0);

  return U.foldout(
    "Processing status",
    {
      id: "timing",
      hint: timing ? `${F.seconds(timing.totalSeconds)} total` : null,
      note:
        "Timings are from the run that produced this stored case, measured on this machine. " +
        "A cached case is served without recomputing, so a reload does not repeat them.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      job && job.state
        ? h(
            "div",
            { class: "inline small" },
            job.state === "running" || job.state === "queued"
              ? h("span", { class: "spinner" })
              : icon(job.state === "failed" ? ICONS.warning : ICONS.check, { size: 14 }),
            h("span", { class: job.state === "failed" ? "oil-text" : "muted" },
              `Last submitted ${F.label(job.kind)} job: ${job.state}${job.error ? ` — ${job.error}` : ""}`),
          )
        : null,
      h(
        "div",
        { class: "bars" },
        stages.map((stage) =>
          h(
            "div",
            { class: "bar" },
            h("div", { class: "bar__label" }, F.label(stage.stage)),
            h(
              "div",
              { class: "bar__track" },
              h("div", {
                class: "bar__fill",
                style: {
                  width: `${slowest > 0 ? ((stage.seconds || 0) / slowest) * 100 : 0}%`,
                  background: "var(--reference)",
                },
              }),
            ),
            h("div", { class: "bar__value" }, F.seconds(stage.seconds)),
            // What the stage actually did, under its own bar rather than in a block of ten
            // sentences below the chart. A `title` tooltip would be shorter but unreachable
            // by keyboard and invisible on a touch screen, and this text is the explanation.
            stage.note ? h("div", { class: "bar__note" }, stage.note) : null,
          ),
        ),
      ),
      // Each stage's note now sits under its own bar, so there is nothing left to repeat here.
      stages.length
        ? null
        : U.emptyState({ title: "No stage timings", body: "This case was stored without a timing block." }),
    ),
  );
}

function shortlistCard(ctx, caseDoc, candidates) {
  const top = candidates.slice(0, 4);
  return U.card(
    "Candidate shortlist",
    {
      id: "shortlist",
      hint: caseDoc.attribution?.candidateLabel,
      note: caseDoc.attribution?.caveat,
      actions: U.button("All candidates", {
        kind: "quiet",
        small: true,
        iconPath: ICONS.chevronRight,
        onClick: () => ctx.navigate("/vessels"),
      }),
    },
    top.length
      ? h(
          "div",
          { class: "stack stack--tight" },
          top.map((entry) =>
            h(
              "button",
              {
                class: "candidate-card",
                type: "button",
                onClick: () => ctx.navigate("/vessels", { vessel: entry.mmsi }),
              },
              h("span", { class: "candidate-card__rank" }, String(entry.rank)),
              h(
                "span",
                { class: "candidate-card__main" },
                h("span", { class: "candidate-card__name" }, entry.name),
                h(
                  "span",
                  { class: "candidate-card__meta" },
                  `${entry.type} · closest approach ${F.km(entry.evidence?.closestApproachKm)} km · ${entry.band || ""}`,
                ),
              ),
              h("span", { class: "candidate-card__score" }, F.num(entry.score, 1)),
            ),
          ),
        )
      : U.missingState({
          title: "No candidates",
          body: "Attribution has not run for this case, or no synthetic vessel passed near the estimated origin.",
        }),
  );
}

function limitsCard(caseDoc) {
  const limits = caseDoc.limits || [];
  if (!limits.length) return null;
  return U.card("What this does not tell you", { id: "limits" }, U.limitList(limits));
}
