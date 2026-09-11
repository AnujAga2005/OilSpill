/** Shared interface pieces: stats, badges, notices and the four load states.
 *
 * The four states are separate components on purpose. "Loading", "this case has not
 * been computed", "the computation failed" and "the computation succeeded and found
 * nothing" need different words and different next actions, and collapsing them into
 * one grey box is how a dashboard ends up lying about what it knows.
 */

import { h, icon, frag, trapFocus } from "./dom.js";
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
    // Skipped rather than rendered empty: `.stat` is a flex column with a gap, so a label-less
    // stat with an empty label div carries a stray 4 px of space above the figure.
    label ? h("div", { class: "stat__label" }, label) : null,
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

/** A hint is normally a short tag pushed to the right of the title. A long one wraps onto
 * its own line, where right-alignment reads as a layout bug, so it gets its own class. */
const HINT_WRAPS_AT = 48;

function hintClass(hint) {
  return ["card__hint", typeof hint === "string" && hint.length > HINT_WRAPS_AT ? "card__hint--long" : null];
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
          hint ? h("div", { class: hintClass(hint) }, hint) : null,
          actions ? h("div", { class: "inline card__hint no-print" }, actions) : null,
        )
      : null,
    ...body,
    note ? h("p", { class: "card__note" }, note) : null,
  );
}

/**
 * A card that starts closed, for the material a screen has to carry but nobody reads aloud.
 *
 * The provenance of a case -- product id, checkpoint, threshold, polarisations, stage
 * timings -- has to be on the screen, or the findings above it are unverifiable. Rendered
 * open, it is thirty rows of small print with exactly the same weight as the findings, and a
 * reader scanning the page has no way to tell reference from result. That is what made this
 * dashboard read as generated rather than designed: not the palette, but that every block
 * on it shouted equally. Closed, each one is a single line naming what is inside, and one
 * click for anyone who wants to check the number.
 *
 * Nothing is removed by this. `hint` is the part worth seeing while closed, so the headline
 * fact survives the fold and only the detail costs a click.
 *
 * `<details>` rather than a button and a class: keyboard-operable for free, present in the
 * accessibility tree as a disclosure, and forced open by the print sheet so a printed report
 * still carries the whole audit trail.
 */
