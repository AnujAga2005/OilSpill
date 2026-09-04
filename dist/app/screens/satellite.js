/** Screen 2: satellite analysis.
 *
 * The imagery screen exists so a reader can disagree with the model. That means the raw
 * bands, the probability field, the thresholded mask and the supplied reference all have to
 * be reachable in one place, in registration, at the same scale - and the overlay has to be
 * removable, because a mask painted over a slick is very persuasive whether or not it is
 * right.
 *
 * All five layers are the same 512 px georeferenced preview of the same 2048 px arrays, so
 * stacking them with CSS is exact rather than approximate.
 */

import { h, icon } from "../dom.js";
import { ICONS } from "../icons.js";
import * as F from "../format.js";
import * as U from "../ui.js";

export const LEDE =
  "The supplied VV and VH backscatter, the model's probability field, the thresholded " +
  "mask and the dataset's own reference mask - the same pixels, four ways.";

/** Overlay choices. `reference` is dropped when the dataset supplied no mask. */
const OVERLAYS = [
  { value: "none", label: "None" },
  { value: "prediction", label: "Prediction" },
  { value: "referenceMask", label: "Reference" },
  { value: "both", label: "Both" },
];

const BANDS = [
  { value: "vv", label: "VV", title: "Co-polarised. The channel oil suppresses most." },
  { value: "vh", label: "VH", title: "Cross-polarised. Weaker return, noisier over calm water." },
];

export function render(ctx) {
  const { caseDoc, caseStatus, caseId, state } = ctx;

  if (caseStatus !== "ready" || !caseDoc) {
    return U.stateSwitch(caseStatus, caseDoc, () => null, {
      loadingTitle: "Loading imagery",
      missing: {
        title: "Nothing has been detected for this scene yet",
        body: "Run the pipeline once to decode the scene, run the model and write the previews.",
        command: ".venv/bin/python scripts/run_api.py --build-demo",
      },
      failed: { title: "The case could not be loaded", body: "The API did not return a case." },
    });
  }

  const previews = caseDoc.previews;
  if (!previews?.files) {
    return U.missingState({
      title: "This case has no previews",
      body:
        "The case was computed with previews disabled, so there are no PNGs to display. " +
        "The geometry and drift screens still work; re-run with previews enabled to see imagery.",
      command: ".venv/bin/python scripts/run_api.py --build-demo",
    });
  }

  const hasReference = Boolean(previews.files.referenceMask);
  // Local view state. It lives in a closure rather than the store because it is a property
  // of looking at the screen, not of the case.
  const viewState = {
    band: "vv",
    overlay: hasReference ? "both" : "prediction",
    opacity: 0.62,
    compare: false,
    pixelated: true,
  };

  const viewerHost = h("div", { class: "stack stack--tight" });
  const controlsHost = h("div", { class: "inline between" });

  const paint = () => {
    viewerHost.replaceChildren(viewer(ctx, previews, viewState, hasReference));
    controlsHost.replaceChildren(...controls(viewState, hasReference, paint));
  };
  paint();

  return h(
    "div",
    { class: "stack" },
    U.card(
      "Viewer",
      {
        id: "viewer",
        flush: false,
        note: previews.label,
        actions: U.button("Run detection again", {
          kind: "quiet",
          small: true,
          iconPath: ICONS.reset,
          disabled: ctx.api.apiMode() === "offline" || isBusy(state),
          title:
            ctx.api.apiMode() === "offline"
              ? "The API is not running, so no new analysis can be started."
              : "Re-run the model on this scene and rebuild the case.",
          onClick: () => ctx.runAnalysis("detect", { detector: "auto" }),
        }),
      },
      h("div", { class: "stack stack--tight" }, controlsHost, viewerHost),
    ),

    h(
      "div",
      { class: "grid grid--wide-left" },
      tilesCard(ctx, previews, caseId),
      h(
        "div",
        { class: "stack" },
        detectionCard(ctx, caseDoc),
        lookalikeCard(ctx, caseDoc),
        analystCard(ctx, caseDoc),
        georeferenceCard(previews, caseDoc.scene),
      ),
    ),
  );
}

// -- viewer ------------------------------------------------------------------

