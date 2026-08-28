/** Shared interface pieces: stats, badges, notices and the four load states.
 *
 * The four states are separate components on purpose. "Loading", "this case has not
 * been computed", "the computation failed" and "the computation succeeded and found
 * nothing" need different words and different next actions, and collapsing them into
 * one grey box is how a dashboard ends up lying about what it knows.
 */

import { h, icon, frag } from "./dom.js";
import { ICONS } from "./icons.js";
import * as F from "./format.js";

// -- primitives -------------------------------------------------------------

/**
 * One headline figure.
 * `value` of null renders an em dash and the `missing` reason, never a zero.
 */
export function stat({ label, value, unit, sub, tone, missing }) {
  const absent = F.isMissing(value) || value === F.DASH;
  return h(
    "div",
    { class: ["stat", tone ? `stat--${tone}` : null, absent ? "stat--missing" : null] },
    h("div", { class: "stat__label" }, label),
    h(
      "div",
      { class: "stat__value" },
      absent ? F.DASH : String(value),
      !absent && unit ? h("span", { class: "stat__unit" }, unit) : null,
    ),
    absent && missing ? h("div", { class: "stat__sub" }, missing) : null,
    !absent && sub ? h("div", { class: "stat__sub" }, sub) : null,
  );
}

/** A key/value row. `mono` uses tabular figures; `stack` puts the value below. */
export function row(key, value, { mono = false, stack = false, muted = false, title } = {}) {
  const absent = F.isMissing(value) || value === F.DASH || value === "";
  return h(
    "div",
    { class: ["row", stack ? "row--stack" : null], title },
    h("div", { class: "row__key" }, key),
    h(
      "div",
      {
        class: ["row__val", mono ? "mono" : null, absent || muted ? "row__val--muted" : null],
      },
      absent ? F.DASH : String(value),
    ),
  );
}

export function rows(...items) {
  return h("div", { class: "rows" }, items);
}

/** A provenance badge. `kind` selects the colour family. */
export function badge(text, kind, { title } = {}) {
  return h(
    "span",
    { class: ["badge", kind ? `badge--${kind}` : null], title: title || text },
    h("span", { class: "badge__dot" }),
    text,
  );
}

export function notice(text, { kind = "", strongPrefix = "" } = {}) {
  return h(
    "div",
    { class: ["notice", kind ? `notice--${kind}` : null], role: kind === "danger" ? "alert" : null },
    icon(kind === "danger" ? ICONS.warning : kind === "synthetic" ? ICONS.warning : ICONS.info, {
      cls: "notice__icon",
    }),
    h(
      "div",
      null,
      strongPrefix ? h("strong", null, `${strongPrefix} `) : null,
      text,
    ),
  );
}

/**
 * The standing disclosures, as one block instead of a stack of banners.
 *
 * These are conditions of the data, so they appear on every screen and cannot be dismissed.
 * Three separate tinted banners at the top of every screen pushed the actual findings off
 * the first screenful, which is its own kind of dishonesty -- so they are grouped into a
 * single quiet block, at footnote size, with the colour kept on the label of each row. The
 * wording is untouched.
 *
 * `items` is `[{ label, text, kind }]`.
 */
export function disclosureBar(items) {
  const rows_ = items.filter(Boolean);
  if (!rows_.length) return null;
  return h(
    "aside",
    { class: "disclosures", "aria-label": "Standing disclosures about this data" },
    rows_.map(({ label, text, kind }) =>
      h(
        "div",
        { class: ["disclosures__row", kind ? `disclosures__row--${kind}` : null] },
        icon(kind === "synthetic" ? ICONS.warning : ICONS.info, { cls: "disclosures__icon" }),
        h(
          "p",
          null,
          label ? h("strong", { class: "disclosures__label" }, `${label} `) : null,
          text,
        ),
      ),
    ),
  );
}

