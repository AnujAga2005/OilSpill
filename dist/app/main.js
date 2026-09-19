/** Bootstrap: build the frame, resolve the case, render the active screen.
 *
 * There is no framework and no build step. `main.js` owns the shell and the data loads;
 * each screen is a pure function from a context object to a DOM node, re-run whenever the
 * store changes. Re-rendering a whole screen on every change is affordable at this size
 * and removes the entire class of bugs where the interface and the data disagree.
 *
 * The data labels are structural rather than decorative and are painted before any screen
 * gets a chance to render: which AIS, which forcing, which detection source, which status.
 * They live in the shell so no screen can forget them.
 */

import { h, mount, announce, icon } from "./dom.js";
import { ICONS, BRAND_MARK } from "./icons.js";
import * as api from "./api.js";
import * as store from "./state.js";
import * as router from "./router.js";
import * as F from "./format.js";
import * as U from "./ui.js";
import * as X from "./exporters.js";
import { runBar } from "./progress.js";

import * as commandScreen from "./screens/command.js";
import * as analysisScreen from "./screens/analysis.js";
import * as satelliteScreen from "./screens/satellite.js";
import * as slickScreen from "./screens/slick.js";
import * as driftScreen from "./screens/drift.js";
import * as vesselsScreen from "./screens/vessels.js";
import * as methodScreen from "./screens/methodology.js";

const SCREENS = [
  {
    path: "/new",
    title: "New analysis",
    short: "New analysis",
    icon: ICONS.upload,
    module: analysisScreen,
    group: "Case",
  },
  {
    path: "/",
    title: "Command centre",
    short: "Overview",
    step: 1,
    icon: ICONS.gauge,
    module: commandScreen,
    group: "Case",
  },
  {
    path: "/imagery",
    title: "Satellite analysis",
    short: "Imagery",
    step: 2,
    icon: ICONS.satellite,
    module: satelliteScreen,
    group: "Case",
  },
  {
    path: "/slick",
    title: "Slick analysis",
    short: "Slick",
    step: 3,
    icon: ICONS.slick,
    module: slickScreen,
    group: "Case",
  },
  {
    path: "/drift",
    title: "Drift reconstruction",
    short: "Drift",
    step: 4,
    icon: ICONS.drift,
    module: driftScreen,
    group: "Case",
  },
  {
    path: "/vessels",
    title: "Vessel attribution",
    short: "Vessels",
    step: 5,
    icon: ICONS.ship,
    module: vesselsScreen,
    group: "Case",
  },
  {
    path: "/method",
    title: "Methodology and evidence",
    short: "Method",
    icon: ICONS.book,
    module: methodScreen,
    group: "Reference",
  },
];

const refs = {};
let cleanups = [];
let renderQueued = false;

// -- frame ------------------------------------------------------------------

function brand() {
  return h(
    "div",
    { class: "brand" },
    h("div", { class: "brand__mark", html: BRAND_MARK }),
    h(
      "div",
      // `setProperty` needs the CSS spelling, not the camelCase one.
      { style: { "min-width": 0 } },
      h("div", { class: "brand__name" }, "SpillTrace"),
      h("div", { class: "brand__ps" }, "SIH 26143"),
    ),
  );
}

function topbar() {
  refs.topbarTitle = h("div", { class: "topbar__title" }, "Command centre");
  refs.topbarMeta = h("div", { class: "topbar__meta" });
  refs.topbarActions = h("div", { class: "inline no-print" });
  return h(
    "div",
    { class: "topbar" },
    refs.topbarTitle,
    refs.topbarMeta,
    h("div", { class: "topbar__spacer" }),
    refs.topbarActions,
  );
}