function controls(viewState, hasReference, paint) {
  const set = (patch) => {
    Object.assign(viewState, patch);
    paint();
  };
  const overlays = OVERLAYS.filter((o) => hasReference || o.value === "none" || o.value === "prediction");

  return [
    h(
      "div",
      { class: "inline" },
      h(
        "div",
        { class: "field" },
        h("span", { class: "field__label" }, "Band"),
        U.segmented(BANDS, viewState.band, (band) => set({ band }), { ariaLabel: "Radar band" }),
      ),
      h(
        "div",
        { class: "field" },
        h("span", { class: "field__label" }, "Overlay"),
        U.segmented(overlays, viewState.overlay, (overlay) => set({ overlay }), {
          ariaLabel: "Mask overlay",
        }),
      ),
      viewState.overlay === "none"
        ? null
        : h(
            "div",
            { class: "field", style: { "min-width": "150px" } },
            h(
              "span",
              { class: "field__label" },
              `Overlay opacity · ${Math.round(viewState.opacity * 100)}%`,
            ),
            h("input", {
              class: "range",
              type: "range",
              min: "0",
              max: "1",
              step: "0.02",
              value: String(viewState.opacity),
              "aria-label": "Overlay opacity",
              // `input` rather than `change`: the slider has to feel continuous, and a
              // repaint here is four `style` writes.
              onInput: (event) => {
                viewState.opacity = Number(event.target.value);
                const host = event.target.closest(".stack");
                for (const layer of host?.querySelectorAll("[data-overlay]") || []) {
                  layer.style.opacity = String(viewState.opacity);
                }
                const label = event.target.previousElementSibling;
                if (label) {
                  label.textContent = `Overlay opacity · ${Math.round(viewState.opacity * 100)}%`;
                }
              },
            }),
          ),
    ),
    h(
      "div",
      { class: "inline" },
      h(
        "label",
        { class: "switch" },
        h("input", {
          type: "checkbox",
          checked: viewState.compare,
          onChange: (event) => set({ compare: event.target.checked }),
        }),
        h("span", null, "Before / after"),
      ),
      h(
        "label",
        { class: "switch" },
        h("input", {
          type: "checkbox",
          checked: viewState.pixelated,
          onChange: (event) => set({ pixelated: event.target.checked }),
        }),
        h("span", { title: "Show the 512 px preview as the pixels it is, without smoothing." }, "Show pixels"),
      ),
    ),
  ];
}

function viewer(ctx, previews, viewState, hasReference) {
  const { caseId } = ctx;
  const url = (kind) => ctx.api.imageUrl(caseId, kind, previews.files);
  const layerClass = ["viewer__layer", viewState.pixelated ? "viewer__layer--pixel" : null];

  const base = h("img", {
    class: layerClass,
    src: url(viewState.band),
    alt: `${viewState.band.toUpperCase()} backscatter for scene ${previews.scene}`,
    draggable: "false",
  });

  const overlayLayers = [];
  const wantsPrediction = viewState.overlay === "prediction" || viewState.overlay === "both";
  const wantsReference = hasReference && (viewState.overlay === "referenceMask" || viewState.overlay === "both");

  if (wantsReference) {
    overlayLayers.push(
      h("img", {
        class: layerClass,
        src: url("referenceMask"),
        alt: "Supplied reference mask",
        draggable: "false",
        dataset: { overlay: "referenceMask" },
        style: { opacity: String(viewState.opacity) },
      }),
    );
  }
  if (wantsPrediction) {
    overlayLayers.push(
      h("img", {
        class: layerClass,
        src: url("prediction"),
        alt: "Predicted oil mask",
        draggable: "false",
        dataset: { overlay: "prediction" },
        style: { opacity: String(viewState.opacity) },
      }),
    );
  }

  const frame = h("div", { class: "viewer" }, base, ...overlayLayers);

  if (viewState.compare && overlayLayers.length) {
    // Before/after: the overlays are clipped to the right of the handle, so the same
    // pixels are visible with and without the mask at the same instant.
    const wrap = h("div", { class: "viewer__split-line", style: { left: "50%" } },
      h("div", { class: "viewer__split-knob" }, h("span", { html: ICONS.arrowRight })));
    const hit = h("div", { class: "viewer__split" }, wrap);
    let split = 0.5;
    const apply = () => {
      for (const layer of overlayLayers) {
        layer.style.clipPath = `inset(0 0 0 ${(split * 100).toFixed(2)}%)`;
      }
      wrap.style.left = `${(split * 100).toFixed(2)}%`;
    };
    const move = (event) => {
      const rect = hit.getBoundingClientRect();
      split = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      apply();
    };
    hit.addEventListener("pointerdown", (event) => {
      hit.setPointerCapture(event.pointerId);
      move(event);
    });
    hit.addEventListener("pointermove", (event) => {
      if (event.pressure > 0 || event.buttons) move(event);
    });
    apply();
    frame.append(
      hit,
      h("div", { class: "viewer__tag viewer__tag--left" }, `${viewState.band.toUpperCase()} only`),
      h("div", { class: "viewer__tag viewer__tag--right" }, "With mask"),
    );
  }

  return h(
    "div",
    { class: "stack stack--tight" },
    frame,
    U.legend(
      [
        { label: `${viewState.band.toUpperCase()} backscatter, dB`, colour: "var(--text-tertiary)" },
        wantsPrediction ? { label: "Predicted oil", colour: "var(--oil)" } : null,
        wantsReference ? { label: "Supplied reference mask", colour: "var(--reference)" } : null,
      ].filter(Boolean),
    ),
    h(
      "p",
      { class: "small muted" },
      `Preview ${previews.previewSize?.join(" × ")} px, downsampled ${previews.downsampleFactor}× from ` +
        `${previews.sourceSize?.join(" × ")} px. Contrast stretched to the ` +
        `${previews.contrastStretch?.[viewState.band]?.percentiles?.join("–") || "2–98"} percentile ` +
        `range, ${F.num(previews.contrastStretch?.[viewState.band]?.low, 1)} to ` +
        `${F.num(previews.contrastStretch?.[viewState.band]?.high, 1)} dB. Display only; every ` +
        "measurement on the other screens is computed from the full-resolution arrays.",
    ),
  );
}

