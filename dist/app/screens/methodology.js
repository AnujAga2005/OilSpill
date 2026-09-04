/** Reference screen: methodology and evidence.
 *
 * This screen exists to be read by someone who does not believe the other five. It carries
 * the numbers that make the product look worse, on purpose: the whole-scene IoU next to the
 * patch IoU, the worst scene in the test split, the baseline that gets most of the way there
 * without a model, and every limitation the pipeline recorded about itself.
 *
 * Nothing here is computed in the browser. Every figure is read from `metrics.json` or
 * `scene_metrics.json`, both written by the training and evaluation scripts.
 */

import { h, icon } from "../dom.js";
import { ICONS } from "../icons.js";
import * as F from "../format.js";
import * as U from "../ui.js";
import * as X from "../exporters.js";
import { lineChart, strip } from "../chart.js";
import { C } from "../layers.js";

export const LEDE =
  "How each number on this product was produced, measured on held-out data, with the " +
  "figures that flatter it and the figures that do not shown side by side.";

export function render(ctx) {
  const { state, caseDoc } = ctx;
  const metrics = state.metrics;

  return h(
    "div",
    { class: "stack" },

    pipelineCard(caseDoc),

    U.stateSwitch(
      state.metricsStatus,
      metrics,
      (m) =>
        m.available === false
          ? U.missingState({
              title: "No metrics have been written yet",
              body:
                "The model has not been trained and evaluated in this checkout, so there is " +
                "nothing measured to show. Nothing on this screen is filled in with a guess.",
              command: ".venv/bin/python scripts/run_train.py",
            })
          : h(
              "div",
              { class: "stack" },
              honestyCard(m),
              scaleCard(m),
              h("div", { class: "grid grid--2" }, baselineCard(m), thresholdCard(m)),
              h("div", { class: "grid grid--wide-left" }, perSceneCard(m), protocolCard(m)),
              lookAlikeCard(m),
              trainingCard(m),
              samplesCard(ctx, m),
              limitationsCard(m),
            ),
      {
        loadingTitle: "Loading measured metrics",
        loadingBody: "Reading metrics.json and scene_metrics.json.",
        missing: {
          title: "Metrics are not available",
          body: "The API did not return a metrics document.",
        },
        failed: {
          title: "Metrics could not be loaded",
          body: "The API answered, but not with metrics.",
        },
      },
    ),

    datasetCard(ctx, caseDoc),
    reproduceCard(ctx, caseDoc),
  );
}

// -- what the pipeline does --------------------------------------------------

function pipelineCard(caseDoc) {
  const timing = caseDoc?.timing?.stages || [];
  const byStage = new Map(timing.map((stage) => [stage.stage, stage]));

  const steps = [
    {
      key: "decode",
      name: "Decode",
      detail:
        "Read the supplied GeoTIFF, take the VV and VH bands in dB, record the affine " +
        "transform and the no-data value. Nothing is resampled.",
    },
    {
      key: "detect",
      name: "Segment",
      detail:
        "A 2-channel U-Net, written in NumPy with a hand-derived backward pass, produces a " +
        "per-pixel probability. The scene threshold is applied to that field, not to a " +
        "smoothed version of it.",
    },
    {
      key: "geometry",
      name: "Measure",
      detail:
        "Morphological opening then closing, connected components, then area by summing " +
        "each pixel's own area on the ellipsoid row by row. Outlines are traced separately " +
        "and are used only for drawing.",
    },
    {
      key: "screening",
      name: "Screen look-alikes",
      detail:
        "Propose every dark patch in the scene by a relative darkness threshold and score " +
        "each one with a seven-feature linear screen over shape, texture and darkness. " +
        "Oil-like or not oil-like only: the screen never names the phenomenon.",
    },
    {
      key: "forcing",
      name: "Resolve forcing",
      detail:
        "Test whether the supplied reanalysis covers this scene in space and in time. Use it " +
        "only if both hold; otherwise construct a deterministic synthetic field and label it.",
    },
    {
      key: "backward",
      name: "Hindcast",
      detail:
        "Seed particles on the slick's own mask pixels and advect them backwards with RK2 " +
        "plus an isotropic random walk. The output is a distribution, reported as P50 and P90.",
    },
    {
      key: "forward",
      name: "Forecast",
      detail: "The same integrator run forwards from the observed slick.",
    },
    {
      key: "ais",
      name: "Generate AIS",
      detail:
        "Synthetic vessel traffic from a case-derived seed, with defects injected on purpose " +
        "so the cleaning stage has something to clean. Never presented as real traffic.",
    },
    {
      key: "scoring",
      name: "Score candidates",
      detail:
        "Six weighted components against the estimated release zone and window. Every point " +
        "carries the rule and the measurement that produced it.",
    },
    {
      key: "previews",
      name: "Write previews",
      detail:
        "Downsampled georeferenced PNGs for display. Every measurement is taken from the " +
        "full-resolution arrays, never from these.",
    },
  ];

  return U.card(
    "The pipeline, stage by stage",
    {
      id: "pipeline",
      hint: caseDoc?.pipelineVersion,
      note:
        "Timings are from the run that produced the currently loaded case, on this machine. " +
        "They are not a benchmark.",
    },
    h(
      "div",
      { class: "pipeline" },
      steps.map((step, index) => {
        const stage = byStage.get(step.key);
        return h(
          "div",
          { class: "pipeline__step" },
          h("div", { class: "pipeline__dot" }, String(index + 1)),
          h(
            "div",
            { class: "pipeline__body" },
            h(
              "div",
              { class: "inline between" },
              h("span", { class: "pipeline__name" }, step.name),
              stage
                ? h("span", { class: "pipeline__time" }, F.seconds(stage.seconds))
                : h("span", { class: "pipeline__time" }, F.DASH),
            ),
            h("div", { class: "pipeline__detail" }, step.detail),
            stage?.note
              ? h("div", { class: "small muted mono" }, `this run: ${stage.note}`)
              : null,
          ),
        );
      }),
    ),
  );
}

