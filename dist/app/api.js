/** The API client, with an offline fixture fallback.
 *
 * Three things this layer owns:
 *
 * * **Offline mode.** The dashboard has to work with no live services, so every read
 *   falls back to a static fixture under `./demo/` when the API cannot be reached. The
 *   fallback is announced through `mode()` and the interface says which one it is
 *   rather than pretending a replayed case is a fresh one. A 404 is *not* a fallback
 *   trigger: the API answered, and "not computed yet" is a real answer with a fix.
 * * **Jobs, not requests.** A case is about a minute of NumPy. POST returns a job id;
 *   `runJob` polls with backoff and forwards the job's own progress log so the caller
 *   can name the current stage instead of spinning.
 * * **No credentials.** There is nothing to authenticate against and no key anywhere in
 *   this bundle; requests are same-origin with no headers beyond content type.
 *
 * One call breaks the `fetch` pattern: `upload()` uses `XMLHttpRequest`, because a
 * several-hundred-megabyte file needs a progress event and `fetch` has none.
 */

const FIXTURE_BASE = "./demo";
const JSON_HEADERS = { "Content-Type": "application/json" };

let mode = "unknown"; // "live" | "offline" | "unknown"
const listeners = new Set();

export function apiMode() {
  return mode;
}

export function onModeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setMode(next) {
  if (next === mode) return;
  mode = next;
  for (const fn of listeners) fn(mode);
}

export class ApiError extends Error {
  constructor(message, { status = 0, payload = null, url = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.url = url;
  }

  /** True when the server answered but has nothing computed yet. */
  get isMissing() {
    return this.status === 404;
  }

  /** The command that would produce the missing artefact, when the API supplied one. */
  get hint() {
    return this.payload?.hint || null;
  }
}

/** Fixtures are small and immutable; parse each one once. */
const fixtureCache = new Map();

async function fixture(name) {
  if (fixtureCache.has(name)) return fixtureCache.get(name);
  const url = `${FIXTURE_BASE}/${name}.json`;
  const response = await fetch(url, { cache: "force-cache" });
  if (!response.ok) {
    throw new ApiError(`offline fixture ${name} is not in the bundle`, {
      status: response.status,
      url,
    });
  }
  const payload = await response.json();
  fixtureCache.set(name, payload);
  return payload;
}

/**
 * GET JSON from the API, falling back to a bundled fixture when the network fails.
 * @param {string} path - API path, e.g. `/api/cases/demo`
 * @param {string|null} fallback - fixture name to use offline, or null for none
 */
async function get(path, fallback) {
  // Once the probe has settled on offline there is nothing to ask. Requesting anyway
  // would only produce a 404 from whatever is serving the bundle, and that answer is
  // indistinguishable from the API's own "not computed yet".
  if (mode === "offline" && fallback) return fixture(fallback);

  let response;
  try {
    response = await fetch(path, { headers: { Accept: "application/json" } });
  } catch (cause) {
    // A transport failure means no server. Anything the bundle can answer, it answers.
    setMode("offline");
    if (fallback) return fixture(fallback);
    throw new ApiError("the API is not reachable and this view has no offline fixture", {
      url: path,
    });
  }
  if (response.ok) setMode("live");
  if (!response.ok) {
    // A 404 from the API is a real answer -- "not computed yet" -- and the caller shows
    // the command that fixes it. A 404 from a static host serving this bundle is not: it
    // means there is no API at all. Only a live health response distinguishes the two, so
    // without one an error response falls back rather than being reported as an answer.
    if (mode !== "live" && fallback) {
      setMode("offline");
      return fixture(fallback);
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new ApiError(payload?.error || `${response.status} from ${path}`, {
      status: response.status,
      payload,
      url: path,
    });
  }
  return response.json();
}

async function post(path, body) {
  // Nothing to submit to. Said plainly rather than sent and reported as a 404.
  if (mode === "offline") {
    throw new ApiError(
      "the API is not reachable, so no new analysis can be started. The bundled demo " +
        "case is still available for review.",
      { url: path },
    );
  }
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(body ?? {}),
    });
  } catch {
    setMode("offline");
    throw new ApiError(
      "the API is not reachable, so no new analysis can be started. The bundled demo " +
        "case is still available for review.",
      { url: path },
    );
  }
  if (response.ok || response.status === 202) setMode("live");
  if (!response.ok && response.status !== 202) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new ApiError(payload?.error || `${response.status} from ${path}`, {
      status: response.status,
      payload,
      url: path,
    });
  }
  return response.json();
}

// -- reads ------------------------------------------------------------------

export const health = () => get("/api/health", "health");
export const metrics = () => get("/api/metrics", "metrics");
export const scenes = () => get("/api/scenes", "scenes");
export const cases = () => get("/api/cases", "cases");

/** A full case document. Offline, only the bundled demo case exists. */
export const loadCase = (id) =>
  get(`/api/cases/${encodeURIComponent(id)}`, id === "demo" ? "case-demo" : null);

/** The same document without `geometry`, `drift` and `ais` - enough for an overview. */
export const loadCaseLean = (id) =>
  get(`/api/cases/${encodeURIComponent(id)}?lean=1`, id === "demo" ? "case-demo" : null);