export function foldout(title, { hint, note, open = false, id } = {}, ...body) {
  return h(
    "details",
    { class: "card foldout", id, open: open || null },
    h(
      "summary",
      { class: "foldout__summary" },
      icon(ICONS.chevronRight, { cls: "foldout__chevron", size: 15 }),
      h("h2", { class: "card__title" }, title),
      hint ? h("div", { class: hintClass(hint) }, hint) : null,
    ),
    h(
      "div",
      { class: "foldout__body" },
      ...body,
      note ? h("p", { class: "card__note" }, note) : null,
    ),
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

/**
 * One numbered answer to one question the brief asks.
 *
 * This replaced a row of four `metric` cards. Four figures of equal weight left the reader
 * to work out which one the screen was actually for, and the payoff -- how many vessels are
 * worth investigating -- sat fourth, in the same box as the rest. But a spill report is a
 * sequence, not a set: what is in the water, where it came from, who was near it when it
 * started. Each step only means anything once the one before it holds.
 *
 * So the step number is the point of this component. It says there is an order, and the
 * question above the figure means the figure does not have to explain itself -- a reader
 * seeing this screen cold gets the brief restated in words before they get a number.
 *
 * `action` is the screen that shows the working. Everything here is a summary of a
 * computation that happened on another screen, so each answer carries the way through to it
 * rather than leaving the reader to guess which tab proves it.
 */
export function answer({ step, question, value, unit, sub, tone, missing, action } = {}) {
  return h(
    "section",
    { class: ["answer", tone ? `answer--${tone}` : null] },
    h(
      "header",
      { class: "answer__head" },
      h("span", { class: "answer__step" }, String(step)),
      h("h3", { class: "answer__question" }, question),
    ),
    stat({ value, unit, sub, tone, missing }),
    action ? h("div", { class: "answer__action no-print" }, action) : null,
  );
}

/**
 * A caveat list, set as a two-column specification table rather than bullet points.
 *
 * Five full sentences with a dot in front of each read as an undifferentiated wall: every
 * row the same shape, the same weight, the same length, so the eye has nowhere to land and
 * the honesty they carry stops registering. Splitting each one at its first full stop puts
 * the claim in the left column and the qualification in the right, which is how a person
 * actually reads a caveat -- what is limited, then in what way.
 *
 * Anything without a full stop is left whole in the claim column, so a shorter caveat
 * written later still renders.
 */
export function limitList(items) {
  return h(
    "ul",
    { class: "limits" },
    (items || []).map((text) => {
      const at = String(text).indexOf(". ");
      const claim = at === -1 ? String(text) : String(text).slice(0, at);
      const rest = at === -1 ? "" : String(text).slice(at + 2);
      return h(
        "li",
        { class: "limits__item" },
        h("span", { class: "limits__claim" }, claim),
        rest ? h("span", { class: "limits__rest" }, rest) : null,
      );
    }),
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

// -- modal ------------------------------------------------------------------

/**
 * A modal sheet with a cancel and a confirm. Resolves `true` when confirmed and
 * `false` when dismissed, so the caller reads its own inputs from refs it captured
 * while building `body`.
 *
 * This exists because `window.prompt` was doing the job, and `window.prompt` is the
 * wrong tool for anything consequential: it cannot say what the button will actually
 * do, it cannot show a validation message, some browsers suppress it outright, and it
 * blocks the event loop. Dispatching an incident report needs all three of those.
 *
 * `validate` is called on confirm and on submit; returning a string keeps the sheet
 * open and shows that string, so a typo in an address never becomes a request.
 *
 * @param {object} options
 * @param {string} options.title
 * @param {string} [options.lede] - one line under the title
 * @param {string} [options.confirm] - confirm button label
 * @param {string} [options.cancel] - cancel button label
 * @param {() => (string|null)} [options.validate] - error message, or null to proceed
 * @param {...any} body - flowable content between the head and the buttons
 * @returns {Promise<boolean>}
 */
export function dialog({ title, lede, confirm = "Continue", cancel = "Cancel", validate } = {}, ...body) {
  return new Promise((resolve) => {
    const id = `dlg-${Math.random().toString(36).slice(2, 8)}`;
    let error = null;
    let release = () => {};
    let settled = false;

    function close(value) {
      if (settled) return;
      settled = true;
      release();
      backdrop.remove();
      sheet.remove();
      resolve(value);
    }

    function attempt() {
      const message = validate ? validate() : null;
      if (message) {
        error.textContent = message;
        error.hidden = false;
        return;
      }
      close(true);
    }

    const backdrop = h("div", { class: "sheet-backdrop", onClick: () => close(false) });
    const sheet = h(
      "form",
      {
        class: "modal",
        role: "dialog",
        "aria-modal": "true",
        "aria-labelledby": id,
        // `validate` is the authority. Native constraint validation would silently
        // refuse to fire `submit` for a field it dislikes -- leaving the last error
        // message on screen, unchanged, with no way to tell what happened -- and it
        // cannot judge a comma-separated list anyway.
        novalidate: true,
        onSubmit: (event) => {
          event.preventDefault();
          attempt();
        },
        onInput: () => {
          error.hidden = true;
        },
      },
      h(
        "div",
        { class: "modal__head" },
        h("h2", { class: "modal__title", id }, title),
        lede ? h("p", { class: "modal__lede" }, lede) : null,
      ),
      h("div", { class: "modal__body" }, ...body),
      h("p", {
        class: "modal__error",
        role: "alert",
        hidden: true,
        ref: (node) => {
          error = node;
        },
      }),
      h(
        "div",
        { class: "modal__foot" },
        button(cancel, { kind: "quiet", small: true, onClick: () => close(false) }),
        button(confirm, { kind: "primary", small: true, type: "submit" }),
      ),
    );

    document.body.append(backdrop, sheet);
    release = trapFocus(sheet, () => close(false));
  });
}

/** A labelled text field for use inside `dialog`. `ref` hands back the input. */
export function field({ label, hint, value = "", placeholder, type = "text", ref } = {}) {
  const id = `fld-${Math.random().toString(36).slice(2, 8)}`;
  return h(
    "div",
    { class: "field" },
    h("label", { class: "field__label", for: id }, label),
    h("input", {
      class: "input",
      id,
      type,
      value,
      placeholder,
      autocomplete: "off",
      ref,
    }),
    hint ? h("p", { class: "field__hint" }, hint) : null,
  );
}

export { frag };