// -- side cards --------------------------------------------------------------

function tilesCard(ctx, previews, caseId) {
  const files = previews.files || {};
  const tiles = [
    { kind: "vv", name: "VV backscatter", meta: "supplied, dB" },
    { kind: "vh", name: "VH backscatter", meta: "supplied, dB" },
    { kind: "probability", name: "Model probability", meta: "0 to 1, before thresholding" },
    { kind: "prediction", name: "Predicted mask", meta: "thresholded" },
    { kind: "referenceMask", name: "Reference mask", meta: "supplied ground truth" },
    { kind: "comparison", name: "Agreement", meta: "prediction against reference" },
  ];

  return U.card(
    "All layers",
    {
      id: "tiles",
      hint: `scene ${previews.scene}`,
      note:
        previews.legend?.comparison ||
        "Every tile is the same footprint at the same scale, written by the same run.",
    },
    h(
      "div",
      { class: "tile-grid" },
      tiles.map((tile) =>
        files[tile.kind]
          ? h(
              "figure",
              { class: "tile" },
              h(
                "div",
                { class: "tile__frame" },
                h("img", {
                  src: ctx.api.imageUrl(caseId, tile.kind, files),
                  alt: tile.name,
                  loading: "lazy",
                  draggable: "false",
                }),
              ),
              h(
                "figcaption",
                { class: "tile__caption" },
                h("span", { class: "tile__name" }, tile.name),
                h("span", { class: "tile__meta" }, tile.meta),
              ),
            )
          : h(
              "figure",
              { class: "tile" },
              h(
                "div",
                { class: "tile__frame" },
                h(
                  "div",
                  {
                    class: "state",
                    style: { border: "0", background: "none", padding: "var(--s4)", height: "100%" },
                  },
                  icon(ICONS.empty, { cls: "state__icon", size: 22 }),
                  h("span", { class: "small muted" }, "not written"),
                ),
              ),
              h(
                "figcaption",
                { class: "tile__caption" },
                h("span", { class: "tile__name" }, tile.name),
                h("span", { class: "tile__meta" }, "not in this run"),
              ),
            ),
      ),
    ),
  );
}

function detectionCard(ctx, caseDoc) {
  const detection = caseDoc.detection || {};
  const slick = caseDoc.slick || {};
  const scene = ctx.state.metrics?.sceneScale?.test?.atSceneThreshold;

  return U.card(
    "Detection",
    { id: "detection", note: detection.note },
    U.rows(
      U.row("Source", detection.label),
      U.row("Threshold applied", F.num(detection.threshold, 2), { mono: true }),
      U.row("Mean probability over slick", F.pct(slick.confidence), { mono: true }),
      U.row("Slick area", `${F.km2(slick.totalAreaKm2)} km²`, { mono: true }),
      U.row("Raw mask area", `${F.km2(caseDoc.geometry?.rawMask?.areaKm2)} km²`, { mono: true }),
      U.row(
        "Held-out scene IoU",
        scene ? `${F.metric(scene.pooled?.iou, 3)} pooled · ${F.metric(scene.meanSceneIou, 3)} mean` : null,
        { mono: true },
      ),
    ),
    h(
      "p",
      { class: "small muted", style: { "margin-top": "var(--s3)" } },
      slick.confidenceBasis || "",
    ),
  );
}