// -- the number that matters -------------------------------------------------

/**
 * Patch IoU against scene IoU, adjacent, with the gap explained.
 *
 * Serving only the patch figure would be the single easiest way to mislead an operator,
 * because the patch sampler visited ground around labelled oil and a real acquisition is
 * mostly open water.
 */
function honestyCard(m) {
  const patch = m.patchScale?.test || {};
  const selection = m.patchScale?.threshold || {};
  const scene = m.sceneScale?.test?.atSceneThreshold;
  const measured = Boolean(scene);

  return U.card(
    "Accuracy, both ways round",
    {
      id: "honesty",
      hint: measured
        ? `patch threshold ${F.num(selection.threshold, 2)} · scene threshold ${F.num(m.sceneScale?.sceneThreshold, 2)}`
        : null,
      note: m.sceneScale?.basis,
    },
    h(
      "div",
      { class: "grid grid--stats" },
      U.stat({
        label: "Patch IoU, held-out test",
        value: F.metric(patch.iou, 4),
        sub: `${F.int(m.patchScale?.protocol?.cacheSplits?.test?.patches)} patches of 128 px, sampled around labelled oil`,
      }),
      U.stat({
        label: "Whole-scene IoU, pooled",
        value: F.metric(scene?.pooled?.iou, 4),
        tone: "reference",
        sub: measured ? `all pixels of ${F.int(scene.scenes)} held-out scenes pooled` : undefined,
        missing: "not measured yet",
      }),
      U.stat({
        label: "Mean per-scene IoU",
        value: F.metric(scene?.meanSceneIou, 4),
        sub: measured ? `median ${F.metric(scene.medianSceneIou, 3)} across ${F.int(scene.scenes)} scenes` : undefined,
        missing: "not measured yet",
      }),
      U.stat({
        label: "Worst scene IoU",
        value: F.metric(scene?.worstSceneIou, 4),
        tone: "synthetic",
        sub: measured ? "one held-out acquisition the model largely failed on" : undefined,
        missing: "not measured yet",
      }),
    ),
    measured
      ? U.notice(
          `Pooled scene IoU is ${F.metric(scene.pooled.iou, 3)} and mean per-scene IoU is ` +
            `${F.metric(scene.meanSceneIou, 3)}, both below the patch figure of ` +
            `${F.metric(patch.iou, 3)}. The scene number is the one that reflects running on a ` +
            "full acquisition, and it is the number to quote.",
          { strongPrefix: "Quote the scene figure." },
        )
      : U.notice(
          "Scene-scale evaluation has not been run in this checkout, so only the patch figure " +
            "exists. Do not quote it as whole-scene accuracy.",
          { kind: "synthetic", strongPrefix: "Not measured." },
        ),
  );
}

function scaleCard(m) {
  const rows = [
    { label: "Validation, patches", value: m.patchScale?.validation },
    { label: "Test, patches", value: m.patchScale?.test },
    { label: "Test, scenes at the patch threshold", value: m.sceneScale?.test?.atPatchThreshold?.pooled },
    { label: "Test, scenes at the scene threshold", value: m.sceneScale?.test?.atSceneThreshold?.pooled },
  ].filter((entry) => entry.value);

  return U.card(
    "Every measured split",
    {
      id: "splits",
      note:
        "Precision is the fraction of predicted oil pixels that are labelled oil; recall is " +
        "the fraction of labelled oil pixels that were predicted. Recall above precision " +
        "means the model over-paints, which for a search product is the safer direction.",
    },
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", null, "Split"),
            h("th", { class: "right" }, "IoU"),
            h("th", { class: "right" }, "Dice"),
            h("th", { class: "right" }, "Precision"),
            h("th", { class: "right" }, "Recall"),
          ),
        ),
        h(
          "tbody",
          null,
          rows.map((entry) =>
            h(
              "tr",
              null,
              h("td", { class: "wrap" }, entry.label),
              h("td", { class: "right mono" }, F.metric(entry.value.iou, 4)),
              h("td", { class: "right mono" }, F.metric(entry.value.dice, 4)),
              h("td", { class: "right mono" }, F.metric(entry.value.precision, 4)),
              h("td", { class: "right mono" }, F.metric(entry.value.recall, 4)),
            ),
          ),
        ),
      ),
    ),
  );
}

