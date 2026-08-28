# PRD: SpillTrace

## 1. Project Overview

Build SpillTrace, an oil-spill intelligence dashboard for SIH Problem Statement 26143.

The system will:

```text
Satellite SAR image
    -> AI oil-spill segmentation
    -> spill measurements
    -> backward and forward drift simulation
    -> synthetic AIS vessel reconstruction
    -> explainable vessel ranking
    -> interactive investigation report
```

The system is an investigation-support tool. It must never claim that a vessel is legally guilty. It ranks vessels as candidates for investigation.

## 2. Available Data

Use the files supplied by the user:

- Oil-spill Sentinel-1 images: `01_Train_Val_Oil_Spill_images.7z`
- Matching masks: `01_Train_Val_Oil_Spill_mask.7z`
- CMEMS NetCDF: `cmems_mod_glo_phy_my_0.083deg_P1D-m_1787849787864.nc`

The oil images contain two SAR channels, VV and VH, and are 2048 by 2048 GeoTIFF files. The masks are binary and match the image pixels. Mask value `1` represents oil and value `0` represents background.

The supplied example image is georeferenced with EPSG:4326 and is near 55 N, 4 E. Do not assume that the CMEMS file covers this location. Inspect its bounds before using it. If there is no spatial/time overlap, use synthetic drift forcing for the demo and show this limitation in the UI.

Do not require any additional dataset for the first working version. Use generated, clearly labelled synthetic data for AIS, wind and current fields when real matching data is unavailable.

## 3. Product Goal

Create a full interactive prototype that lets an analyst select an oil-spill image and follow the complete investigation workflow:

1. Load a satellite image.
2. Detect and segment the probable oil slick.
3. Compare prediction with the ground-truth mask when available.
4. Calculate spill geometry.
5. Estimate a possible origin by moving particles backward.
6. Predict future movement by moving particles forward.
7. Display nearby synthetic vessel tracks.
8. Rank vessels using transparent evidence.
9. Export an investigation report.

## 4. Important Product Labels

Always display data provenance:

- `Satellite imagery: Supplied Sentinel-1 SAR dataset`
- `Segmentation mask: Model prediction`
- `Reference mask: Supplied ground truth`
- `AIS mode: Synthetic demonstration data`
- `Drift forcing: CMEMS data` when spatial/time overlap is valid
- `Drift forcing: Synthetic scenario data` otherwise
- `Status: Research PoC - human review required`

Never use:

- `Confirmed culprit`
- `Guilty vessel`
- `Proven source vessel`

Use:

- `Priority candidate for investigation`
- `Model-estimated origin`
- `Probable slick`

## 5. Users

### Incident analyst

Needs to inspect a satellite scene, review the AI mask, correct the boundary and view the spill’s likely movement.

### Pollution-response officer

Needs to understand the current spill area, likely source zone and future risk zone.

### Investigator

Needs to inspect vessel tracks, evidence scores, time matches, distance and unusual behaviour.

## 6. Required Application Screens

### 6.1 Command Center

Show:

- SpillTrace name and SIH problem number.
- Active case selector.
- Satellite acquisition metadata if present.
- Spill area.
- Detection confidence.
- Estimated origin window.
- Number of candidate vessels.
- Data provenance badges.
- Recent processing status.

Include a primary button: `Open Investigation`.

### 6.2 Satellite Analysis

Show:

- VV channel image.
- VH channel image.
- AI probability mask.
- Binary predicted mask.
- Ground-truth mask when available.
- Mask overlay on the image.
- Opacity slider.
- Before/after comparison.
- Image coordinates and CRS.
- Confidence and look-alike warning.

Controls:

- Select image.
- Run detection.
- Toggle VV/VH/overlay/reference.
- Accept prediction.
- Mark for analyst review.

### 6.3 Slick Analysis

Show calculated:

- Area in square kilometres.
- Perimeter in kilometres.
- Centroid latitude and longitude.
- Length and width.
- Orientation.
- Length-to-width ratio.
- Number of disconnected regions.
- Average and maximum confidence.

Show the predicted boundary as an editable polygon or editable raster contour. Preserve the original model output after edits.

### 6.4 Drift Reconstruction

Show a map with:

- Current slick polygon.
- Backward hindcast path.
- Estimated origin zone.
- Forward forecast path.
- Particle cloud or uncertainty envelope.
- Synthetic/current vector arrows.
- Coastline.

Controls:

- Backward/forward mode.
- Time horizon: 6, 12, 24, 48 and 72 hours.
- Windage factor.
- Particle count.
- Play/pause timeline.
- Reset view.

Show a forcing-data card:

- Current source.
- Wind source.
- Spatial overlap status.
- Time overlap status.
- Drift confidence.

### 6.5 Vessel Attribution

Show:

- Synthetic AIS tracks on the map.
- Estimated origin zone.
- Release-time window.
- Candidate vessel markers.
- Ranked candidate table.

For every candidate show:

