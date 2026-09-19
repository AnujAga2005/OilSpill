/** The operator upload form, on the New analysis screen.
 *
 * Five slots, one required. A SAR GeoTIFF is the scene; a reference mask, an ERA5 wind
 * file, a CMEMS current product and a real AIS extract are each optional and each
 * independent. They are not a bundle: supplying ERA5 alone is a real improvement over the
 * synthetic wind, and the server checks every file against *this* scene on its own terms,
 * so one that does not overlap is reported with a reason rather than quietly ignored.
 *
 * Four fields follow, in the same panel and above the button, because one of them is
 * required and the rest correct what a hand-exported GeoTIFF cannot state for itself:
 *
 * * **Case id.** Required. The label the result is stored and served under.
 * * **Acquired (UTC).** Every forcing lookup and the whole release window are positioned
 *   against the acquisition instant. A DIMAP product states its own; a bare GeoTIFF does
 *   not, and then the operator is the only source. The case records which of the two it
 *   had, so a typed time is never presented as one read off the product.
 * * **Band order.** With no header to name them, the bands are guessed from the supplied
 *   dataset's convention (VH then VV). A scene stored the other way round would be scored
 *   with the polarisations swapped and would look wrong nowhere, so the order can be
 *   declared. A header, when there is one, always wins over this control.
 * * **Analysis name.** Nothing to do with the computation: it is what the case list shows
 *   instead of an id, so a shelf of finished runs stays readable a week later.
 *
 * Each slot shows one line about the file it wants and carries the full description as its
 * tooltip. Five cards that each explain themselves in a paragraph push the button that uses
 * them off the screen, and the button is the point of the panel.
 *
 * The whole form is disabled with the API offline: there is no server to receive a file
 * and no pipeline to run it through, and a form that accepted a 43 MB drag-and-drop before
 * saying so would be worse than one that says so first.
 */

import { h, icon } from "./dom.js";
import { ICONS } from "./icons.js";
import * as F from "./format.js";
import * as U from "./ui.js";
import { runBar } from "./progress.js";

/** The slots, in the order they are worth filling.
 *
 * `note` is what the card shows and `hint` is its tooltip: the one-liner is enough to choose
 * a file, and the paragraph is still a hover away for anyone who wants the condition behind
 * it. `tone` is the app's own colour vocabulary, not decoration, and it groups the five into
 * three jobs at a glance: amber is oil and so the scene is amber; cyan is real reference
 * data throughout the interface, which is what a mask and a real AIS extract are; purple is
 * drift, and wind and currents are both drift forcing. The two forcing slots carry the same
 * icon for the same reason — they do the same job to the same stage.
 */
const SLOTS = [
  {
    kind: "scene",
    label: "SAR scene",
    required: true,
    accept: ".tif,.tiff",
    tone: "oil",
    iconKey: "satellite",
    note: "Two-band GRD GeoTIFF, VV and VH.",
    hint: "Sentinel-1 GRD GeoTIFF, two bands (VV and VH), with a geotransform.",
  },
  {
    kind: "mask",
    label: "Reference mask",
    accept: ".tif,.tiff",
    tone: "reference",
    iconKey: "slick",
    note: "Optional. Compared, never substituted.",
    hint: "Optional. Same pixel dimensions as the scene. Used for comparison only — it never replaces the prediction.",
  },
  {
    kind: "era5",
    label: "ERA5 wind",
    accept: ".nc,.nc4",
    tone: "drift",
    iconKey: "drift",
    note: "Optional. Replaces the synthetic wind.",
    hint: "Optional, NetCDF-4. A few MB, and the single biggest improvement to the drift: it replaces the synthetic wind term.",
  },
  {
    kind: "cmems",
    label: "CMEMS currents",
    accept: ".nc,.nc4",
    tone: "drift",
    iconKey: "drift",
    note: "Optional. Used where it overlaps.",
    hint: "Optional, NetCDF-4. Used only where it covers this scene in space and within 24 hours in time; otherwise the reason is recorded.",
  },
  {
    kind: "ais",
    label: "AIS extract",
    accept: ".csv",
    tone: "reference",
    iconKey: "ship",
    note: "Optional. Real broadcast positions.",
    hint: "Optional, MarineCadastre CSV. Replaces the synthetic vessel traffic with the real broadcast positions in the file.",
  },
];

const BAND_ORDERS = [
  { value: "", label: "Auto", title: "Read the band names from the product header; fall back to the supplied dataset's order (VH, then VV)" },
  { value: "vh-vv", label: "VH, VV", title: "Band 1 is VH and band 2 is VV" },
  { value: "vv-vh", label: "VV, VH", title: "Band 1 is VV and band 2 is VH" },
];

