/** Charts, as hand-built SVG.
 *
 * Small enough that a plotting library would be more code than the charts. Everything is
 * one `<svg>` with a fixed viewBox and no width, so it scales with its container and
 * stays crisp; there is no canvas and no resize listener to get wrong.
 *
 * Every series carries a `label`, and every chart takes a `caption` that becomes the
 * accessible description, because a line going up is not self-explanatory to a screen
 * reader and "IoU against threshold" is the whole point of that particular line.
 */

import { h } from "./dom.js";
import * as F from "./format.js";

const PAD = { top: 10, right: 12, bottom: 22, left: 34 };

/**
 * A multi-series line chart.
 *
 * @param {object} spec
 * @param {{label: string, colour: string, points: [number, number][], dash?: number[], dots?: boolean}[]} spec.series
 * @param {string} spec.xLabel
 * @param {string} spec.yLabel
 * @param {[number, number]} [spec.yDomain] - defaults to the data range, padded
 * @param {[number, number]} [spec.xDomain]
 * @param {number} [spec.height=150] - viewBox height; width is fixed at 320
 * @param {{x?: number, label?: string, colour?: string}} [spec.marker] - a vertical rule
 */
export function lineChart({
  series = [],
  xLabel = "",
  yLabel = "",
  yDomain,
  xDomain,
  height = 150,
  width = 320,
  marker,
  caption = "",
  yFormat = (v) => F.num(v, 2),
  xFormat = (v) => F.num(v, 1),
}) {
  const points = series.flatMap((s) => s.points || []).filter((p) => Number.isFinite(p[1]));
  if (!points.length) {
    return h("p", { class: "muted small" }, "No series to plot.");
  }

  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const domainX = xDomain || [Math.min(...xs), Math.max(...xs)];
  const domainY = yDomain || padDomain(Math.min(...ys), Math.max(...ys));

  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const sx = (v) => PAD.left + ((v - domainX[0]) / span(domainX)) * plotW;
  const sy = (v) => PAD.top + plotH - ((v - domainY[0]) / span(domainY)) * plotH;

  const yTicks = ticks(domainY, 4);
  const xTicks = ticks(domainX, 4);

  return h(
    "svg",
    {
      class: "chart",
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: "xMidYMid meet",
      role: "img",
      "aria-label": caption || `${yLabel} against ${xLabel}`,
    },
    caption ? h("title", null, caption) : null,

    yTicks.map((t) =>
      h(
        "g",
        null,
        h("line", { class: "chart__grid", x1: PAD.left, y1: sy(t), x2: width - PAD.right, y2: sy(t) }),
        h("text", { class: "chart__label", x: PAD.left - 5, y: sy(t) + 3, "text-anchor": "end" }, yFormat(t)),
      ),
    ),
    xTicks.map((t) =>
      h(
        "text",
        { class: "chart__label", x: sx(t), y: height - 8, "text-anchor": "middle" },
        xFormat(t),
      ),
    ),
    h("line", {
      class: "chart__axis",
      x1: PAD.left, y1: PAD.top, x2: PAD.left, y2: PAD.top + plotH,
    }),
    h("line", {
      class: "chart__axis",
      x1: PAD.left, y1: PAD.top + plotH, x2: width - PAD.right, y2: PAD.top + plotH,
    }),

    marker && Number.isFinite(marker.x)
      ? h(
          "g",
          null,
          h("line", {
            x1: sx(marker.x), y1: PAD.top - 2, x2: sx(marker.x), y2: PAD.top + plotH,
            stroke: marker.colour || "var(--oil)",
            "stroke-width": 1.25,
            "stroke-dasharray": "3 3",
          }),
          marker.label
            ? h(
                "text",
                {
                  class: "chart__label",
                  x: sx(marker.x) + (sx(marker.x) > width * 0.68 ? -4 : 4),
                  y: PAD.top + 7,
                  "text-anchor": sx(marker.x) > width * 0.68 ? "end" : "start",
                  fill: marker.colour || "var(--oil)",
                },
                marker.label,
              )
            : null,
        )
      : null,

    series.map((s) => {
      const path = (s.points || [])
        .filter((p) => Number.isFinite(p[1]))
        .map((p, index) => `${index === 0 ? "M" : "L"}${sx(p[0]).toFixed(2)} ${sy(p[1]).toFixed(2)}`)
        .join(" ");
      return h(
        "g",
        null,
        s.area
          ? h("path", {
              d: `${path} L${sx(domainX[1]).toFixed(2)} ${sy(domainY[0]).toFixed(2)} L${sx(
                domainX[0],
              ).toFixed(2)} ${sy(domainY[0]).toFixed(2)} Z`,
              fill: s.area,
              stroke: "none",
            })
          : null,
        h("path", {
          class: "chart__line",
          d: path,
          stroke: s.colour,
          "stroke-dasharray": s.dash ? s.dash.join(" ") : null,
        }),
        s.dots
          ? (s.points || [])
              .filter((p) => Number.isFinite(p[1]))
              .map((p) =>
                h("circle", {
                  class: "chart__dot",
                  cx: sx(p[0]).toFixed(2),
                  cy: sy(p[1]).toFixed(2),
                  r: 2.2,
                  fill: s.colour,
                }),
              )
          : null,
      );
    }),

    yLabel
      ? h(
          "text",
          {
            class: "chart__label",
            x: 3, y: PAD.top - 2,
            "text-anchor": "start",
          },
          yLabel,
        )
      : null,
    xLabel
      ? h(
          "text",
          { class: "chart__label", x: width - PAD.right, y: PAD.top - 2, "text-anchor": "end" },
          xLabel,
        )
      : null,
  );
}