- Rank.
- Vessel name.
- MMSI.
- Vessel type.
- Total score out of 100.
- Distance from estimated origin.
- Time-window match.
- Trajectory match.
- Speed/stopping behaviour.
- AIS completeness.

Selecting a candidate must highlight its track and open an evidence drawer.

### 6.6 Methodology and Evidence

Show:

- Complete processing pipeline.
- Model architecture.
- Vessel scoring formula.
- Drift assumptions.
- Dataset citations.
- Synthetic-data warning.
- Known limitations.
- Version and timestamp of each processing run.

Include buttons:

- `Download JSON report`
- `Download CSV candidates`
- `Print / Save case report`

## 7. Machine Learning Requirements

### 7.1 Data audit first

Before building the final UI, create a script that reports:

- Number of images.
- Number of masks.
- Matching and unmatched filenames.
- Image dimensions.
- Number of channels.
- Data type and value range.
- CRS and geographic bounds.
- Mask unique values.
- Missing or invalid values.

Stop and report errors if image-mask pairing is not valid.

### 7.2 Preprocessing

- Read VV and VH channels.
- Convert invalid values to an explicit invalid mask.
- Normalize each channel using training-split statistics.
- Resize to 256 by 256 or tile into smaller patches.
- Apply the same spatial transform to the image and mask.
- Use deterministic train/validation/test splits.
- Prevent augmented copies of the same scene from crossing splits.

### 7.3 Model

Implement a baseline and a learned model:

- Baseline: threshold or classical segmentation.
- Main model: lightweight U-Net accepting VV and VH channels.

The model outputs an oil probability mask. Use a combined Dice and binary-cross-entropy loss, with configurable thresholding.

### 7.4 Metrics

Calculate on held-out data:

- IoU.
- Dice/F1.
- Pixel precision.
- Pixel recall.
- Pixel accuracy.
- False-positive rate.

Show metrics separately for the available positive dataset. Do not claim no-oil/look-alike performance because the supplied Part I data contains oil-spill scenes only.

## 8. Slick Geometry Requirements

Use the GeoTIFF transform and CRS to convert the pixel mask into geographic coordinates.

The implementation must:

- Preserve raster resolution.
- Remove tiny noise components using configurable morphology.
- Polygonize the binary mask.
- Calculate area using a suitable projected CRS or geodesic calculation.
- Return GeoJSON geometry.
- Preserve the raw prediction and filtered prediction separately.
- Reject invalid or self-intersecting polygons.

## 9. Synthetic Drift Requirements

If valid CMEMS forcing exists at the image location and time, use its surface `uo` and `vo` variables. Otherwise generate a deterministic synthetic scenario around the image bounds.

Synthetic forcing must:

- Have a stable seed.
- Be physically plausible in magnitude.
- Include a visible `Synthetic scenario` label.
- Be stored with the case output.
- Be reproducible from configuration.

Use a particle model:

```text
velocity = current_velocity + windage_factor * wind_velocity
new_position = old_position + velocity * time_step
```

Requirements:

- Generate particles from the slick polygon or centroid.
- Run backward hindcast by reversing velocity.
- Run forward forecast using normal velocity.
- Display a particle cloud or uncertainty envelope.
- Stop or flag particles that hit land or leave the data domain.
- Support current-only and current-plus-wind scenarios.
- Show assumptions, time step, horizon and windage in the UI.

The system may use a deterministic synthetic wind field when real wind data is unavailable. It must not present that field as observed weather.

## 10. Synthetic AIS Requirements

Create a deterministic AIS generator for the case. Generate at least 10 vessels with tracks before, during and after the estimated release window.

Include:

- Vessel ID/MMSI.
- Vessel name.
- Vessel type.
- UTC timestamp.
- Latitude and longitude.
- Speed over ground.
- Course over ground.
- Heading.

Generate varied evidence:

- One high-scoring vessel close to the origin at the correct time.
- One vessel close to the origin at the wrong time.
- One vessel with a matching course but large distance.
- One vessel with a speed reduction or stop near the origin.
- Several irrelevant vessels.

Label all records as synthetic. Do not create a realistic real-world identity that could be mistaken for actual evidence.

## 11. Vessel Scoring Requirements

Use a transparent weighted score:

| Component | Maximum |
|---|---:|
| Distance from estimated origin | 30 |
| Presence during release window | 25 |
| Trajectory consistency | 20 |
| Speed/behaviour anomaly | 10 |
| Vessel type relevance | 10 |
| Data completeness | 5 |

Every candidate result must include the component scores and a plain-language explanation.

Example:

```text
MV Example 01 - Priority candidate for investigation
Total: 86/100

Distance: 28/30 - passed close to the origin zone
Time: 24/25 - present during the estimated release window
Trajectory: 18/20 - track intersects the origin zone
Behaviour: 8/10 - speed reduction detected
Type: 8/10 - tanker category
Data quality: 5/5 - complete synthetic track
```

## 12. Technical Architecture