/**
 * Form state, at module scope on purpose.
 *
 * The screen re-renders on every store change. Closure state would be rebuilt each time,
 * so a file chosen before a case switch or a finished job would vanish with it. This is
 * the state of a form the operator is filling in, which outlives any one render of the
 * screen. (Progress updates no longer re-render anything -- see `progress.js` -- but a
 * store change still can, and this is what survives one.)
 */
const form = {
  caseId: "",
  label: "",
  acquiredUtc: "",
  bandOrder: "",
  /** kind -> {file, name, sizeBytes, uploadId, status, fraction, error} */
  slots: {},
  busy: false,
  /** `busy` covers uploading and clearing too; this is the pipeline job alone, which is
   *  the only one of the three that has stages to draw a bar from. */
  running: false,
  error: null,
};

/** Said in the card that holds the form, because it is true of every file that enters it. */
export const UPLOAD_NOTE =
  "Uploaded files are stored outside the evaluated dataset and are never added to " +
  "it. The accuracy figures on the Method screen were measured on the audited test " +
  "split and do not describe how the model performs on a scene from elsewhere.";

/**
 * The whole form, in one node, for the dark panel at the top of the screen.
 *
 * Files, then the four fields, then the button — in that order and in that one box, because
 * a case id is required to run and a required field below the button that needs it is a
 * form that lies about its own order. Everything here is something you fill in before you
 * press Run; everything that merely explains the run is in the cards underneath.
 *
 * `form` is at module scope rather than in this closure, so a file staged before a
 * re-render is still staged after one — see the note on `form` above.
 */
export function uploadPanel(ctx) {
  const offline = ctx.api.apiMode() === "offline";
  const host = h("div", { class: "intake-upload" });
  const paint = () => host.replaceChildren(body(ctx, offline, paint));
  paint();
  return host;
}

function body(ctx, offline, paint) {
  if (offline) {
    return U.missingState({
      title: "No API to upload to",
      body:
        "This is the offline bundle, which serves one stored case and cannot run the " +
        "pipeline. Start the API to analyse a scene of your own.",
      command: ".venv/bin/python scripts/run_api.py",
    });
  }

  const scene = form.slots.scene;
  const mask = form.slots.mask;
  const filled = SLOTS.filter((slot) => form.slots[slot.kind]?.uploadId).length;

  return h(
    "div",
    { class: "stack stack--tight" },

    sectionLabel(
      "Files",
      filled ? `${F.int(filled)} of ${SLOTS.length} staged` : "only the scene is required",
    ),
    h(
      "div",
      { class: "upload-grid" },
      SLOTS.map((slot) => slotRow(ctx, slot, paint)),
    ),

    sectionLabel("Details", "what the file cannot state for itself"),
    fields(paint),

    mask && scene
      ? U.notice(
          "A reference mask is compared against the prediction; it is never substituted " +
            "for it. If the two disagree, the case says so.",
          { kind: "" },
        )
      : null,
    form.error ? U.notice(form.error, { kind: "danger" }) : null,

    // The primary action sits on its own bar rather than loose under the last field, so the
    // one thing that starts a twenty-second job is not the same weight as the controls that
    // configure it.
    h(
      "div",
      { class: "upload-run" },
      h(
        "div",
        { class: "inline" },
        U.button("Run the pipeline", {
          kind: "primary",
          iconPath: ICONS.play,
          disabled: form.busy || !scene?.uploadId,
          title: scene?.uploadId
            ? "Detect, measure, hindcast the drift and rank the vessels near the origin"
            : "A SAR scene is required",
          onClick: () => run(ctx, paint),
        }),
        U.button("Clear", {
          kind: "quiet",
          small: true,
          iconPath: ICONS.trash,
          disabled: form.busy || !Object.keys(form.slots).length,
          title: "Delete every file this session uploaded from the server",
          onClick: () => clearAll(ctx, paint),
        }),
      ),
      // While the job runs the note is replaced by the bar, in the same slot: the line that
      // said how long this would take now says how far along it is. The bar repaints itself
      // from the job's own stage lines and re-renders nothing around it.
      form.running
        ? runBar()
        : h(
            "p",
            { class: "small upload-run__note" },
            !scene?.uploadId
              ? "A two-band GeoTIFF with a geotransform is the one required input."
              : !form.caseId.trim()
                ? "Give the case an id above, then this runs."
                : "About twenty seconds, then the Command centre opens on the result.",
          ),
    ),
  );
}

