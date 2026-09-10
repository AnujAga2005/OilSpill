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

export const LEDE =
  "One acquisition, one detection, one ranked shortlist. Every figure below is computed " +
  "from the supplied scene; nothing on this screen is a placeholder.";

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
        ? `Across ${F.int(slick.slickCount)} disconnected region${slick.slickCount === 1 ? "" : "s"} in the supplied ${scene.region || "scene"}, acquired ${F.utc(scene.acquiredStartUtc)}.`
        : "No slick was measured in this scene.",
      chips: [
        scene.mission
          ? U.chip(`${scene.mission} ${scene.mode || ""}`.trim(), { iconPath: ICONS.satellite })
          : null,
        U.chip(
          caseDoc.detection?.source === "model" ? "U-Net prediction" : "Supplied reference mask",
          { tone: caseDoc.detection?.source === "model" ? "oil" : "reference" },
        ),
        U.chip("Synthetic AIS", { tone: "synthetic" }),
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
      provenanceCard(caseDoc),
      processingCard(ctx, caseDoc),
    ),

    limitsCard(caseDoc),
  );
}

// -- cards ------------------------------------------------------------------

function locatorCard(ctx, caseDoc) {
  const holder = h("div", { style: { height: "clamp(260px, 38vh, 420px)" } });

  // The map has a lifetime, so it is built after mount and torn down on navigation.
  requestAnimationFrame(() => {
    if (!holder.isConnected) return;
    const map = createMap(holder, { readout: false, onSelect: null });
    const { rasters, vectors } = sceneVectors(caseDoc, ctx, {
      includeDrift: true,
      includeVessels: false,
    });
    map.setRasters(rasters).setVectors(vectors).fitContent(0.12);
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
      { label: "Predicted slick", colour: "var(--oil)" },
      caseDoc.scene?.hasReferenceMask
        ? { label: "Supplied reference mask", colour: "var(--reference)", shape: "dash" }
        : null,
      { label: "Backward drift to origin", colour: "var(--drift)" },
      { label: "Estimated origin zone", colour: "var(--reference)", shape: "dash" },
    ]),
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
  return U.foldout(
    "Acquisition",
    {
      id: "acq",
      hint: `${scene.mission || F.DASH} · ${F.utc(scene.acquiredStartUtc)}`,
      note: scene.regionNote,
    },
    U.rows(
      U.row("Scene", scene.name, { mono: true }),
      U.row("Mission", `${scene.mission || F.DASH} · ${scene.mode || F.DASH} ${scene.productType || ""}`.trim()),
      U.row("Polarisations", (scene.polarisations || []).join(" / ")),
      U.row("Acquired", F.utc(scene.acquiredStartUtc, { seconds: true }), { mono: true }),
      U.row("Centre", F.latLon([
        (scene.bounds?.[0] + scene.bounds?.[2]) / 2,
        (scene.bounds?.[1] + scene.bounds?.[3]) / 2,
      ]), { mono: true }),
      U.row("Extent", `${scene.width} × ${scene.height} px`, { mono: true }),
      U.row("CRS", scene.crs, { mono: true }),
      U.row("Reference mask", scene.hasReferenceMask ? "supplied" : "not supplied"),
      U.row("Product", F.clip(scene.productId, 44), { mono: true, stack: true, title: scene.productId }),
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
      U.provenanceBadges(caseDoc),
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
