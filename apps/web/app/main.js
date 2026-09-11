/** Bootstrap: build the frame, resolve the case, render the active screen.
 *
 * There is no framework and no build step. `main.js` owns the shell and the data loads;
 * each screen is a pure function from a context object to a DOM node, re-run whenever the
 * store changes. Re-rendering a whole screen on every change is affordable at this size
 * and removes the entire class of bugs where the interface and the data disagree.
 *
 * Two disclosures are structural rather than decorative and are painted before any screen
 * gets a chance to render: the synthetic-AIS label and the research-status label. They live
 * in the shell so no screen can forget them.
 */

import { h, mount, announce, icon } from "./dom.js";
import { ICONS, BRAND_MARK } from "./icons.js";
import * as api from "./api.js";
import * as store from "./state.js";
import * as router from "./router.js";
import * as F from "./format.js";
import * as U from "./ui.js";
import * as X from "./exporters.js";

import * as commandScreen from "./screens/command.js";
import * as satelliteScreen from "./screens/satellite.js";
import * as slickScreen from "./screens/slick.js";
import * as driftScreen from "./screens/drift.js";
import * as vesselsScreen from "./screens/vessels.js";
import * as methodScreen from "./screens/methodology.js";

const SCREENS = [
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

function activeScreen(route) {
  return SCREENS.find((screen) => screen.path === route.path) || SCREENS[0];
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
    announce,
  };
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

  mount(refs.topbarMeta, topbarMeta(state));
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
      disclosures(state),
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
  return h(
    "header",
    { class: "page__head" },
    h(
      "div",
      { class: "page__head-text" },
      h(
        "div",
        { class: "page__eyebrow" },
        screen.step ? `Step ${screen.step} of 5` : "Reference",
      ),
      h("h1", { class: "page__title" }, screen.title),
      h(
        "p",
        { class: "page__lede" },
        screen.module.LEDE || "",
      ),
    ),
    h(
      "div",
      { class: "page__actions no-print" },
      caseSelector(state),
      U.button("Export JSON", {
        kind: "quiet",
        small: true,
        iconPath: ICONS.download,
        disabled: !state.caseDoc,
        onClick: () => {
          X.downloadJson(X.exportName(state.caseDoc, "case", "json"), state.caseDoc);
          announce("Case document downloaded.", { kind: "success" });
        },
      }),
      state.caseDoc && api.apiMode() === "live"
        ? U.button("PDF Report", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.download,
            href: api.reportPdfUrl(state.caseId),
            title: "Generate and download the incident report PDF",
          })
        : null,
      state.caseDoc && api.apiMode() === "live"
        ? U.button("Email Report", {
            kind: "quiet",
            small: true,
            iconPath: ICONS.mail,
            onClick: () => dispatchIncidentEmail(state.caseId, announce),
            title: "Build the incident PDF and hand it to the dispatcher — the next step "
              + "states whether it will be sent or written as a .eml",
          })
        : null,
      U.button("Print", {
        kind: "quiet",
        small: true,
        iconPath: ICONS.print,
        onClick: X.printPage,
      }),
    ),
    scene
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
        `${entry.scene || entry.id}${entry.isDemo ? " · demo" : ""}`,
      ),
    ),
  );
}

/**
 * Ask for recipients, then build the incident report and hand it to the dispatcher.
 *
 * The wording is decided by the server, not guessed here. `GET .../report` reports
 * whether an SMTP host and a recipient allowlist are configured, and with either one
 * missing the endpoint writes a `.eml` next to the PDF and sends nothing. A button that
 * said "email sent" in that state would be a lie, so the sheet states which of the two
 * will happen before the reader commits, and the completion message repeats whichever
 * the server actually did.
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
      hint: "Comma-separated. The server refuses any address outside its allowlist.",
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
  if (!dispatch.recipientsConfigured) {
    return "SPILLTRACE_ALERT_RECIPIENTS is unset, so nothing may be sent; the server " +
      "writes a .eml file beside the PDF.";
  }
  return "SPILLTRACE_EMAIL_DRY_RUN is set, so the server writes a .eml file and sends nothing.";
}

function topbarMeta(state) {
  const scene = state.caseDoc?.scene;
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
      ? h(
          "span",
          { class: "inline small muted" },
          h("span", { class: "spinner" }),
          h("span", null, jobStageLabel(job)),
          U.button("Cancel", { kind: "quiet", small: true, onClick: cancelAnalysis }),
        )
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

/**
 * The line shown beside a running job's spinner.
 *
 * A job record carries `message` -- its most recent progress line -- and `log`, all of them
 * up to the server's cap. Both are plain sentences written by the pipeline stage that is
 * running, so this shows that text instead of guessing at a stage name from the client side.
 * Clipped because it goes in a single-line header slot.
 */
function jobStageLabel(job) {
  const line = job.message || (job.log || []).at(-1);
  return line ? F.clip(String(line), 44) : "working";
}

/**
 * The disclosures every screen inherits. Not dismissible: the AIS label and the research
 * status are conditions of the data, not notifications about it.
 */
function disclosures(state) {
  const caseDoc = state.caseDoc;
  if (!caseDoc) return null;
  const items = [];

  if (caseDoc.ais?.label) {
    items.push({
      kind: "synthetic",
      label: caseDoc.ais.label,
      text: caseDoc.ais.disclaimer || "",
    });
  }
  if (caseDoc.forcing?.mode === "synthetic") {
    items.push({
      kind: "synthetic",
      label: caseDoc.forcing.label || "Synthetic forcing.",
      text:
        caseDoc.forcing.forcing?.warning ||
        "Drift is driven by a deterministic synthetic current field because the supplied " +
          "reanalysis does not cover this acquisition time.",
    });
  }
  if (api.apiMode() === "offline") {
    items.push({
      label: "Offline.",
      text:
        "The API is not reachable, so this is the bundled demo case replayed from static " +
        "files. New analyses cannot be started until the API is running.",
    });
  }
  return U.disclosureBar(items);
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
  announce(`${F.label(kind)} started.`);
  try {
    const job = await api.runJob(
      kind,
      scene,
      options,
      (update) => store.set({ job: { ...update, kind } }),
      controller.signal,
    );
    store.set({ job: { ...job, kind }, jobAbort: null });
    const id = job.result?.caseId || job.caseId || state.caseId;
    router.setParams({ case: id });
    await loadCase(id, { force: true });
    await store.load("cases", api.cases);
    announce("Analysis finished.", { kind: "success" });
    return job;
  } catch (error) {
    if (error?.name === "AbortError") {
      store.set({ job: { state: "cancelled", kind, log: [] }, jobAbort: null });
      announce("Analysis cancelled.");
      return null;
    }
    store.set({
      job: { state: "failed", kind, error: String(error?.message || error), log: [] },
      jobAbort: null,
    });
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