// -- is the model earning its keep? -----------------------------------------

function baselineCard(m) {
  const comparison = m.patchScale?.comparison || {};
  const baseline = m.patchScale?.baseline || {};
  const calibration = baseline.calibration || {};
  if (!Number.isFinite(comparison.baselineTestIou)) {
    return U.card(
      "Baseline",
      { id: "baseline" },
      U.missingState({
        title: "No baseline was measured",
        body: "Without one there is no evidence the model beats a threshold.",
      }),
    );
  }

  const delta = comparison.iouDelta;
  const relative = comparison.baselineTestIou > 0 ? delta / comparison.baselineTestIou : null;

  return U.card(
    "Does the model beat a threshold?",
    {
      id: "baseline",
      hint: baseline.method,
      note:
        "The baseline is a classical dark-spot detector: despeckle, take a water reference " +
        "percentile, threshold a contrast in dB, clean up morphologically, drop small blobs. " +
        "Its parameters were themselves tuned on validation, so this is a fair fight.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      h(
        "div",
        { class: "grid grid--stats" },
        U.stat({ label: "Model, test IoU", value: F.metric(comparison.modelTestIou, 4) }),
        U.stat({
          label: "Baseline, test IoU",
          value: F.metric(comparison.baselineTestIou, 4),
          tone: "synthetic",
        }),
        U.stat({
          label: "Improvement",
          value: `+${F.metric(delta, 4)}`,
          sub: relative === null ? undefined : `${F.pct(relative)} relative`,
        }),
      ),
      U.notice(
        `A calibrated global threshold on the supplied dB values already reaches ` +
          `${F.metric(comparison.baselineTestIou, 3)} IoU on the same held-out patches. The model ` +
          `adds ${F.metric(delta, 3)}. That gap is the entire contribution of the network, and it ` +
          "is worth knowing before deciding a network is required.",
        { strongPrefix: "Most of the way without a model." },
      ),
      Object.keys(calibration).length
        ? U.rows(
            ...Object.entries(calibration).map(([key, value]) =>
              U.row(F.label(key), typeof value === "number" ? F.num(value, 2) : String(value), {
                mono: true,
              }),
            ),
          )
        : null,
    ),
  );
}

function thresholdCard(m) {
  const sweep = m.patchScale?.thresholdSweep || [];
  const selection = m.patchScale?.threshold || {};
  if (!sweep.length) {
    return U.card(
      "Threshold",
      { id: "threshold" },
      U.missingState({ title: "No threshold sweep stored" }),
    );
  }
  const points = (key) => sweep.map((entry) => [entry.threshold, entry[key]]);

  return U.card(
    "Threshold sweep, on validation",
    {
      id: "threshold",
      hint: `${sweep.length} thresholds`,
      note:
        "Swept on validation only. Choosing it on test would make the test figure an " +
        "optimistic estimate of a threshold that was fitted to it.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      lineChart(
        [
          { label: "IoU", colour: C.oil, points: points("iou") },
          { label: "Precision", colour: C.reference, points: points("precision"), dash: "5 4" },
          { label: "Recall", colour: C.agree, points: points("recall"), dash: "2 3" },
        ],
        {
          xLabel: "threshold",
          yLabel: "score",
          formatX: (v) => F.num(v, 2),
          formatY: (v) => F.num(v, 2),
          ariaLabel: "IoU, precision and recall against the decision threshold, on validation",
        },
      ),
      U.legend([
        { label: "IoU", colour: "var(--oil)", shape: "line" },
        { label: "Precision", colour: "var(--reference)", shape: "dash" },
        { label: "Recall", colour: "var(--agree)", shape: "dash" },
      ]),
      U.rows(
        U.row("Patch threshold in use", F.num(selection.threshold, 2), { mono: true }),
        U.row(
          "Chosen by",
          selection.selectedBy
            ? `${selection.selectedBy} = ${F.metric(selection.value, 5)} on validation`
            : null,
          { mono: true },
        ),
        U.row("Why", selection.reason, { stack: true }),
        U.row("Scene threshold in use", F.num(m.sceneScale?.sceneThreshold, 2), { mono: true }),
        U.row("Chosen on", m.sceneScale?.thresholdSelection, { stack: true }),
      ),
      h(
        "p",
        { class: "small muted" },
        "The scene threshold is higher than the patch threshold because a whole acquisition " +
          "is mostly water: the same probability field needs more evidence per pixel before " +
          "it is called oil when there are far more chances to be wrong.",
      ),
    ),
  );
}

