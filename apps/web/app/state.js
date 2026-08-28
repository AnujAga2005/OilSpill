/** Application state: one store, explicit transitions, no framework.
 *
 * The store holds three things a screen can be in the middle of: the loaded case, the
 * running job, and the analyst's own annotations (accepted / flagged for review). Every
 * async load is tracked with an explicit status so a screen can render loading, failed,
 * empty and missing-data separately rather than collapsing them into "no data".
 *
 * Analyst annotations persist in `localStorage`, keyed by case, because "I accepted this
 * prediction" should survive a reload. They are the analyst's notes about the case, not
 * part of the case document, and they never overwrite the stored model output.
 */

const STORAGE_KEY = "spilltrace.annotations.v1";

/** @typedef {"idle"|"loading"|"ready"|"missing"|"failed"} Status */

const state = {
  /** @type {Status} */
  caseStatus: "idle",
  caseId: null,
  /** @type {object|null} */
  caseDoc: null,
  /** @type {Error|null} */
  caseError: null,

  /** @type {Status} */
  healthStatus: "idle",
  health: null,

  /** @type {Status} */
  metricsStatus: "idle",
  metrics: null,

  /** @type {Status} */
  scenesStatus: "idle",
  scenes: null,

  /** @type {Status} */
  casesStatus: "idle",
  cases: null,

  /** @type {object|null} the running job, with its progress log */
  job: null,
  /** @type {AbortController|null} */
  jobAbort: null,

  /** id of the selected candidate vessel, shared between map and table */
  selectedVessel: null,

  annotations: loadAnnotations(),
};

const listeners = new Set();

export function get() {
  return state;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Merge a patch and notify. Keys not in the patch are untouched. */
export function set(patch) {
  let changed = false;
  for (const [key, value] of Object.entries(patch)) {
    if (state[key] !== value) {
      state[key] = value;
      changed = true;
    }
  }
  if (changed) {
    for (const fn of [...listeners]) fn(state, patch);
  }
}

// -- annotations ------------------------------------------------------------

function loadAnnotations() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    // A disabled or full localStorage is not an error worth surfacing; annotations
    // simply become session-scoped.
    return {};
  }
}

function persistAnnotations() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.annotations));
  } catch {
    /* ignore */
  }
}

export function annotation(caseId) {
  return state.annotations[caseId] || { accepted: null, flagged: false, note: "" };
}

export function annotate(caseId, patch) {
  const next = { ...annotation(caseId), ...patch, updatedUtc: new Date().toISOString() };
  state.annotations = { ...state.annotations, [caseId]: next };
  persistAnnotations();
  for (const fn of [...listeners]) fn(state, { annotations: state.annotations });
  return next;
}

// -- loaders ---------------------------------------------------------------

/**
 * Run an async loader into a `<name>Status` / `<name>` pair.
 * `missing` is distinguished from `failed` so screens can offer the build command.
 */
export async function load(name, loader, { statusKey = `${name}Status` } = {}) {
  set({ [statusKey]: "loading" });
  try {
    const value = await loader();
    set({ [name]: value, [statusKey]: "ready" });
    return value;
  } catch (error) {
    const missing = error && error.isMissing;
    set({
      [name]: null,
      [statusKey]: missing ? "missing" : "failed",
      ...(name === "caseDoc" ? { caseError: error } : {}),
    });
    return null;
  }
}

/** Reset per-case selections. Called whenever the active case changes. */
export function resetSelection() {
  set({ selectedVessel: null });
}
