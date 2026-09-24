# Test data for the New analysis screen — where to get it, and which files to use

You want to drive the **New analysis** screen yourself: drop a scene in, press **Run the pipeline**,
and watch a real case come out the far end. This document says exactly which files to use, where they
already sit on this machine, and the one thing you have to understand before you trust the result.

**The short answer:** you do not need to download anything. The dataset is already on disk — 1,200 SAR
GeoTIFFs in `Oil/`, and a SAR GeoTIFF is the only required input. Six of them have already been copied
into **`data/raw/sample_uploads/`** so you can point the file dialog at one folder instead of
scrolling through twelve hundred. Which six, and why those six and not any others, is the rest of this
document.


---

## The one thing to understand first: train vs test

The model was **trained** on most of the scenes in `Oil/`. If you upload one it was trained on, it
will reproduce the answer almost perfectly — because it has seen that exact water before. That is not
a test of anything; it is the model reciting. A judge who knows how machine learning works will ask
"was that scene in your training set?", and if the answer is yes, the impressive number means nothing.

The dataset is split three ways — **169 train, 36 validation, 35 test** — grouped so that no parent
Sentinel-1 product ever lands on two sides of the line (that is the leaky-split fix from 10 September;
see [`03-STATUS-AND-ROADMAP.md`](03-STATUS-AND-ROADMAP.md)). The **35 test scenes are the only ones
the model never saw during training.** Those are the honest test set. Upload one of those and the
result is a fair demonstration; upload a train scene and it is not.

Every scene recommended below is from the **test split**, and none of them already has a built case,
so each one is genuinely a fresh run you are watching for the first time.

> **The already-built demo cases** — `00223`, `00734`, `00100`, `00119`, `00295`, `00844`, `01041`,
> `01083`, `00096`, `00014` and `demo` — are the ones the app already ships as finished cases. You can
> re-run them, but there is no surprise in it. The five below are chosen precisely because they are
> *not* on that list.

---

## Five scenes to use — all on disk, all held-out

Each row is a scene file in `Oil/` and its matching reference mask in `Mask_oil/`. All are two-band
Sentinel-1A GRD GeoTIFFs, roughly **40 MB** each — comfortably inside the scene slot's 512 MB cap, and
on a hosted deployment the browser chunks anything over 24 MiB automatically so it clears the
platform's 32 MiB per-request cap (a 42 MB scene sent whole would otherwise come back 413 from the
proxy, not the app) — and every one **carries its own acquisition time and band names in the embedded
DIMAP header**. That
matters at the form: you can leave *Acquired (UTC)* and *Band order* alone, because a time in the file
wins over anything typed, and the band names are read from the product. The case id even suggests
itself from the filename. In practice you drop the file in and press the button.

| Upload this scene | …with this mask | Region | Acquired | Held-out IoU | What you'll see |
|---|---|---|---|---|---|
| `Oil/00805.tif` | `Mask_oil/00805.tif` | Eastern Mediterranean | 2017-07-22 | **0.85** | A clean, strong result — a compact slick the model delineates tightly. The confidence case. |
| `Oil/01191.tif` | `Mask_oil/01191.tif` | Gulf of Mexico | 2018-08-21 | **0.83** | The biggest slick of the five: 6.7 % of the scene against an 8.0 % prediction. Visually dramatic — the one to screenshot. |
| `Oil/00366.tif` | `Mask_oil/00366.tif` | Nile Delta shelf | 2017-08-26 | **0.85** | A different sea, a different look. Shows the model is not tuned to one basin. |
| `Oil/00099.tif` | `Mask_oil/00099.tif` | North Sea | 2017-09-01 | **0.78** | A middling, realistic result — not a showpiece, which is the point of including it. |
| `Oil/00753.tif` | `Mask_oil/00753.tif` | South China Sea | 2019-01-08 | **0.78** | A small, faint slick — 0.6 % of the scene. Tests the hard, low-signal end. |

**Optionally, one scene where the model does badly** — because being able to show a failure and
explain it is stronger than only ever showing wins:

| Upload this scene | …with this mask | Region | Acquired | Held-out IoU | What you'll see |
|---|---|---|---|---|---|
| `Oil/00629.tif` | `Mask_oil/00629.tif` | Bay of Biscay | 2015-10-31 | **0.17** | The model **over-predicts**: it calls 6.95 % of the scene oil where the truth is 1.19 %. Upload the mask too, set the Imagery overlay to **Both**, and you can point at exactly where it went wrong. This is what a look-alike-rich scene does to a detector, and saying so out loud is the honest version. |

> The shipped case `00014` is the same story and worse (IoU **0.046**, 15.9 % predicted against 0.73 %
> true) — it is already built, so it is the one to *show*; `00629` is the one to *run live* if you want
> the failure to happen in front of you rather than be recalled.

You do **not** have to supply the mask. A scene on its own produces a prediction-only case, which is
the real operational situation — at sea there is no ground-truth mask to compare against. Supplying
the mask only adds the *Reference* overlay and the IoU-against-truth figure; it never changes the
prediction. (The mask is **compared, never substituted** — that rule holds on the upload path too.)