/**
 * The look-alike warning PRD 6.2 asks for, built only from measured facts: the training
 * set contains no labelled look-alikes, so the model has never been scored on one.
 */
function lookalikeCard(ctx, caseDoc) {
  const limits = ctx.state.metrics?.patchScale?.limitations || [];
  const lookalike = limits.find((text) => /look-alike/i.test(text));
  const flags = caseDoc.slick?.qualityFlags || [];

  return U.card(
    "Look-alike risk",
    { id: "lookalike" },
    h(
      "div",
      { class: "stack stack--tight" },
      U.notice(
        lookalike ||
          "The supplied dataset contains no labelled look-alikes, so the model's ability to " +
            "reject algal blooms, low-wind zones, rain cells and ship wakes is untested here.",
        { kind: "synthetic", strongPrefix: "Untested rejection." },
      ),
      flags.length
        ? h(
            "ul",
            { class: "bullets" },
            flags.map((flag) => h("li", null, h("span", null, flag))),
          )
        : h("p", { class: "small muted" }, "The geometry stage raised no quality flags for this slick."),
      h(
        "p",
        { class: "small muted" },
        "A dark patch in SAR is low backscatter, not oil. Confirming oil needs a second " +
          "look: wind speed at the acquisition, a second polarisation, or an overflight.",
      ),
    ),
  );
}

/** The analyst's own verdict. Stored beside the case, never written into it. */
function analystCard(ctx, caseDoc) {
  const id = ctx.caseId;
  const note = ctx.store.annotation(id);
  const set = (patch) => {
    ctx.store.annotate(id, patch);
    ctx.announce("Analyst note saved locally.", { kind: "success" });
  };

  return U.card(
    "Analyst review",
    {
      id: "review",
      hint: note.updatedUtc ? `saved ${F.utc(note.updatedUtc)}` : null,
      note:
        "Kept in this browser only, keyed by case. It never overwrites the model output, " +
        "and it is not sent anywhere.",
    },
    h(
      "div",
      { class: "stack stack--tight" },
      h(
        "div",
        { class: "inline" },
        U.button("Accept prediction", {
          kind: note.accepted === true ? "primary" : "",
          small: true,
          iconPath: ICONS.check,
          onClick: () => set({ accepted: note.accepted === true ? null : true }),
        }),
        U.button("Reject prediction", {
          kind: "",
          small: true,
          iconPath: ICONS.close,
          onClick: () => set({ accepted: note.accepted === false ? null : false }),
        }),
        U.button(note.flagged ? "Flagged for review" : "Mark for analyst review", {
          kind: note.flagged ? "primary" : "",
          small: true,
          iconPath: ICONS.flag,
          onClick: () => set({ flagged: !note.flagged }),
        }),
      ),
      h(
        "div",
        { class: "field" },
        h("label", { class: "field__label", for: "analyst-note" }, "Note"),
        h(
          "textarea",
          {
            class: "input",
            id: "analyst-note",
            rows: "3",
            placeholder: "What made you accept, reject or flag this?",
            onChange: (event) => set({ note: event.target.value }),
          },
          // A textarea's value is its text content; the `value` attribute does nothing.
          note.note || "",
        ),
      ),
      h(
        "div",
        { class: "inline small muted" },
        note.accepted === true
          ? U.badge("Accepted by analyst", "ok")
          : note.accepted === false
            ? U.badge("Rejected by analyst", "warn")
            : h("span", null, "No verdict recorded."),
        note.flagged ? U.badge("Flagged", "synthetic") : null,
      ),
    ),
  );
}

function georeferenceCard(previews, scene) {
  const bounds = previews.bounds || scene?.bounds || [];
  return U.card(
    "Georeferencing",
    { id: "geo", hint: scene?.crs },
    U.rows(
      U.row("CRS", `${scene?.crs} (EPSG:${previews.epsg || scene?.epsg})`, { mono: true }),
      U.row("North-west", F.latLon([bounds[0], bounds[3]]), { mono: true }),
      U.row("South-east", F.latLon([bounds[2], bounds[1]]), { mono: true }),
      U.row("Pixel size", `${F.num(scene?.transform?.[1] ?? null, 8)}°`, { mono: true }),
      U.row("Scene size", `${scene?.width} × ${scene?.height} px`, { mono: true }),
      U.row("No-data value", F.num(scene?.nodata, 1), { mono: true }),
    ),
  );
}

function isBusy(state) {
  return state.job?.state === "running" || state.job?.state === "queued";
}
