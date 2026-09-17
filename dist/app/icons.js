/** Icon paths. Authored here, so `h(..., {html})` never receives API data.
 *
 * 24x24 grid, 1.6 stroke, round caps - one visual family with the SF Symbols weight.
 */

export const ICONS = {
  gauge:
    '<path d="M12 13.5V8"/><path d="M4.2 18a9 9 0 1 1 15.6 0"/><circle cx="12" cy="18" r="1.6"/>',
  satellite:
    '<path d="M9.5 4.5 4.5 9.5l3 3 5-5z"/><path d="M14.5 9.5l-5 5 3 3 5-5z"/><path d="M12 12l2.5-2.5"/><path d="M17 4a3 3 0 0 1 3 3"/><path d="M17 1.5A5.5 5.5 0 0 1 22.5 7"/><path d="M6.5 15.5 3 19l2 2 3.5-3.5"/>',
  slick:
    '<path d="M3.6 13.4c1.6-3.6 5-6 8.9-6 4.6 0 7.9 2.8 7.9 5.6 0 2-1.7 3.6-4 3.6-2 0-3.4-1.2-3.4-2.6 0-1.2 1-2 2.2-2"/><path d="M5.2 17.6c1.6 1.2 3.7 1.9 6 1.9"/>',
  drift:
    '<path d="M3 8.5c2-1.6 4-1.6 6 0s4 1.6 6 0 4-1.6 6 0"/><path d="M3 14.5c2-1.6 4-1.6 6 0s4 1.6 6 0 4-1.6 6 0"/><path d="M17.5 19.5 20 17l-2.5-2.5"/>',
  ship:
    '<path d="M4 12.5V9.5h16v3"/><path d="M12 9.5V5.5H8"/><path d="M3 13.5l1.6 5.2a1.5 1.5 0 0 0 1.44 1.05h11.92a1.5 1.5 0 0 0 1.44-1.05L21 13.5z"/><path d="M12 20v-6.5"/>',
  book:
    '<path d="M5 4.5h5.5a2 2 0 0 1 2 2V20a1.6 1.6 0 0 0-1.6-1.6H5z"/><path d="M19 4.5h-5.5a2 2 0 0 0-2 2V20a1.6 1.6 0 0 1 1.6-1.6H19z"/>',
  download: '<path d="M12 3.5v11"/><path d="M8 11l4 4 4-4"/><path d="M4.5 19.5h15"/>',
  print:
    '<path d="M7 8.5V4h10v4.5"/><rect x="4" y="8.5" width="16" height="7" rx="1.6"/><path d="M7 15.5h10V20H7z"/>',
  arrowRight: '<path d="M5 12h13"/><path d="M13 7l5 5-5 5"/>',
  play: '<path d="M8 5.5l10 6.5-10 6.5z"/>',
  pause: '<path d="M9 5.5v13"/><path d="M15 5.5v13"/>',
  reset:
    '<path d="M4 12a8 8 0 1 0 2.5-5.8"/><path d="M4 4.5V10h5.5"/>',
  close: '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><circle cx="12" cy="8" r=".9" fill="currentColor" stroke="none"/>',
  warning:
    '<path d="M12 4.2 21 19H3z"/><path d="M12 9.8v4.4"/><circle cx="12" cy="16.6" r=".9" fill="currentColor" stroke="none"/>',
  empty:
    '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="M3.5 10.5h17"/><path d="M9 15h6"/>',
  offline:
    '<path d="M3 3l18 18"/><path d="M8.6 15.2a4.8 4.8 0 0 1 6.8 0"/><path d="M5.4 11.8a9.4 9.4 0 0 1 4-2.4"/><path d="M14.6 9.4a9.4 9.4 0 0 1 4 2.4"/><circle cx="12" cy="18.6" r=".9" fill="currentColor" stroke="none"/>',
  check: '<path d="M4.5 12.5l5 5 10-11"/>',
  flag: '<path d="M6 3.5V21"/><path d="M6 4.6h12l-2.6 4.4L18 13.4H6z"/>',
  zoomIn: '<circle cx="11" cy="11" r="6.5"/><path d="M11 8.5v5"/><path d="M8.5 11h5"/><path d="M15.8 15.8 20.5 20.5"/>',
  zoomOut: '<circle cx="11" cy="11" r="6.5"/><path d="M8.5 11h5"/><path d="M15.8 15.8 20.5 20.5"/>',
  layers:
    '<path d="M12 3.5 3.5 8 12 12.5 20.5 8z"/><path d="M3.5 12.5 12 17l8.5-4.5"/><path d="M3.5 16.5 12 21l8.5-4.5"/>',
  chevronRight: '<path d="M9.5 6l6 6-6 6"/>',
  target:
    '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.5"/><path d="M12 1.5v3"/><path d="M12 19.5v3"/><path d="M1.5 12h3"/><path d="M19.5 12h3"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3.2 2"/>',
  mail: '<rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="M3.8 7l8.2 6 8.2-6"/>',
  upload: '<path d="M12 19.5v-13"/><path d="M7 11l5-5 5 5"/><path d="M4.5 20.5h15"/>',
  file:
    '<path d="M13.5 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z"/><path d="M13.5 3.5V9H19"/>',
  trash:
    '<path d="M4.5 6.5h15"/><path d="M9.5 6.5V4.8a1.3 1.3 0 0 1 1.3-1.3h2.4a1.3 1.3 0 0 1 1.3 1.3v1.7"/><path d="M6.5 6.5 7.4 19a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4l.9-12.5"/>',
};

/** The SpillTrace mark: a slick outline over a satellite pass line. */
export const BRAND_MARK = `
<svg viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
  <circle cx="12" cy="12" r="10.4" stroke="rgba(255,255,255,0.16)" stroke-width="1.2"/>
  <path d="M4.6 14.6c1.3-3.1 4.3-5.2 7.7-5.2 3.9 0 6.6 2.3 6.6 4.6 0 1.7-1.4 3-3.3 3-1.7 0-2.9-1-2.9-2.2 0-1 .8-1.7 1.8-1.7"
        stroke="#ff8a4c" stroke-width="1.7" stroke-linecap="round" fill="none"/>
  <path d="M2.6 7.4 21.4 4.2" stroke="#5ec8ff" stroke-width="1.2" stroke-linecap="round" stroke-dasharray="2.6 2.6"/>
</svg>`;
