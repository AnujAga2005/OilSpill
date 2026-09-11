/** Screen 3: slick analysis.
 *
 * Two jobs. First, publish the geometry the pipeline measured, with the method attached -
 * an area in square kilometres means nothing without knowing whether it came from counting
 * pixels on a sphere or from a polygon nobody validated. Second, let an analyst disagree
 * with the boundary and see what that does to the number.
 *
 * The model outline is never modified. The editable ring is a simplified copy that lives in
 * local annotations, and the recomputed area is labelled as the analyst's, not the model's.
 */

import { h } from "../dom.js";
import { ICONS } from "../icons.js";
import * as F from "../format.js";
import * as U from "../ui.js";
import * as X from "../exporters.js";
import { createMap, mapLegend } from "../mapview.js";
import { baseRasters, slickRings, slickLayers, C, Z } from "../layers.js";

/** Handle radius in screen pixels, and the grab tolerance around one. */
const HANDLE = 4.2;
const GRAB = 11;
/** Above this many vertices a ring is too dense to edit by hand. */
const EDIT_VERTEX_TARGET = 48;

export function render(ctx) {
  const { caseDoc, caseStatus } = ctx;

  if (caseStatus !== "ready" || !caseDoc) {
    return U.stateSwitch(caseStatus, caseDoc, () => null, {
      loadingTitle: "Loading geometry",
      missing: {
        title: "No geometry has been measured yet",
        body: "The geometry stage runs as part of a detection.",
        command: ".venv/bin/python scripts/run_api.py --build-demo",
      },
      failed: { title: "The case could not be loaded", body: "The API did not return a case." },
    });
  }

  const slick = caseDoc.slick || {};
  const regions = caseDoc.geometry?.slicks || [];
  const rings = slickRings(caseDoc);

  if (!rings.length) {
    return U.emptyState({
      title: "No slick was delineated",
      body:
        `The detector ran and the geometry stage found no connected region above the ` +
        `${F.int(caseDoc.geometry?.config?.min_area_px)} pixel minimum. That is a valid ` +
        "result: this scene has no mapped slick.",
    });
  }

  // Which region the tables and the detail card are talking about.
  const selectedId = ctx.route.get("slick") || regions[0]?.id || rings[0]?.id;
  const selected = regions.find((r) => r.id === selectedId) || regions[0] || null;

  return h(
    "div",
    { class: "stack" },

    U.card(
      "Measured extent",
      {
        id: "extent",
        hint: `${F.int(slick.slickCount)} region${slick.slickCount === 1 ? "" : "s"}`,
        note: slick.areaMethod,
        actions: h(
          "div",
          { class: "inline" },
          U.button("GeoJSON", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.download,
            onClick: () => {
              X.downloadJson(
                X.exportName(caseDoc, "slicks", "geojson"),
                caseDoc.geometry?.geojson || { type: "FeatureCollection", features: [] },
              );
              ctx.announce("Slick outlines downloaded as GeoJSON.", { kind: "success" });
            },
          }),
          U.button("CSV", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.download,
            onClick: () => {
              X.downloadCsv(X.exportName(caseDoc, "slicks", "csv"), X.slicksCsv(caseDoc));
              ctx.announce("Slick table downloaded as CSV.", { kind: "success" });
            },
          }),
        ),
      },
      h(
        "div",
        { class: "grid grid--stats" },
        U.stat({
          label: "Total area",
          value: F.km2(slick.totalAreaKm2),
          unit: "km²",
          tone: "oil",
          sub: `across ${F.int(slick.slickCount)} disconnected region${slick.slickCount === 1 ? "" : "s"}`,
        }),
        U.stat({
          label: "Largest region",
          value: F.km2(slick.areaKm2),
          unit: "km²",
          sub: `${F.pct(slick.areaKm2 / slick.totalAreaKm2)} of the total`,
        }),
        U.stat({
          label: "Extent",
          value: `${F.km(slick.lengthKm, 1)} × ${F.km(slick.widthKm, 1)}`,
          unit: "km",
          sub: `elongation ${F.num(slick.elongation, 2)}, bearing ${F.bearing(slick.orientationDegFromNorth)}`,
        }),
        U.stat({
          label: "Perimeter",
          value: F.km(slick.perimeterKm, 1),
          unit: "km",
          sub: `compactness ${F.num(slick.compactness, 3)} — 1.0 is a circle`,
        }),
      ),
    ),

    boundaryCard(ctx, caseDoc, rings, selectedId),

    h(
      "div",
      { class: "grid grid--wide-left" },
      regionsCard(ctx, caseDoc, regions, selectedId),
      regionDetailCard(selected),
    ),

    screeningCard(ctx, caseDoc),

    // -- how the numbers were produced, folded ----------------------------
    // The morphology parameters and the pixel-area arithmetic have to be on the screen for
    // the areas above to be checkable. They were a right-hand column next to the region
    // table, where they competed with a finding for the same glance.
    h(
      "div",
      { class: "stack stack--tight" },
      methodCard(caseDoc),
      qualityCard(caseDoc, slick),
    ),
  );
}