export const imageIndex = (id) =>
  get(`/api/cases/${encodeURIComponent(id)}/images`, id === "demo" ? "images-demo" : null);

export const report = (id) =>
  get(`/api/cases/${encodeURIComponent(id)}/report`, id === "demo" ? "report-demo" : null);

/**
 * The URL for one preview raster.
 * Offline this resolves into the bundle, where the build step copied the PNGs.
 */
/** Direct URL for the server-generated incident PDF. */
export const reportPdfUrl = (caseId) =>
  mode === "offline"
    ? null
    : `/api/cases/${encodeURIComponent(caseId)}/report?format=pdf`;

/** Queue an incident-report email. `recipients` may be an array or comma-separated string. */
export const dispatchEmail = (caseId, options) =>
  post(`/api/cases/${encodeURIComponent(caseId)}/dispatch`, options);

export function imageUrl(caseId, kind, files) {
  if (mode === "offline") {
    const name = files?.[kind];
    return name ? `${FIXTURE_BASE}/previews/${name}` : null;
  }
  return `/api/cases/${encodeURIComponent(caseId)}/images?kind=${encodeURIComponent(kind)}`;
}

/**
 * The URL for one qualitative evaluation strip.
 *
 * `file` is the path metrics.json recorded, relative to the preview directory
 * (`eval/test_0117_iou100.png`). Only the basename is ever sent.
 */
export function evalImageUrl(file) {
  const name = String(file || "").split("/").pop();
  if (!name) return null;
  return mode === "offline"
    ? `${FIXTURE_BASE}/eval/${name}`
    : `/api/eval/${encodeURIComponent(name)}`;
}

/** CSV is generated server-side; offline the caller builds it from the case document. */
export function csvUrl(caseId) {
  return mode === "offline"
    ? null
    : `/api/cases/${encodeURIComponent(caseId)}/report?format=csv`;
}

// -- uploads ----------------------------------------------------------------

/** What the server currently holds, and the rules each slot enforces. */
export const uploadState = () => get("/api/uploads", null);

/** Delete every stored upload. Returns `{removed, uploads}`. */
export const clearUploads = () => post("/api/uploads/clear", {});

/**
 * Name a stored case, and optionally describe it.
 *
 * Presentation only: the run is already on disk, and this writes what a person calls it so
 * the case list can show that instead of an id. Returns the case's refreshed summary, which
 * is the same shape the list is built from.
 *
 * @param {string} caseId
 * @param {{label?: string|null, description?: string|null}} fields
 */
export const saveCaseLabel = (caseId, fields) =>
  post(`/api/cases/${encodeURIComponent(caseId)}/label`, fields);

/**
 * Delete a stored analysis: the case document, its cached detection mask and its previews.
 *
 * Only pipeline output goes. The scene it was built from is untouched, and so is anything
 * in `data/uploads/` -- `clearUploads` is what removes those. Returns
 * `{deleted, removedPreviews, cases}`, the last being the refreshed list.
 *
 * @param {string} caseId
 */
export const deleteCase = (caseId) =>
  post(`/api/cases/${encodeURIComponent(caseId)}/delete`, {});

/**
 * Send one file to one slot.
 *
 * `XMLHttpRequest` rather than `fetch`, for one reason: a CMEMS product is around
 * 370 MB, and `fetch` has no upload-progress event. Without one the interface can only
 * say "uploading" for a minute and a half, which is indistinguishable from a hang. The
 * body is the `File` itself, not multipart -- the server streams the raw bytes to disk
 * and takes the slot and the operator's filename from the query string, so there is no
 * boundary to parse and nothing is buffered in memory on either side.
 *
 * @param {"scene"|"mask"|"era5"|"cmems"|"ais"} kind
 * @param {File} file
 * @param {(fraction: number) => void} [onProgress] - 0..1, or called with NaN when the
 *   browser cannot compute a total
 * @param {AbortSignal} [signal]
 * @returns {Promise<object>} `{upload, uploads, slots, note}`
 */
export function upload(kind, file, onProgress, signal) {
  if (mode === "offline") {
    return Promise.reject(
      new ApiError(
        "the API is not reachable, so there is nowhere to send a file. The bundled demo " +
          "case is still available for review.",
        { url: "/api/uploads" },
      ),
    );
  }
  const url =
    `/api/uploads?kind=${encodeURIComponent(kind)}` +
    `&name=${encodeURIComponent(file?.name || "")}`;

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url, true);
    // Octet-stream, deliberately: the body is the file's bytes and nothing else.
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.responseType = "text";

    const abort = () => request.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const done = () => signal?.removeEventListener("abort", abort);

    request.upload.onprogress = (event) => {
      onProgress?.(event.lengthComputable ? event.loaded / event.total : NaN);
    };
    request.onerror = () => {
      done();
      setMode("offline");
      reject(new ApiError("the upload did not reach the API", { url }));
    };
    request.onabort = () => {
      done();
      reject(new DOMException("cancelled", "AbortError"));
    };
    request.onload = () => {
      done();
      let payload = null;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        payload = null;
      }
      if (request.status === 201) {
        setMode("live");
        onProgress?.(1);
        resolve(payload);
        return;
      }
      // The server's own message names the file and the reason -- "the bytes in this
      // file are not a TIFF", "capped at 512 MB". Replacing it with a status code would
      // throw away the only part an operator can act on.
      reject(
        new ApiError(payload?.error || `${request.status} from ${url}`, {
          status: request.status,
          payload,
          url,
        }),
      );
    };
    request.send(file);
  });
}