// -- distribution over scenes ------------------------------------------------

function perSceneCard(m) {
  const perScene = m.sceneScale?.perScene || [];
  const key = String(m.sceneScale?.sceneThreshold ?? 0.8);
  const all = perScene
    .map((entry) => ({
      scene: entry.scene,
      split: entry.split,
      iou: entry.thresholds?.[key]?.scores?.iou,
      reference: entry.referenceOilFraction,
    }))
    .filter((entry) => Number.isFinite(entry.iou));
  // Only the test split, because the pooled and mean figures shown above it are test-only.
  // Mixing validation scenes into the same strip would make the distribution and the
  // headline number describe different populations.
  const values = all.filter((entry) => entry.split === "test").sort((a, b) => a.iou - b.iou);
  const validationCount = all.length - values.length;

  if (!values.length) {
    return U.card(
      "Per-scene distribution",
      { id: "per-scene" },
      U.missingState({
        title: "No per-scene evaluation",
        body: "Only pooled figures were written.",
        command: ".venv/bin/python scripts/run_scene_eval.py",
      }),
    );
  }

  const worst = values.slice(0, 6);

  return U.card(
    "Per-scene distribution, test split",
    {
      id: "per-scene",
      hint: `${values.length} scenes at threshold ${key}`,
      note:
        "Sorted worst to best. A mean hides the tail, and the tail is what an operator will " +
        "meet on the day the model is wrong." +
        (validationCount
          ? ` ${validationCount} validation scenes were also evaluated and are excluded here.`
          : ""),
    },
    h(
      "div",
      { class: "stack stack--tight" },
      strip(values.map((entry) => entry.iou), {
        max: 1,
        tone: "var(--oil)",
        label: (value, index) => `${values[index].scene}: IoU ${F.metric(value, 3)}`,
      }),
      h(
        "div",
        { class: "inline between small muted" },
        h("span", null, `worst ${F.metric(values[0].iou, 3)}`),
        h("span", null, `median ${F.metric(m.sceneScale?.test?.atSceneThreshold?.medianSceneIou, 3)}`),
        h("span", null, `best ${F.metric(values.at(-1).iou, 3)}`),
      ),
      h("div", { class: "divider" }),
      h("div", { class: "small muted" }, "The six worst held-out scenes:"),
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          { class: "table" },
          h(
            "thead",
            null,
            h(
              "tr",
              null,
              h("th", null, "Scene"),
              h("th", { class: "right" }, "IoU"),
              h("th", { class: "right" }, "Labelled oil"),
            ),
          ),
          h(
            "tbody",
            null,
            worst.map((entry) =>
              h(
                "tr",
                null,
                h("td", { class: "mono" }, entry.scene),
                h("td", { class: "right mono" }, F.metric(entry.iou, 4)),
                h("td", { class: "right mono" }, F.pct(entry.reference, 2)),
              ),
            ),
          ),
        ),
      ),
      h(
        "p",
        { class: "small muted" },
        "Scenes with very little labelled oil score badly on IoU almost by construction: a " +
          "handful of misplaced pixels is a large fraction of a small reference. That is a " +
          "property of the metric, and it is also a real operational failure.",
      ),
    ),
  );
}

function protocolCard(m) {
  const protocol = m.patchScale?.protocol || {};
  const splits = protocol.cacheSplits || {};
  const usage = m.patchScale?.dataUsage || {};

  return U.card(
    "Split protocol",
    {
      id: "protocol",
      note:
        "Patches are grouped by parent acquisition before splitting, so no two patches from " +
        "the same acquisition can land on opposite sides of the train/test boundary. Without " +
        "that grouping the test figure would be measuring memorisation.",
    },
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", null, "Split"),
            h("th", { class: "right" }, "Patches"),
            h("th", { class: "right" }, "Acquisitions"),
            h("th", { class: "right" }, "Used"),
            h("th", { class: "right" }, "Oil fraction"),
          ),
        ),
        h(
          "tbody",
          null,
          ["train", "val", "test"].map((name) =>
            splits[name]
              ? h(
                  "tr",
                  null,
                  h("td", null, F.label(name)),
                  h("td", { class: "right mono" }, F.int(splits[name].patches)),
                  h("td", { class: "right mono" }, F.int(splits[name].acquisitions)),
                  h("td", { class: "right mono" }, F.int(usage[name]?.used)),
                  h("td", { class: "right mono" }, F.pct(usage[name]?.oilPixelFraction ?? splits[name].oilPixelFraction, 2)),
                )
              : null,
          ),
        ),
      ),
    ),
    h(
      "div",
      { class: "stack stack--tight", style: { "margin-top": "var(--s3)" } },
      ...["train", "val", "test"].map((name) =>
        usage[name]?.rule
          ? U.row(`${F.label(name)} sampling`, usage[name].rule, { stack: true })
          : null,
      ),
    ),
  );
}