// -- look-alike screening ----------------------------------------------------

/** Verdict label -> badge tone. Amber is the one that wants an analyst's eye. */
const VERDICT_TONE = {
  accepted: "model",
  uncertain: "synthetic",
  rejected: "supplied",
  unscreened: "",
};

function verdictBadge(label) {
  if (!label) return null;
  return U.badge(label, VERDICT_TONE[label] ?? "", {
    title:
      label === "rejected"
        ? "More consistent with a look-alike than with oil. The screen does not name which look-alike."
        : label === "accepted"
          ? "Consistent with an oil film."
          : label === "uncertain"
            ? "Between the two thresholds, so kept for human review."
            : "No clear water around this region to measure it against, so no verdict is offered.",
  });
}

/**
 * What else in this scene looked like oil, and what the screen made of it.
 *
 * This is the one card on the screen that reports something the pipeline *rejected*, so it
 * is written to be checkable: every patch carries the three statistics the decision turned
 * on, the verdict, and whether it lands on a published slick or somewhere else entirely. A
 * rejected patch that overlaps nothing was never in the case, and the limits say so - the
 * value is the evidence about what the screen throws out, not a correction to the table above.
 */
function screeningCard(ctx, caseDoc) {
  const block = caseDoc.screening;
  if (!block) return null;

  const counts = block.counts || {};
  const patches = block.patches || [];
  const thresholds = block.thresholds || {};
  const components = block.components || {};
  const rejected = patches.filter((patch) => patch.label === "rejected");

  return U.card(
    "Look-alike screening",
    {
      id: "screening",
      hint: block.fitted ? "seven-feature linear screen" : "no screen fitted",
      note: block.question,
      actions: patches.length
        ? U.button("CSV", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.download,
            onClick: () => {
              X.downloadCsv(X.exportName(caseDoc, "dark-patches", "csv"), X.patchesCsv(caseDoc));
              ctx.announce("Screened dark patches downloaded as CSV.", { kind: "success" });
            },
          })
        : null,
    },
    h(
      "div",
      { class: "stack stack--tight" },

      block.fitted
        ? null
        : U.notice(block.limits?.[0] || "No look-alike screen has been fitted.", {
            kind: "synthetic",
            strongPrefix: "Unfitted.",
          }),

      // The reconciling fact, above the patch counts rather than below them. The proposals
      // are cut out of the scene by a darkness threshold and the regions above by the
      // network, so the screen can reject every proposal and still keep every region --
      // and a reader who saw only the rejections would think this scene held no oil.
      components.screened
        ? U.notice(
            // An em dash, not a full stop: the note is written to follow a label, so it
            // opens lower-case and a sentence break would read as a typo here.
            `${F.int(components.accepted)} of ${F.int(components.screened)} published ` +
              `${components.screened === 1 ? "region is" : "regions are"} consistent with oil — ` +
              components.note,
            { strongPrefix: "The regions above:" },
          )
        : null,

      h(
        "div",
        { class: "grid grid--stats" },
        U.stat({
          label: "Dark patches examined",
          value: F.int(counts.proposed),
          sub: `${F.int(counts.overlappingPublishedSlick)} of them land on a published slick`,
        }),
        U.stat({
          label: "Rejected as look-alikes",
          value: F.int(counts.rejected),
          sub: `likelihood at or below ${F.num(thresholds.rejectAtOrBelow, 2)}`,
        }),
        U.stat({
          label: "Kept for review",
          value: F.int(counts.uncertain),
          tone: "synthetic",
          sub: "between the two thresholds, so not suppressed",
        }),
        U.stat({
          label: "Consistent with oil",
          value: F.int(counts.accepted),
          tone: "oil",
          sub: `likelihood at or above ${F.num(thresholds.acceptAtOrAbove, 2)}`,
        }),
      ),

      patches.length ? patchTable(ctx, caseDoc, patches) : null,

      rejected.length
        ? h(
            "div",
            { class: "stack stack--tight" },
            h(
              "p",
              { class: "small muted" },
              `Why ${F.int(rejected.length)} patch${rejected.length === 1 ? " was" : "es were"} rejected:`,
            ),
            h(
              "ul",
              { class: "bullets" },
              rejected.slice(0, 4).map((patch) =>
                h(
                  "li",
                  null,
                  h("span", null, h("span", { class: "mono" }, patch.id), " — ", patch.reasons?.[0] || patch.headline),
                ),
              ),
            ),
            rejected.length > 4
              ? h("p", { class: "small muted" }, `${F.int(rejected.length - 4)} more in the table above.`)
              : null,
          )
        : null,

      block.limits?.length
        ? h(
            "ul",
            { class: "bullets small muted" },
            (block.fitted ? block.limits : block.limits.slice(1)).map((limit) =>
              h("li", null, h("span", null, limit)),
            ),
          )
        : null,

      U.rows(
        U.row("Proposer", block.proposer?.rule, { stack: true }),
        U.row("Calibration", block.calibration, { stack: true }),
      ),
    ),
  );
}