function sidenav() {
  const groups = [];
  for (const name of ["Case", "Reference"]) {
    const items = SCREENS.filter((screen) => screen.group === name);
    groups.push(
      h(
        "nav",
        { class: "sidenav__group", "aria-label": name },
        h("div", { class: "sidenav__group-label" }, name),
        items.map((screen) =>
          h(
            "a",
            {
              class: "sidenav__link",
              href: router.href(screen.path),
              "data-path": screen.path,
            },
            screen.step ? h("span", { class: "sidenav__step" }, String(screen.step)) : null,
            icon(screen.icon, { cls: "sidenav__icon", size: 17 }),
            h("span", { class: "ellipsis" }, screen.short),
          ),
        ),
      ),
    );
  }
  refs.sidenav = h("aside", { class: "sidenav" }, groups);
  return refs.sidenav;
}

function frame() {
  refs.main = h("main", { class: "main", id: "main", tabindex: "-1" });
  return h(
    "div",
    { class: "app" },
    h("a", { class: "skip-link", href: "#main" }, "Skip to content"),
    brand(),
    topbar(),
    sidenav(),
    refs.main,
  );
}

// -- context ----------------------------------------------------------------

const COMMAND_CENTRE = SCREENS.find((screen) => screen.path === "/");

function activeScreen(route) {
  // The fallback is the Command Centre by name, not by position. `SCREENS[0]` is the
  // intake screen, and an unknown route landing on "upload something" rather than on the
  // case would be a worse answer than the one the reader asked for.
  return SCREENS.find((screen) => screen.path === route.path) || COMMAND_CENTRE;
}

function context(route) {
  const state = store.get();
  return {
    route,
    state,
    caseDoc: state.caseDoc,
    caseId: state.caseId,
    caseStatus: state.caseStatus,
    api,
    store,
    screens: SCREENS,
    /** Register teardown for anything with a lifetime, e.g. a map or a timer. */
    onCleanup(fn) {
      cleanups.push(fn);
    },
    navigate: router.go,
    setParams: router.setParams,
    href: router.href,
    reloadCase: () => loadCase(state.caseId, { force: true }),
    runAnalysis,
    cancelAnalysis,
    selectCase,
    openSavedCases,
    announce,
  };
}

// -- the intake gate --------------------------------------------------------

/**
 * Whether this tab has already been past the intake screen.
 *
 * `sessionStorage`, not a module variable: a reload has to stay where the reader was, and
 * not `localStorage`, because a machine that has run one analysis should still open on the
 * intake screen tomorrow. Wrapped because a browser with storage disabled throws on access,
 * and the worst that costs is one extra click.
 */
const PASS_KEY = "spilltrace.sawIntake";

function sawIntake() {
  try {
    return window.sessionStorage.getItem(PASS_KEY) === "1";
  } catch {
    return true;
  }
}

function markIntakeSeen() {
  try {
    window.sessionStorage.setItem(PASS_KEY, "1");
  } catch {
    /* storage disabled; the gate simply does not stick */
  }
}

/** Leave the intake screen for the Command Centre, optionally on a named case. */
function openSavedCases(caseId = null) {
  markIntakeSeen();
  // The route listener resolves the case and loads it, so nothing is loaded here: doing
  // both would fetch the same document twice.
  router.go("/", caseId ? { case: caseId, vessel: null } : {});
}

/**
 * Open on the intake screen when the reader has not asked for anything in particular.
 *
 * Four conditions, and all four have to hold. **Live only**: the offline bundle has no API
 * to upload to, so sending it to an intake screen would be sending it to a dead end -- it
 * opens on its one stored case, and "New analysis" stays in the nav where it explains
 * itself. **No `case` param**, because a pasted link names the case it wants. **The bare
 * route**, because a link to `#/drift` is a link to the drift screen. And **not already
 * past it** in this tab, so a reload does not throw the reader back to the front door.
 *
 * `replace`, not a push: the intake screen is where this tab started, not somewhere it
 * navigated to, and Back should leave the app rather than cycle through a redirect.
 */