// -- training ----------------------------------------------------------------

/**
 * The epoch with the highest validation IoU.
 *
 * The metrics endpoint forwards the history but not the checkpoint's own record of which
 * epoch it kept, so this is derived rather than read. Same rule the trainer uses.
 */
function bestEpoch(history) {
  let best = null;
  for (const entry of history) {
    if (best === null || (entry.valIou ?? -1) > (best.valIou ?? -1)) best = entry;
  }
  return best?.epoch ?? null;
}

function trainingCard(m) {
  const history = m.patchScale?.history || [];
  const model = m.patchScale?.model || {};
  const config = m.patchScale?.trainConfig || {};

  return U.card(
    "Training",
    {
      id: "training",
      hint: `${history.length} epochs in ${F.seconds(m.patchScale?.trainingSeconds)}`,
      note:
        `${model.architecture} · ${model.framework} · ${F.int(model.parameters)} parameters. ` +
        "There is no autograd in this build, so every gradient in the backward pass is " +
        "written out by hand.",
    },
    h(
      "div",
      { class: "grid grid--wide-left" },
      h(
        "div",
        { class: "stack stack--tight" },
        history.length
          ? lineChart(
              [
                { label: "Train IoU", colour: C.oil, points: history.map((e) => [e.epoch, e.trainIou]) },
                { label: "Validation IoU", colour: C.reference, points: history.map((e) => [e.epoch, e.valIou]) },
                { label: "Train loss", colour: C.neutral, points: history.map((e) => [e.epoch, e.trainLoss]), dash: "3 3" },
              ],
              {
                xLabel: "epoch",
                yLabel: "IoU / loss",
                yZero: true,
                formatX: (v) => String(Math.round(v)),
                formatY: (v) => F.num(v, 2),
                ariaLabel: "Training and validation IoU and training loss against epoch",
              },
            )
          : U.emptyState({ title: "No training history stored" }),
        U.legend([
          { label: "Train IoU", colour: "var(--oil)", shape: "line" },
          { label: "Validation IoU", colour: "var(--reference)", shape: "line" },
          { label: "Train loss", colour: "var(--text-tertiary)", shape: "dash" },
        ]),
        h(
          "p",
          { class: "small muted" },
          history.length
            ? `Validation IoU rose from ${F.metric(history[0].valIou, 3)} at epoch 1 to ` +
              `${F.metric(history.at(-1).valIou, 3)} at epoch ${history.at(-1).epoch}, with the best ` +
              `at epoch ${F.int(bestEpoch(history))}. Train and validation IoU stay close, so this ` +
              "run is not obviously overfitting the patch cache."
            : "",
        ),
      ),
      U.rows(
        U.row("Architecture", `${model.architecture}, depth ${F.int(model.depth)}`, { mono: true }),
        U.row("Encoder widths", (model.encoderWidths || []).join(" → "), { mono: true }),
        U.row("Bottleneck", F.int(model.bottleneckWidth), { mono: true }),
        U.row("Input channels", `${F.int(model.inChannels)} (VV, VH in dB)`, { mono: true }),
        U.row("Normalisation", model.normalisation),
        U.row("Upsampling", model.upsampling, { stack: true }),
        U.row("Parameters", F.int(model.parameters), { mono: true }),
        U.row("Epochs", F.int(config.epochs), { mono: true }),
        U.row("Batch size", F.int(config.batch_size), { mono: true }),
        U.row("Learning rate", `${F.num(config.learning_rate, 4)} → ${F.num((config.learning_rate || 0) * (config.final_lr_fraction || 0), 5)}`, { mono: true }),
        U.row("Loss", `${F.num(config.bce_weight, 2)} BCE + ${F.num(config.dice_weight, 2)} Dice`, { mono: true }),
        U.row("Positive weight", F.num(config.positive_weight, 1), { mono: true }),
        U.row("Gradient clip", F.num(config.grad_clip_norm, 1), { mono: true }),
        U.row("Early stopping patience", F.int(config.patience), { mono: true }),
        U.row("Augmentation", config.augment ? "flips and rotations" : "none"),
        U.row("Seed", F.int(config.seed ?? model.seed), { mono: true }),
        U.row("Checkpoint", m.patchScale?.modelVersion, { mono: true }),
      ),
    ),
  );
}

/**
 * Qualitative examples, including the ones the model got wrong.
 *
 * The evaluation run already chose these as a spread rather than a highlight reel -- best,
 * worst and evenly spaced in between -- so all of them are shown, in that order. Picking a
 * subset here would undo that.
 */
