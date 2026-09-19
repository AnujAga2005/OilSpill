/** The run indicator: one horizontal bar, driven by the job's own progress lines.
 *
 * A detection job is ten stages and about twenty seconds, and the server names each stage
 * as it starts it. That is real information, so the bar is built from it rather than from
 * a spinner that only means "something is happening".
 *
 * Two things are worth being exact about, because a progress bar is a claim:
 *
 *  - **The stage is measured, the position between two stages is estimated.** The bar steps
 *    forward when a line arrives, which is a fact. In between it creeps towards the next
 *    boundary at the rate that stage has historically taken, and it is clamped so it can
 *    never cross a boundary the server has not announced. If a stage runs long the bar
 *    waits at the line rather than inventing progress past it. Detection is the exception
 *    and the better case: it reports `inference 64/361 tiles` as it goes, so across the
 *    stage that costs the most after decoding, the bar is following a count.
 *  - **The scale is time, not stage count.** Decoding a 2048x2048 GeoTIFF is half the run
 *    and scoring vessels is a twentieth of a second, so ten evenly-spaced steps would put
 *    the bar at 50% after a second and a half. The weights below are the median seconds
 *    each stage took across the twelve stored cases in `data/processed/cases/`, summing to
 *    19.99 s -- the same twenty seconds the intake screen promises.
 *
 * The bar also exists to *stop* a redraw. Before this, every poll pushed the job into the
 * store, the store notified the renderer, and the renderer rebuilt the whole page with
 * `replaceChildren` -- fifteen times in one run, which is the flicker people saw. Progress
 * now arrives on its own channel (`state.emitProgress`) and this component writes to its
 * own two or three nodes. Nothing else on the screen is touched while a job runs.
 */

import { h } from "./dom.js";
import { subscribeProgress, runningJob } from "./state.js";

/**
 * The pipeline, in the order `case.py` runs it, with the line each stage prints when it
 * starts and the median seconds it took across the stored cases.
 *
 * `is` matches the server's text. Where a stage has two possible lines -- a fresh
 * detection or a reused one, synthetic AIS or a supplied extract -- both are listed,
 * because which one you get depends on the request rather than on the stage.
 */
const RAW_STAGES = [
  {
    key: "decode",
    seconds: 9.549,
    is: (line) => line.startsWith("decoding "),
  },
  {
    key: "detect",
    seconds: 5.721,
    is: (line) =>
      line.startsWith("detecting oil") ||
      line.startsWith("reusing the stored detection") ||
      line.startsWith("loaded the stored detection mask") ||
      line.startsWith("no cached mask"),
  },
  {
    key: "geometry",
    seconds: 0.472,
    is: (line) => line.startsWith("measuring slick geometry"),
  },
  {
    key: "screening",
    seconds: 3.261,
    is: (line) => line.startsWith("screening dark patches"),
  },
  {
    key: "forcing",
    seconds: 0.021,
    is: (line) => line.startsWith("resolving drift forcing"),
  },
  {
    key: "backward",
    seconds: 0.264,
    is: (line) => line.startsWith("reconstructing backward drift"),
  },
  {
    key: "forward",
    seconds: 0.249,
    is: (line) => line.startsWith("projecting forward drift"),
  },
  {
    key: "ais",
    seconds: 0.173,
    is: (line) =>
      line.startsWith("generating synthetic AIS") ||
      line.startsWith("reading the supplied AIS extract"),
  },
  {
    key: "scoring",
    seconds: 0.067,
    is: (line) => line.startsWith("scoring vessels"),
  },
  {
    key: "previews",
    seconds: 0.214,
    is: (line) => line.startsWith("rendering previews"),
  },
];

/**
 * Detection reports its own progress: `inference 64/361 tiles`, every 64 tiles.
 *
 * That is a count, not a clock, and detection is the second-heaviest stage of the run -- so
 * wherever this line exists the bar follows it and the estimate below is not used at all.
 * Only the last line counts. A drift job reuses the stored mask and never prints one, and
 * then the clock is all there is.
 *
 * @param {string[]} log
 * @returns {number|null} 0..1, or null when the job has not reported a tile count
 */
export function tileFraction(log) {
  for (let index = (log || []).length - 1; index >= 0; index -= 1) {
    const match = /^inference (\d+)\/(\d+) tiles/.exec(String(log[index] || ""));
    if (!match) continue;
    const done = Number(match[1]);
    const total = Number(match[2]);
    if (!(total > 0)) return null;
    return Math.max(0, Math.min(1, done / total));
  }
  return null;
}

/** The same table with each stage's start and width as a fraction of a whole run. */
export const STAGES = (() => {
  const total = RAW_STAGES.reduce((sum, stage) => sum + stage.seconds, 0);
  let cursor = 0;
  return RAW_STAGES.map((stage) => {
    const share = stage.seconds / total;
    const entry = { ...stage, share, start: cursor };
    cursor += share;
    return entry;
  });
})();

/**
 * Which stage a job's log has reached: the highest matching index, never a lower one.
 *
 * Highest rather than last, because a drift job reuses a stored detection and therefore
 * prints its detection line *before* `build_case` prints its decode line. Taking the
 * maximum means the bar cannot walk backwards on a job that legitimately revisits a stage.
 *
 * @param {string[]} log
 * @returns {number} `-1` before the first recognised line
 */