Use a monorepo with:

```text
apps/web        React + TypeScript dashboard
services/api    FastAPI REST API
services/ml     preprocessing, training and inference
services/drift  drift and synthetic-data engine
data/raw        local raw datasets, never committed
data/processed  generated compact fixtures
models          model checkpoints, never committed if large
```

Recommended technologies:

- Frontend: React, TypeScript, Vite or Next.js.
- Map: MapLibre GL JS or Leaflet.
- Backend: FastAPI and Pydantic.
- ML: PyTorch, Rasterio, GeoPandas, Shapely and xarray.
- Database: PostgreSQL/PostGIS for production; SQLite is acceptable for local PoC.
- Storage: local filesystem for development, object storage for production.

The application must work in offline demo mode using a seeded case fixture.

## 13. API Requirements

Implement:

- `GET /api/health`
- `GET /api/cases`
- `GET /api/cases/{case_id}`
- `GET /api/cases/{case_id}/images`
- `POST /api/cases/{case_id}/detect`
- `GET /api/cases/{case_id}/slick`
- `POST /api/cases/{case_id}/drift`
- `GET /api/cases/{case_id}/trajectories`
- `GET /api/cases/{case_id}/vessels`
- `GET /api/cases/{case_id}/report`

Long-running model and drift actions must return a job ID and expose status.

## 14. Frontend Data Contract

```ts
type SpillCase = {
  id: string
  image: {
    url: string
    vvUrl?: string
    vhUrl?: string
    width: number
    height: number
    crs?: string
    bounds?: [number, number, number, number]
  }
  provenance: {
    satellite: string
    aisMode: 'synthetic' | 'real'
    driftMode: 'cmems' | 'synthetic'
  }
  slick: {
    polygon: [number, number][]
    areaKm2: number
    centroid: [number, number]
    confidence: number
  }
  trajectories: {
    backward: [number, number][]
    forward: [number, number][]
    uncertainty?: [number, number][]
  }
  vessels: Array<{
    rank: number
    name: string
    mmsi: string
    type: string
    score: number
    components: Record<string, number>
    explanation: string[]
    track: [number, number][]
  }>
}
```

## 15. Non-Functional Requirements

- Responsive on desktop, tablet and mobile.
- Clear loading, failed-job, empty-result and missing-data states.
- Accessible keyboard controls and readable contrast.
- No raw 40 GB archive loaded into the browser.
- Large raster files must use thumbnails or tiles.
- No credentials or secret keys in frontend code.
- Original input files remain unchanged.
- Processing settings and model version are stored with every result.
- All displayed timestamps state UTC or `unknown`.

## 16. Execution Phases

Opus must work sequentially and run tests after each phase.

### Phase 1: Audit

Inspect actual files. Produce `DATA_AUDIT.md`. Do not build the final UI yet.

### Phase 2: Preprocessing

Create image-mask pairing, splits, normalized samples and preview images.

### Phase 3: ML

Train baseline and U-Net. Produce metrics, checkpoint and sample predictions.

### Phase 4: Geospatial analysis

Produce georeferenced mask, GeoJSON and geometry statistics.

### Phase 5: Drift

Implement CMEMS validation, synthetic fallback, hindcast, forecast and uncertainty.

### Phase 6: AIS

Implement deterministic synthetic AIS generation, cleaning, track reconstruction and scoring.

### Phase 7: Backend

Implement API, jobs, persistence, report generation and offline seeded case.

### Phase 8: Frontend

Implement all screens and connect them to API/fixtures.

### Phase 9: Hardening

Test the full workflow, fix mobile/accessibility issues, improve performance and verify labels.

## 17. Acceptance Criteria

- The audit identifies all image and mask files and reports mismatches.
- A user can select a supplied image.
- VV and VH channels display correctly.
- The model produces a predicted oil mask.
- The reference mask can be compared with the prediction.
- IoU, Dice, precision and recall are calculated on held-out data.
- Slick geometry is calculated with correct geospatial units.
- A backward origin path and forward forecast path render on a map.
- Synthetic forcing is visibly labelled when used.
- At least 10 synthetic vessels appear with tracks.
- At least three candidates receive different explainable scores.
- Selecting a candidate highlights its track and evidence.
- JSON/CSV report download works.
- The system handles invalid files and missing forcing data without crashing.
- The dashboard works offline using a seeded case.
- No UI text calls any vessel guilty or confirmed responsible.
- Tests pass for image-mask alignment, geometry, drift direction, scoring and core API routes.

## 18. Final Presentation Message

Present SpillTrace as:

> An explainable satellite-to-AIS decision-support platform that detects probable marine oil slicks, reconstructs their likely movement, forecasts risk zones and prioritizes vessels for human investigation.

Make clear that real deployment would integrate real-time satellite catalogs, regional wind/current products and licensed AIS feeds. The supplied demo uses real satellite imagery and masks, while synthetic AIS and synthetic drift forcing are labelled where matching real data is unavailable.