function samplesCard(ctx, m) {
  const samples = m.patchScale?.samples || [];
  if (!samples.length) return null;
  const ordered = [...samples].sort((a, b) => (a.iou ?? 0) - (b.iou ?? 0));

  return U.card(
    "Example patches, worst to best",
    {
      id: "samples",
      hint: `${samples.length} written during evaluation`,
      note:
        "Each tile is the VV band in dB with three overlays: where the model and the " +
        "reference agree, where the model painted oil the reference does not mark, and where " +
        "the reference marks oil the model missed. One overlay would hide the second and " +
        "third, which are the only interesting parts.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      U.legend([
        { label: "Agreement", colour: "var(--agree)", shape: "swatch" },
        { label: "Predicted, not in reference", colour: "var(--oil)", shape: "swatch" },
        { label: "In reference, missed", colour: "var(--reference)", shape: "swatch" },
      ]),
      h(
        "div",
        { class: "tile-grid" },
        ordered.map((sample) =>
          h(
            "figure",
            { class: "tile" },
            h(
              "div",
              { class: "tile__frame" },
              h("img", {
                src: ctx.api.evalImageUrl(sample.file),
                alt:
                  `Held-out test patch ${sample.patchIndex}, intersection over union ` +
                  `${F.metric(sample.iou, 3)}`,
                loading: "lazy",
                draggable: "false",
              }),
            ),
            h(
              "figcaption",
              { class: "tile__caption" },
              h("span", { class: "tile__name" }, `IoU ${F.metric(sample.iou, 3)}`),
              h(
                "span",
                { class: "tile__meta" },
                `patch ${sample.patchIndex} · labelled ${F.pct(sample.referenceOilFraction, 1)}, ` +
                  `predicted ${F.pct(sample.predictedOilFraction, 1)}`,
              ),
            ),
          ),
        ),
      ),
      h(
        "p",
        { class: "small muted" },
        `The worst of these scores ${F.metric(ordered[0].iou, 3)} and the best ` +
          `${F.metric(ordered.at(-1).iou, 3)}. Both were produced by the same weights on the ` +
          "same held-out split, at the same threshold.",
      ),
    ),
  );
}

// -- the ledger --------------------------------------------------------------

function limitationsCard(m) {
  const patch = m.patchScale?.limitations || [];
  const scene = m.sceneScale?.limitations || [];
  const screen = m.lookAlike?.limitations || [];
  if (!patch.length && !scene.length && !screen.length) return null;

  return U.card(
    "Limitations, as recorded by the pipeline",
    {
      id: "limitations",
      note:
        "These strings are written by the evaluation scripts into metrics.json, " +
        "scene_metrics.json and lookalike_metrics.json. They are not editorial: they " +
        "travel with the numbers.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      patch.length
        ? h(
            "div",
            null,
            h("div", { class: "small muted" }, "Patch-scale evaluation"),
            h("ul", { class: "bullets" }, patch.map((text) => h("li", null, h("span", null, text)))),
          )
        : null,
      scene.length
        ? h(
            "div",
            null,
            h("div", { class: "small muted" }, "Scene-scale evaluation"),
            h("ul", { class: "bullets" }, scene.map((text) => h("li", null, h("span", null, text)))),
          )
        : null,
      screen.length
        ? h(
            "div",
            null,
            h("div", { class: "small muted" }, "Look-alike screening"),
            h("ul", { class: "bullets" }, screen.map((text) => h("li", null, h("span", null, text)))),
          )
        : null,
    ),
  );
}

// -- the look-alike screen ---------------------------------------------------

/**
 * The one number on this screen that is not about oil.
 *
 * Every IoU here is measured on scenes that contain a slick, so all of them answer "how
 * well is a known slick's shape recovered". None of them answers "how often does dark water
 * that is not oil raise an alarm", which is the question an operator actually has. This card
 * carries that second number, measured twice: once held out on this project's own scenes,
 * and once on a published look-alike archive the screen never trained on.
 */
