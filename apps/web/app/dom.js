/** Minimal hyperscript. No framework, because none is reachable from this sandbox.
 *
 * `h(tag, props, ...children)` builds real DOM nodes. Screens return a node and the
 * router swaps it in; there is no virtual DOM and no diffing, which for seven screens
 * that each re-render on case change is the simpler correct thing.
 *
 * Text is set through `textContent`, never `innerHTML`, so a string that arrived from
 * the API cannot inject markup. The single exception is `svgRaw`, which is used only
 * with literals written in this repository.
 */

import { ICONS } from "./icons.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const SVG_TAGS = new Set([
  "svg", "g", "path", "circle", "ellipse", "line", "polyline", "polygon", "rect",
  "text", "tspan", "defs", "linearGradient", "radialGradient", "stop", "clipPath",
  "use", "title", "image", "pattern", "marker",
]);

/**
 * @param {string} tag
 * @param {object|null} props - attributes; `class`, `style` (object or string),
 *   `dataset`, `on*` handlers and `ref` (callback) are handled specially.
 * @param {...any} children - nodes, strings, numbers, arrays; null/false/undefined skip.
 */
export function h(tag, props, ...children) {
  const el = SVG_TAGS.has(tag)
    ? document.createElementNS(SVG_NS, tag)
    : document.createElement(tag);

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "ref" && typeof value === "function") {
      value(el);
    } else if (key === "style" && typeof value === "object") {
      for (const [prop, v] of Object.entries(value)) {
        if (v !== null && v !== undefined) el.style.setProperty(prop, String(v));
      }
    } else if (key === "dataset") {
      for (const [prop, v] of Object.entries(value)) {
        if (v !== null && v !== undefined) el.dataset[prop] = String(v);
      }
    } else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "class") {
      el.setAttribute("class", Array.isArray(value) ? value.filter(Boolean).join(" ") : String(value));
    } else if (key === "html") {
      // Repository-authored markup only (icon paths). Never API data.
      el.innerHTML = String(value);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }

  append(el, children);
  return el;
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    parent.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

/** Replace everything inside `host` with `nodes`. */
export function mount(host, ...nodes) {
  host.replaceChildren();
  append(host, nodes);
  return host;
}

/** A document fragment, so a screen can return a list without a wrapper element. */
export function frag(...children) {
  return append(document.createDocumentFragment(), children);
}

/** An inline SVG icon from a path string authored in `icons.js`. */
export function icon(path, { size = 16, cls = "", stroke = 1.6 } = {}) {
  return h(
    "svg",
    {
      class: cls,
      width: size,
      height: size,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": stroke,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
      focusable: "false",
      html: path,
    },
    null,
  );
}

/**
 * Trap Tab inside `container` and restore focus on teardown.
 * Returns a function that releases the trap.
 */
export function trapFocus(container, onEscape) {
  const previous = document.activeElement;
  const selector =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]),' +
    ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function keydown(event) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onEscape?.();
      return;
    }
    if (event.key !== "Tab") return;
    const stops = [...container.querySelectorAll(selector)].filter(
      (node) => node.offsetParent !== null || node === document.activeElement,
    );
    if (stops.length === 0) return;
    const first = stops[0];
    const last = stops[stops.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  container.addEventListener("keydown", keydown);
  const target = container.querySelector(selector);
  target?.focus();

  return () => {
    container.removeEventListener("keydown", keydown);
    if (previous instanceof HTMLElement && document.contains(previous)) previous.focus();
  };
}

/** Announce a message to screen readers, and show the same message on screen.
 *
 * Two nodes, deliberately. The live region is a permanent `sr-only` div, because a
 * freshly inserted `aria-live` element is not reliably spoken -- assistive tech watches
 * regions it already knows about. The visible toast is rebuilt per message and marked
 * `aria-hidden`, so one sentence is never read twice.
 *
 * Every status in this app once went to the live region alone. That made the interface
 * silent for anyone not running a screen reader: clicking "Write .eml" closed the sheet,
 * the server wrote both the PDF and the .eml, and the page said nothing at all. Feedback
 * a sighted reader cannot see is not feedback.
 *
 * `progress` has no timeout -- it is superseded by the next call, so a multi-step action
 * reads as one message that changes rather than a stack that piles up. Errors sit ten
 * times longer than a confirmation because they are the ones worth reading twice.
 *
 * `silent` speaks without showing. The two channels do not want the same messages: a
 * screen reader needs to be told the case changed, because the whole screen just did,
 * while a sighted reader can see the new case name in the topbar and does not need a
 * card about it on every load.
 *
 * @param {string} message
 * @param {object} [options]
 * @param {"info"|"success"|"error"|"progress"} [options.kind]
 * @param {boolean} [options.silent] - announce to assistive tech only, show no toast
 * @param {{label: string, href: string, download?: string}} [options.action] - one
 *   optional link, for when the message names something the reader will want to open.
 */