function maybeOpenIntake() {
  const route = router.current();
  if (api.apiMode() !== "live") return;
  if (route.path !== "/" || route.get("case")) return;
  if (sawIntake()) return;
  router.go("/new", {}, { replace: true });
}

// -- rendering --------------------------------------------------------------

function render() {
  renderQueued = false;
  const route = router.current();
  const screen = activeScreen(route);
  const state = store.get();

  for (const fn of cleanups) {
    try {
      fn();
    } catch {
      /* a failed teardown must not block the next screen */
    }
  }
  cleanups = [];

  document.title = `${screen.title} · SpillTrace`;
  refs.topbarTitle.textContent = screen.title;

  for (const link of refs.sidenav.querySelectorAll(".sidenav__link")) {
    const isCurrent = link.dataset.path === screen.path;
    link.setAttribute("href", router.href(link.dataset.path));
    if (isCurrent) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }

  mount(refs.topbarMeta, topbarMeta(state, screen));
  mount(refs.topbarActions, topbarActions(state));

  const ctx = context(route);
  let body;
  try {
    body = screen.module.render(ctx);
  } catch (error) {
    // A screen that throws should not take the shell with it: the nav has to keep
    // working so the analyst can get to a screen that does render.
    console.error(`screen ${screen.path} failed to render`, error);
    body = U.failedState({
      title: "This screen could not be drawn",
      body: "The case document is loaded but this view hit an error while rendering it.",
      detail: String(error && error.message ? error.message : error),
    });
  }

  mount(
    refs.main,
    h(
      "div",
      { class: "page" },
      pageHeader(screen, state, ctx),
      body,
      U.runFooter(state.caseDoc),
    ),
  );
}

function schedule() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(render);
}

function pageHeader(screen, state, ctx) {
  const scene = state.caseDoc?.scene;
  // The intake screen is about choosing what to analyse, so it carries none of the
  // case actions: exporting, printing or emailing a report for a case the reader has not
  // asked for yet is an offer about the wrong thing. The picker goes with them -- the
  // screen has its own door to the saved cases, and one that lands on the Command Centre
  // rather than leaving the reader on the intake screen with a different case behind it.
  const caseActions = screen.path !== "/new";
  return h(
    "header",
    { class: "page__head" },
    h(
      "div",
      { class: "page__head-text" },
      h("h1", { class: "page__title" }, screen.title),
    ),
    h(
      "div",
      { class: "page__actions no-print" },
      caseActions ? caseSelector(state) : null,
      caseActions
        ? U.button("Export JSON", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.download,
            disabled: !state.caseDoc,
            onClick: () => {
              X.downloadJson(X.exportName(state.caseDoc, "case", "json"), state.caseDoc);
              announce("Case document downloaded.", { kind: "success" });
            },
          })
        : null,
      caseActions && state.caseDoc && api.apiMode() === "live"
        ? U.button("PDF Report", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.download,
            href: api.reportPdfUrl(state.caseId),
            title: "Generate and download the incident report PDF",
          })
        : null,
      caseActions && state.caseDoc && api.apiMode() === "live"
        ? U.button("Email Report", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.mail,
            onClick: () => dispatchIncidentEmail(state.caseId, announce),
            title: "Build the incident PDF and hand it to the dispatcher — the next step "
              + "states whether it will be sent or written as a .eml",
          })
        : null,
      caseActions && state.caseDoc && api.apiMode() === "live"
        ? U.button("Save analysis", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.file,
            onClick: () => nameCase(state, announce),
            title: "Give this run a name, so the case picker shows it instead of the id",
          })
        : null,
      caseActions && state.caseDoc && api.apiMode() === "live"
        ? U.button("Delete analysis", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.trash,
            onClick: () => removeCase(state, announce),
            title: "Remove this stored case, its detection mask and its previews from the "
              + "server — the scene it was built from is not touched",
          })
        : null,
      caseActions
        ? U.button("Print", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.print,
            onClick: X.printPage,
          })
        : null,
    ),
    scene && caseActions
      ? h(
          "p",
          { class: "sr-only" },
          `Scene ${scene.name}, acquired ${F.utc(scene.acquiredStartUtc)}, centred on ${
            F.latLon(sceneCentre(scene))}.`,
        )
      : null,
  );
}

