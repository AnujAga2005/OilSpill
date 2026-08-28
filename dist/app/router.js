/** A hash router.
 *
 * Hash routing rather than the History API because the dashboard has to work from
 * `file://` and from a static directory with no rewrite rules, and `#/drift?case=demo`
 * survives both. The active case rides in the query string so a link to a screen is a link
 * to a screen *of a case* - the thing an analyst would actually paste to a colleague.
 */

const listeners = new Set();

/** Parse `#/drift?case=demo&vessel=999100000` into a route object. */
export function parse(hash = window.location.hash) {
  const raw = String(hash || "").replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const params = new URLSearchParams(query);
  const segments = path.split("/").filter(Boolean);
  return {
    path: `/${segments.join("/")}`,
    name: segments[0] || "",
    segments,
    params,
    get(key, fallback = null) {
      return params.get(key) ?? fallback;
    },
  };
}

export function current() {
  return parse();
}

/** Build a hash URL, carrying `case` forward unless it is overridden. */
export function href(path, patch = {}) {
  const params = new URLSearchParams(current().params);
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === undefined || value === "") params.delete(key);
    else params.set(key, String(value));
  }
  const query = params.toString();
  return `#${path}${query ? `?${query}` : ""}`;
}

/** Navigate. `replace` avoids stacking history entries for transient state. */
export function go(path, patch = {}, { replace = false } = {}) {
  const url = href(path, patch);
  if (url === `#${current().path}${current().params.toString() ? `?${current().params}` : ""}`) {
    return;
  }
  if (replace) window.history.replaceState(null, "", url);
  else window.location.hash = url.slice(1);
  if (replace) emit();
}

/** Update query params on the current screen without adding a history entry. */
export function setParams(patch) {
  go(current().path, patch, { replace: true });
}

function emit() {
  const route = current();
  for (const fn of [...listeners]) fn(route);
}

export function onRoute(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function start() {
  window.addEventListener("hashchange", emit);
  if (!window.location.hash) {
    window.history.replaceState(null, "", "#/");
  }
  emit();
}
