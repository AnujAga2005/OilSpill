/** Small SVG charts.
 *
 * Two screens need to plot a series against a numeric axis - drift spread against time, and
 * the threshold sweep and training history on the methodology screen. Rather than duplicate
 * the axis arithmetic twice, it lives here.
 *
 * These are deliberately plain: no tooltips library, no animation, no gradients. A chart on
 * this product exists so a reader can see the shape of a number they can also read in a
 * table, and every one of them is paired with that table.
 */

import { h } from "./dom.js";

/** `h` already namespaces every tag in its SVG set, so these are plain `h` calls. */
const s = h;

function extent(values) {
  let min = Infinity;
  let max = -Infinity;
  for (const value of values) {
    if (!Number.isFinite(value)) continue;
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (min === Infinity) return [0, 1];
  if (min === max) return [min - 0.5, max + 0.5];
  return [min, max];
}

/** Round a range outwards to something a person would choose for an axis. */
function nice([min, max]) {
  const span = max - min;
  const step = Math.pow(10, Math.floor(Math.log10(span))) / 2;
  return [Math.floor(min / step) * step, Math.ceil(max / step) * step];
}

/**
 * A multi-series line chart.
 *
 * `series` is `[{label, colour, points: [[x, y], ...], dash, dots}]`. All series share one
 * pair of axes, so only plot things measured in the same unit together.
 */
export function lineChart(series, {
  width = 520,
  height = 190,
  pad = { top: 10, right: 12, bottom: 26, left: 40 },
  xLabel = "",
  yLabel = "",
  xTicks = 5,
  yTicks = 4,
  yZero = false,
  formatX = (v) => String(Math.round(v * 100) / 100),
  formatY = (v) => String(Math.round(v * 1000) / 1000),
  ariaLabel,
} = {}) {
  const live = series.filter((entry) => entry?.points?.length);
  if (!live.length) return h("div", { class: "small muted" }, "No series to plot.");

  const xs = live.flatMap((entry) => entry.points.map((p) => p[0]));
  const ys = live.flatMap((entry) => entry.points.map((p) => p[1]));
  const [x0, x1] = extent(xs);
  let [y0, y1] = nice(extent(ys));
  if (yZero) y0 = Math.min(0, y0);

  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const sx = (v) => pad.left + (x1 === x0 ? plotW / 2 : ((v - x0) / (x1 - x0)) * plotW);
  const sy = (v) => pad.top + plotH - (y1 === y0 ? plotH / 2 : ((v - y0) / (y1 - y0)) * plotH);

  const gridlines = [];
  for (let i = 0; i <= yTicks; i += 1) {
    const value = y0 + ((y1 - y0) * i) / yTicks;
    const y = sy(value);
    gridlines.push(s("line", { class: "chart__grid", x1: pad.left, x2: width - pad.right, y1: y, y2: y }));
    gridlines.push(
      s("text", { class: "chart__label", x: pad.left - 6, y: y + 3, "text-anchor": "end" }, formatY(value)),
    );
  }

  const xLabels = [];
  for (let i = 0; i <= xTicks; i += 1) {
    const value = x0 + ((x1 - x0) * i) / xTicks;
    xLabels.push(
      s(
        "text",
        {
          class: "chart__label",
          x: sx(value),
          y: height - pad.bottom + 14,
          "text-anchor": i === 0 ? "start" : i === xTicks ? "end" : "middle",
        },
        formatX(value),
      ),
    );
  }

  const paths = live.map((entry) => {
    const d = entry.points
      .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))
      .map((p, index) => `${index === 0 ? "M" : "L"}${sx(p[0]).toFixed(2)} ${sy(p[1]).toFixed(2)}`)
      .join(" ");
    return s("path", {
      class: "chart__line",
      d,
      stroke: entry.colour,
      "stroke-dasharray": entry.dash || null,
      opacity: entry.opacity ?? 1,
    });
  });

  const dots = live.flatMap((entry) =>
    entry.dots
      ? entry.points
          .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))
          .map((p) =>
            s("circle", {
              class: "chart__dot",
              cx: sx(p[0]).toFixed(2),
              cy: sy(p[1]).toFixed(2),
              r: 2.6,
              fill: entry.colour,
            }),
          )
      : [],
  );

  return s(
    "svg",
    {
      class: "chart",
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: "none",
      role: "img",
      "aria-label": ariaLabel || `${yLabel} against ${xLabel}`,
    },
    gridlines,
    s("line", {
      class: "chart__axis",
      x1: pad.left,
      x2: width - pad.right,
      y1: height - pad.bottom,
      y2: height - pad.bottom,
    }),
    s("line", {
      class: "chart__axis",
      x1: pad.left,
      x2: pad.left,
      y1: pad.top,
      y2: height - pad.bottom,
    }),
    paths,
    dots,
    xLabels,
    xLabel
      ? s(
          "text",
          { class: "chart__label", x: width - pad.right, y: height - 2, "text-anchor": "end" },
          xLabel,
        )
      : null,
    yLabel ? s("text", { class: "chart__label", x: 2, y: pad.top - 2 }, yLabel) : null,
  );
}

/**
 * A one-row distribution strip: one bar per value, height proportional to the value.
 *
 * Used for per-scene IoU, where the point is the spread and the tail rather than any
 * individual bar. `tone` is a CSS colour applied to every bar; `mark` highlights one index.
 */
export function strip(values, { max = null, tone = "var(--oil)", label = (v) => String(v), mark = -1 } = {}) {
  const ceiling = max ?? Math.max(...values.filter(Number.isFinite), 1);
  return h(
    "div",
    { class: "strip" },
    values.map((value, index) =>
      h("div", {
        class: "strip__bar",
        style: {
          height: `${Math.max(2, ((value || 0) / ceiling) * 100)}%`,
          background: index === mark ? "var(--reference)" : tone,
        },
        title: label(value, index),
      }),
    ),
  );
}

/** A stacked proportion bar, for particle outcomes and score components. */
export function stackBar(parts, { height = 8 } = {}) {
  const total = parts.reduce((sum, part) => sum + (part.value || 0), 0) || 1;
  return h(
    "div",
    {
      style: {
        display: "flex",
        height: `${height}px`,
        "border-radius": "999px",
        overflow: "hidden",
        background: "var(--line)",
      },
    },
    parts
      .filter((part) => (part.value || 0) > 0)
      .map((part) =>
        h("div", {
          style: { width: `${((part.value || 0) / total) * 100}%`, background: part.colour },
          title: `${part.label}: ${part.value}`,
        }),
      ),
  );
}