export function card(title, { hint, note, flush = false, sunken = false, id, actions } = {}, ...body) {
  return h(
    "section",
    {
      class: ["card", flush ? "card--flush" : null, sunken ? "card--sunken" : null],
      id,
      "aria-labelledby": title && id ? `${id}-title` : null,
    },
    title
      ? h(
          "header",
          { class: "card__head" },
          h("h2", { class: "card__title", id: id ? `${id}-title` : null }, title),
          hint ? h("div", { class: "card__hint" }, hint) : null,
          actions ? h("div", { class: "inline card__hint no-print" }, actions) : null,
        )
      : null,
    ...body,
    note ? h("p", { class: "card__note" }, note) : null,
  );
}

/**
 * The one inverted card on a screen, holding the figure that screen exists to report.
 *
 * There is deliberately no `tone` here. The hero is dark, the figure is white, and the
 * colour coding stays on the map and in the badges -- a headline that changes hue with the
 * result would read as a verdict, which is exactly what this product must not deliver.
 */
export function hero({ label, value, unit, sub, chips = [], aside } = {}) {
  const absent = F.isMissing(value) || value === F.DASH;
  return h(
    "section",
    { class: "hero" },
    h(
      "div",
      { class: "hero__main" },
      label ? h("div", { class: "hero__label" }, label) : null,
      h(
        "div",
        { class: "hero__figure" },
        absent ? F.DASH : String(value),
        !absent && unit ? h("span", { class: "hero__unit" }, unit) : null,
      ),
      sub ? h("p", { class: "hero__sub" }, sub) : null,
      chips.length ? h("div", { class: "hero__chips" }, chips) : null,
    ),
    aside ? h("div", { class: "hero__aside" }, aside) : null,
  );
}

/**
 * A glass sub-panel for the hero aside: an indicator ring, a figure and a caption.
 *
 * The ring carries no figure of its own. The same number inside the ring and beside it is
 * padding, and at any ring size that fitted next to the headline the inner digits sat hard
 * against the stroke. So the ring is the indicator and the text is the value.
 */
export function heroStat({ value, unit, label, fraction, title } = {}) {
  const absent = F.isMissing(value) || value === F.DASH;
  return h(
    "div",
    { class: "hero__stat", title: title || null },
    F.isMissing(fraction) ? null : scoreRing(Number(fraction) * 100, 100, { size: 44, bare: true }),
    h(
      "div",
      { class: "hero__stat-text" },
      h(
        "div",
        { class: "hero__stat-value" },
        absent ? F.DASH : String(value),
        !absent && unit ? h("span", { class: "hero__stat-unit" }, unit) : null,
      ),
      label ? h("div", { class: "hero__stat-label" }, label) : null,
    ),
  );
}

/**
 * A glass pill for the hero card.
 * `tone` colours only the leading dot, using the graphic value -- on a near-black card the
 * saturated palette is legible, so no `-text` substitution is needed here.
 */
export function chip(text, { iconPath, tone, title } = {}) {
  return h(
    "span",
    { class: "chip", title: title || null },
    iconPath ? icon(iconPath, { cls: "chip__icon" }) : null,
    !iconPath && tone
      ? h("span", { class: "chip__dot", style: { color: `var(--${tone})` } })
      : null,
    text,
  );
}

/** A stat promoted to its own card, with a tinted glyph tile above it. */
export function metric({ label, value, unit, sub, tone, missing, iconPath }) {
  return h(
    "div",
    { class: "metric" },
    iconPath
      ? h(
          "div",
          { class: ["icon-tile", tone ? `icon-tile--${tone}` : "icon-tile--tint"] },
          icon(iconPath, { size: 18 }),
        )
      : null,
    stat({ label, value, unit, sub, tone, missing }),
  );
}

export function button(text, { onClick, kind = "", iconPath, iconAfter, small = false, disabled = false, type = "button", ariaLabel, title, href } = {}) {
  const children = [
    iconPath ? icon(iconPath, { cls: "btn__icon" }) : null,
    text ? h("span", null, text) : null,
    // A "go on to the next thing" arrow belongs after the label it is pointing away from.
    iconAfter ? icon(iconAfter, { cls: "btn__icon btn__icon--after" }) : null,
  ];
  const props = {
    class: ["btn", kind ? `btn--${kind}` : null, small ? "btn--sm" : null],
    "aria-label": ariaLabel,
    title: title || null,
  };
  if (href) {
    return h("a", { ...props, href, download: "" }, children);
  }
  return h("button", { ...props, type, disabled, onClick }, children);
}