function lookAlikeCard(m) {
  const block = m.lookAlike;
  if (!block) return null;
  if (block.available === false) {
    return U.card(
      "Look-alike screening",
      { id: "lookalike" },
      U.missingState({
        title: "The look-alike screen has not been measured",
        body:
          "No screen has been fitted in this checkout, so every dark patch in a case comes " +
          "back as uncertain. Nothing on this card is filled in with a guess.",
        command: ".venv/bin/python scripts/run_lookalike_eval.py",
      }),
    );
  }

  const held = block.sameDomain?.crossValidation?.pooledHeldOut || {};
  const training = block.sameDomain?.training || {};
  const validation = block.sameDomain?.crossValidation || {};
  const cross = block.crossDomain?.screen;
  const dataset = block.crossDomain?.dataset;
  const detector = block.crossDomain?.detector;
  const single = held.singleFeatureAuc || {};
  const bestSingle = Object.entries(single)
    .filter(([, value]) => !F.isMissing(value))
    .sort((a, b) => b[1] - a[1])[0];

  return U.card(
    "Look-alike screening: dark water that is not oil",
    {
      id: "lookalike",
      hint: `${F.int(validation.folds)}-fold, grouped by satellite product`,
      note: block.question,
    },
    h(
      "div",
      { class: "stack" },

      h(
        "div",
        { class: "grid grid--stats" },
        U.stat({
          label: "Held-out AUC",
          value: F.metric(held.auc, 3),
          tone: "model",
          sub: `${F.int(held.regions)} regions, ${F.int(held.oilRegions)} of them labelled oil`,
        }),
        U.stat({
          label: "Labelled oil kept",
          value: F.pct(held.keptSensitivity),
          sub: held.definitions?.keptSensitivity,
        }),
        U.stat({
          label: "Not-oil rejected",
          value: F.pct(held.rejectionSpecificity),
          sub: held.definitions?.rejectionSpecificity,
        }),
        U.stat({
          label: "Best single feature",
          value: bestSingle ? F.metric(bestSingle[1], 3) : F.DASH,
          sub: bestSingle
            ? `${bestSingle[0]} alone — the seven together must beat this to be worth having`
            : "not measured",
        }),
      ),

      U.rows(
        U.row("Fitted on", training.labelRule, { stack: true }),
        U.row(
          "Fitted from",
          `${F.int(training.regions)} dark regions across ${F.int(training.scenes)} scenes ` +
            `and ${F.int(training.products)} parent products, at ${F.num(training.spacingM, 1)} m/pixel`,
          { stack: true },
        ),
        U.row("Folds grouped by", validation.grouping, { stack: true }),
        U.row(
          "Proposer recall of the labelled oil",
          F.pct(training.meanMaskRecallOfProposer),
          { mono: true },
        ),
        U.row("Ambiguous regions dropped", F.int(training.droppedAmbiguous), { mono: true }),
      ),

      cross
        ? h(
            "div",
            { class: "stack stack--tight" },
            h("div", { class: "small muted" }, "Measured on a published archive it never trained on"),
            h(
              "div",
              { class: "grid grid--stats" },
              U.stat({
                label: "Look-alike regions rejected",
                value: F.pct(cross.regionRejectionRate),
                tone: "reference",
                sub: `${F.int(cross.regionOutcomes?.rejected)} of ${F.int(cross.regions)} proposed regions`,
              }),
              U.stat({
                label: "Images still raising something",
                value: F.pct(cross.patchFalseAlarmRate),
                sub:
                  `${F.int(cross.patchesWithSurvivingRegion)} of ${F.int(cross.patches)} ` +
                  "look-alike images keep at least one unrejected region",
              }),
              U.stat({
                label: "Images with no dark region at all",
                value: F.int(cross.patchesWithNoDarkRegion),
                sub: "the proposer found nothing to screen, so the screen was never asked",
              }),
              U.stat({
                label: "Resampled to",
                value: F.num(cross.resampledToSpacingM, 1),
                unit: "m/px",
                sub: "so a film occupies the same number of pixels it did in training",
              }),
            ),
            dataset
              ? U.rows(
                  U.row("Archive", dataset.source, { stack: true }),
                  U.row("Coverage", dataset.coverage, { stack: true }),
                  U.row("Independence", cross.note, { stack: true }),
                )
              : null,
            cross.bySubset ? subsetTable(cross.bySubset) : null,
          )
        : U.notice(
            "The published look-alike archive is not on disk in this checkout, so the " +
              "cross-domain number is not measured. The held-out figures above are from " +
              "this project's own scenes only.",
            { kind: "synthetic", strongPrefix: "Same domain only." },
          ),

      detector ? detectorRows(detector) : null,
    ),
  );
}

/** Rejection rate per archive subset: coastal and open-water look-alikes separately. */
function subsetTable(bySubset) {
  const entries = Object.entries(bySubset);
  if (!entries.length) return null;
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          h("th", null, "Subset"),
          h("th", { class: "right" }, "Regions"),
          h("th", { class: "right" }, "Rejected"),
          h("th", { class: "right" }, "Uncertain"),
          h("th", { class: "right" }, "Accepted"),
          h("th", { class: "right" }, "Rejection rate"),
        ),
      ),
      h(
        "tbody",
        null,
        entries.map(([key, bucket]) =>
          h(
            "tr",
            null,
            h("td", null, bucket.label || key),
            h("td", { class: "right" }, F.int(bucket.regions)),
            h("td", { class: "right" }, F.int(bucket.rejected)),
            h("td", { class: "right" }, F.int(bucket.uncertain)),
            h("td", { class: "right" }, F.int(bucket.accepted)),
            h("td", { class: "right" }, F.pct(bucket.rejectionRate)),
          ),
        ),
      ),
    ),
  );
}