---

## The optional slots — wind, currents, AIS

The four optional slots each *improve* a run where they overlap the scene, and state a reason where
they don't. Here is where each one comes from and whether it is already on this machine.

### Reference mask (`.tif`) — already on disk
`Mask_oil/<same-number>.tif`, ~4 MB each. Covered above. Same pixel dimensions as the scene, which is
all the upload check requires.

### CMEMS currents (`.nc`) — already on disk, and worth trying for what it teaches
The file is at the repository root:
`cmems_mod_glo_phy_my_0.083deg_P1D-m_1787849787864.nc`, **370 MB**, a real Copernicus Marine
global-physics product (well under the 1 GB slot cap). It is gitignored, so it is on this machine and
not in the repository.

Upload it with any of the scenes above and you will see the **fallback path work honestly**. The
product is global in space — latitude −80 to 90, longitude −180 to 180 — so it covers every scene in
the dataset. But it has exactly **one time step, 2026-06-23**, which is years from any acquisition.
The audit records the consequence precisely: of 270 parent products checked, **0 usable, 270
spatial-only**. So the case reports spatial overlap, no temporal overlap, and the reason lands on the
Drift screen's Forcing card rather than nowhere.

What it falls back to is worth saying accurately, because it is not "throw the file away": the
*magnitude* is still taken from the real product — the synthetic field is scaled to the mean current
speed CMEMS reports over that scene's footprint — and only the spatial *pattern* is invented. The
label says synthetic because the pattern is. See [`RUNBOOK.md`](../../RUNBOOK.md) §6b.

The demonstration point is the one judges care about: **"your file was not used" never arrives as
silence.**

### ERA5 wind (`.nc`) — free download, needs an account
There is no ERA5 file on this machine, and it is the **single most worthwhile thing to go and get**.
Oil moves at about 3 % of the wind speed, which on these scenes is the same size as the current term
— so wind is half the drift answer, not a footnote. The file is tiny: a padded footprint at ERA5's
0.25° grid is a couple of megabytes for three days of hourly wind.

Get it from the Copernicus Climate Data Store:

- Register at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu), accept the ERA5 licence,
  open **ERA5 hourly data on single levels**, and request exactly `10m_u_component_of_wind` and
  `10m_v_component_of_wind` — the reader looks for `u10`/`v10` and refuses a file missing either.
- Ask for the acquisition day **and the day either side** (a 24 h hindcast reaches back past
  midnight), all 24 hours, area padded ~1° around the scene footprint, format **NetCDF4** — GRIB is
  not readable here.
- The exact click-path, including how to turn a scene's bounds into the N/W/S/E box, is
  [`RUNBOOK.md`](../../RUNBOOK.md) §6a.

Two ways to use it once downloaded: drop it in the **ERA5 wind** slot on the screen, *or* save it as
`data/raw/era5_wind_<something>.nc` — anything matching `era5_wind*.nc` there is found automatically
at startup, no upload needed.

### Real AIS (`.csv`) — one real file is here, but it is too big to upload as-is
`data/raw/AIS_2022_06_01.csv` is a real MarineCadastre daily extract — the one whose 17-column header
was matched byte-for-byte against what the app emits. But the AIS slot is capped at 512 MB and this
file is over 880 MB, so the server refuses it from the `Content-Length` header before reading a byte,
and tells you exactly why:

> `a ais upload is capped at 512 MB; this one declares 883 MB`

Two honest options:

- **Slice it.** Keep the header row and a geographic or time subset — the vessels near the slick are
  all that matter to one scene — and upload that.
- **Use the synthetic default.** With nothing in the AIS slot the run uses the seeded synthetic feed,
  labelled `AIS mode: Synthetic demonstration data` on the Vessels screen. That is the shipped
  behaviour, and it is stated on its face rather than hidden.

Note the AIS file is the one slot **not** checked by magic bytes (a CSV has no magic number); its
header is validated when it is read instead.

### What each slot will actually accept

| Slot | Extensions | Size cap | Checked how |
|---|---|---|---|
| SAR scene — **required** | `.tif` `.tiff` | 512 MB | TIFF magic bytes, after landing |
| Reference mask | `.tif` `.tiff` | 128 MB | TIFF magic bytes |
| ERA5 wind | `.nc` `.nc4` | 256 MB | HDF5/NetCDF4 magic bytes |
| CMEMS currents | `.nc` `.nc4` | 1024 MB | HDF5/NetCDF4 magic bytes |
| AIS extract | `.csv` | 512 MB | header, at read time |

Two things follow from "magic bytes, after landing". A file that is over the cap is refused from its
`Content-Length` **before a single byte is read**. And a file that is named right but is not what it
claims gets a blunt answer — *the bytes in this file are not a TIFF, whatever it is named*. Renaming
a PNG to `.tif` does not get it in.