/** A segmented picker. `options` is `[{value, label, title}]`. */
export function segmented(options, current, onSelect, { ariaLabel } = {}) {
  return h(
    "div",
    { class: "segmented", role: "group", "aria-label": ariaLabel },
    options.map((option) =>
      h(
        "button",
        {
          class: "segmented__item",
          type: "button",
          "aria-pressed": String(option.value === current),
          title: option.title || null,
          disabled: option.disabled || false,
          onClick: () => onSelect(option.value),
        },
        option.label,
      ),
    ),
  );
}

/** A horizontal bar for one score component. */
export function bar(label, value, max, { tone } = {}) {
  const fraction = max > 0 ? Math.max(0, Math.min(1, Number(value) / max)) : 0;
  return h(
    "div",
    { class: "bar" },
    h("div", { class: "bar__label" }, label),
    h(
      "div",
      {
        class: "bar__track",
        role: "img",
        "aria-label": `${label}: ${F.num(value, 1)} of ${max}`,
      },
      h("div", {
        class: "bar__fill",
        style: {
          width: `${(fraction * 100).toFixed(1)}%`,
          background: tone ? `var(--${tone})` : null,
        },
      }),
    ),
    h("div", { class: "bar__value" }, `${F.num(value, 1)}/${max}`),
  );
}

/**
 * A circular score dial, 0..max.
 * `caption` replaces the default "of max" sub-label; pass `""` for a bare figure.
 * `bare` draws the arc alone, for use next to a figure that is already on the page.
 */
export function scoreRing(value, max = 100, { size = 68, caption, bare = false } = {}) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const fraction = max > 0 ? Math.max(0, Math.min(1, Number(value) / max)) : 0;
  const sub = caption === undefined ? `of ${max}` : caption;
  return h(
    "svg",
    {
      class: "score__ring",
      width: size,
      height: size,
      viewBox: "0 0 68 68",
      role: "img",
      "aria-label": `Score ${F.num(value, 1)} out of ${max}`,
    },
    h("circle", {
      class: "score__ring-track",
      cx: 34, cy: 34, r: radius, fill: "none", "stroke-width": 5,
    }),
    h("circle", {
      class: "score__ring-fill",
      cx: 34, cy: 34, r: radius, fill: "none", "stroke-width": 5,
      "stroke-dasharray": circumference.toFixed(2),
      "stroke-dashoffset": (circumference * (1 - fraction)).toFixed(2),
      transform: "rotate(-90 34 34)",
    }),
    bare
      ? null
      : h(
          "text",
          { class: "score__num", x: 34, y: sub ? 35 : 40, "text-anchor": "middle" },
          F.num(value, 1),
        ),
    !bare && sub ? h("text", { class: "score__den", x: 34, y: 46, "text-anchor": "middle" }, sub) : null,
  );
}

export function legend(items) {
  return h(
    "div",
    { class: "legend" },
    items.map(({ label, colour, shape = "swatch" }) =>
      h(
        "span",
        { class: "legend__item", style: { color: colour } },
        h("span", {
          class: [
            "legend__swatch",
            shape === "line" ? "legend__swatch--line" : null,
            shape === "dash" ? "legend__swatch--dash" : null,
          ],
        }),
        h("span", { style: { color: "var(--text-tertiary)" } }, label),
      ),
    ),
  );
}

// -- the four states --------------------------------------------------------

export function loadingState(title = "Loading", body = "") {
  return h(
    "div",
    { class: "state", role: "status", "aria-live": "polite" },
    h("div", { class: "spinner", style: { width: "22px", height: "22px" } }),
    h("div", { class: "state__title" }, title),
    body ? h("p", { class: "state__body" }, body) : null,
  );
}