const TOAST_MS = { info: 5000, success: 9000, error: 14000, progress: 0 };
const TOAST_GLYPH = { info: "info", success: "check", error: "warning", progress: null };

let liveRegion = null;
let toastHost = null;
let toastTimer = 0;
let currentToast = null;

export function announce(message, { kind = "info", action = null, silent = false } = {}) {
  if (!liveRegion) {
    liveRegion = h("div", {
      class: "sr-only",
      role: "status",
      "aria-live": "polite",
      "aria-atomic": "true",
    });
    document.body.appendChild(liveRegion);
  }
  liveRegion.textContent = "";
  // A fresh tick so repeated identical messages are still announced.
  setTimeout(() => {
    liveRegion.textContent = message;
  }, 30);

  if (!silent) toast(message, kind, action);
}

/** The visible half of `announce`. Not exported: one status channel, not two. */
function toast(message, kind, action) {
  if (!toastHost) {
    // `aria-hidden` for the whole layer: the live region above already spoke. Everything
    // focusable inside is therefore `tabindex="-1"`, since focusing a hidden node is
    // invalid ARIA -- the toast expires on its own, so nothing here is a dead end.
    toastHost = h("div", { class: "toast-host", "aria-hidden": "true" });
    document.body.appendChild(toastHost);
  }
  clearTimeout(toastTimer);

  // Reuse the card that is already on screen rather than rebuilding it. A multi-step
  // action -- email dispatch polls "queued" then "building" then "sent" -- would otherwise
  // tear the node down and replay the `toast-in` entrance on every poll, which reads as a
  // flashing toast. Updated in place, one toast stays put: the progress pulse keeps
  // breathing, and when the outcome arrives the same card morphs to it and then leaves.
  const live =
    currentToast && currentToast.isConnected && !currentToast.classList.contains("is-leaving");
  if (live) {
    updateToast(currentToast, message, kind, action);
  } else {
    currentToast = buildToast(message, kind, action);
    mount(toastHost, currentToast);
  }

  const ms = TOAST_MS[kind] ?? TOAST_MS.info;
  if (ms) toastTimer = setTimeout(dismissToast, ms);
}

/** A fresh toast node, animated in. */
function buildToast(message, kind, action) {
  return h(
    "div",
    { class: `toast toast--${kind}`, onClick: dismissToast },
    toastGlyph(kind),
    h("p", { class: "toast__msg" }, message),
    toastAction(action),
    h(
      "button",
      { class: "toast__close", type: "button", tabindex: "-1", onClick: dismissToast },
      icon(ICONS.close, { size: 13 }),
    ),
  );
}

/** Change an on-screen toast's kind, glyph, message and action without remounting it,
 *  so the entrance animation does not replay. */
function updateToast(card, message, kind, action) {
  card.className = `toast toast--${kind}`;
  card.querySelector(".toast__glyph").replaceWith(toastGlyph(kind));
  const msg = card.querySelector(".toast__msg");
  if (msg) msg.textContent = message;
  const next = toastAction(action);
  const existing = card.querySelector(".toast__action");
  if (existing && next) existing.replaceWith(next);
  else if (existing) existing.remove();
  else if (next) card.insertBefore(next, card.querySelector(".toast__close"));
}

function toastGlyph(kind) {
  const name = TOAST_GLYPH[kind];
  return h(
    "span",
    { class: "toast__glyph" },
    name ? icon(ICONS[name], { size: 15 }) : h("span", { class: "toast__pulse" }),
  );
}

function toastAction(action) {
  if (!action?.href) return null;
  return h(
    "a",
    {
      class: "toast__action",
      href: action.href,
      target: "_blank",
      rel: "noopener",
      download: action.download || null,
      tabindex: "-1",
      onClick: (event) => event.stopPropagation(),
    },
    action.label,
  );
}

function dismissToast() {
  clearTimeout(toastTimer);
  const card = currentToast || toastHost?.firstElementChild;
  if (!card) return;
  currentToast = null;
  card.classList.add("is-leaving");
  setTimeout(() => card.remove(), 220);
}

/** Trailing-edge debounce, for resize and pointer-move handlers. */
export function debounce(fn, ms = 120) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/** One rAF-coalesced call per frame, for anything that touches layout. */
export function raf(fn) {
  let queued = false;
  return (...args) => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      fn(...args);
    });
  };
}