/**
 * A sorted distribution strip - one bar per scene, tallest first is *not* used; the bars
 * stay in the given order so a caller can sort by score and show the tail explicitly.
 */
export function distributionStrip(values, { colour = "var(--oil)", format = (v) => F.metric(v, 3), max } = {}) {
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return h("p", { class: "muted small" }, "No per-scene values.");
  const top = max ?? Math.max(...finite);
  return h(
    "div",
    { class: "strip", role: "img", "aria-label": `Distribution over ${finite.length} scenes` },
    finite.map((value, index) =>
      h("div", {
        class: "strip__bar",
        style: {
          height: `${Math.max(2, (value / (top || 1)) * 100)}%`,
          background: colour,
        },
        title: `Scene ${index + 1}: ${format(value)}`,
      }),
    ),
  );
}

/** A histogram of a numeric sample, with the bin edges shown. */
export function histogram(values, { bins = 12, colour = "var(--oil)", format = (v) => F.num(v, 2), label = "" } = {}) {
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return h("p", { class: "muted small" }, "Nothing to bin.");
  const low = Math.min(...finite);
  const high = Math.max(...finite);
  const width = (high - low) / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const value of finite) {
    counts[Math.min(bins - 1, Math.floor((value - low) / width))] += 1;
  }
  const peak = Math.max(...counts);
  return h(
    "div",
    null,
    h(
      "div",
      { class: "strip", role: "img", "aria-label": label || `Histogram of ${finite.length} values` },
      counts.map((count, index) =>
        h("div", {
          class: "strip__bar",
          style: { height: `${Math.max(1, (count / peak) * 100)}%`, background: colour },
          title: `${format(low + index * width)} to ${format(low + (index + 1) * width)}: ${count}`,
        }),
      ),
    ),
    h(
      "div",
      { class: "inline between small muted", style: { marginTop: "var(--s2)" } },
      h("span", null, format(low)),
      h("span", null, format(high)),
    ),
  );
}

/** A one-line sparkline, no axes, for a table cell or a stat card. */
export function sparkline(values, { colour = "var(--oil)", width = 96, height = 22 } = {}) {
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length < 2) return h("span", { class: "muted" }, F.DASH);
  const low = Math.min(...finite);
  const high = Math.max(...finite);
  const range = high - low || 1;
  const path = finite
    .map((value, index) => {
      const x = (index / (finite.length - 1)) * (width - 2) + 1;
      const y = height - 1 - ((value - low) / range) * (height - 2);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return h(
    "svg",
    { viewBox: `0 0 ${width} ${height}`, width, height, class: "chart", "aria-hidden": "true" },
    h("path", { d: path, fill: "none", stroke: colour, "stroke-width": 1.4, "stroke-linejoin": "round" }),
  );
}

/**
 * A stacked bar showing how a score decomposes, one segment per component.
 * Used for "these six things add to 91.5 out of 100".
 */
export function stackedBar(parts, total, { height = 12 } = {}) {
  const sum = parts.reduce((acc, p) => acc + (Number(p.value) || 0), 0);
  return h(
    "div",
    {
      class: "scorebar",
      role: "img",
      "aria-label": `${F.num(sum, 1)} of ${total}, from ${parts.length} components`,
      style: { height: `${height}px` },
    },
    parts.map((part) =>
      h("span", {
        class: "scorebar__seg",
        style: {
          width: `${((Number(part.value) || 0) / total) * 100}%`,
          background: part.colour,
        },
        title: `${part.label}: ${F.num(part.value, 1)} of ${part.max}`,
      }),
    ),
    sum < total
      ? h("span", {
          class: "scorebar__seg scorebar__seg--rest",
          style: { width: `${((total - sum) / total) * 100}%` },
          title: `${F.num(total - sum, 1)} not awarded`,
        })
      : null,
  );
}

// -- helpers ----------------------------------------------------------------

function span([low, high]) {
  return high - low || 1;
}

function padDomain(low, high) {
  if (low === high) return [low - 0.5, high + 0.5];
  const margin = (high - low) * 0.08;
  return [low - margin, high + margin];
}

function ticks([low, high], count) {
  const step = (high - low) / count;
  return Array.from({ length: count + 1 }, (_, index) => low + index * step);
}
