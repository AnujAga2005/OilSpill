/** The intake screen: choose what to analyse.
 *
 * This is where the app opens when an API is running and nothing has been asked for yet,
 * because the first question a detection product has to answer is "on what?". Two doors,
 * side by side and neither hidden: hand it a scene, or open the runs already on this
 * server. Nothing is computed here — the form stages files and hands the job to
 * `ctx.runAnalysis`, which is the same path the Command Centre's foldout used.
 *
 * The whole form is inside the inverted panel, and the panel is kept short enough to read
 * in one look: files, the four fields, then the button. Above them is a title and three
 * chips and nothing else, because the panel is the first thing on the app's first screen
 * and what belongs in that position is the thing the screen is for, not a paragraph about
 * it. What explains the run rather than configures it — the four named stages and the shelf
 * of finished runs — is in ordinary cards below.
 *
 * The screen renders identically offline, with one difference it cannot hide: there is no
 * API to upload to, so the panel holds the command that starts one and the card that
 * describes a run is not rendered at all. The saved-cases door still works — the offline
 * bundle ships one stored case, and that is the whole point of it.
 */

import { h, icon } from "../dom.js";
import { ICONS } from "../icons.js";
import * as F from "../format.js";
import * as U from "../ui.js";
import { uploadPanel, UPLOAD_NOTE } from "../upload.js";

/** What the run actually does, in the order it does it. Four of the ten stages, named. */
const PIPELINE = [
  {
    title: "Detect",
    body: "a U-Net segments the slick from the VV and VH backscatter",
    iconKey: "satellite",
    tone: "oil",
  },
  {
    title: "Measure",
    body: "every region's area and extent, computed on the sphere",
    iconKey: "slick",
    tone: "reference",
  },
  {
    title: "Hindcast",
    body: "particles run backwards to a plausible release zone",
    iconKey: "drift",
    tone: "drift",
  },
  {
    title: "Rank",
    body: "vessels near that zone, scored component by component",
    iconKey: "ship",
    tone: "synthetic",
  },
];

export function render(ctx) {
  const offline = ctx.api.apiMode() === "offline";

  return h(
    "div",
    { class: "stack" },
    intakeHero(offline, uploadPanel(ctx)),
    offline ? null : pipelineCard(),
    savedCasesCard(ctx),
  );
}

/** The inverted panel: what this screen is for, with the thing it is for inside it. */
function intakeHero(offline, panel) {
  return h(
    "section",
    { class: "hero intake-hero" },
    // The chips sit beside the prose rather than under it. They are three short facts about
    // the run, and a row of its own for them is a row the form does not get. There is no
    // standfirst under the title either: the files group is labelled "only the scene is
    // required" and each optional card opens with the word "Optional", so a paragraph
    // saying so again would cost the panel a row to repeat what it already says.
    h(
      "div",
      { class: "hero__main intake-hero__head" },
      h(
        "div",
        { class: "intake-hero__lede" },
        h("div", { class: "hero__label" }, "New analysis"),
        h("h2", { class: "intake-hero__title" }, "One Sentinel-1 scene in, a full case out."),
      ),
      h(
        "div",
        { class: "hero__chips" },
        offline
          ? U.chip("Offline bundle · no pipeline to run here", {
              iconPath: ICONS.offline,
              tone: "synthetic",
            })
          : U.chip("Runs on this machine", { iconPath: ICONS.check, tone: "agree" }),
        U.chip("Nothing leaves the server", { iconPath: ICONS.info }),
        U.chip("≈ 20 s on a 2048 × 2048 scene", { iconPath: ICONS.clock }),
      ),
    ),
    panel,
  );
}

/** The detail the panel no longer carries: what the twenty seconds are spent on. */
function pipelineCard() {
  return U.card(
    "What the run does",
    { id: "pipeline-intro", hint: "ten stages, four of them named", note: UPLOAD_NOTE },
    h(
      "ol",
      { class: "intake-steps" },
      PIPELINE.map((step, index) =>
        h(
          "li",
          { class: "intake-step" },
          h(
            "span",
            { class: ["icon-tile", "intake-step__mark", `icon-tile--${step.tone}`] },
            icon(ICONS[step.iconKey], { size: 16 }),
            h("span", { class: "intake-step__num" }, String(index + 1)),
          ),
          h(
            "span",
            { class: "intake-step__text" },
            h("span", { class: "intake-step__title" }, step.title),
            h("span", { class: "intake-step__body" }, step.body),
          ),
        ),
      ),
    ),
  );
}

/** The other door: everything already computed on this server. */
function savedCasesCard(ctx) {
  const { state } = ctx;
  const list = state.cases?.cases || [];
  const loading = state.casesStatus === "loading";

  return U.card(
    "Or open a case that has already been run",
    {
      id: "saved",
      hint: loading
        ? null
        : `${F.int(list.length)} stored case${list.length === 1 ? "" : "s"}`,
    },
    h(
      "div",
      { class: "stack stack--tight" },
      h(
        "p",
        { class: "small muted", style: { margin: "0" } },
        list.length
          ? "A finished run is a document on disk, so it opens instantly and reads exactly " +
              "as it did when it was computed. Use the picker in the header to move between them."
          : "Nothing has been computed yet. Run a scene above, or build the seeded demo case " +
              "to see the finished product on a scene from the supplied dataset.",
      ),
      loading ? U.skeleton("220px", "30px") : null,
      !loading && list.length
        ? h(
            "ul",
            { class: "case-list" },
            list.slice(0, 6).map((entry) =>
              h(
                "li",
                { class: "case-list__item" },
                h(
                  "a",
                  {
                    class: "case-list__link",
                    // A real href, so the row can be middle-clicked and copied like any
                    // link. The handler only records that the intake screen has been seen;
                    // it resolves to the same URL, so the navigation happens once.
                    href: ctx.href("/", { case: entry.id, vessel: null }),
                    onClick: () => ctx.openSavedCases(entry.id),
                  },
                  h("span", { class: "case-list__name ellipsis" }, entry.label || entry.scene || entry.id),
                  entry.isDemo ? U.badge("demo", "") : null,
                  icon(ICONS.arrowRight, { cls: "case-list__go", size: 14 }),
                ),
                h(
                  "span",
                  { class: "case-list__meta small muted ellipsis" },
                  [
                    entry.region,
                    Number.isFinite(entry.totalAreaKm2) ? `${F.km2(entry.totalAreaKm2)} km²` : null,
                    F.utc(entry.acquiredStartUtc),
                  ]
                    .filter(Boolean)
                    .join(" · "),
                ),
              ),
            ),
          )
        : null,
      h(
        "div",
        { class: "inline" },
        U.button("Load previous saved cases", {
          kind: list.length ? "primary" : "quiet",
          iconPath: ICONS.layers,
          disabled: loading,
          title: "Open the Command Centre on the most recent stored case",
          onClick: () => ctx.openSavedCases(),
        }),
      ),
      list.length > 6
        ? h(
            "p",
            { class: "small muted", style: { margin: "0" } },
            `${F.int(list.length - 6)} more in the case picker.`,
          )
        : null,
    ),
  );
}