function patchTable(ctx, caseDoc, patches) {
  const regionIds = new Set((caseDoc.geometry?.slicks || []).map((region) => region.id));
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
          h("th", null, "Patch"),
          h("th", { class: "right" }, "Area km²"),
          h("th", { class: "right" }, "Darkness σ"),
          h("th", { class: "right" }, "Roughness"),
          h("th", { class: "right" }, "Elongation"),
          h("th", { class: "right" }, "Oil-like"),
          h("th", null, "Verdict"),
          h("th", null, "Region"),
        ),
      ),
      h(
        "tbody",
        null,
        patches.map((patch) => {
          const measured = patch.measured || {};
          // Clicking a patch that sits on a published slick selects that slick, so the
          // detail card and the map follow. A patch that overlaps nothing has nowhere to go.
          const target = regionIds.has(patch.overlapsSlick) ? patch.overlapsSlick : null;
          const select = target ? () => ctx.setParams({ slick: target }) : null;
          return h(
            "tr",
            {
              tabindex: target ? "0" : null,
              role: target ? "button" : null,
              onClick: select,
              onKeydown: select
                ? (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      select();
                    }
                  }
                : null,
            },
            h("td", { class: "mono" }, patch.id),
            h("td", { class: "right" }, F.km2(patch.areaKm2)),
            h("td", { class: "right" }, F.num(measured.darknessZ, 2)),
            h("td", { class: "right" }, F.num(measured.textureRatio, 2)),
            h("td", { class: "right" }, F.num(measured.elongation, 2)),
            h("td", { class: "right" }, F.pct(patch.oilLikelihood)),
            h("td", null, verdictBadge(patch.label)),
            h(
              "td",
              { class: "mono" },
              patch.overlapsSlick
                ? `${patch.overlapsSlick} · ${F.pct(patch.overlapFraction)}`
                : h("span", { class: "muted" }, "elsewhere in the scene"),
            ),
          );
        }),
      ),
    ),
  );
}

// -- the editable boundary ---------------------------------------------------

/**
 * The map, plus a boundary editor.
 *
 * The editor is deliberately narrow in scope: move, insert and delete vertices on one
 * simplified ring. It does not offer freehand drawing, because a hand-drawn outline that
 * was never registered against the pixels would produce an area figure with no provenance
 * at all.
 */
