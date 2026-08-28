/** Formatters.
 *
 * Two rules run through all of this:
 *
 * * A missing value is never formatted as zero. `null`, `undefined` and `NaN` all
 *   render as an em dash and, where there is room, the reason. A spill area of "0.00
 *   km2" and one of "not measured" are different claims about the world.
 * * Every timestamp carries its zone. The pipeline works in UTC end to end, so the
 *   suffix is literal " UTC" - never a locale conversion, which would silently move an
 *   acquisition time into the reader's timezone.
 */

export const DASH = "—";

export function isMissing(value) {
  return value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value));
}

/** A number with fixed decimals and thin-space thousands grouping. */
export function num(value, decimals = 2) {
  if (isMissing(value)) return DASH;
  const n = Number(value);
  const fixed = n.toFixed(decimals);
  const [whole, fraction] = fixed.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return fraction ? `${grouped}.${fraction}` : grouped;
}

export function int(value) {
  return num(value, 0);
}

/** Square kilometres. Below 1 km2 two decimals hide the difference, so use three. */
export function km2(value) {
  if (isMissing(value)) return DASH;
  return num(value, Math.abs(Number(value)) < 1 ? 3 : 2);
}

export function km(value, decimals = 2) {
  return num(value, decimals);
}

/** A ratio in 0..1 as a percentage. */
export function pct(value, decimals = 1) {
  if (isMissing(value)) return DASH;
  return `${num(Number(value) * 100, decimals)}%`;
}

/** A metric already expressed in 0..1 (IoU, Dice) at the precision it was measured. */
export function metric(value, decimals = 4) {
  return num(value, decimals);
}

/** Signed degrees to a degrees/minutes string with a hemisphere letter. */
export function lat(value) {
  if (isMissing(value)) return DASH;
  return dms(Math.abs(value), Number(value) >= 0 ? "N" : "S");
}

export function lon(value) {
  if (isMissing(value)) return DASH;
  return dms(Math.abs(value), Number(value) >= 0 ? "E" : "W");
}

function dms(absolute, hemisphere) {
  const degrees = Math.floor(absolute);
  const minutes = (absolute - degrees) * 60;
  return `${degrees}° ${minutes.toFixed(3)}' ${hemisphere}`;
}

/** Decimal degrees, which is what the pipeline actually stores. */
export function coord(pair, decimals = 6) {
  if (!Array.isArray(pair) || pair.length < 2 || isMissing(pair[0]) || isMissing(pair[1])) {
    return DASH;
  }
  return `${num(pair[0], decimals)}, ${num(pair[1], decimals)}`;
}

/** Human coordinate pair: latitude first, as an operator reads it. */
export function latLon(pair) {
  if (!Array.isArray(pair) || pair.length < 2) return DASH;
  return `${lat(pair[1])}  ${lon(pair[0])}`;
}

/**
 * An ISO instant as `YYYY-MM-DD HH:MM UTC`.
 * The pipeline emits UTC, so the string is reformatted, never re-zoned.
 */
export function utc(value, { seconds = false } = {}) {
  if (!value || typeof value !== "string") return "unknown";
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/,
  );
  if (!match) return value;
  const [, y, mo, d, hh, mm, ss] = match;
  const time = seconds && ss ? `${hh}:${mm}:${ss}` : `${hh}:${mm}`;
  return `${y}-${mo}-${d} ${time} UTC`;
}

/** Just the date part, for column headers where the time is noise. */
export function utcDate(value) {
  if (!value || typeof value !== "string") return "unknown";
  return value.slice(0, 10);
}

/** A duration in hours, phrased the way an operator would say it. */
export function hours(value) {
  if (isMissing(value)) return DASH;
  const n = Math.abs(Number(value));
  if (n < 1) return `${num(n * 60, 0)} min`;
  if (n < 48) return `${num(n, n % 1 === 0 ? 0 : 1)} h`;
  return `${num(n / 24, 1)} d`;
}

export function seconds(value) {
  if (isMissing(value)) return DASH;
  const n = Number(value);
  if (n < 1) return `${num(n * 1000, 0)} ms`;
  if (n < 90) return `${num(n, n < 10 ? 2 : 1)} s`;
  return `${num(n / 60, 1)} min`;
}

export function bytes(value) {
  if (isMissing(value)) return DASH;
  const units = ["B", "kB", "MB", "GB"];
  let n = Number(value);
  let index = 0;
  while (n >= 1024 && index < units.length - 1) {
    n /= 1024;
    index += 1;
  }
  return `${num(n, index === 0 ? 0 : 1)} ${units[index]}`;
}

/** A bearing in degrees, with the compass point that contains it. */
export function bearing(value) {
  if (isMissing(value)) return DASH;
  const points = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const deg = ((Number(value) % 360) + 360) % 360;
  return `${num(deg, 1)}° ${points[Math.round(deg / 22.5) % 16]}`;
}

/** An axis orientation, which is folded to 0-180 and so has two compass points. */
export function axis(value) {
  if (isMissing(value)) return DASH;
  const deg = ((Number(value) % 180) + 180) % 180;
  return `${num(deg, 1)}°`;
}

/** Title-case a snake_case or kebab-case key for display. */
export function label(key) {
  if (!key) return DASH;
  return String(key)
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^./, (c) => c.toUpperCase());
}

/** Truncate for a single-line cell, on a word boundary where possible. */
export function clip(text, max = 80) {
  if (!text) return DASH;
  const s = String(text);
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** A filename-safe slug for downloads. */
export function slug(text) {
  return String(text || "case")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "") || "case";
}