/** The four controls. One is required; the other three correct a default.
 *
 * Each hint is one line. Four two-line hints cost the panel another row, and the condition
 * behind each one is stated in full on the Method screen and in the case the run produces.
 */
function fields(paint) {
  return h(
    "div",
    { class: "upload-fields" },
    U.field({
      label: "Case id",
      value: form.caseId,
      placeholder: "e.g. gulf-2024-05-01",
      hint: "Required. Letters, digits, . - _ only.",
      ref: (node) => {
        node.addEventListener("input", () => {
          form.caseId = node.value;
        });
        // The run bar below says whether an id is still missing, so it is repainted when
        // the id changes. On blur rather than on every keystroke: repainting mid-word
        // would take the caret with it.
        node.addEventListener("change", paint);
      },
    }),
    U.field({
      label: "Analysis name",
      value: form.label,
      placeholder: "e.g. Malta channel, morning pass",
      hint: "Optional. What the case list shows instead.",
      ref: (node) => {
        node.addEventListener("input", () => {
          form.label = node.value;
        });
      },
    }),
    U.field({
      label: "Acquired (UTC)",
      type: "datetime-local",
      value: form.acquiredUtc,
      hint: "Optional, UTC. A time in the file wins.",
      ref: (node) => {
        node.addEventListener("input", () => {
          form.acquiredUtc = node.value;
        });
      },
    }),
    h(
      "div",
      { class: "field" },
      h("span", { class: "field__label" }, "Band order"),
      U.segmented(
        BAND_ORDERS,
        form.bandOrder,
        (value) => {
          form.bandOrder = value;
          paint();
        },
        { ariaLabel: "Band order of the uploaded scene" },
      ),
      h("p", { class: "field__hint" }, "Which polarisation is band 1."),
    ),
  );
}

/** A hairline heading above a group, so the slots read as a labelled set. */
function sectionLabel(text, hint) {
  return h(
    "div",
    { class: "form-section" },
    h("span", { class: "form-section__title" }, text),
    hint ? h("span", { class: "form-section__hint" }, hint) : null,
  );
}

function slotRow(ctx, slot, paint) {
  const state = form.slots[slot.kind];
  const id = `upl-${slot.kind}`;
  const sending = state?.status === "sending";

  const input = h("input", {
    type: "file",
    id,
    class: "sr-only",
    accept: slot.accept,
    disabled: form.busy,
    onChange: (event) => {
      const file = event.target.files?.[0];
      // Let the same file be chosen twice in a row: without this the browser suppresses
      // the second `change` and a re-try after a failed upload does nothing.
      event.target.value = "";
      if (file) send(ctx, slot, file, paint);
    },
  });

  const status = () => {
    if (!state) return null;
    if (state.error) {
      return h("span", { class: "upload-slot__status oil-text" }, state.error);
    }
    if (sending) {
      return h(
        "span",
        { class: "upload-slot__status muted" },
        Number.isFinite(state.fraction)
          ? `Uploading · ${F.pct(state.fraction, 0)}`
          : "Uploading…",
      );
    }
    return h(
      "span",
      { class: "upload-slot__status" },
      icon(ICONS.check, { size: 13, cls: "upload-slot__tick" }),
      h("span", { class: "muted" }, `${state.name} · ${F.bytes(state.sizeBytes)}`),
    );
  };

  // A `<label for>` rather than a `<button>`: it opens the file picker with no script, is
  // keyboard-operable for free, and a label pointing at a disabled input is already inert,
  // so the class below only has to say so visually.
  const pick = h(
    "label",
    {
      class: ["btn", "btn--sm", "upload-slot__pick", form.busy ? "is-disabled" : null],
      for: id,
      "aria-disabled": form.busy ? "true" : null,
      title: state?.uploadId ? `Replace the staged ${slot.label.toLowerCase()}` : null,
    },
    icon(ICONS.upload, { cls: "btn__icon", size: 14 }),
    state?.uploadId ? "Replace file" : sending ? "Uploading…" : "Choose file",
  );

  return h(
    "div",
    {
      class: ["upload-slot", state?.uploadId ? "upload-slot--filled" : null],
      // The card shows one line; the paragraph behind it is a hover away rather than gone.
      title: slot.hint,
    },
    input,
    h(
      "div",
      { class: "upload-slot__head" },
      h(
        "span",
        { class: ["icon-tile", "upload-slot__tile", slot.tone ? `icon-tile--${slot.tone}` : null] },
        icon(state?.uploadId ? ICONS.file : ICONS[slot.iconKey]),
      ),
      h("span", { class: "upload-slot__label" }, slot.label),
      // Only the required slot is tagged. Every other note already opens with the word
      // "Optional", and four OPTIONAL tags in a row of five say nothing the one REQUIRED
      // tag does not say better -- while costing the head room it needs for its name.
      slot.required ? h("span", { class: "upload-slot__req" }, "required") : null,
    ),
    h("p", { class: "upload-slot__hint" }, slot.note),
    sending
      ? h(
          "div",
          { class: "bar__track upload-slot__bar" },
          h("div", {
            class: "bar__fill",
            style: {
              width: `${Number.isFinite(state.fraction) ? Math.round(state.fraction * 100) : 30}%`,
              background: "var(--reference)",
            },
          }),
        )
      : null,
    status(),
    h("div", { class: "upload-slot__foot" }, pick),
  );
}