function boundaryCard(ctx, caseDoc, rings, selectedId) {
  const holder = h("div", { style: { height: "clamp(300px, 52vh, 560px)" } });
  const stored = ctx.store.annotation(ctx.caseId);
  const target = rings.find((r) => r.id === selectedId) || rings[0];
  const modelRing = (target?.rings || [])[0] || [];

  // Edit state. `null` draft means "not editing"; the stored boundary is re-hydrated so a
  // saved edit survives a reload.
  const edit = {
    on: false,
    draft: null,
    dragIndex: -1,
    hoverIndex: -1,
    modelAreaKm2: target?.areaKm2 ?? null,
    savedRing: stored.boundary?.slickId === (target?.id || null) ? stored.boundary.ring : null,
  };

  const readoutHost = h("div", { class: "inline between" });
  const controlHost = h("div", { class: "inline" });
  let map = null;

  const draftAreaKm2 = () => (edit.draft ? ringAreaKm2(edit.draft) : null);

  /** Everything drawn: the model ring always, the draft on top, handles when editing. */
  const paintMap = () => {
    if (!map) return;
    const vectors = [
      ...slickLayers(caseDoc, {
        fill: !edit.on,
        width: edit.on ? 1 : 1.5,
        pickable: !edit.on,
      }),
    ];
    if (edit.on) for (const v of vectors) v.opacity = 0.45;

    const ring = edit.draft || edit.savedRing;
    if (ring && ring.length > 2) {
      vectors.push({
        type: "polygon",
        id: "analyst-boundary",
        kind: "analyst",
        rings: [ring],
        stroke: C.agree,
        fill: "rgba(110, 231, 183, 0.14)",
        width: 2,
        z: Z.selected,
        label: `Analyst boundary · ${F.km2(ringAreaKm2(ring))} km²`,
        pickable: !edit.on,
      });
    }
    if (edit.on && edit.draft) {
      edit.draft.forEach((point, index) => {
        vectors.push({
          type: "point",
          id: `handle:${index}`,
          kind: "handle",
          at: point,
          r: index === edit.hoverIndex || index === edit.dragIndex ? HANDLE + 1.8 : HANDLE,
          fill: index === edit.dragIndex ? C.oil : C.agree,
          stroke: C.agree,
          z: Z.marker,
        });
      });
    }
    map.setVectors(vectors).redraw();
  };

  const paintChrome = () => {
    controlHost.replaceChildren(...editControls(ctx, edit, target, { paintMap, paintChrome }));
    readoutHost.replaceChildren(areaReadout(edit, draftAreaKm2()));
  };

  requestAnimationFrame(() => {
    if (!holder.isConnected) return;
    map = createMap(holder, {
      onSelect: (hit) => {
        if (hit?.id?.startsWith("slick:")) {
          ctx.setParams({ slick: hit.id.slice("slick:".length) });
        }
      },
    });
    map.setRasters(baseRasters(caseDoc, ctx.caseId, { kind: "vv", opacity: 0.95 }));
    paintMap();
    // Frame the slick, not the acquisition it sits in. The backdrop is the whole 2048 px
    // scene and the slick is a fraction of it, so fitting both puts the subject of this
    // screen on screen as a sliver.
    map.fitFindings();

    // The gesture claim: while editing, a pointerdown near a handle drags it instead of
    // panning the view.
    map.setGesture({
      hover: (point) => {
        if (!edit.on || !edit.draft) return false;
        const index = nearestHandle(map, edit.draft, point);
        if (index !== edit.hoverIndex) {
          edit.hoverIndex = index;
          paintMap();
        }
        map.canvas.style.cursor = index >= 0 ? "grab" : "crosshair";
        return true;
      },
      down: (point, event) => {
        if (!edit.on || !edit.draft) return false;
        const index = nearestHandle(map, edit.draft, point);
        if (index >= 0) {
          // Alt-click or right-click removes a vertex; a ring needs at least a triangle.
          if ((event.altKey || event.button === 2) && edit.draft.length > 3) {
            edit.draft.splice(index, 1);
            edit.hoverIndex = -1;
            paintMap();
            paintChrome();
            return true;
          }
          edit.dragIndex = index;
          map.canvas.style.cursor = "grabbing";
          paintMap();
          return true;
        }
        // Empty space on the ring inserts a vertex into the nearest edge.
        const edge = nearestEdge(map, edit.draft, point);
        if (edge >= 0) {
          edit.draft.splice(edge + 1, 0, [point.lon, point.lat]);
          edit.dragIndex = edge + 1;
          paintMap();
          paintChrome();
          return true;
        }
        return false;
      },
      move: (point) => {
        if (edit.dragIndex < 0 || !edit.draft) return;
        edit.draft[edit.dragIndex] = [point.lon, point.lat];
        paintMap();
        readoutHost.replaceChildren(areaReadout(edit, draftAreaKm2()));
      },
      up: () => {
        if (edit.dragIndex < 0) return;
        edit.dragIndex = -1;
        map.canvas.style.cursor = "grab";
        paintMap();
        paintChrome();
      },
    });

    // A right-click inside the map would otherwise open the browser menu mid-delete.
    const blockMenu = (event) => {
      if (edit.on) event.preventDefault();
    };
    map.canvas.addEventListener("contextmenu", blockMenu);

    ctx.onCleanup(() => {
      map.canvas.removeEventListener("contextmenu", blockMenu);
      map.destroy();
    });
  });

  paintChrome();

  return U.card(
    "Boundary",
    {
      id: "boundary",
      hint: target ? `${target.id} · ${F.km2(target.areaKm2)} km²` : null,
      note:
        "The model outline stays exactly as the pipeline traced it. An analyst edit is a " +
        "separate ring, measured with the same spherical formula and stored in this " +
        "browser only.",
      actions: controlHost,
    },
    h(
      "div",
      { class: "stack stack--tight" },
      holder,
      readoutHost,
      mapLegend([
        { label: "Model outline", colour: "var(--oil)" },
        { label: "Analyst boundary", colour: "var(--agree)" },
      ]),
      h(
        "p",
        { class: "small muted" },
        "Drag to pan, scroll to zoom, double-click to zoom in. While editing: drag a handle " +
          "to move it, click the outline to insert one, alt-click or right-click a handle to " +
          "remove it.",
      ),
    ),
  );
}