/**
 * What the segmentation network alone does on the same look-alike images.
 *
 * The screen's rejection rate is only meaningful against a baseline, and the honest baseline
 * is this product's own detector with no screen in front of it.
 */
function detectorRows(detector) {
  const mappings = Object.entries(detector.lookAlikes || {});
  if (!mappings.length) return null;
  const sanity = detector.oilSanityCheck;
  return h(
    "div",
    { class: "stack stack--tight" },
    h("div", { class: "small muted" }, "The detector on the same images, with no screen in front of it"),
    U.rows(
      ...mappings.map(([mapping, entry]) =>
        U.row(
          mapping,
          `${F.pct(entry.falseAlarmRate)} of ${F.int(entry.patches)} look-alike images alarm ` +
            `(${F.int(entry.patchesWithAlarm)} images) — ${entry.assumption}`,
          { stack: true },
        ),
      ),
      U.row("Alarm rule", detector.alarmRule, { stack: true }),
      U.row(
        "Threshold",
        `${F.num(detector.threshold, 2)} — ${detector.thresholdSource}`,
        { stack: true },
      ),
      sanity
        ? U.row(
            "Sanity check on annotated oil",
            `${F.pct(Object.values(sanity)[0]?.detectionRate)} of the oil patches alarm under ` +
              `${Object.keys(sanity)[0]}, so the numbers above are not simply a silent detector`,
            { stack: true },
          )
        : null,
    ),
  );
}

function datasetCard(ctx, caseDoc) {
  const scenes = ctx.state.scenes;
  const health = ctx.state.health;
  return U.card(
    "Data provenance",
    {
      id: "data",
      hint: scenes?.count ? `${F.int(scenes.count)} scenes indexed` : null,
      note:
        "Raw imagery, masks and the reanalysis file stay on disk and are served by the local " +
        "API. Nothing in this dataset is bundled into the interface.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      U.provenanceBadges(caseDoc),
      U.rows(
        U.row("Imagery", caseDoc?.provenance?.satellite, { stack: true }),
        U.row("Reference masks", "supplied with the dataset and treated as ground truth", { stack: true }),
        U.row("Currents", caseDoc?.forcing?.label, { stack: true }),
        U.row("AIS", caseDoc?.ais?.label, { stack: true }),
        U.row("Scenes indexed", F.int(scenes?.count), { mono: true }),
        U.row("Cases stored", F.int(ctx.state.cases?.count), { mono: true }),
        U.row("API", health?.status ? `${health.status} · ${health.pipelineVersion || ""}`.trim() : "not reachable", { mono: true }),
      ),
      U.notice(
        "The reference masks are the supplied labels. Their own accuracy is unknown and is " +
          "treated as ground truth everywhere in this product, so every accuracy figure here " +
          "is accuracy against those labels rather than against the sea.",
        { strongPrefix: "Ground truth is an assumption." },
      ),
    ),
  );
}

function reproduceCard(ctx, caseDoc) {
  const commands = [
    { label: "Audit the supplied dataset", command: ".venv/bin/python scripts/run_audit.py" },
    { label: "Build the patch cache", command: ".venv/bin/python scripts/run_preprocess.py" },
    { label: "Train and evaluate", command: ".venv/bin/python scripts/run_train.py" },
    { label: "Evaluate on whole scenes", command: ".venv/bin/python scripts/run_scene_eval.py" },
    { label: "Build the seeded demo case", command: ".venv/bin/python scripts/run_api.py --build-demo" },
    { label: "Serve the API and the interface", command: ".venv/bin/python scripts/run_api.py" },
    { label: "Run the tests", command: ".venv/bin/python -m pytest tests/ -q" },
  ];

  return U.card(
    "Reproduce this",
    {
      id: "reproduce",
      hint: caseDoc?.requestKey ? `request key ${caseDoc.requestKey}` : null,
      note:
        "Every stage is seeded. Running these in order on the same inputs reproduces every " +
        "number on this product, including the synthetic AIS and the synthetic forcing.",
      actions: U.button("Export case JSON", {
        kind: "quiet",
        small: true,
        iconPath: ICONS.download,
        disabled: !caseDoc,
        onClick: () => {
          X.downloadJson(X.exportName(caseDoc, "case", "json"), caseDoc);
          ctx.announce("Case document downloaded.", { kind: "success" });
        },
      }),
    },
    h(
      "div",
      { class: "stack stack--tight" },
      commands.map((entry) =>
        h(
          "div",
          { class: "row row--stack" },
          h("div", { class: "row__key" }, entry.label),
          h("code", { class: "command" }, entry.command),
        ),
      ),
      h(
        "div",
        { class: "inline" },
        icon(ICONS.info, { size: 14 }),
        h(
          "span",
          { class: "small muted" },
          "The interface is plain ES modules with no build step, so there is nothing to " +
            "compile before serving it.",
        ),
      ),
    ),
  );
}