async function send(ctx, slot, file, paint) {
  form.error = null;
  form.slots[slot.kind] = {
    name: file.name,
    sizeBytes: file.size,
    status: "sending",
    fraction: 0,
    uploadId: null,
    error: null,
  };
  paint();

  try {
    const result = await ctx.api.upload(slot.kind, file, (fraction) => {
      const state = form.slots[slot.kind];
      if (!state) return;
      state.fraction = fraction;
      paint();
    });
    const upload = result?.upload || {};
    form.slots[slot.kind] = {
      name: upload.originalName || file.name,
      sizeBytes: upload.sizeBytes ?? file.size,
      status: "ready",
      fraction: 1,
      uploadId: upload.uploadId,
      error: null,
    };
    // A scene is usually the first thing uploaded and usually names the case well enough
    // to save typing. Only ever a suggestion: the operator can overwrite it, and it is
    // never applied over something they have already typed.
    if (slot.kind === "scene" && !form.caseId) {
      form.caseId = suggestId(upload.originalName || file.name);
    }
    ctx.announce?.(`${slot.label} uploaded.`, { silent: true });
  } catch (error) {
    if (error?.name === "AbortError") {
      delete form.slots[slot.kind];
    } else {
      // The server's own message names what was wrong with the file. It is shown on the
      // slot rather than in a banner, because it is about that one file.
      form.slots[slot.kind] = {
        name: file.name,
        sizeBytes: file.size,
        status: "failed",
        uploadId: null,
        error: String(error?.message || error),
      };
    }
  }
  paint();
}

/** A plausible case id from a filename, within the server's own id rules. */
function suggestId(name) {
  const stem = String(name || "").replace(/\.[^.]*$/, "");
  const cleaned = stem.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "");
  return cleaned.slice(0, 48).toLowerCase();
}

async function run(ctx, paint) {
  const scene = form.slots.scene;
  if (!scene?.uploadId) return;

  const caseId = form.caseId.trim();
  if (!caseId) {
    form.error = "Give the case an id: it is the label this result is stored and served under.";
    paint();
    return;
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(caseId)) {
    form.error =
      "A case id starts with a letter or a digit and contains only letters, digits, " +
      "dots, dashes and underscores.";
    paint();
    return;
  }

  form.busy = true;
  form.running = true;
  form.error = null;
  paint();

  const options = { scene: caseId, sceneUpload: scene.uploadId, previews: true };
  for (const slot of SLOTS) {
    const id = form.slots[slot.kind]?.uploadId;
    if (id && slot.kind !== "scene") options[`${slot.kind}Upload`] = id;
  }
  if (form.acquiredUtc) options.acquiredUtc = form.acquiredUtc;
  if (form.bandOrder) options.bandOrder = form.bandOrder;
  if (form.label.trim()) options.label = form.label.trim();

  // `runAnalysis` owns the job: it polls, forwards progress to the Processing status card,
  // switches the active case on success and reports a failure through the announcer. The
  // only thing left here is to release the form afterwards.
  const job = await ctx.runAnalysis("detect", options);
  form.busy = false;
  form.running = false;
  if (job) {
    // The files stay on the server: a second run with a different horizon should not need
    // a 43 MB re-upload. Only the staging form is reset.
    form.error = null;
  }
  paint();
}

async function clearAll(ctx, paint) {
  form.busy = true;
  paint();
  try {
    const result = await ctx.api.clearUploads();
    form.slots = {};
    form.error = null;
    ctx.announce?.(
      `${F.int(result?.removed ?? 0)} uploaded file${result?.removed === 1 ? "" : "s"} deleted from the server.`,
    );
  } catch (error) {
    form.error = `The uploads could not be cleared: ${error?.message || error}`;
  }
  form.busy = false;
  paint();
}