function caseSelector(state) {
  const list = state.cases?.cases || [];
  if (state.casesStatus === "loading") return U.skeleton("150px", "30px");
  if (!list.length) {
    return h("span", { class: "small muted" }, state.caseId ? `Case ${state.caseId}` : "");
  }
  return h(
    "select",
    {
      class: "select",
      "aria-label": "Active case",
      onChange: (event) => selectCase(event.target.value),
    },
    list.map((entry) =>
      h(
        "option",
        { value: entry.id, selected: entry.id === state.caseId },
        // The operator's own name for the run when there is one: a shelf of ids reads as
        // scene numbers, which is exactly what a person renaming a case wanted to escape.
        `${entry.label || entry.scene || entry.id}${entry.isDemo ? " · demo" : ""}`,
      ),
    ),
  );
}

/**
 * Name the loaded case, so the picker shows that instead of its id.
 *
 * Deliberately not called "Save": the run was written to disk the moment it finished, and a
 * button that implied otherwise would be claiming to do the one thing it does not do. What
 * this writes is the name and the note — the sheet says so in as many words, because a
 * reader who believed the opposite would think an unnamed case had been lost.
 */
async function nameCase(state, announceFn) {
  const caseId = state.caseId;
  if (!caseId) return;
  const entry = (state.cases?.cases || []).find((row) => row.id === caseId);
  const current = state.caseDoc?.label || entry?.label || "";
  const currentNote = state.caseDoc?.description || entry?.description || "";

  let nameInput = null;
  let noteInput = null;
  const confirmed = await U.dialog(
    {
      title: "Name this analysis",
      lede:
        "This run is already stored on the server — it was written when the pipeline " +
        "finished. A name is what the case picker shows in place of the id.",
      confirm: "Save name",
      validate: () => ((nameInput?.value || "").trim() ? null : "Give the analysis a name."),
    },
    U.field({
      label: "Analysis name",
      value: current,
      placeholder: "e.g. Malta channel, morning pass",
      // No character count here on purpose: the cap lives on the server, and a number
      // repeated in the client is a number that can disagree with the one enforced.
      hint: "Shown in the case picker, in place of the case id.",
      ref: (node) => {
        nameInput = node;
      },
    }),
    U.field({
      label: "Description",
      value: currentNote,
      placeholder: "e.g. Re-run after the ERA5 file arrived",
      hint: "Optional. A note to yourself about why this run exists.",
      ref: (node) => {
        noteInput = node;
      },
    }),
  );
  if (!confirmed) return;

  const label = nameInput.value.trim();
  const description = noteInput.value.trim();
  try {
    await api.saveCaseLabel(caseId, { label, description });
    // Patch the loaded document rather than refetching it: the server changed two strings
    // in it and nothing else, and a re-read would redraw every map on the screen. An empty
    // description is a cleared one there, so it is removed here too.
    if (state.caseDoc) {
      const next = { ...state.caseDoc, label };
      if (description) next.description = description;
      else delete next.description;
      store.set({ caseDoc: next });
    }
    await store.load("cases", api.cases);
    announceFn(`Saved as “${label}”.`, { kind: "success" });
  } catch (error) {
    announceFn(`The name could not be saved: ${error?.message || error}`, { kind: "error" });
  }
}

/**
 * Delete the open analysis, after a confirmation that names what goes.
 *
 * Only pipeline output is removed: the case document, its cached detection mask and its
 * preview PNGs. The scene itself is untouched, which is why the dialog can promise the run
 * is repeatable -- and says the command that repeats it. Uploaded files are a separate
 * thing with a separate control, so this does not silently take those too.
 */