function editControls(ctx, edit, target, { paintMap, paintChrome }) {
  const controls = [];

  if (!edit.on) {
    controls.push(
      U.button(edit.savedRing ? "Resume editing" : "Edit boundary", {
        kind: "",
        small: true,
        iconPath: ICONS.target,
        disabled: !target,
        onClick: () => {
          const source = edit.savedRing || (target?.rings || [])[0] || [];
          edit.draft = simplifyRing(source, EDIT_VERTEX_TARGET);
          edit.on = true;
          paintMap();
          paintChrome();
          ctx.announce(`Boundary editor open with ${edit.draft.length} vertices.`);
        },
      }),
    );
    if (edit.savedRing) {
      controls.push(
        U.button("Discard saved edit", {
          kind: "quiet",
          small: true,
          iconPath: ICONS.close,
          onClick: () => {
            edit.savedRing = null;
            edit.draft = null;
            ctx.store.annotate(ctx.caseId, { boundary: null });
            ctx.announce("Analyst boundary discarded.");
          },
        }),
      );
    }
    return controls;
  }

  controls.push(
    U.button("Save boundary", {
      kind: "primary",
      small: true,
      iconPath: ICONS.check,
      onClick: () => {
        const ring = edit.draft;
        // `annotate` notifies the store, which re-renders this screen from the saved ring.
        ctx.store.annotate(ctx.caseId, {
          boundary: {
            slickId: target?.id || null,
            ring,
            areaKm2: Number(ringAreaKm2(ring).toFixed(6)),
            modelAreaKm2: edit.modelAreaKm2,
            vertices: ring.length,
            method:
              "spherical shoelace on a sphere of radius 6371008.8 m, the same radius the " +
              "pipeline integrates pixel areas on",
            note: "Analyst delineation. Not a model output.",
          },
        });
        ctx.announce(
          `Analyst boundary saved, ${F.km2(ringAreaKm2(ring))} square kilometres.`,
          { kind: "success" },
        );
      },
    }),
    U.button("Reset to model", {
      kind: "quiet",
      small: true,
      iconPath: ICONS.reset,
      onClick: () => {
        edit.draft = simplifyRing((target?.rings || [])[0] || [], EDIT_VERTEX_TARGET);
        paintMap();
        paintChrome();
      },
    }),
    U.button("Close editor", {
      kind: "quiet",
      small: true,
      iconPath: ICONS.close,
      onClick: () => {
        edit.on = false;
        edit.draft = null;
        edit.hoverIndex = -1;
        paintMap();
        paintChrome();
      },
    }),
  );
  return controls;
}

function areaReadout(edit, areaKm2) {
  const ring = edit.draft || edit.savedRing;
  if (!ring) {
    return h(
      "p",
      { class: "small muted" },
      "No analyst boundary. The figures above are the model's.",
    );
  }
  const area = areaKm2 ?? ringAreaKm2(ring);
  const model = edit.modelAreaKm2;
  const delta = Number.isFinite(model) && model > 0 ? (area - model) / model : null;

  return h(
    "div",
    { class: "inline between" },
    h(
      "div",
      { class: "inline" },
      U.badge(`Analyst area ${F.km2(area)} km²`, "ok"),
      Number.isFinite(model)
        ? h(
            "span",
            { class: "small muted" },
            `model ${F.km2(model)} km²${
              delta === null ? "" : ` · ${delta >= 0 ? "+" : ""}${F.pct(delta)} difference`
            }`,
          )
        : null,
    ),
    h(
      "span",
      { class: "small muted mono" },
      `${ring.length} vertices${edit.on ? " · editing" : " · saved"}`,
    ),
  );
}

// -- tables ------------------------------------------------------------------