// -- jobs -------------------------------------------------------------------
export const submitDetect = (scene, options) =>
  post(`/api/cases/${encodeURIComponent(scene)}/detect`, options);

export const submitDrift = (scene, options) =>
  post(`/api/cases/${encodeURIComponent(scene)}/drift`, options);

export const jobStatus = (jobId) => get(`/api/jobs/${encodeURIComponent(jobId)}`, null);

export const cancelJob = (jobId) => post(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {});

/**
 * Submit a job and poll to completion.
 *
 * @param {"detect"|"drift"} kind
 * @param {string} scene
 * @param {object} options - request body
 * @param {(job: object) => void} onProgress - called on every poll with the job record
 * @param {AbortSignal} [signal] - cancels polling (and the job) when aborted
 * @returns {Promise<object>} the finished job record
 */
export async function runJob(kind, scene, options, onProgress, signal) {
  const submitted = kind === "drift"
    ? await submitDrift(scene, options)
    : await submitDetect(scene, options);
  return awaitJob(submitted, onProgress, signal);
}

/**
 * Poll a job the caller has already submitted, until it reaches a terminal state.
 *
 * Split out of `runJob` so that every job the interface starts -- detection, drift and
 * incident dispatch -- waits the same way: the same backoff, the same cancellation, and
 * a failed job raising an `ApiError` carrying the server's own message rather than the
 * caller inventing one. The alternative, a fixed number of fixed-delay `fetch` calls,
 * has no way to distinguish "still running" from "gave up watching".
 *
 * @param {object} submitted - the accepted-job body: needs `jobId`
 * @param {(job: object) => void} [onProgress]
 * @param {AbortSignal} [signal]
 * @returns {Promise<object>} the finished job record
 * @throws {ApiError} when the job fails, or when it outlives `POLL_CEILING_MS`
 * @throws {DOMException} `AbortError` when cancelled from either end
 */
export async function awaitJob(submitted, onProgress, signal) {
  const jobId = submitted?.jobId;
  if (!jobId) throw new ApiError("the server accepted no job", { payload: submitted, status: 500 });
  onProgress?.({ ...submitted, state: submitted.state || "queued", log: [] });

  // Poll fast at first - a cached case finishes in well under a second - then ease off
  // so a full minute of inference is not 120 requests.
  let wait = 220;
  const deadline = Date.now() + POLL_CEILING_MS;
  for (;;) {
    if (signal?.aborted) {
      await cancelJob(jobId).catch(() => {});
      throw new DOMException("cancelled", "AbortError");
    }
    await sleep(wait, signal);
    wait = Math.min(Math.round(wait * 1.35), 1600);
    const job = await jobStatus(jobId);
    onProgress?.(job);
    if (job.state === "done") return job;
    if (job.state === "failed") {
      throw new ApiError(job.error || "the job failed", { payload: job, status: 500 });
    }
    if (job.state === "cancelled") {
      throw new DOMException("cancelled", "AbortError");
    }
    if (Date.now() > deadline) {
      // The job may well still be running server-side; say so rather than claiming
      // it failed. `jobId` lets the reader check `/api/jobs/<id>` by hand.
      throw new ApiError(
        `job ${jobId} is still ${job.state} after ${Math.round(POLL_CEILING_MS / 1000)}s; ` +
          "it may still finish - check /api/jobs/" + jobId,
        { payload: job, status: 504 },
      );
    }
  }
}

/** How long the browser watches one job before it stops waiting. A full-scene
 *  detection is ~19 s and a 1200-particle drift ~20 s, so five minutes is slack. */
const POLL_CEILING_MS = 300_000;

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("cancelled", "AbortError"));
      },
      { once: true },
    );
  });
}

/**
 * Probe the API once at start-up so the mode is settled before any screen loads.
 *
 * This goes to `fetch` directly rather than through `health()`, because the whole point
 * is to find out whether an API exists and `health()` would happily answer from the
 * fixture. Only a 200 with a JSON body counts: a static file host -- which is how `dist/`
 * is meant to be deployed -- answers `/api/health` with its own 404, and an SPA rewrite
 * answers it with `index.html` and a 200. Both mean there is no API, and calling either
 * one "live" would leave the dashboard reporting a connection it does not have while
 * every screen shows nothing.
 *
 * Never throws: an unreachable API is a supported state, not an error.
 */
export async function probe() {
  let response;
  try {
    response = await fetch("/api/health", { headers: { Accept: "application/json" } });
  } catch {
    setMode("offline");
    return null;
  }
  if (!response.ok) {
    setMode("offline");
    return null;
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    setMode("offline");
    return null;
  }
  setMode("live");
  return payload;
}
