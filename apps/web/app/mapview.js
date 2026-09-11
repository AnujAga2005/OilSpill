/** The map: a canvas, an equirectangular projection, and no tile server.
 *
 * There is no basemap provider here and no MapLibre. The SAR preview raster *is* the
 * basemap: the pipeline already knows its corner coordinates, so the image is drawn as a
 * georeferenced rectangle and every vector overlay is projected into the same frame. That
 * keeps the whole dashboard offline-capable, keeps the imagery and the geometry in exact
 * registration - they came out of the same array - and avoids showing a coastline from a
 * different source next to a detection, which would invite the reader to trust an
 * alignment nobody checked.
 *
 * Projection: equirectangular, with longitudes scaled by cos(latitude of the view centre)
 * so a scene near 25 degrees north is not stretched sideways. Over a single Sentinel-1
 * subscene - about 20 km across - the error against a proper UTM grid is far below one
 * pixel, and the georeferenced raster stays an axis-aligned rectangle, which is what makes
 * `drawImage` legitimate rather than approximate.
 *
 * The viewport fits the union of the scene footprint and every overlay, because drift
 * corridors and synthetic vessel tracks routinely run well outside the image.
 */

import { h } from "./dom.js";
import { ICONS } from "./icons.js";

const KM_PER_DEG_LAT = 111.32;
const MIN_SCALE = 4; // px per degree of latitude
const MAX_SCALE = 4_000_000;

/**
 * @param {HTMLElement} container - gets `.map`; sized by the caller
 * @param {object} options
 * @param {(hit: object|null) => void} [options.onSelect]
 * @param {boolean} [options.graticule=true]
 * @param {boolean} [options.readout=true]
 */