async function removeCase(state, announceFn) {
  const caseId = state.caseId;
  if (!caseId) return;
  const entry = (state.cases?.cases || []).find((row) => row.id === caseId);
  const name = state.caseDoc?.label || entry?.label || caseId;
  const isDemo = Boolean(entry?.isDemo);

  const confirmed = await U.dialog(
    {
      title: `Delete ${name}?`,
      lede:
        "The case document, its cached detection mask and its preview images are deleted " +
        "from this server. The scene it was built from is not touched, so the same run " +
        "can be made again.",
      confirm: "Delete analysis",
    },
    U.notice(
      isDemo
        ? "This is the case the demo walkthrough uses. Rebuild it with " +
            "`.venv/bin/python scripts/build_cases.py` before presenting."
        : "Uploaded files are not affected — Clear, on the New analysis screen, is what " +
            "removes those.",
      { kind: isDemo ? "danger" : "" },
    ),
  );
  if (!confirmed) return;

  try {
    const result = await api.deleteCase(caseId);
    await store.load("cases", api.cases);
    const remaining = store.get().cases?.cases || [];
    const next = remaining.find((row) => row.isDemo)?.id || remaining[0]?.id || null;
    announceFn(
      `${name} deleted${result?.removedPreviews ? `, with ${F.int(result.removedPreviews)} preview images` : ""}.`,
      { kind: "success" },
    );
    // Nowhere to go once the last case is gone, so go where a new one is started.
    if (next) {
      router.go("/", { case: next, vessel: null });
      await loadCase(next, { force: true });
    } else {
      store.set({ caseDoc: null, caseId: null, caseStatus: "idle" });
      router.go("/new", { case: null, vessel: null });
    }
  } catch (error) {
    announceFn(`${name} could not be deleted: ${error?.message || error}`, { kind: "error" });
  }
}

/**
 * Ask for recipients, then build the incident report and hand it to the dispatcher.
 *
 * The wording is decided by the server, not guessed here. `GET .../report` reports whether
 * an SMTP host is configured and whether the operator has narrowed where mail may go, and
 * with no SMTP host the endpoint writes a `.eml` next to the PDF and sends nothing. A button
 * that said "email sent" in that state would be a lie, so the sheet states which of the two
 * will happen before the reader commits, and the completion message repeats whichever the
 * server actually did.
 */
async function dispatchIncidentEmail(caseId, announceFn) {
  let dispatch = null;
  try {
    dispatch = (await api.report(caseId))?.dispatch || null;
  } catch {
    // Not fatal: the dispatch endpoint is the authority, and it re-checks anyway.
  }

  const willSend = dispatch?.mode === "send";
  let input = null;
  const confirmed = await U.dialog(
    {
      title: willSend ? "Email incident report" : "Prepare incident report",
      lede: willSend
        ? "The server will build the PDF and send it over SMTP."
        : dispatchExplanation(dispatch),
      confirm: willSend ? "Send report" : "Write .eml",
      validate: () => {
        const value = (input?.value || "").trim();
        if (!value) return "Enter at least one recipient address.";
        const parts = value.split(/[,;]/).map((p) => p.trim()).filter(Boolean);
        const bad = parts.find((p) => !/^[^\s@]+@[^\s@.]+\.[^\s@]+$/.test(p));
        if (bad) return `${bad} is not an email address.`;
        return null;
      },
    },
    U.field({
      label: "Recipients",
      type: "email",
      placeholder: "ops@example.gov, duty@example.gov",
      hint: dispatch?.recipientsRestricted
        ? "Comma-separated. This server is configured to refuse any address outside its own list."
        : "Comma-separated. Any valid address.",
      ref: (node) => {
        input = node;
      },
    }),
    U.notice(
      "The report names a priority candidate for investigation. It does not establish " +
        "responsibility and requires verification against licensed AIS.",
      { kind: "synthetic" },
    ),
  );
  if (!confirmed) return;

  const recipients = input.value.trim();
  try {
    const submitted = await api.dispatchEmail(caseId, { recipients });
    announceFn(`Incident report queued as job ${submitted.jobId}.`, { kind: "progress" });
    const job = await api.awaitJob(submitted, (update) => {
      if (update.state === "running") {
        announceFn("Building the incident PDF…", { kind: "progress" });
      }
    });
    const result = job.result || {};
    // The server rewrites both paths to be repository-relative before they leave the
    // process, so quoting one here cannot disclose the host's directory layout.
    const written = result.emailPath || result.reportPath;
    announceFn(
      result.dryRun
        ? `Dry run complete — ${result.reason || "sending disabled"}. Written to ${
            written || "the server log"}. Open it in Mail to see the message that would be sent.`
        : `Incident report sent to ${recipients}.`,
      {
        kind: "success",
        action: { label: "Open PDF", href: api.reportPdfUrl(caseId) },
      },
    );
  } catch (error) {
    announceFn(`Incident report not dispatched: ${error?.message || error}`, { kind: "error" });
  }
}