One failure you may genuinely hit on a real download: if the CDS hands you a **classic NetCDF-3**
file, the slot says so by name — *this is a classic NetCDF-3 file; the era5 reader in this project
decodes NetCDF-4 (HDF5) only. Re-save it as NetCDF-4 and upload again.* Choose NetCDF-4 in the CDS
form and it does not arise.

---

## How to actually pick the file

The upload control opens your operating system's file dialog, and scrolling to one file among 1,200
is the only genuinely annoying part of this. Two ways around it:

1. **The six are already gathered for you.** They were copied into `data/raw/sample_uploads/` when
   this document was written — `scenes/` and `masks/`, six files each, 269 MB in total. Point the file
   dialog there once and everything you need is in two folders. To rebuild it, or to change which
   scenes it holds:

```bash
mkdir -p data/raw/sample_uploads/scenes data/raw/sample_uploads/masks && for n in 00805 01191 00366 00099 00753 00629; do cp "Oil/$n.tif" "data/raw/sample_uploads/scenes/$n.tif"; cp "Mask_oil/$n.tif" "data/raw/sample_uploads/masks/$n.tif"; done && du -sh data/raw/sample_uploads
```

   The filenames are kept exactly as they are in `Oil/`, deliberately — the case id is suggested from
   the filename, so `00805.tif` gives you the id `00805`, and a file renamed to `scene_00805.tif`
   would give you `scene_00805` instead.

   It lands in `data/raw/`, which is already gitignored, so nothing there can be committed by
   accident. The originals in `Oil/` and `Mask_oil/` are untouched copies-from, and you can delete the
   folder whenever you like:

```bash
rm -rf data/raw/sample_uploads
```

2. **Or paste the path.** In the macOS Open dialog press **⌘⇧G** and type the path, e.g.
   `~/Resume Projects/OilSpill/Oil/00805.tif`. On Windows or Linux, paste it into the filename box.

Also worth knowing: **the files stay on the server between runs.** Pressing *Run the pipeline* a
second time with a different setting does not make you re-upload 40 MB.

---

## What happens to the file you upload

An upload is written to `data/uploads/` — a **separate tree** from `Oil/` and `Mask_oil/`, gitignored,
and it can never add to, shadow or overwrite the evaluated dataset. That is a property of the
directory layout, not a check that could be forgotten. An uploaded scene is used because a path is on
the request, never because its name matched something.

The case is stored under the id you chose. If that id already names a stored case the server answers
**409** — `case '00223' already exists; choose another id for the uploaded scene` — rather than
replacing it. That is the one error you are likely to hit, and it is why the recommended five are all
ids with no case behind them.

The **Clear** control on the screen deletes every file you uploaded from the server and tells you how
many went, so you can take your own data back off the machine after a demo without opening a shell.

Because uploads never join the dataset, **testing this way costs nothing and changes no measured
number.** The accuracy figures on the Method screen were measured on the audited test split; a scene
you upload is scored and shown, but it does not move those figures. The screen says as much, in the
panel, unprompted.

---

## A brand-new scene, not in this dataset at all

If you want to prove it works on water the dataset has never contained — an Indian scene, say, which
the recommended dataset has none of — you download a Sentinel-1 product and process it through SNAP to
match our chain (`Orb_NR_Cal_Spk_TC_dB`), export a two-band VH/VV GeoTIFF, and upload that. The full
recipe, including the SNAP graph and the band order, is in [`RUNBOOK.md`](../../RUNBOOK.md) §6 and
[`03-STATUS-AND-ROADMAP.md`](03-STATUS-AND-ROADMAP.md) §3.8 item 6.

The dataset itself is public if you want more scenes than the 1,200 here: *Sentinel-1 SAR oil-spill
detection dataset, Part I*, Zenodo, DOI **10.5281/zenodo.8346860**, CC BY 4.0. Expect a fresh scene to
look *worse* than the test scenes above — no reference mask, a different sea state, possibly a
different processing chain — and say so; that gap is the honest measure of how the method generalises.

---

## The 60-second self-test

1. Start the API — the app opens on **New analysis**:

```bash
.venv/bin/python scripts/run_api.py
```

2. Put `data/raw/sample_uploads/scenes/00805.tif` in the **SAR scene** slot. The case id fills itself
   in as `00805`.
3. Optionally put `data/raw/sample_uploads/masks/00805.tif` in the **Reference mask** slot.
4. Leave *Acquired (UTC)* and *Band order* alone — the product header carries both and wins.
5. Press **Run the pipeline**. About twenty seconds later the Command centre opens on the finished
   case, which is now in the case list beside the shipped ones.
6. Walk it exactly like the demo case — Imagery, Slick, Drift, Vessels — and on Imagery set the
   overlay to **Both** to see prediction and reference on the same pixels.
7. Press **Clear** on New analysis when you are done, to wipe the uploaded files off the server.

Do that once with `00805` and once with `00629`, and you have both halves of the honest story: a
scene the model gets right and a scene it gets wrong, on data it was never trained on, run in front of
whoever is watching.