/**
 * The API answered, but nothing has been computed. This is the state that carries the
 * command that fixes it - which is why it is not merged with `failedState`.
 */
export function missingState({ title, body, command, action }) {
  return h(
    "div",
    { class: "state" },
    icon(ICONS.empty, { cls: "state__icon", size: 30 }),
    h("div", { class: "state__title" }, title || "Not computed yet"),
    h(
      "p",
      { class: "state__body" },
      body || "Nothing has been computed for this case.",
      command ? h("code", { class: "state__cmd" }, command) : null,
    ),
    action || null,
  );
}

export function failedState({ title, body, detail, action }) {
  return h(
    "div",
    { class: "state state--danger", role: "alert" },
    icon(ICONS.warning, { cls: "state__icon", size: 30 }),
    h("div", { class: "state__title" }, title || "That did not work"),
    h("p", { class: "state__body" }, body || "The request failed."),
    detail ? h("code", { class: "state__cmd" }, detail) : null,
    action || null,
  );
}

/** The computation succeeded and produced nothing. A real, valid result. */
export function emptyState({ title, body, action }) {
  return h(
    "div",
    { class: "state" },
    icon(ICONS.check, { cls: "state__icon", size: 30 }),
    h("div", { class: "state__title" }, title || "No detections"),
    h("p", { class: "state__body" }, body || ""),
    action || null,
  );
}

/**
 * Render whichever state applies, or call `render` when the data is ready.
 * Keeps the four-state handling in one place instead of six copies of the same `if`.
 */
export function stateSwitch(status, value, render, options = {}) {
  if (status === "loading" || status === "idle") {
    return loadingState(options.loadingTitle, options.loadingBody);
  }
  if (status === "missing") return missingState(options.missing || {});
  if (status === "failed") return failedState(options.failed || {});
  if (!value) return emptyState(options.empty || {});
  return render(value);
}

/** A skeleton block sized like the content it stands in for. */
export function skeleton(width = "100%", height = "1em") {
  return h("span", { class: "skeleton", style: { display: "block", width, height } }, " ");
}

/** A short provenance strip, used in the header bar and on every screen that needs it. */
export function provenanceBadges(caseDoc, { compact = false } = {}) {
  const p = caseDoc?.provenance || {};
  const badges = [];
  if (p.satellite) {
    badges.push(badge(compact ? "Supplied SAR" : p.satellite, "supplied", { title: p.satellite }));
  }
  if (p.detectionLabel) {
    badges.push(
      badge(
        compact
          ? p.detectionSource === "reference" ? "Reference mask" : "Model prediction"
          : p.detectionLabel,
        p.detectionSource === "reference" ? "supplied" : "model",
        { title: p.detectionLabel },
      ),
    );
  }
  if (p.aisLabel) {
    badges.push(badge(compact ? "Synthetic AIS" : p.aisLabel, "synthetic", { title: p.aisLabel }));
  }
  if (p.driftLabel) {
    badges.push(
      badge(
        compact ? (p.driftMode === "cmems" ? "CMEMS drift" : "Synthetic drift") : p.driftLabel,
        p.driftMode === "cmems" ? "drift" : "synthetic",
        { title: p.driftLabel },
      ),
    );
  }
  if (caseDoc?.status) {
    badges.push(badge(compact ? "Research PoC" : caseDoc.status, "warn", { title: caseDoc.status }));
  }
  return h("div", { class: "badge-row" }, badges);
}

/** The footer that stamps every screen with version, run time and status. */
export function runFooter(caseDoc) {
  if (!caseDoc) return null;
  return h(
    "footer",
    { class: "footer" },
    h("span", null, caseDoc.pipelineVersion || "pipeline version unknown"),
    h("span", null, `Computed ${F.utc(caseDoc.generatedUtc, { seconds: true })}`),
    caseDoc.requestKey ? h("span", { class: "mono" }, `request ${caseDoc.requestKey}`) : null,
    h("span", null, caseDoc.status || ""),
  );
}

export { frag };