/** Why a dispatch will be a dry run, in the words of whichever piece is missing. */
function dispatchExplanation(dispatch) {
  if (!dispatch) return "Sending may be disabled on this server; a .eml file is written instead.";
  if (!dispatch.smtpConfigured) {
    return "No SMTP host is configured, so the server writes a .eml file beside the PDF " +
      "and sends nothing.";
  }
  return "SPILLTRACE_EMAIL_DRY_RUN is set, so the server writes a .eml file and sends nothing.";
}

function topbarMeta(state, screen) {
  const scene = state.caseDoc?.scene;
  // Nothing on the intake screen. A case is always loaded -- the app resolves one on boot --
  // but naming it here would answer a question the reader has not asked, next to a title
  // that says "New analysis". The case screens are where the identity belongs.
  if (screen?.path === "/new") return null;
  if (state.caseStatus === "loading") return U.skeleton("220px", "14px");
  if (!scene) return h("span", { class: "muted" }, "no case loaded");
  return h(
    "div",
    { class: "topbar__meta" },
    h("span", { class: "mono" }, scene.name),
    h("span", { class: "faint" }, "·"),
    h("span", null, scene.region || "region unknown"),
    h("span", { class: "faint" }, "·"),
    h("span", { class: "mono" }, F.utc(scene.acquiredStartUtc)),
  );
}

/** The scene footprint's centre, as `[lon, lat]`. There is no stored centroid. */
function sceneCentre(scene) {
  const b = scene?.bounds;
  if (!Array.isArray(b) || b.length < 4) return [];
  return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
}

function topbarActions(state) {
  const mode = api.apiMode();
  const job = state.job;
  return h(
    "div",
    { class: "inline no-print" },
    job && (job.state === "running" || job.state === "queued")
      ? runBar({
          compact: true,
          trailing: U.button("Cancel", { kind: "quiet", small: true, onClick: cancelAnalysis }),
        })
      : null,
    h(
      "span",
      {
        class: ["badge", mode === "offline" ? "badge--synthetic" : "badge--ok"],
        title:
          mode === "offline"
            ? "The API is not reachable. The bundled demo case is being replayed from static files."
            : "Connected to the local SpillTrace API.",
      },
      mode === "offline"
        ? icon(ICONS.offline, { size: 12 })
        : h("span", { class: "badge__dot" }),
      mode === "offline" ? "Offline demo" : "Live API",
    ),
  );
}

// -- data -------------------------------------------------------------------

async function loadCase(id, { force = false } = {}) {
  if (!id) return null;
  const state = store.get();
  if (!force && state.caseId === id && state.caseStatus === "ready") return state.caseDoc;
  store.set({ caseId: id });
  store.resetSelection();
  // `caseStatus` rather than the default `caseDocStatus`, so screens and the shell read the
  // same key.
  const doc = await store.load("caseDoc", () => api.loadCase(id), { statusKey: "caseStatus" });
  // Spoken, not shown: the topbar and the hero already name the case for anyone who can
  // see them, and this fires on every boot and every switch.
  if (doc) announce(`Case ${doc.scene?.name || id} loaded.`, { silent: true });
  return doc;
}