function regionsCard(ctx, caseDoc, regions, selectedId) {
  if (!regions.length) {
    return U.card(
      "Regions",
      { id: "regions" },
      U.emptyState({ title: "No per-region breakdown", body: "This case stored no region table." }),
    );
  }

  const summary = caseDoc.geometry?.summary || {};
  return U.card(
    "Regions",
    {
      id: "regions",
      hint: `${F.int(summary.componentsPublished)} of ${F.int(summary.componentsFound)} components published`,
      note:
        `${F.int(summary.componentsBelowMinArea)} component(s) fell below the ` +
        `${F.int(caseDoc.geometry?.config?.min_area_px)} pixel minimum and were dropped; ` +
        `${F.int(summary.componentsWithInvalidOutline)} traced outline(s) self-intersect and ` +
        "are published for display only.",
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
            h("th", null, "Region"),
            h("th", { class: "right" }, "Area km²"),
            h("th", { class: "right" }, "Perimeter km"),
            h("th", { class: "right" }, "Confidence"),
            h("th", null, "Flags"),
          ),
        ),
        h(
          "tbody",
          null,
          regions.map((region) =>
            h(
              "tr",
              {
                // `.table tbody tr[aria-selected="true"]` is the selected style; there is no
                // `is-selected` class in the stylesheet.
                tabindex: "0",
                role: "button",
                "aria-selected": String(region.id === selectedId),
                onClick: () => ctx.setParams({ slick: region.id }),
                onKeydown: (event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    ctx.setParams({ slick: region.id });
                  }
                },
              },
              h("td", { class: "mono" }, region.id),
              h("td", { class: "right" }, F.km2(region.areaKm2)),
              h("td", { class: "right" }, F.km(region.perimeterM / 1000, 1)),
              h("td", { class: "right" }, F.pct(region.confidence)),
              h(
                "td",
                null,
                h(
                  "div",
                  { class: "inline" },
                  region.touchesSceneEdge ? U.badge("edge", "warn") : null,
                  region.geometryValid === false ? U.badge("outline", "warn") : null,
                  region.elongation >= (caseDoc.geometry?.config?.elongation_flag || 4)
                    ? U.badge("linear", "synthetic")
                    : null,
                  // Only the exceptions. A badge on every accepted region would be noise,
                  // and this column is where a reader looks for what needs a second look.
                  region.screening?.label === "rejected"
                    ? U.badge("look-alike", "supplied", {
                        title:
                          "The look-alike screen finds this region more consistent with " +
                          "something other than oil. It is still published.",
                      })
                    : null,
                  region.screening?.label === "uncertain"
                    ? U.badge("uncertain", "synthetic", {
                        title: "The look-alike screen could not separate this region either way.",
                      })
                    : null,
                ),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

function regionDetailCard(region) {
  if (!region) {
    return U.card("Region detail", { id: "region" }, U.emptyState({ title: "Nothing selected" }));
  }
  const screening = region.screening;
  const measured = screening?.measured || {};
  return U.card(
    "Region detail",
    { id: "region", hint: region.id },
    U.rows(
      U.row("Area", `${F.km2(region.areaKm2)} km²`, { mono: true }),
      U.row("Pixels", F.int(region.pixels), { mono: true }),
      U.row("Perimeter", `${F.km(region.perimeterM / 1000, 2)} km`, { mono: true }),
      U.row("Length × width", `${F.km(region.lengthM / 1000, 2)} × ${F.km(region.widthM / 1000, 2)} km`, { mono: true }),
      U.row("Elongation", F.num(region.elongation, 2), { mono: true }),
      U.row("Orientation", F.bearing(region.orientationDegFromNorth), { mono: true }),
      U.row("Compactness", F.num(region.compactness, 3), { mono: true }),
      U.row("Centroid", F.latLon(region.centroid), { mono: true }),
      U.row("Mean probability", F.pct(region.meanProbability), { mono: true }),
      U.row("Median probability", F.pct(region.medianProbability), { mono: true }),
      U.row("10th percentile", F.pct(region.p10Probability), { mono: true }),
      U.row("Above 0.8", F.pct(region.fractionAbove0_8), { mono: true }),
      U.row("Outline geometry", region.ringGeometry, { mono: true }),
    ),
    // The look-alike verdict for this one region, kept below the geometry because it is a
    // different kind of claim: everything above is measured, this is inferred.
    screening
      ? h(
          "div",
          { class: "stack stack--tight", style: { "margin-top": "var(--s4)" } },
          h(
            "div",
            { class: "inline" },
            verdictBadge(screening.label),
            h("span", { class: "small muted" }, screening.headline),
          ),
          U.rows(
            U.row("Oil-likelihood", F.pct(screening.oilLikelihood), { mono: true }),
            U.row("Darkness", `${F.num(measured.darknessZ, 2)} σ below the water around it`, { mono: true }),
            U.row("Darkest tenth", `${F.num(measured.darknessP10Z, 2)} σ`, { mono: true }),
            U.row("Interior roughness", `${F.num(measured.textureRatio, 2)} × the background`, { mono: true }),
            U.row("Edge definition", `${F.num(measured.edgeSharpness, 2)} × the ambient gradient`, { mono: true }),
            U.row("Solidity", F.num(measured.solidity, 3), { mono: true }),
            U.row(
              "Contrast",
              F.isMissing(measured.contrast)
                ? null
                : `${F.num(measured.contrast, 2)} ${measured.contrastUnit || ""}`.trim(),
              { mono: true },
            ),
            U.row(
              "Depolarisation",
              F.isMissing(measured.depolarisation) ? null : `${F.num(measured.depolarisation, 2)} dB VV−VH`,
              { mono: true },
            ),
          ),
          screening.reasons?.length
            ? h(
                "ul",
                { class: "bullets small" },
                screening.reasons.map((reason) => h("li", null, h("span", null, reason))),
              )
            : null,
          h("p", { class: "card__note" }, screening.appliedTo),
        )
      : null,
  );
}

function methodCard(caseDoc) {
  const raster = caseDoc.geometry?.raster || {};
  const config = caseDoc.geometry?.config || {};
  const filtered = caseDoc.geometry?.filteredMask || {};
  const raw = caseDoc.geometry?.rawMask || {};

  return U.foldout(
    "How the area was measured",
    {
      id: "method",
      // The pixel-area contrast top to bottom is the evidence for the claim the card makes:
      // a pixel is not a constant patch of ground, so areas are integrated row by row.
      hint: `pixel ${F.num(raster.pixelAreaM2AtTop, 3)} → ${F.num(raster.pixelAreaM2AtBottom, 3)} m², top to bottom`,
      note: caseDoc.geometry?.areaMethod,
    },
    U.rows(
      U.row("Raster", `${raster.width} × ${raster.height} px, EPSG:${raster.epsg}`, { mono: true }),
      U.row(
        "Pixel area",
        `${F.num(raster.pixelAreaM2AtTop, 3)} m² at the top, ${F.num(raster.pixelAreaM2AtBottom, 3)} m² at the bottom`,
        { stack: true },
      ),
      U.row("Raw mask", `${F.int(raw.pixels)} px · ${F.km2(raw.areaKm2)} km²`, { mono: true }),
      U.row("After morphology", `${F.int(filtered.pixels)} px · ${F.km2(filtered.areaKm2)} km²`, { mono: true }),
      U.row("Opening / closing radius", `${config.open_radius} / ${config.close_radius} px`, { mono: true }),
      U.row("Minimum region", `${F.int(config.min_area_px)} px`, { mono: true }),
      U.row("Outline simplification", `${F.num(config.simplify_tolerance_px, 1)} px`, { mono: true }),
      U.row("Polygon cap", F.int(config.max_polygons), { mono: true }),
    ),
    h("p", { class: "small muted", style: { "margin-top": "var(--s3)" } }, filtered.note || ""),
  );
}

/**
 * Caveats on the geometry.
 *
 * Open when there is something to say and closed when there is not. A card whose whole
 * content is "no quality flags were raised" still reads as a warning at a glance, which is
 * the opposite of what it means; as a closed row saying "no flags raised" it says the same
 * thing without claiming the reader's attention.
 */
function qualityCard(caseDoc, slick) {
  const flags = slick.qualityFlags || [];
  const truncated = Boolean(slick.touchesSceneEdge);
  const invalid = slick.geometryValid === false;
  const anything = flags.length > 0 || truncated || invalid;
  return U.foldout(
    "Caveats on this geometry",
    {
      id: "quality",
      open: anything,
      hint: anything
        ? [
            flags.length ? `${F.int(flags.length)} flag${flags.length === 1 ? "" : "s"}` : null,
            truncated ? "truncated by the scene edge" : null,
            invalid ? "outline is display-only" : null,
          ]
            .filter(Boolean)
            .join(" · ")
        : "no flags raised",
      note: caseDoc.geometry?.geojson?.note,
    },
    h(
      "div",
      { class: "stack stack--tight" },
      flags.length
        ? // Set as a claim-and-qualifier table, the same treatment the case-level limits get
          // on the command centre, so a caveat reads as a caveat and not as five identical
          // bullet points.
          U.limitList(flags)
        : h(
            "p",
            { class: "small muted" },
            "The geometry stage raised no quality flags for the largest region.",
          ),
      invalid
        ? U.notice(
            "Area, perimeter, length, width, elongation and compactness are all computed " +
              "from the pixel mask, not from the traced outline, so a self-intersecting " +
              "ring does not affect any figure on this screen. It affects only the drawing.",
            { strongPrefix: "Outline is display-only." },
          )
        : null,
      truncated
        ? U.notice(
            "The slick reaches the edge of the acquisition, so the measured area is a lower " +
              "bound: whatever continues outside the frame was never imaged.",
            { kind: "synthetic", strongPrefix: "Truncated by the scene." },
          )
        : null,
    ),
  );
}

// -- geometry helpers --------------------------------------------------------

/**
 * Area of a lon/lat ring in square kilometres, by spherical excess.
 *
 * Same radius the pipeline uses, so an analyst boundary and a model boundary are directly
 * comparable rather than differing by the choice of earth model.
 */
export function ringAreaKm2(ring) {
  if (!Array.isArray(ring) || ring.length < 3) return 0;
  const R = 6371.0088; // km
  const rad = Math.PI / 180;
  let total = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const [lon1, lat1] = ring[i];
    const [lon2, lat2] = ring[(i + 1) % ring.length];
    total += (lon2 - lon1) * rad * (Math.sin(lat1 * rad) + Math.sin(lat2 * rad));
  }
  return Math.abs((total * R * R) / 2);
}

/**
 * Douglas-Peucker down to roughly `target` vertices.
 *
 * A traced 2048 px mask outline runs to thousands of points, which is unusable as a set of
 * drag handles. The tolerance is searched rather than guessed so the result lands near the
 * target for any ring size.
 */
export function simplifyRing(ring, target = EDIT_VERTEX_TARGET) {
  if (!Array.isArray(ring) || ring.length <= target) return (ring || []).map((p) => [p[0], p[1]]);

  // Longitude degrees are shorter than latitude degrees, so distances are measured in a
  // locally isotropic frame before simplifying.
  const lat0 = ring.reduce((sum, p) => sum + p[1], 0) / ring.length;
  const k = Math.max(0.05, Math.cos((lat0 * Math.PI) / 180));
  const flat = ring.map(([lon, lat]) => [lon * k, lat]);

  let low = 0;
  let high = 0.5;
  let best = flat;
  for (let step = 0; step < 24; step += 1) {
    const mid = (low + high) / 2;
    const kept = douglasPeucker(flat, mid);
    if (kept.length > target) low = mid;
    else {
      best = kept;
      high = mid;
    }
    if (Math.abs(kept.length - target) <= 2) {
      best = kept;
      break;
    }
  }
  return best.map(([x, lat]) => [x / k, lat]);
}

function douglasPeucker(points, tolerance) {
  if (points.length < 3) return points;
  let index = -1;
  let maxDistance = 0;
  const first = points[0];
  const last = points[points.length - 1];
  for (let i = 1; i < points.length - 1; i += 1) {
    const distance = perpendicular(points[i], first, last);
    if (distance > maxDistance) {
      maxDistance = distance;
      index = i;
    }
  }
  if (maxDistance <= tolerance || index < 0) return [first, last];
  return [
    ...douglasPeucker(points.slice(0, index + 1), tolerance).slice(0, -1),
    ...douglasPeucker(points.slice(index), tolerance),
  ];
}

function perpendicular(point, a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(point[0] - a[0], point[1] - a[1]);
  const t = Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / lengthSquared));
  return Math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy));
}

/** Index of the handle under the pointer, or -1. Measured in screen pixels. */
function nearestHandle(map, ring, point) {
  let best = -1;
  let bestDistance = GRAB;
  ring.forEach((vertex, index) => {
    const [x, y] = map.project(vertex[0], vertex[1]);
    const distance = Math.hypot(x - point.x, y - point.y);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  });
  return best;
}

/** Index of the ring edge under the pointer, or -1. */
function nearestEdge(map, ring, point) {
  let best = -1;
  let bestDistance = GRAB;
  for (let i = 0; i < ring.length; i += 1) {
    const a = map.project(ring[i][0], ring[i][1]);
    const b = map.project(ring[(i + 1) % ring.length][0], ring[(i + 1) % ring.length][1]);
    const distance = segmentDistance(point.x, point.y, a[0], a[1], b[0], b[1]);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = i;
    }
  }
  return best;
}

function segmentDistance(px, py, x0, y0, x1, y1) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(px - x0, py - y0);
  const t = Math.max(0, Math.min(1, ((px - x0) * dx + (py - y0) * dy) / lengthSquared));
  return Math.hypot(px - (x0 + t * dx), py - (y0 + t * dy));
}