export function createMap(container, options = {}) {
  const canvas = h("canvas", { class: "map__canvas", tabindex: "0", role: "application" });
  const tip = h("div", { class: "map__tip", hidden: true });
  const readout = options.readout === false ? null : h("div", { class: "map__readout" }, "—");
  const scaleBar = h(
    "div",
    { class: "map__scale" },
    h("span", { class: "map__scale-text" }, ""),
    h("div", { class: "map__scale-bar", style: { width: "60px" } }),
  );

  container.classList.add("map");
  container.append(canvas, tip, scaleBar);
  if (readout) container.append(readout);
  container.append(controls());

  const context = canvas.getContext("2d");

  /** @type {{image: HTMLImageElement|null, bounds: number[]|null, opacity: number}[]} */
  let rasters = [];
  /** @type {object[]} */
  let vectors = [];
  let view = { lon: 0, lat: 0, scale: 1000 }; // centre and px per degree latitude
  let fitted = null; // the bounds the current view was fitted to
  let hovered = null;
  let size = { w: 1, h: 1 };
  let frame = 0;

  // -- projection ----------------------------------------------------------

  function cosLat() {
    return Math.max(0.05, Math.cos((view.lat * Math.PI) / 180));
  }

  function toScreen(lon, lat) {
    const k = cosLat();
    return [
      (lon - view.lon) * k * view.scale + size.w / 2,
      (view.lat - lat) * view.scale + size.h / 2,
    ];
  }

  function toLonLat(x, y) {
    const k = cosLat();
    return [
      (x - size.w / 2) / (k * view.scale) + view.lon,
      view.lat - (y - size.h / 2) / view.scale,
    ];
  }

  /** Kilometres per screen pixel, measured along a meridian. */
  function kmPerPixel() {
    return KM_PER_DEG_LAT / view.scale;
  }

  // -- view ----------------------------------------------------------------

  /** Read the canvas's laid-out size into `size`.
   *
   * `draw()` also does this, but `draw()` runs on a rAF and every fit needs the real size
   * *now*: `fit()` divides the canvas dimensions by the span it is framing, so computing one
   * before the first frame divides by the initial 1x1 and clamps to MIN_SCALE -- a 2000 km
   * view of a 16 km scene. A screen that sets its layers and fits in the same frame, which
   * is all of them, hit that on every load.
   */
  function measure() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    // Before the element is laid out both are 0. Keep the old size rather than adopting a
    // zero, so a fit issued that early is merely stale rather than degenerate, and the
    // ResizeObserver re-runs it as soon as there is a box to measure.
    if (width > 0 && height > 0) size = { w: width, h: height };
  }

  /** Fit `[west, south, east, north]` with a margin. */
  function fit(bounds, pad = 0.08) {
    if (!bounds || bounds.length < 4) return;
    measure();
    const [west, south, east, north] = bounds;
    const lon = (west + east) / 2;
    const lat = (south + north) / 2;
    const k = Math.max(0.05, Math.cos((lat * Math.PI) / 180));
    const spanLon = Math.max(east - west, 1e-6) * k;
    const spanLat = Math.max(north - south, 1e-6);
    const scale = Math.min(
      size.w / (spanLon * (1 + pad * 2)),
      size.h / (spanLat * (1 + pad * 2)),
    );
    view = { lon, lat, scale: clamp(scale, MIN_SCALE, MAX_SCALE) };
    // What the view was fitted to, so a resize or a late-arriving raster can re-fit.
    // Recording the *intent* rather than only the bounds matters: a screen that framed
    // its findings must re-frame its findings, not the extent they happened to have
    // before the rest of them were painted.
    fitted = { bounds: bounds.slice(), pad, mode: "explicit" };
    schedule();
  }

  /** Re-run whichever fit produced the current view, against freshly measured bounds. */
  function refit() {
    if (!fitted) return schedule();
    if (fitted.mode === "content") return api.fitContent(fitted.pad);
    if (fitted.mode === "findings") return api.fitFindings(fitted.pad);
    return fit(fitted.bounds, fitted.pad);
  }

  /** The union of vector coordinates, and optionally the raster footprints too.
   *
   * The distinction matters because the backdrop is the whole 2048 px acquisition while
   * the finding is usually a few kilometres of it. Framing the union of both puts a
   * slick that is 22% of the scene height on screen as a sliver. `includeRasters: false`
   * frames the findings and lets the imagery run off the edges, which is what every
   * screen that exists to show one slick, one corridor or one shortlist actually wants.
   */
  function contentBounds({ includeRasters = true } = {}) {
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;

    const eat = (lon, lat) => {
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;
      if (lon < west) west = lon;
      if (lon > east) east = lon;
      if (lat < south) south = lat;
      if (lat > north) north = lat;
    };

    if (includeRasters) {
      for (const raster of rasters) {
        if (!raster.bounds) continue;
        eat(raster.bounds[0], raster.bounds[1]);
        eat(raster.bounds[2], raster.bounds[3]);
      }
    }
    for (const vector of vectors) {
      if (vector.hidden) continue;
      if (vector.type === "polygon") {
        for (const ring of vector.rings || []) for (const p of ring) eat(p[0], p[1]);
      } else if (vector.type === "line") {
        for (const p of vector.path || []) eat(p[0], p[1]);
      } else if (vector.type === "point") {
        eat(vector.at?.[0], vector.at?.[1]);
      } else if (vector.type === "ring") {
        const dLat = (vector.radiusKm || 0) / KM_PER_DEG_LAT;
        const k = Math.max(0.05, Math.cos(((vector.at?.[1] || 0) * Math.PI) / 180));
        eat(vector.at?.[0] - dLat / k, vector.at?.[1] - dLat);
        eat(vector.at?.[0] + dLat / k, vector.at?.[1] + dLat);
      }
    }
    if (!Number.isFinite(west)) return null;
    // A degenerate extent - a single point - still needs a window to sit in.
    if (east - west < 1e-5) { west -= 5e-6; east += 5e-6; }
    if (north - south < 1e-5) { south -= 5e-6; north += 5e-6; }
    return [west, south, east, north];
  }

  function zoomBy(factor, anchorX, anchorY) {
    const ax = anchorX ?? size.w / 2;
    const ay = anchorY ?? size.h / 2;
    const [lonBefore, latBefore] = toLonLat(ax, ay);
    view.scale = clamp(view.scale * factor, MIN_SCALE, MAX_SCALE);
    const [lonAfter, latAfter] = toLonLat(ax, ay);
    view.lon += lonBefore - lonAfter;
    view.lat += latBefore - latAfter;
    fitted = null;
    schedule();
  }

  // -- drawing -------------------------------------------------------------

  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      draw();
    });
  }

  function draw() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = canvas.clientWidth || 1;
    const height = canvas.clientHeight || 1;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    measure();
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    context.fillStyle = "#05070a";
    context.fillRect(0, 0, width, height);

    for (const raster of rasters) drawRaster(raster);
    if (options.graticule !== false) drawGraticule();

    const ordered = vectors
      .filter((v) => !v.hidden)
      .map((v, i) => ({ v, i }))
      .sort((a, b) => (a.v.z || 0) - (b.v.z || 0) || a.i - b.i);
    for (const { v } of ordered) drawVector(v);

    updateScaleBar();
  }

  function drawRaster(raster) {
    const { image, bounds, opacity = 1 } = raster;
    if (!image || !image.complete || !image.naturalWidth || !bounds) return;
    const [x0, y0] = toScreen(bounds[0], bounds[3]); // north-west
    const [x1, y1] = toScreen(bounds[2], bounds[1]); // south-east
    context.save();
    context.globalAlpha = opacity;
    // Smoothing off when zoomed past the raster's own resolution: a 512 px preview blown
    // up to 2000 px should look like the pixels it is, not like a soft photograph that
    // implies detail the data does not have.
    context.imageSmoothingEnabled = x1 - x0 < image.naturalWidth * 1.5;
    context.drawImage(image, x0, y0, x1 - x0, y1 - y0);
    context.restore();
  }

  function drawGraticule() {
    const [westLon, northLat] = toLonLat(0, 0);
    const [eastLon, southLat] = toLonLat(size.w, size.h);
    const step = niceStep((northLat - southLat) / 4);
    if (!Number.isFinite(step) || step <= 0) return;

    context.save();
    context.strokeStyle = "rgba(255,255,255,0.07)";
    context.lineWidth = 1;
    context.fillStyle = "rgba(255,255,255,0.34)";
    context.font = "9px ui-monospace, SFMono-Regular, monospace";

    for (let lat = Math.ceil(southLat / step) * step; lat <= northLat; lat += step) {
      const [, y] = toScreen(0, lat);
      line(0, y, size.w, y);
      context.fillText(`${lat.toFixed(decimalsFor(step))}°`, 4, y - 3);
    }
    for (let lon = Math.ceil(westLon / step) * step; lon <= eastLon; lon += step) {
      const [x] = toScreen(lon, 0);
      line(x, 0, x, size.h);
      context.fillText(`${lon.toFixed(decimalsFor(step))}°`, x + 3, size.h - 5);
    }
    context.restore();
  }

  function line(x0, y0, x1, y1) {
    context.beginPath();
    context.moveTo(x0, y0);
    context.lineTo(x1, y1);
    context.stroke();
  }

  function drawVector(vector) {
    const active = vector.id && vector.id === hovered?.id;
    context.save();
    context.lineJoin = "round";
    context.lineCap = "round";
    context.setLineDash(vector.dash || []);
    context.strokeStyle = vector.stroke || "#ffffff";
    context.lineWidth = (vector.width || 1.4) * (active ? 1.9 : 1);
    context.globalAlpha = vector.opacity ?? 1;

    if (vector.type === "polygon") {
      context.beginPath();
      for (const ring of vector.rings || []) {
        if (ring.length < 2) continue;
        ring.forEach((p, index) => {
          const [x, y] = toScreen(p[0], p[1]);
          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.closePath();
      }
      if (vector.fill) {
        context.fillStyle = vector.fill;
        // Non-zero would flood the holes the geometry stage measured; even-odd keeps a
        // ring inside a ring as a hole, which is what the area figure already assumes.
        context.fill("evenodd");
      }
      if (vector.stroke) context.stroke();
    } else if (vector.type === "line") {
      const path = vector.path || [];
      if (path.length >= 2) {
        context.beginPath();
        path.forEach((p, index) => {
          const [x, y] = toScreen(p[0], p[1]);
          if (index === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        });
        context.stroke();
        if (vector.arrow) arrowHead(path, vector);
        if (vector.endDot) {
          const [x, y] = toScreen(path.at(-1)[0], path.at(-1)[1]);
          dot(x, y, vector.endDot, vector.stroke);
        }
      }
    } else if (vector.type === "point") {
      const [x, y] = toScreen(vector.at[0], vector.at[1]);
      if (vector.cross) {
        context.beginPath();
        context.moveTo(x - 7, y); context.lineTo(x + 7, y);
        context.moveTo(x, y - 7); context.lineTo(x, y + 7);
        context.stroke();
      }
      dot(x, y, (vector.r || 4) * (active ? 1.4 : 1), vector.fill || vector.stroke, vector.stroke);
      if (vector.label && (active || vector.alwaysLabel)) labelAt(x, y, vector.label, vector.stroke);
    } else if (vector.type === "ring") {
      const [x, y] = toScreen(vector.at[0], vector.at[1]);
      const radius = ((vector.radiusKm || 0) / kmPerPixel());
      if (radius > 0.5) {
        context.beginPath();
        context.arc(x, y, radius, 0, Math.PI * 2);
        if (vector.fill) { context.fillStyle = vector.fill; context.fill(); }
        if (vector.stroke) context.stroke();
      }
    }
    context.restore();
  }

  function arrowHead(path, vector) {
    // Anchor on the last segment that is long enough on screen to have a direction.
    let tip = null;
    let tail = null;
    for (let i = path.length - 1; i > 0; i -= 1) {
      const a = toScreen(path[i][0], path[i][1]);
      const b = toScreen(path[i - 1][0], path[i - 1][1]);
      if (Math.hypot(a[0] - b[0], a[1] - b[1]) > 5) { tip = a; tail = b; break; }
    }
    if (!tip) return;
    const angle = Math.atan2(tip[1] - tail[1], tip[0] - tail[0]);
    const length = vector.arrow === true ? 8 : vector.arrow;
    context.save();
    context.setLineDash([]);
    context.beginPath();
    context.moveTo(tip[0], tip[1]);
    context.lineTo(
      tip[0] - length * Math.cos(angle - 0.45),
      tip[1] - length * Math.sin(angle - 0.45),
    );
    context.lineTo(
      tip[0] - length * Math.cos(angle + 0.45),
      tip[1] - length * Math.sin(angle + 0.45),
    );
    context.closePath();
    context.fillStyle = vector.stroke;
    context.fill();
    context.restore();
  }

  function dot(x, y, radius, fill, stroke) {
    context.save();
    context.setLineDash([]);
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = fill || "#fff";
    context.fill();
    if (stroke) {
      context.strokeStyle = "rgba(5,7,10,0.85)";
      context.lineWidth = 1.25;
      context.stroke();
    }
    context.restore();
  }

  function labelAt(x, y, text, colour) {
    context.save();
    context.setLineDash([]);
    context.font = "500 11px -apple-system, BlinkMacSystemFont, system-ui, sans-serif";
    const width = context.measureText(text).width;
    context.fillStyle = "rgba(5,7,10,0.78)";
    context.fillRect(x + 8, y - 9, width + 10, 17);
    context.fillStyle = colour || "#fff";
    context.fillText(text, x + 13, y + 3);
    context.restore();
  }

  function updateScaleBar() {
    const target = Math.min(120, size.w * 0.22);
    const km = niceStep(target * kmPerPixel());
    const pixels = km / kmPerPixel();
    scaleBar.querySelector(".map__scale-text").textContent =
      km >= 1 ? `${trim(km)} km` : `${trim(km * 1000)} m`;
    scaleBar.querySelector(".map__scale-bar").style.width = `${Math.round(pixels)}px`;
  }

  // -- hit testing ---------------------------------------------------------

  function hitTest(x, y, tolerance = 9) {
    let best = null;
    for (const vector of vectors) {
      if (vector.hidden || !vector.pickable) continue;
      let distance = Infinity;
      if (vector.type === "point") {
        const [px, py] = toScreen(vector.at[0], vector.at[1]);
        distance = Math.hypot(px - x, py - y);
      } else if (vector.type === "line") {
        const path = vector.path || [];
        for (let i = 1; i < path.length; i += 1) {
          const a = toScreen(path[i - 1][0], path[i - 1][1]);
          const b = toScreen(path[i][0], path[i][1]);
          distance = Math.min(distance, pointToSegment(x, y, a[0], a[1], b[0], b[1]));
        }
      } else if (vector.type === "polygon") {
        for (const ring of vector.rings || []) {
          for (let i = 1; i < ring.length; i += 1) {
            const a = toScreen(ring[i - 1][0], ring[i - 1][1]);
            const b = toScreen(ring[i][0], ring[i][1]);
            distance = Math.min(distance, pointToSegment(x, y, a[0], a[1], b[0], b[1]));
          }
        }
      }
      if (distance <= tolerance && (!best || distance < best.distance)) {
        best = { id: vector.id, label: vector.label, kind: vector.kind, distance, vector };
      }
    }
    return best;
  }

  // -- interaction ---------------------------------------------------------

  let dragging = null;
  /**
   * An optional gesture claim, used by the slick screen's boundary editor. When `down`
   * returns truthy the map stops panning and hit-testing for the rest of the gesture, so
   * dragging a vertex does not also drag the view.
   */
  let gesture = null;
  let claimed = false;

  /** Pointer position in canvas pixels and in degrees. */
  function pointerAt(event) {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const [lon, lat] = toLonLat(x, y);
    return { x, y, lon, lat };
  }

  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    const point = pointerAt(event);
    claimed = Boolean(gesture?.down?.(point, event));
    if (claimed) {
      event.preventDefault();
      return;
    }
    dragging = { x: event.clientX, y: event.clientY, lon: view.lon, lat: view.lat, moved: 0 };
  });

  canvas.addEventListener("pointermove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    if (claimed) {
      gesture?.move?.(pointerAt(event), event);
      return;
    }

    if (dragging) {
      const dx = event.clientX - dragging.x;
      const dy = event.clientY - dragging.y;
      dragging.moved = Math.max(dragging.moved, Math.hypot(dx, dy));
      view.lon = dragging.lon - dx / (cosLat() * view.scale);
      view.lat = dragging.lat + dy / view.scale;
      fitted = null;
      schedule();
      return;
    }

    const [lon, lat] = toLonLat(x, y);
    if (readout) {
      readout.textContent = `${lat >= 0 ? "N" : "S"} ${Math.abs(lat).toFixed(5)}°   ${
        lon >= 0 ? "E" : "W"} ${Math.abs(lon).toFixed(5)}°`;
    }

    // While a gesture handler is installed it owns the cursor, so the editor can show
    // "grab this vertex" instead of the map's own pointer feedback.
    if (gesture?.hover?.(pointerAt(event), event)) {
      tip.hidden = true;
      return;
    }

    const hit = hitTest(x, y);
    if ((hit?.id || null) !== (hovered?.id || null)) {
      hovered = hit;
      schedule();
    }
    if (hit?.label) {
      tip.textContent = hit.label;
      tip.hidden = false;
      tip.style.left = `${x}px`;
      tip.style.top = `${y}px`;
      canvas.style.cursor = "pointer";
    } else {
      tip.hidden = true;
      canvas.style.cursor = "";
    }
  });

  canvas.addEventListener("pointerup", (event) => {
    const wasDrag = dragging && dragging.moved > 4;
    dragging = null;
    canvas.releasePointerCapture?.(event.pointerId);
    if (claimed) {
      claimed = false;
      gesture?.up?.(pointerAt(event), event);
      return;
    }
    if (wasDrag) return;
    const rect = canvas.getBoundingClientRect();
    const hit = hitTest(event.clientX - rect.left, event.clientY - rect.top);
    options.onSelect?.(hit ? { id: hit.id, kind: hit.kind, label: hit.label } : null);
  });

  canvas.addEventListener("pointerleave", () => {
    tip.hidden = true;
    if (readout) readout.textContent = "—";
    if (hovered) { hovered = null; schedule(); }
  });

  canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      // Trackpad pinch arrives as ctrl+wheel with small deltas; treat both the same but
      // damp the raw value so a hard flick does not jump six zoom levels.
      const factor = Math.exp(-clamp(event.deltaY, -60, 60) * 0.0032);
      zoomBy(factor, event.clientX - rect.left, event.clientY - rect.top);
    },
    { passive: false },
  );

  canvas.addEventListener("dblclick", (event) => {
    const rect = canvas.getBoundingClientRect();
    zoomBy(1.9, event.clientX - rect.left, event.clientY - rect.top);
  });

  canvas.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 120 : 40;
    const keys = {
      ArrowLeft: () => { view.lon -= step / (cosLat() * view.scale); },
      ArrowRight: () => { view.lon += step / (cosLat() * view.scale); },
      ArrowUp: () => { view.lat += step / view.scale; },
      ArrowDown: () => { view.lat -= step / view.scale; },
      "+": () => zoomBy(1.35),
      "=": () => zoomBy(1.35),
      "-": () => zoomBy(1 / 1.35),
      _: () => zoomBy(1 / 1.35),
      "0": () => api.fitContent(),
    };
    const action = keys[event.key];
    if (!action) return;
    event.preventDefault();
    action();
    fitted = event.key === "0" ? fitted : null;
    schedule();
  });

  function controls() {
    const make = (path, label, onClick) =>
      h(
        "button",
        { class: "map__control", type: "button", title: label, "aria-label": label, onClick },
        h("span", { html: `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>` }),
      );
    return h(
      "div",
      { class: "map__controls no-print" },
      make(ICONS.zoomIn, "Zoom in", () => zoomBy(1.4)),
      make(ICONS.zoomOut, "Zoom out", () => zoomBy(1 / 1.4)),
      make(ICONS.target, "Fit to content", () => api.fitContent()),
    );
  }

  const observer = new ResizeObserver(() => {
    // Re-fit rather than re-centre, so a phone rotating from portrait to landscape keeps
    // the whole corridor in frame instead of cropping it.
    refit();
  });
  observer.observe(canvas);

  const images = new Map();

  /**
   * Re-fit or redraw once a raster's pixels exist.
   *
   * A screen sets its rasters and fits in the same frame, before the imagery has been
   * fetched. The footprint is known synchronously, so the fit itself is already right;
   * what the load event changes is that there are now pixels to draw. Re-running the
   * fit still matters for the findings mode, where vectors painted after the first fit
   * would otherwise sit outside the frame.
   *
   * A reader who has zoomed or panned has cleared `fitted`, and keeps their view.
   */
  function onRasterReady() {
    refit();
  }

  /** Load a raster once and redraw when it arrives. */
  function raster(url, bounds, opacity = 1) {
    if (!url || !bounds) return null;
    let image = images.get(url);
    if (!image) {
      image = new Image();
      image.decoding = "async";
      image.addEventListener("load", onRasterReady);
      // A missing preview is not fatal: the vectors still carry the answer, so the map
      // degrades to a graticule with geometry on it rather than an error.
      image.addEventListener("error", () => {
        images.set(url, null);
        schedule();
      });
      image.src = url;
      images.set(url, image);
    } else if (image.complete && image.naturalWidth) {
      // Already decoded from an earlier screen. The load event will not fire again, so
      // the re-fit has to be requested here or the second visit keeps the first fit.
      onRasterReady();
    }
    return { image, bounds, opacity };
  }

  const api = {
    element: container,
    canvas,

    /** Replace the raster stack. `specs` is `[{url, bounds, opacity}]`, back to front. */
    setRasters(specs) {
      rasters = (specs || [])
        .map((spec) => raster(spec.url, spec.bounds, spec.opacity))
        .filter(Boolean);
      schedule();
      return api;
    },

    setVectors(next) {
      vectors = next || [];
      schedule();
      return api;
    },

    fit,

    fitContent(pad = 0.08) {
      const bounds = contentBounds();
      if (bounds) {
        fit(bounds, pad);
        fitted = { bounds, pad, mode: "content" };
      }
      return api;
    },

    /** Frame the findings, letting the backdrop imagery run past the edges.
     *
     * This is the right default for any screen whose subject is the geometry rather
     * than the acquisition: the slick, the drift corridor, the vessel shortlist. When
     * a case has no vectors at all it falls back to framing everything, so a screen
     * still shows the scene rather than an empty graticule.
     */
    fitFindings(pad = 0.14) {
      const bounds = contentBounds({ includeRasters: false }) || contentBounds();
      if (bounds) {
        fit(bounds, pad);
        fitted = { bounds, pad, mode: "findings" };
      }
      return api;
    },

    redraw: schedule,

    get view() {
      return { ...view };
    },

    setHovered(id) {
      const next = id ? { id } : null;
      if ((next?.id || null) !== (hovered?.id || null)) {
        hovered = next;
        schedule();
      }
      return api;
    },

    /** Screen position of a coordinate, for placing DOM labels over the canvas. */
    project(lon, lat) {
      return toScreen(lon, lat);
    },

    /** The inverse of `project`, in canvas pixels. */
    unproject(x, y) {
      return toLonLat(x, y);
    },

    /** Kilometres per screen pixel, so a caller can size a hit tolerance in metres. */
    kmPerPixel,

    /**
     * Install or clear a gesture claim. `handler` may implement `down`, `move`, `up` and
     * `hover`; `down` returning truthy takes the gesture away from the pan handler.
     */
    setGesture(handler) {
      gesture = handler || null;
      claimed = false;
      if (!handler) canvas.style.cursor = "";
      return api;
    },

    destroy() {
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
      for (const image of images.values()) if (image) image.src = "";
      images.clear();
    },
  };

  // First paint after layout, so `clientWidth` is real.
  requestAnimationFrame(() => schedule());
  return api;
}