function selectCase(id) {
  if (!id || id === store.get().caseId) return;
  router.setParams({ case: id, vessel: null });
  loadCase(id);
}

/** Resolve which case the URL is asking for, falling back to the newest available. */
function resolveCaseId(route) {
  const requested = route.get("case");
  if (requested) return requested;
  const list = store.get().cases?.cases || [];
  const demo = list.find((entry) => entry.isDemo);
  return demo?.id || list[0]?.id || "demo";
}

async function runAnalysis(kind, options = {}) {
  const state = store.get();
  const scene = options.scene || state.caseDoc?.scene?.name || state.caseId;
  if (!scene) return null;
  const controller = new AbortController();
  store.set({ job: { state: "queued", kind, log: [] }, jobAbort: controller });
  store.emitProgress({ state: "queued", kind, log: [] });
  announce(`${F.label(kind)} started.`);
  try {
    const job = await api.runJob(
      kind,
      scene,
      options,
      (update) => {
        const next = { ...update, kind };
        // Every line goes to the progress bar, which repaints itself. Only a change of
        // *state* goes to the store, because that is the only thing the rest of the app
        // renders differently -- and a store change rebuilds the whole page, which at two
        // updates a second is the flicker this split exists to remove.
        store.emitProgress(next);
        if (next.state !== store.get().job?.state) store.set({ job: next });
      },
      controller.signal,
    );
    store.emitProgress({ ...job, kind });
    store.set({ job: { ...job, kind }, jobAbort: null });
    const id = job.result?.caseId || job.caseId || state.caseId;
    // `go`, not `setParams`: a run started from the intake screen has to land on the
    // finished case, and `setParams` keeps whatever path it was called from.
    markIntakeSeen();
    router.go("/", { case: id, vessel: null });
    await loadCase(id, { force: true });
    await store.load("cases", api.cases);
    announce("Analysis finished.", { kind: "success" });
    return job;
  } catch (error) {
    if (error?.name === "AbortError") {
      store.set({ job: { state: "cancelled", kind, log: [] }, jobAbort: null });
      store.emitProgress({ state: "cancelled", kind, log: [] });
      announce("Analysis cancelled.");
      return null;
    }
    store.set({
      job: { state: "failed", kind, error: String(error?.message || error), log: [] },
      jobAbort: null,
    });
    store.emitProgress({ state: "failed", kind, log: [] });
    announce(`Analysis failed: ${error?.message || error}`, { kind: "error" });
    return null;
  }
}

function cancelAnalysis() {
  store.get().jobAbort?.abort();
}

// -- start ------------------------------------------------------------------

async function boot() {
  document.body.append(frame());
  store.subscribe(schedule);
  api.onModeChange(schedule);
  router.onRoute((route) => {
    const wanted = resolveCaseId(route);
    if (wanted !== store.get().caseId) loadCase(wanted);
    else schedule();
    // Focus the content region on navigation so a keyboard user does not have to tab
    // back through the whole nav after every jump.
    refs.main.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  });

  router.start();

  // The probe settles live-versus-offline before any screen paints, so the mode badge
  // and the offline notice are right the first time rather than after a flicker.
  await api.probe();
  maybeOpenIntake();
  store.load("health", api.health);
  store.load("cases", api.cases).then(() => {
    const wanted = resolveCaseId(router.current());
    if (wanted !== store.get().caseId || store.get().caseStatus === "idle") loadCase(wanted);
  });
  store.load("metrics", api.metrics);
  store.load("scenes", api.scenes);
  loadCase(resolveCaseId(router.current()));
}

boot();