export function stageIndex(log) {
  let best = -1;
  for (const entry of log || []) {
    const line = String(entry || "");
    for (let index = STAGES.length - 1; index > best; index -= 1) {
      if (STAGES[index].is(line)) {
        best = index;
        break;
      }
    }
  }
  return best;
}

/** Where the detect stage sits, so its reported tile count can be applied to it alone. */
export const DETECT_INDEX = STAGES.findIndex((stage) => stage.key === "detect");

/** How far through a run a stage is, given how long it has been in that stage.
 *
 * `msInStage` is clamped to the stage's own width, so a stage that runs longer than usual
 * parks the bar at the next boundary instead of overrunning it. The 0.97 keeps it a hair
 * short of the boundary, so arriving at the boundary is always the server's word.
 *
 * `measured` replaces the estimate outright when the stage reports its own progress -- see
 * `tileFraction`. A measurement does not need a clock behind it.
 */
export function fractionFor(index, msInStage, finished, measured = null) {
  if (finished) return 1;
  if (index < 0) return 0;
  const stage = STAGES[index];
  const within = Number.isFinite(measured)
    ? Math.max(0, Math.min(1, measured))
    : stage.seconds > 0
      ? Math.min(1, msInStage / (stage.seconds * 1000))
      : 1;
  return Math.min(stage.start + within * stage.share * 0.97, 0.995);
}

/** True once the job has reported a terminal state. */
function isFinished(job) {
  return job?.state === "done" || job?.state === "failed" || job?.state === "cancelled";
}

/** The line to show: the server's own sentence, so the bar never renames a stage. */
function currentLine(job, index) {
  const line = job?.message || (job?.log || []).at(-1) || "";
  if (line && line !== "started") return String(line);
  return index >= 0 ? STAGES[index].key : "starting";
}

/**
 * A horizontal progress bar that follows the running job and repaints itself.
 *
 * It subscribes to the progress channel and drops the subscription the moment its node
 * leaves the document, so a re-render that throws the old bar away does not leave a
 * listener or an interval behind.
 *
 * @param {object} [options]
 * @param {boolean} [options.compact] - the one-line form used in the top bar
 * @param {Node} [options.trailing] - put beside the bar, e.g. a Cancel button
 * @returns {HTMLElement}
 */
export function runBar({ compact = false, trailing = null } = {}) {
  let stage = -1;
  let stageAtMs = performance.now();
  let elapsedBaseS = 0;
  let elapsedAtMs = performance.now();
  let finished = false;
  /** The detect stage's own tile count, when it has reported one. */
  let measured = null;

  let fill = null;
  let stageEl = null;
  let countEl = null;
  let timeEl = null;

  const track = h(
    "div",
    { class: "runbar__track" },
    h("div", { class: "runbar__fill", ref: (node) => (fill = node) }),
  );
  const head = h(
    "div",
    { class: "runbar__head" },
    h("span", { class: "runbar__stage", ref: (node) => (stageEl = node) }, "starting"),
    h("span", { class: "runbar__count mono", ref: (node) => (countEl = node) }, ""),
    h("span", { class: "runbar__time mono", ref: (node) => (timeEl = node) }, "0s"),
  );

  const node = h(
    "div",
    {
      class: ["runbar", compact ? "runbar--compact" : null],
      role: "progressbar",
      "aria-valuemin": "0",
      "aria-valuemax": "100",
      "aria-valuenow": "0",
    },
    compact ? null : head,
    track,
    compact ? head : null,
    trailing,
  );

  function paint() {
    const now = performance.now();
    const fraction = fractionFor(stage, now - stageAtMs, finished, measured);
    const percent = Math.round(fraction * 100);
    const label = stageEl.textContent;
    fill.style.setProperty("width", `${(fraction * 100).toFixed(1)}%`);
    countEl.textContent = stage >= 0 ? `${stage + 1}/${STAGES.length}` : "";
    timeEl.textContent = `${Math.max(0, Math.round(elapsedBaseS + (now - elapsedAtMs) / 1000))}s`;
    node.setAttribute("aria-valuenow", String(percent));
    node.setAttribute(
      "aria-valuetext",
      stage >= 0 ? `Stage ${stage + 1} of ${STAGES.length}, ${label}` : label,
    );
  }

  function apply(job) {
    if (!job) return;
    const now = performance.now();
    const next = stageIndex(job.log);
    if (next > stage) {
      stage = next;
      stageAtMs = now;
    }
    if (Number.isFinite(job.elapsedSeconds)) {
      elapsedBaseS = Number(job.elapsedSeconds);
      elapsedAtMs = now;
    }
    // The tile count belongs to the detect stage and to no other, so it is read only while
    // the bar is in that stage. Once the stage has moved on, the line is still in the log
    // and applying it would hold the bar at whatever fraction detection ended on.
    measured = stage === DETECT_INDEX ? tileFraction(job.log) : null;
    finished = isFinished(job);
    stageEl.textContent = finished && job.state === "done" ? "finished" : currentLine(job, stage);
    paint();
  }

  const unsubscribe = subscribeProgress(apply);
  // 200 ms is the creep's resolution, not a poll: nothing is fetched here. The check on
  // `isConnected` is the teardown -- a bar that has been replaced stops on its own tick
  // rather than needing the screen that built it to remember to say so.
  const timer = setInterval(() => {
    if (!node.isConnected) {
      clearInterval(timer);
      unsubscribe();
      return;
    }
    if (!finished) paint();
  }, 200);

  apply(runningJob());
  paint();
  return node;
}