/**
 * The key for a map, rendered in flow underneath it.
 *
 * Deliberately not an overlay on the canvas: a legend plate drawn on the imagery covers the
 * pixels it is there to explain, and on a 4:3 stage that is a real amount of water.
 */
export function mapLegend(items) {
  return h(
    "div",
    { class: "legend legend--under" },
    items.filter(Boolean).map(({ label, colour, shape = "line" }) =>
      h(
        "span",
        { class: "legend__item" },
        h("span", {
          class: [
            "legend__swatch",
            shape === "line" ? "legend__swatch--line" : null,
            shape === "dash" ? "legend__swatch--dash" : null,
          ],
          style: { color: colour, background: shape === "swatch" ? colour : null },
        }),
        h("span", null, label),
      ),
    ),
  );
}

// -- helpers ----------------------------------------------------------------

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

/** The largest 1/2/5 x 10^n at or below `value`. Used for graticules and scale bars. */
function niceStep(value) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalised = value / magnitude;
  const step = normalised >= 5 ? 5 : normalised >= 2 ? 2 : 1;
  return step * magnitude;
}

function decimalsFor(step) {
  return Math.max(0, Math.min(6, Math.ceil(-Math.log10(step)) + 1));
}

function trim(value) {
  return Number(value.toFixed(3)).toString();
}

function pointToSegment(px, py, x0, y0, x1, y1) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(px - x0, py - y0);
  const t = clamp(((px - x0) * dx + (py - y0) * dy) / lengthSquared, 0, 1);
  return Math.hypot(px - (x0 + t * dx), py - (y0 + t * dy));
}

/** Metres between two coordinates on a sphere. Used for readouts, not for geometry. */
export function haversineKm(a, b) {
  const R = 6371.0088;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);
  const s =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
}
