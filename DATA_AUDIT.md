# SpillTrace - Data Audit

Phase 1 of the SpillTrace build (SIH Problem Statement 26143).

Every value below was read from the supplied files. Nothing is assumed: filenames, dimensions, band order, acquisition times, coordinates and the CMEMS overlap verdict are all derived from file contents at audit time.

- Generated: `2026-08-28T06:01:44Z`
- Pipeline version: `spilltrace-0.1.0`
- Runtime: 104.84 s
- Image directory: `Oil`
- Mask directory: `Mask_oil`

## Verdict: PASS

All 1200 images pair 1:1 with a mask of identical raster size, every mask is binary, and every image carries a usable WGS84 geotransform. The dataset is fit for the pipeline.

4 non-blocking warning(s) were recorded and are listed below.

## 1. File counts and pairing

| Measure | Value |
| --- | --- |
| Image files (`.tif`) | 1200 |
| Mask files (`.tif`) | 1200 |
| Matched pairs | 1200 |
| Images with no mask | 0 |
| Masks with no image | 0 |
| Pairs fully scanned | 1200 |
| Distinct parent acquisitions | 270 |

Pairing rule: image and mask share the same file stem and raster size.

No unmatched filenames in either direction.

## 2. Raster structure

### Dimensions

| Size (w x h) | Files |
| --- | --- |
| 2048x2048 | 1200 |

### Channels

| Channels | Files |
| --- | --- |
| 2 | 1200 |

| Band order | Files |
| --- | --- |
| `Sigma0_VH_db`, `Sigma0_VV_db` | 1200 |

Band order is taken from the `BAND_NAME` elements of the BEAM-DIMAP document that SNAP embeds in TIFF tag 65000, not guessed from position. The pipeline resolves VV and VH by name for every scene.

### Data types and encoding

| Image dtype | Files |
| --- | --- |
| `>f4` | 1200 |

| Mask dtype | Files |
| --- | --- |
| `|u1` | 1200 |

| Compression | Files |
| --- | --- |
| LZW | 1200 |

### SNAP processing chain (from the product identifier)

| Chain | Files |
| --- | --- |
| `Orb_NR_Cal_Spk_TC_dB` | 1200 |

## 3. Georeferencing

| CRS | Files |
| --- | --- |
| `EPSG:4326` | 1200 |

| EPSG | Files |
| --- | --- |
| 4326 | 1200 |

Combined dataset bounds (WGS84): west `-95.251858`, south `-8.1591`, east `130.300843`, north `61.454896`.

Masks carrying usable geo tags: **0**; masks without: **1200**.

**4 mask(s) carry a pixel-space transform that is not georeferencing.** They store an identity-style matrix with no CRS - an artefact of the raster editor that produced them, not a map projection. Trusting it would place the slick at degenerate coordinates, so the audit rejects any mask transform that has no EPSG code, a pixel size of 1 or more, or an origin outside valid latitude. Affected: `00055`, `00058`, `00059`, `00085`.

Handling: no mask carries usable georeferencing, so every geometry calculation reads the transform and CRS from the paired image after confirming both rasters have identical dimensions; a mask transform is only trusted when it also has an EPSG code, a sub-degree pixel size and an origin inside valid latitude.

### Approximate regions covered

| Region (approximate label) | Scenes |
| --- | --- |
| Gulf of Mexico | 392 |
| Eastern Mediterranean | 172 |
| Persian Gulf | 88 |
| North Sea | 69 |
| Western Mediterranean | 62 |
| North Atlantic Ocean | 60 |
| Red Sea | 57 |
| Gulf of Guinea | 55 |
| Nile Delta shelf | 53 |
| South Atlantic Ocean | 39 |
| Caribbean Sea | 29 |
| Java Sea | 26 |
| Central Mediterranean | 21 |
| North Indian Ocean | 20 |
| Strait of Malacca | 15 |
| Suez Canal approaches | 9 |
| South Indian Ocean | 9 |
| Black Sea | 7 |
| Bay of Biscay | 6 |
| North Pacific Ocean | 5 |
| East China Sea | 4 |
| Sea of Japan | 2 |

Region names come from an offline bounding-box lookup and are display labels only. All geometry, drift and scoring use the raster transform, never these names.

## 4. Acquisitions

| Mission | Scenes |
| --- | --- |
| Sentinel-1A | 1076 |
| Sentinel-1B | 124 |

| Mode | Scenes |
| --- | --- |
| IW | 1200 |

| Measure | Value |
| --- | --- |
| Earliest acquisition (UTC) | 2015-03-12T05:12:51.445Z |
| Latest acquisition (UTC) | 2019-10-30T00:25:55.551Z |
| Distinct parent products | 270 |

Each file is a `subset_N_of_<product>` crop, so several scenes share one parent acquisition. The parent product identifier is the grouping key for train/validation/test splits, which is how the pipeline prevents crops of the same acquisition from crossing splits.

Largest acquisition groups:

| Parent product | Scenes |
| --- | --- |
| `S1A_IW_GRDH_1SDV_20191014T031500_20191014T031525_029449_03598E_ED4C` | 23 |
| `S1A_IW_GRDH_1SDV_20170311T021505_20170311T021528_015638_019B8B_9D85` | 15 |
| `S1A_IW_GRDH_1SDV_20170817T023857_20170817T023922_017957_01E207_7BFA` | 15 |
| `S1A_IW_GRDH_1SDV_20191022T034356_20191022T034421_029566_035D93_FD78` | 15 |
| `S1A_IW_GRDH_1SDV_20181111T001537_20181111T001602_024533_02B121_8A1D` | 14 |

## 5. Mask values

Every mask was fully decoded. Observed pixel values across the set: `0`, `1`.

| Mask value | Files containing it |
| --- | --- |
| `0.0` | 1200 |
| `1.0` | 1200 |

| Oil-pixel fraction | Value |
| --- | --- |
| Minimum | 0.001228 |
| Median | 0.017626 |
| Mean | 0.029796 |
| Maximum | 0.57374 |
| Masks with no oil pixels | 0 |

The oil class is heavily minority, which the loss function has to account for; the pipeline therefore trains on a combined Dice + BCE objective rather than BCE alone.

## 6. Pixel value ranges

Value ranges come from fully decoded scenes only. Decoding every scene would cost roughly two hours of pure-Python LZW, so the audit samples deterministically and reports the sample size.

Scenes decoded for this section (12): `00000`, `00119`, `00250`, `00368`, `00474`, `00582`, `00694`, `00805`, `00909`, `01010`, `01118`, `01230`

Raw statistics, every decoded sample including no-data padding:

| Band | Name | Min (dB) | Max (dB) | Mean (dB) | p0.5 | p99.5 | Exact zeros | Non-finite |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | `Sigma0_VH_db` | -78.54 | 5.65 | -32.64 | -48.59 | 0 | 1124540 | 0 |
| 1 | `Sigma0_VV_db` | -72.45 | 18.36 | -18.38 | -28.86 | 0 | 1124540 | 0 |

Values are calibrated backscatter in decibels (DIMAP `PHYSICAL_UNIT` = `intensity_db`), not raw DN. The DIMAP no-data value is `0.0`; the pipeline treats exact zeros and non-finite samples as invalid and records them in an explicit invalid mask rather than feeding them to the model.

### Valid-sample statistics (no-data excluded)

The raw table above is contaminated by the no-data border: because zero sits above the real backscatter distribution, it drags the upper percentile to `0.00` and hides the range the model actually has to cover. These are the same statistics with exact-`0.0` and non-finite samples removed, and they are what the normalisation clip limits are derived from.

| Band | Name | Min (dB) | Max (dB) | Mean (dB) | Std (dB) | p0.5 | Median | p99.5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | `Sigma0_VH_db` | -78.54 | 5.65 | -33.38 | 3.29 | -48.59 | unknown | -20.39 |
| 1 | `Sigma0_VV_db` | -72.45 | 18.36 | -18.85 | 1.67 | -29.2 | unknown | -10.97 |

The two channels sit in visibly different ranges, so normalisation is per channel rather than shared.

### Backscatter separation between labelled oil and background

| Scene | Band | Oil mean (dB) | Background mean (dB) | Separation (dB) |
| --- | --- | --- | --- | --- |
| `00000` | 0 | -32.46 | -34.28 | 1.82 |
| `00000` | 1 | -26.81 | -20.76 | -6.05 |
| `00119` | 0 | -31.82 | -31.43 | -0.4 |
| `00119` | 1 | -25.27 | -18.66 | -6.61 |
| `00250` | 0 | -33.67 | -32.6 | -1.07 |
| `00250` | 1 | -21.83 | -17.88 | -3.94 |
| `00368` | 0 | -38.82 | -37.48 | -1.35 |
| `00368` | 1 | -25.52 | -21.59 | -3.93 |
| `00474` | 0 | -33.34 | -32.57 | -0.77 |
| `00474` | 1 | -21.41 | -16.53 | -4.88 |
| `00582` | 0 | -35.26 | -34.9 | -0.36 |
| `00582` | 1 | -26.03 | -18.96 | -7.07 |
| `00694` | 0 | -32.78 | -32.83 | 0.06 |
| `00694` | 1 | -26.4 | -22.63 | -3.77 |
| `00805` | 0 | -31.99 | -30.95 | -1.04 |
| `00805` | 1 | -24.13 | -18.46 | -5.67 |
| `00909` | 0 | -33.3 | -32.8 | -0.5 |
| `00909` | 1 | -23.61 | -17.16 | -6.45 |
| `01010` | 0 | -36.94 | -35.77 | -1.17 |
| `01010` | 1 | -24.77 | -19.35 | -5.41 |
| `01118` | 0 | -33.71 | -33.29 | -0.42 |
| `01118` | 1 | -20.17 | -16.68 | -3.49 |
| `01230` | 0 | -32.18 | -31.51 | -0.66 |
| `01230` | 1 | -20.94 | -16.66 | -4.28 |

Negative separation means labelled oil is darker than its surroundings, which is the expected damping signature. Only valid samples are compared, so no-data padding cannot manufacture a difference. This is a sanity check that image and mask are spatially aligned - a misaligned pair would show near-zero separation.

## 7. CMEMS forcing overlap

| Property | Value |
| --- | --- |
| File | `cmems_mod_glo_phy_my_0.083deg_P1D-m_1787849787864.nc` |
| Current variables | `uo` / `vo` |
| Grid shape (lat x lon) | 2041 x 4320 |
| Latitude range | -80.0 to 90.0 |
| Longitude range | -180.0 to 179.916687 |
| Resolution (deg) | 0.083333 x 0.083333 |
| Surface depth (m) | 0.494 |
| Time steps | 1 |
| Times (UTC) | 2026-06-23T00:00:00Z |

Acquisitions tested: **270**; usable (space *and* time): **0**; spatial match only: **270**.

| Representative scene | Region | Scene time (UTC) | Spatial | Time | Gap (days) | Water cells | Mean speed (m/s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `00022` | Central Mediterranean | 2015-03-12T05:12:51.445Z | yes | no | 4120.8 | 194 | 0.087 |
| `00415` | Eastern Mediterranean | 2015-04-02T15:49:25.993Z | yes | no | 4099.3 | 180 | 0.119 |
| `00635` | North Atlantic Ocean | 2015-07-15T18:05:57.825Z | yes | no | 3995.2 | 190 | 0.04 |
| `00391` | Eastern Mediterranean | 2015-07-31T15:49:33.609Z | yes | no | 3979.3 | 156 | 0.181 |
| `00223` | Central Mediterranean | 2015-08-04T16:55:41.752Z | yes | no | 3975.3 | 192 | 0.214 |
| `00176` | Central Mediterranean | 2015-08-28T16:55:36.200Z | yes | no | 3951.3 | 172 | 0.173 |
| `00408` | Eastern Mediterranean | 2015-08-31T15:41:00.251Z | yes | no | 3948.3 | 189 | 0.187 |
| `00100` | Eastern Mediterranean | 2015-09-06T03:51:24.510Z | yes | no | 3942.8 | 164 | 0.188 |
| `00096` | Eastern Mediterranean | 2015-10-12T03:51:20.086Z | yes | no | 3906.8 | 191 | 0.092 |
| `00295` | Central Mediterranean | 2015-10-27T16:55:34.030Z | yes | no | 3891.3 | 210 | 0.085 |
| `00629` | Bay of Biscay | 2015-10-31T18:04:25.378Z | yes | no | 3887.2 | 196 | 0.04 |
| `00134` | Eastern Mediterranean | 2015-11-22T03:59:30.968Z | yes | no | 3865.8 | 196 | 0.115 |
| `00530` | North Atlantic Ocean | 2015-12-25T05:11:10.165Z | yes | no | 3832.8 | 210 | 0.116 |
| `00237` | Nile Delta shelf | 2016-02-13T15:57:18.446Z | yes | no | 3782.3 | 186 | 0.205 |
| `00265` | Nile Delta shelf | 2016-04-25T15:57:10.381Z | yes | no | 3710.3 | 196 | 0.136 |
| `01334` | Gulf of Mexico | 2016-05-03T00:01:53.666Z | yes | no | 3703.0 | 183 | 0.245 |
| `00696` | North Indian Ocean | 2016-05-11T04:22:57.544Z | yes | no | 3694.8 | 181 | 0.175 |
| `00303` | Eastern Mediterranean | 2016-06-02T15:40:54.904Z | yes | no | 3672.3 | 196 | 0.141 |
| `00087` | Caribbean Sea | 2016-06-07T22:17:47.861Z | yes | no | 3667.1 | 151 | 0.657 |
| `00009` | Nile Delta shelf | 2016-07-07T04:00:14.019Z | yes | no | 3637.8 | 196 | 0.163 |
| `00974` | Western Mediterranean | 2016-07-15T17:14:48.363Z | yes | no | 3629.3 | 190 | 0.11 |
| `00624` | Western Mediterranean | 2016-07-25T17:30:42.261Z | yes | no | 3619.3 | 174 | 0.107 |
| `00914` | Western Mediterranean | 2016-07-27T17:14:13.600Z | yes | no | 3617.3 | 198 | 0.072 |
| `00093` | North Sea | 2016-08-06T06:21:11.393Z | yes | no | 3607.7 | 210 | 0.078 |
| `00020` | North Sea | 2016-08-27T17:09:46.547Z | yes | no | 3586.3 | 192 | 0.211 |
| `00727` | Western Mediterranean | 2016-08-29T05:44:44.952Z | yes | no | 3584.8 | 210 | 0.107 |
| `00767` | North Sea | 2016-09-25T17:18:05.374Z | yes | no | 3557.3 | 210 | 0.077 |
| `00014` | Eastern Mediterranean | 2016-10-11T04:00:12.296Z | yes | no | 3541.8 | 210 | 0.139 |
| `00036` | North Sea | 2016-10-14T17:09:45.354Z | yes | no | 3538.3 | 133 | 0.189 |
| `00253` | Eastern Mediterranean | 2016-10-29T15:49:33.104Z | yes | no | 3523.3 | 175 | 0.164 |
| `00319` | Black Sea | 2016-11-19T15:27:24.866Z | yes | no | 3502.4 | 196 | 0.074 |
| `00007` | Nile Delta shelf | 2017-01-15T04:00:19.083Z | yes | no | 3445.8 | 196 | 0.205 |
| `00145` | South Atlantic Ocean | 2017-01-16T04:52:04.860Z | yes | no | 3444.8 | 196 | 0.149 |
| `00011` | Eastern Mediterranean | 2017-02-08T04:00:10.774Z | yes | no | 3421.8 | 196 | 0.142 |
| `00853` | North Atlantic Ocean | 2017-02-14T06:27:23.740Z | yes | no | 3415.7 | 196 | 0.068 |
| `00615` | Gulf of Mexico | 2017-02-15T00:01:48.344Z | yes | no | 3415.0 | 202 | 0.226 |
| `00363` | Gulf of Guinea | 2017-02-21T04:51:37.147Z | yes | no | 3408.8 | 180 | 0.401 |
| `00421` | Persian Gulf | 2017-03-08T14:24:44.182Z | yes | no | 3393.4 | 195 | 0.093 |
| `00045` | Persian Gulf | 2017-03-11T02:15:08.266Z | yes | no | 3390.9 | 195 | 0.126 |
| `00377` | Gulf of Mexico | 2017-04-02T00:15:34.213Z | yes | no | 3369.0 | 196 | 0.169 |
| `00593` | Gulf of Mexico | 2017-04-28T00:01:42.079Z | yes | no | 3343.0 | 185 | 0.239 |
| `00450` | Eastern Mediterranean | 2017-05-03T03:59:23.772Z | yes | no | 3337.8 | 188 | 0.181 |
| `01262` | North Pacific Ocean | 2017-05-04T11:18:54.947Z | yes | no | 3336.5 | 197 | 0.183 |
| `00164` | Nile Delta shelf | 2017-05-09T15:49:08.414Z | yes | no | 3331.3 | 210 | 0.097 |
| `00062` | Persian Gulf | 2017-05-10T02:15:21.143Z | yes | no | 3330.9 | 151 | 0.029 |
| `00146` | South Atlantic Ocean | 2017-05-16T04:52:32.255Z | yes | no | 3324.8 | 196 | 0.267 |
| `00240` | South Atlantic Ocean | 2017-05-28T04:52:07.279Z | yes | no | 3312.8 | 225 | 0.152 |
| `00267` | South Atlantic Ocean | 2017-05-28T04:52:34.796Z | yes | no | 3312.8 | 196 | 0.255 |
| `00252` | Gulf of Mexico | 2017-06-06T00:25:35.869Z | yes | no | 3304.0 | 210 | 0.315 |
| `00114` | North Sea | 2017-06-16T17:19:13.289Z | yes | no | 3293.3 | 196 | 0.091 |
| `00475` | Gulf of Mexico | 2017-06-27T00:01:45.131Z | yes | no | 3283.0 | 183 | 0.245 |
| `00458` | Strait of Malacca | 2017-07-04T11:17:08.461Z | yes | no | 3275.5 | 208 | 0.418 |
| `00113` | South Atlantic Ocean | 2017-07-08T05:00:23.946Z | yes | no | 3271.8 | 210 | 0.109 |
| `00103` | North Sea | 2017-07-08T17:35:32.702Z | yes | no | 3271.3 | 196 | 0.085 |
| `00496` | Gulf of Mexico | 2017-07-09T00:01:47.212Z | yes | no | 3271.0 | 183 | 0.234 |
| `00365` | Suez Canal approaches | 2017-07-09T03:52:20.663Z | yes | no | 3270.8 | 156 | 0.184 |
| `00088` | Strait of Malacca | 2017-07-10T22:47:32.500Z | yes | no | 3269.1 | 204 | 0.408 |
| `00805` | Eastern Mediterranean | 2017-07-22T15:33:16.375Z | yes | no | 3257.4 | 138 | 0.178 |
| `00612` | Nile Delta shelf | 2017-07-26T04:00:32.533Z | yes | no | 3253.8 | 141 | 0.23 |
| `00399` | Nile Delta shelf | 2017-08-01T15:49:08.170Z | yes | no | 3247.3 | 196 | 0.156 |
| `00486` | Gulf of Mexico | 2017-08-02T00:01:47.819Z | yes | no | 3247.0 | 194 | 0.235 |
| `00231` | Gulf of Mexico | 2017-08-07T00:08:43.663Z | yes | no | 3242.0 | 196 | 0.151 |
| `00150` | Persian Gulf | 2017-08-10T02:47:08.612Z | yes | no | 3238.9 | 210 | 0.064 |
| `00039` | Persian Gulf | 2017-08-10T02:47:22.692Z | yes | no | 3238.9 | 120 | 0.073 |
| `00119` | North Sea | 2017-08-13T17:35:10.262Z | yes | no | 3235.3 | 196 | 0.1 |
| `00551` | Gulf of Mexico | 2017-08-14T00:01:48.089Z | yes | no | 3235.0 | 185 | 0.239 |
| `00368` | Nile Delta shelf | 2017-08-14T03:52:16.505Z | yes | no | 3234.8 | 177 | 0.221 |
| `00188` | Persian Gulf | 2017-08-17T02:39:18.143Z | yes | no | 3231.9 | 210 | 0.091 |
| `00797` | North Atlantic Ocean | 2017-08-18T06:35:52.005Z | yes | no | 3230.7 | 196 | 0.085 |
| `00218` | South Atlantic Ocean | 2017-08-20T04:52:11.832Z | yes | no | 3228.8 | 196 | 0.143 |
| `00131` | North Sea | 2017-08-25T17:35:10.475Z | yes | no | 3223.3 | 210 | 0.096 |
| `00366` | Nile Delta shelf | 2017-08-26T03:52:21.453Z | yes | no | 3222.8 | 142 | 0.252 |
| `00130` | North Sea | 2017-08-27T17:19:14.059Z | yes | no | 3221.3 | 210 | 0.113 |
| `00135` | North Sea | 2017-08-30T17:43:02.739Z | yes | no | 3218.3 | 196 | 0.079 |
| `00490` | Eastern Mediterranean | 2017-09-01T15:41:25.113Z | yes | no | 3216.3 | 195 | 0.174 |
| `00099` | North Sea | 2017-09-01T17:27:03.248Z | yes | no | 3216.3 | 196 | 0.089 |
| `00402` | Gulf of Mexico | 2017-09-05T00:15:42.025Z | yes | no | 3213.0 | 196 | 0.159 |
| `00398` | North Atlantic Ocean | 2017-09-05T18:26:31.941Z | yes | no | 3212.2 | 157 | 0.046 |
| `00484` | Gulf of Mexico | 2017-09-07T00:01:48.723Z | yes | no | 3211.0 | 185 | 0.239 |
| `00152` | South Atlantic Ocean | 2017-09-13T04:52:13.833Z | yes | no | 3204.8 | 196 | 0.149 |
| `00372` | Gulf of Mexico | 2017-09-17T00:15:42.810Z | yes | no | 3201.0 | 196 | 0.169 |
| `00293` | Gulf of Mexico | 2017-09-24T00:09:48.375Z | yes | no | 3194.0 | 210 | 0.115 |
| `00180` | South Atlantic Ocean | 2017-09-25T04:52:14.854Z | yes | no | 3192.8 | 196 | 0.146 |
| `00275` | Gulf of Mexico | 2017-10-01T00:01:25.695Z | yes | no | 3187.0 | 196 | 0.078 |
| `00737` | Persian Gulf | 2017-10-06T02:23:15.218Z | yes | no | 3181.9 | 170 | 0.185 |
| `00778` | Central Mediterranean | 2017-10-09T17:04:07.229Z | yes | no | 3178.3 | 206 | 0.2 |
| `00348` | Persian Gulf | 2017-10-10T14:24:48.318Z | yes | no | 3177.4 | 224 | 0.104 |
| `00815` | Persian Gulf | 2017-10-11T02:31:15.503Z | yes | no | 3176.9 | 178 | 0.113 |
| `00935` | Persian Gulf | 2017-10-11T02:31:55.181Z | yes | no | 3176.9 | 156 | 0.04 |
| `00513` | Gulf of Mexico | 2017-10-13T00:01:47.348Z | yes | no | 3175.0 | 188 | 0.234 |
| `00376` | Nile Delta shelf | 2017-10-13T03:52:16.966Z | yes | no | 3174.8 | 196 | 0.205 |
| `00025` | Persian Gulf | 2017-10-16T02:39:25.771Z | yes | no | 3171.9 | 165 | 0.1 |
| `00793` | Eastern Mediterranean | 2017-10-17T15:58:03.546Z | yes | no | 3170.3 | 196 | 0.114 |
| `00438` | Eastern Mediterranean | 2017-10-18T03:59:50.482Z | yes | no | 3169.8 | 196 | 0.11 |
| `00111` | North Sea | 2017-10-31T17:27:21.504Z | yes | no | 3156.3 | 210 | 0.11 |
| `00451` | Gulf of Mexico | 2017-11-02T23:35:54.590Z | yes | no | 3154.0 | 196 | 0.191 |
| `00224` | South Atlantic Ocean | 2017-11-12T04:52:13.332Z | yes | no | 3144.8 | 196 | 0.156 |
| `00171` | Gulf of Mexico | 2017-11-16T00:17:13.356Z | yes | no | 3141.0 | 210 | 0.343 |
| `00522` | Gulf of Mexico | 2017-11-18T00:01:50.797Z | yes | no | 3139.0 | 194 | 0.233 |
| `00255` | Gulf of Mexico | 2017-12-03T00:25:39.786Z | yes | no | 3124.0 | 210 | 0.317 |
| `00384` | Gulf of Guinea | 2017-12-06T04:51:44.454Z | yes | no | 3120.8 | 168 | 0.409 |
| `00286` | Gulf of Mexico | 2017-12-12T00:01:20.872Z | yes | no | 3115.0 | 196 | 0.056 |
| `00564` | Gulf of Mexico | 2017-12-12T00:01:48.792Z | yes | no | 3115.0 | 197 | 0.24 |
| `00419` | Gulf of Mexico | 2017-12-22T00:15:41.574Z | yes | no | 3105.0 | 196 | 0.169 |
| `00258` | Gulf of Mexico | 2017-12-24T00:01:24.146Z | yes | no | 3103.0 | 196 | 0.064 |
| `00599` | Gulf of Mexico | 2017-12-24T00:01:48.101Z | yes | no | 3103.0 | 185 | 0.239 |
| `00236` | Gulf of Mexico | 2017-12-27T00:25:39.108Z | yes | no | 3100.0 | 196 | 0.316 |
| `00285` | Gulf of Mexico | 2018-01-05T00:01:24.219Z | yes | no | 3091.0 | 210 | 0.085 |
| `00556` | Gulf of Mexico | 2018-01-05T00:01:48.502Z | yes | no | 3091.0 | 183 | 0.234 |
| `00537` | North Sea | 2018-01-11T17:25:32.960Z | yes | no | 3084.3 | 206 | 0.132 |
| `00104` | North Sea | 2018-01-11T17:27:26.191Z | yes | no | 3084.3 | 210 | 0.059 |
| `00748` | Eastern Mediterranean | 2018-02-05T03:43:29.299Z | yes | no | 3059.8 | 196 | 0.187 |
| `00326` | Gulf of Mexico | 2018-02-08T00:15:40.164Z | yes | no | 3057.0 | 210 | 0.161 |
| `00663` | Eastern Mediterranean | 2018-02-16T15:41:32.271Z | yes | no | 3048.3 | 210 | 0.192 |
| `00412` | Gulf of Mexico | 2018-02-20T00:15:40.142Z | yes | no | 3045.0 | 196 | 0.172 |
| `00248` | Gulf of Mexico | 2018-02-27T00:08:38.745Z | yes | no | 3038.0 | 196 | 0.173 |
| `01259` | Caribbean Sea | 2018-03-05T22:17:33.583Z | yes | no | 3031.1 | 162 | 0.659 |
| `00339` | Gulf of Guinea | 2018-03-12T04:51:45.027Z | yes | no | 3024.8 | 179 | 0.4 |
| `00428` | Eastern Mediterranean | 2018-03-12T15:40:55.129Z | yes | no | 3024.3 | 210 | 0.199 |
| `00704` | Eastern Mediterranean | 2018-03-13T03:44:14.693Z | yes | no | 3023.8 | 90 | 0.158 |
| `01267` | Caribbean Sea | 2018-03-17T22:17:32.727Z | yes | no | 3019.1 | 151 | 0.657 |
| `00427` | Gulf of Guinea | 2018-03-24T04:51:42.366Z | yes | no | 3012.8 | 158 | 0.403 |
| `00847` | North Atlantic Ocean | 2018-04-02T16:58:25.509Z | yes | no | 3003.3 | 193 | 0.087 |
| `00874` | North Atlantic Ocean | 2018-04-03T06:35:28.490Z | yes | no | 3002.7 | 146 | 0.047 |
| `00734` | North Sea | 2018-04-10T17:33:07.313Z | yes | no | 2995.3 | 196 | 0.063 |
| `00548` | North Sea | 2018-04-15T17:41:43.028Z | yes | no | 2990.3 | 189 | 0.104 |
| `00444` | North Sea | 2018-04-15T17:43:14.049Z | yes | no | 2990.3 | 196 | 0.096 |
| `00777` | Gulf of Mexico | 2018-04-23T00:01:47.426Z | yes | no | 2983.0 | 212 | 0.233 |
| `00577` | Gulf of Mexico | 2018-04-26T00:25:38.880Z | yes | no | 2980.0 | 210 | 0.317 |
| `00545` | Gulf of Mexico | 2018-04-28T00:09:51.637Z | yes | no | 2978.0 | 210 | 0.117 |
| `00263` | North Sea | 2018-04-29T17:27:18.076Z | yes | no | 2976.3 | 210 | 0.05 |
| `00700` | Gulf of Mexico | 2018-05-03T00:15:41.581Z | yes | no | 2973.0 | 224 | 0.165 |
| `00610` | Gulf of Mexico | 2018-05-05T00:01:24.028Z | yes | no | 2971.0 | 210 | 0.077 |
| `00559` | Gulf of Mexico | 2018-05-08T00:25:39.611Z | yes | no | 2968.0 | 196 | 0.319 |
| `00828` | Western Mediterranean | 2018-05-19T17:54:20.116Z | yes | no | 2956.3 | 177 | 0.108 |
| `00660` | Eastern Mediterranean | 2018-05-22T03:59:55.525Z | yes | no | 2953.8 | 196 | 0.128 |
| `00441` | South Atlantic Ocean | 2018-05-23T04:52:13.844Z | yes | no | 2952.8 | 210 | 0.145 |
| `00764` | Eastern Mediterranean | 2018-05-24T03:43:33.959Z | yes | no | 2951.8 | 196 | 0.191 |
| `00603` | Gulf of Mexico | 2018-05-29T00:01:22.159Z | yes | no | 2947.0 | 210 | 0.057 |
| `00781` | Gulf of Mexico | 2018-05-29T00:01:50.021Z | yes | no | 2947.0 | 183 | 0.245 |
| `00591` | Gulf of Mexico | 2018-06-01T00:25:40.329Z | yes | no | 2944.0 | 196 | 0.32 |
| `00879` | Eastern Mediterranean | 2018-06-02T15:57:33.588Z | yes | no | 2942.3 | 196 | 0.256 |
| `00730` | Eastern Mediterranean | 2018-06-16T15:41:33.097Z | yes | no | 2928.3 | 196 | 0.184 |
| `00783` | Gulf of Mexico | 2018-06-22T00:01:50.661Z | yes | no | 2923.0 | 196 | 0.196 |
| `01319` | Gulf of Mexico | 2018-06-28T23:54:03.995Z | yes | no | 2916.0 | 190 | 0.223 |
| `01013` | Gulf of Mexico | 2018-07-02T00:18:09.509Z | yes | no | 2913.0 | 196 | 0.081 |
| `01222` | Gulf of Mexico | 2018-07-04T00:01:52.065Z | yes | no | 2911.0 | 183 | 0.245 |
| `01176` | Gulf of Mexico | 2018-07-16T00:01:52.978Z | yes | no | 2899.0 | 185 | 0.239 |
| `01035` | Gulf of Mexico | 2018-07-19T00:25:01.529Z | yes | no | 2896.0 | 196 | 0.197 |
| `01023` | North Indian Ocean | 2018-07-24T04:23:23.250Z | yes | no | 2890.8 | 167 | 0.105 |
| `00978` | Central Mediterranean | 2018-07-29T17:12:13.423Z | yes | no | 2885.3 | 217 | 0.066 |
| `00000` | North Sea | 2018-08-03T17:25:57.581Z | yes | no | 2880.3 | 210 | 0.094 |
| `00801` | North Atlantic Ocean | 2018-08-07T18:26:44.469Z | yes | no | 2876.2 | 210 | 0.057 |
| `00569` | North Sea | 2018-08-13T17:43:11.575Z | yes | no | 2870.3 | 196 | 0.092 |
| `01114` | Gulf of Mexico | 2018-08-19T00:15:49.211Z | yes | no | 2865.0 | 195 | 0.185 |
| `01191` | Gulf of Mexico | 2018-08-21T00:01:55.267Z | yes | no | 2863.0 | 212 | 0.23 |
| `01225` | Western Mediterranean | 2018-08-23T17:54:28.553Z | yes | no | 2860.3 | 181 | 0.119 |
| `00666` | Gulf of Guinea | 2018-08-27T04:51:53.454Z | yes | no | 2856.8 | 184 | 0.398 |
| `01095` | Gulf of Mexico | 2018-08-31T00:15:49.925Z | yes | no | 2853.0 | 195 | 0.185 |
| `01275` | Sea of Japan | 2018-09-01T21:16:54.902Z | yes | no | 2851.1 | 130 | 0.178 |
| `00838` | North Sea | 2018-09-06T17:41:24.508Z | yes | no | 2846.3 | 206 | 0.076 |
| `01108` | Gulf of Mexico | 2018-09-12T00:15:46.785Z | yes | no | 2841.0 | 210 | 0.127 |
| `01134` | Gulf of Mexico | 2018-09-24T00:15:49.993Z | yes | no | 2829.0 | 196 | 0.172 |
| `01084` | Gulf of Mexico | 2018-10-06T00:15:49.845Z | yes | no | 2817.0 | 195 | 0.173 |
| `00985` | Western Mediterranean | 2018-10-09T17:14:45.793Z | yes | no | 2813.3 | 193 | 0.085 |
| `00949` | Western Mediterranean | 2018-10-14T17:22:46.868Z | yes | no | 2808.3 | 193 | 0.069 |
| `01107` | Gulf of Mexico | 2018-10-30T00:15:50.118Z | yes | no | 2793.0 | 196 | 0.172 |
| `00674` | Gulf of Guinea | 2018-11-07T04:51:58.469Z | yes | no | 2784.8 | 127 | 0.365 |
| `01137` | Gulf of Mexico | 2018-11-11T00:15:49.269Z | yes | no | 2781.0 | 196 | 0.159 |
| `00997` | Gulf of Mexico | 2018-11-11T00:16:39.887Z | yes | no | 2781.0 | 210 | 0.211 |
| `00432` | Caribbean Sea | 2018-11-12T09:52:12.471Z | yes | no | 2779.6 | 182 | 0.788 |
| `01007` | Gulf of Mexico | 2018-11-18T00:10:03.120Z | yes | no | 2774.0 | 196 | 0.069 |
| `01152` | Gulf of Mexico | 2018-11-23T00:15:50.519Z | yes | no | 2769.0 | 196 | 0.185 |
| `01188` | Gulf of Mexico | 2018-11-25T00:01:57.951Z | yes | no | 2767.0 | 180 | 0.226 |
| `00769` | Gulf of Mexico | 2018-12-05T00:15:49.380Z | yes | no | 2757.0 | 196 | 0.172 |
| `01277` | Caribbean Sea | 2018-12-06T22:17:41.520Z | yes | no | 2755.1 | 151 | 0.657 |
| `01201` | Gulf of Mexico | 2018-12-07T00:01:56.471Z | yes | no | 2755.0 | 194 | 0.233 |
| `01209` | Gulf of Mexico | 2018-12-19T00:01:55.201Z | yes | no | 2743.0 | 185 | 0.239 |
| `00719` | South Atlantic Ocean | 2018-12-25T04:52:21.091Z | yes | no | 2736.8 | 196 | 0.115 |
| `01120` | Gulf of Mexico | 2018-12-29T00:15:48.771Z | yes | no | 2733.0 | 196 | 0.172 |
| `01207` | Gulf of Mexico | 2018-12-31T00:01:55.309Z | yes | no | 2731.0 | 191 | 0.237 |
| `00753` | South Indian Ocean | 2019-01-08T22:40:29.702Z | yes | no | 2722.1 | 196 | 0.253 |
| `01204` | Gulf of Mexico | 2019-01-12T00:01:54.254Z | yes | no | 2719.0 | 194 | 0.242 |
| `00435` | Caribbean Sea | 2019-02-04T09:52:13.202Z | yes | no | 2695.6 | 151 | 0.657 |
| `00685` | Gulf of Guinea | 2019-02-11T04:51:57.074Z | yes | no | 2688.8 | 140 | 0.376 |
| `00579` | Strait of Malacca | 2019-02-15T11:42:47.659Z | yes | no | 2684.5 | 139 | 0.164 |
| `00921` | Gulf of Mexico | 2019-03-13T00:01:54.139Z | yes | no | 2659.0 | 183 | 0.234 |
| `01215` | Gulf of Mexico | 2019-03-25T00:01:56.212Z | yes | no | 2647.0 | 193 | 0.217 |
| `00845` | Eastern Mediterranean | 2019-04-07T15:33:20.541Z | yes | no | 2633.4 | 135 | 0.169 |
| `00710` | South Atlantic Ocean | 2019-04-12T04:52:15.914Z | yes | no | 2628.8 | 196 | 0.199 |
| `00472` | South Atlantic Ocean | 2019-04-29T05:00:31.480Z | yes | no | 2611.8 | 196 | 0.107 |
| `01190` | Gulf of Mexico | 2019-04-30T00:01:50.931Z | yes | no | 2611.0 | 143 | 0.21 |
| `01066` | Eastern Mediterranean | 2019-06-04T15:50:01.328Z | yes | no | 2575.3 | 181 | 0.119 |
| `01197` | Gulf of Mexico | 2019-06-05T00:01:56.019Z | yes | no | 2575.0 | 154 | 0.135 |
| `00900` | Eastern Mediterranean | 2019-06-10T03:59:52.708Z | yes | no | 2569.8 | 196 | 0.104 |
| `01240` | Java Sea | 2019-06-12T11:15:25.991Z | yes | no | 2567.5 | 154 | 0.155 |
| `00695` | Java Sea | 2019-06-20T22:33:41.615Z | yes | no | 2559.1 | 141 | 0.338 |
| `00981` | Eastern Mediterranean | 2019-06-23T15:41:42.977Z | yes | no | 2556.3 | 207 | 0.148 |
| `00970` | Gulf of Mexico | 2019-07-02T00:25:49.769Z | yes | no | 2548.0 | 196 | 0.312 |
| `01251` | Java Sea | 2019-07-02T22:33:58.320Z | yes | no | 2547.1 | 164 | 0.416 |
| `00942` | Gulf of Mexico | 2019-07-04T00:10:08.279Z | yes | no | 2546.0 | 196 | 0.229 |
| `01083` | Nile Delta shelf | 2019-07-10T15:49:16.056Z | yes | no | 2539.3 | 196 | 0.114 |
| `00939` | Gulf of Mexico | 2019-07-16T00:10:03.941Z | yes | no | 2534.0 | 196 | 0.108 |
| `00844` | Persian Gulf | 2019-07-16T02:23:25.177Z | yes | no | 2533.9 | 175 | 0.185 |
| `01232` | Java Sea | 2019-07-18T11:15:28.566Z | yes | no | 2531.5 | 154 | 0.155 |
| `00996` | Persian Gulf | 2019-07-23T02:15:25.035Z | yes | no | 2526.9 | 186 | 0.078 |
| `01041` | Eastern Mediterranean | 2019-07-29T15:41:33.976Z | yes | no | 2520.3 | 207 | 0.168 |
| `01237` | Java Sea | 2019-07-30T11:15:31.505Z | yes | no | 2519.5 | 119 | 0.159 |
| `00955` | Gulf of Mexico | 2019-08-07T00:25:51.221Z | yes | no | 2512.0 | 210 | 0.318 |
| `01172` | Gulf of Mexico | 2019-08-09T00:10:30.285Z | yes | no | 2510.0 | 125 | 0.116 |
| `01060` | Gulf of Mexico | 2019-08-16T00:01:15.944Z | yes | no | 2503.0 | 196 | 0.244 |
| `00962` | Gulf of Mexico | 2019-08-26T00:17:57.221Z | yes | no | 2493.0 | 196 | 0.377 |
| `00869` | Eastern Mediterranean | 2019-08-28T03:51:40.799Z | yes | no | 2490.8 | 210 | 0.091 |
| `00864` | Nile Delta shelf | 2019-08-28T03:52:28.698Z | yes | no | 2490.8 | 210 | 0.143 |
| `00925` | Gulf of Mexico | 2019-08-31T23:27:46.113Z | yes | no | 2487.0 | 193 | 0.602 |
| `00947` | Gulf of Mexico | 2019-09-07T00:17:25.238Z | yes | no | 2481.0 | 196 | 0.292 |
| `01171` | Eastern Mediterranean | 2019-09-08T15:49:37.601Z | yes | no | 2479.3 | 194 | 0.098 |
| `01079` | Eastern Mediterranean | 2019-09-08T15:50:20.242Z | yes | no | 2479.3 | 196 | 0.092 |
| `00994` | Gulf of Mexico | 2019-09-19T00:16:46.175Z | yes | no | 2469.0 | 196 | 0.196 |
| `00328` | Caribbean Sea | 2019-09-20T09:52:21.140Z | yes | no | 2467.6 | 147 | 0.678 |
| `00857` | Eastern Mediterranean | 2019-09-21T03:52:26.341Z | yes | no | 2466.8 | 225 | 0.113 |
| `01309` | Red Sea | 2019-09-25T03:22:56.907Z | yes | no | 2462.9 | 196 | 0.151 |
| `00346` | Caribbean Sea | 2019-10-02T09:52:19.642Z | yes | no | 2455.6 | 168 | 0.772 |
| `00958` | Gulf of Mexico | 2019-10-06T00:25:54.317Z | yes | no | 2452.0 | 210 | 0.319 |
| `00929` | Eastern Mediterranean | 2019-10-08T04:00:07.709Z | yes | no | 2449.8 | 210 | 0.161 |
| `01282` | Red Sea | 2019-10-14T03:14:47.372Z | yes | no | 2443.9 | 210 | 0.164 |
| `00637` | Red Sea | 2019-10-14T03:15:00.420Z | yes | no | 2443.9 | 196 | 0.187 |
| `01173` | Gulf of Mexico | 2019-10-15T00:02:03.448Z | yes | no | 2443.0 | 194 | 0.233 |
| `00468` | Red Sea | 2019-10-16T02:59:47.936Z | yes | no | 2441.9 | 201 | 0.092 |
| `01044` | Eastern Mediterranean | 2019-10-22T03:44:09.830Z | yes | no | 2435.8 | 196 | 0.103 |
| `01295` | Red Sea | 2019-10-26T03:14:41.517Z | yes | no | 2431.9 | 166 | 0.104 |
| `00317` | Caribbean Sea | 2019-10-26T09:52:19.359Z | yes | no | 2431.6 | 132 | 0.436 |
| `01278` | Caribbean Sea | 2019-10-26T22:17:45.624Z | yes | no | 2431.1 | 168 | 0.772 |
| `00965` | Gulf of Mexico | 2019-10-30T00:25:54.217Z | yes | no | 2428.0 | 196 | 0.316 |
| `00203` | Eastern Mediterranean | 2017-08-01T03:59:24.142Z | yes | no | 3247.8 | 196 | 0.251 |
| `01328` | South Atlantic Ocean | 2017-08-17T17:32:56.924Z | yes | no | 3231.3 | 131 | 0.349 |
| `00461` | North Atlantic Ocean | 2017-08-18T18:25:43.993Z | yes | no | 3230.2 | 210 | 0.052 |
| `01162` | North Atlantic Ocean | 2017-08-19T06:26:46.097Z | yes | no | 3229.7 | 196 | 0.058 |
| `00227` | Nile Delta shelf | 2017-09-13T03:51:39.990Z | yes | no | 3204.8 | 135 | 0.264 |
| `00792` | Central Mediterranean | 2017-09-14T17:11:03.557Z | yes | no | 3203.3 | 202 | 0.064 |
| `00791` | Central Mediterranean | 2017-09-26T17:11:09.105Z | yes | no | 3191.3 | 210 | 0.081 |
| `00112` | Nile Delta shelf | 2017-09-29T15:56:12.574Z | yes | no | 3188.3 | 138 | 0.203 |
| `01323` | Gulf of Guinea | 2017-10-04T17:32:49.803Z | yes | no | 3183.3 | 186 | 0.375 |
| `00126` | Nile Delta shelf | 2017-10-23T15:56:12.835Z | yes | no | 3164.3 | 109 | 0.264 |
| `00244` | Eastern Mediterranean | 2017-10-24T03:58:50.765Z | yes | no | 3163.8 | 196 | 0.107 |
| `01261` | Gulf of Mexico | 2017-12-01T12:00:34.539Z | yes | no | 3125.5 | 210 | 0.176 |
| `00082` | Gulf of Mexico | 2017-12-04T00:15:17.573Z | yes | no | 3123.0 | 210 | 0.129 |
| `00167` | Eastern Mediterranean | 2017-12-11T03:59:22.739Z | yes | no | 3115.8 | 210 | 0.242 |
| `01325` | South Atlantic Ocean | 2017-12-27T17:33:03.053Z | yes | no | 3099.3 | 132 | 0.267 |
| `00210` | Suez Canal approaches | 2017-12-30T03:51:40.900Z | yes | no | 3096.8 | 182 | 0.177 |
| `00452` | East China Sea | 2018-01-20T09:29:06.568Z | yes | no | 3075.6 | 196 | 0.725 |
| `00821` | Western Mediterranean | 2018-01-31T06:00:46.510Z | yes | no | 3064.7 | 190 | 0.098 |
| `00160` | Eastern Mediterranean | 2018-02-04T03:51:10.861Z | yes | no | 3060.8 | 196 | 0.141 |
| `00206` | Nile Delta shelf | 2018-02-21T03:59:37.804Z | yes | no | 3043.8 | 196 | 0.107 |
| `00074` | Gulf of Mexico | 2018-02-26T00:15:17.514Z | yes | no | 3039.0 | 196 | 0.172 |
| `00211` | Suez Canal approaches | 2018-02-28T03:51:34.393Z | yes | no | 3036.8 | 190 | 0.191 |
| `00073` | Gulf of Mexico | 2018-03-10T00:15:16.726Z | yes | no | 3027.0 | 196 | 0.159 |
| `00387` | Eastern Mediterranean | 2018-03-11T15:48:58.435Z | yes | no | 3025.3 | 196 | 0.107 |
| `01268` | Gulf of Mexico | 2018-03-31T12:00:31.563Z | yes | no | 3005.5 | 210 | 0.171 |
| `00156` | Gulf of Mexico | 2018-04-03T00:15:17.768Z | yes | no | 3003.0 | 196 | 0.159 |
| `00893` | North Atlantic Ocean | 2018-04-04T06:26:41.850Z | yes | no | 3001.7 | 210 | 0.049 |
| `00888` | North Atlantic Ocean | 2018-04-08T16:57:42.201Z | yes | no | 2997.3 | 198 | 0.091 |
| `00809` | Western Mediterranean | 2018-06-01T17:46:09.349Z | yes | no | 2943.3 | 191 | 0.097 |
| `00506` | Eastern Mediterranean | 2018-06-15T15:49:15.777Z | yes | no | 2929.3 | 180 | 0.103 |
| `00878` | Central Mediterranean | 2018-06-17T17:11:06.063Z | yes | no | 2927.3 | 203 | 0.062 |
| `00840` | North Indian Ocean | 2018-07-06T04:22:44.822Z | yes | no | 2908.8 | 194 | 0.143 |
| `01269` | Gulf of Mexico | 2018-07-17T12:00:37.518Z | yes | no | 2897.5 | 210 | 0.171 |
| `00456` | Gulf of Mexico | 2018-07-20T00:15:24.992Z | yes | no | 2895.0 | 210 | 0.176 |
| `00837` | Eastern Mediterranean | 2018-07-21T15:49:07.902Z | yes | no | 2893.3 | 196 | 0.114 |
| `01270` | Gulf of Mexico | 2018-07-29T12:00:36.207Z | yes | no | 2885.5 | 196 | 0.149 |

**Verdict:** CMEMS covers 270/270 acquisitions in space but none in time, so drift uses deterministic synthetic forcing and the UI reports the time-overlap failure.

Drift mode: `synthetic`. UI label: `Drift forcing: Synthetic scenario data`.

The spatial test requires the scene footprint to sit inside the product grid *and* the covered cells to contain water rather than land. The temporal test requires the nearest product timestep to be within 24 h of the acquisition. Both must pass before CMEMS forcing is used.

## Problems

Blocking errors: **0**. Warnings: **4**.

### Warning summary

| Kind | Count |
| --- | --- |
| mask-pixel-space-transform | 4 |

First warnings in detail:

| File | Kind | Detail |
| --- | --- | --- |
| `00055` | mask-pixel-space-transform | mask carries a non-geographic transform [0.0, 1.0, 0.0, 2048.0, 0.0, -1.0] with no CRS; ignored in favour of the paired image transform |
| `00058` | mask-pixel-space-transform | mask carries a non-geographic transform [0.0, 1.0, 0.0, 2048.0, 0.0, -1.0] with no CRS; ignored in favour of the paired image transform |
| `00059` | mask-pixel-space-transform | mask carries a non-geographic transform [0.0, 1.0, 0.0, 2048.0, 0.0, -1.0] with no CRS; ignored in favour of the paired image transform |
| `00085` | mask-pixel-space-transform | mask carries a non-geographic transform [0.0, 1.0, 0.0, 2048.0, 0.0, -1.0] with no CRS; ignored in favour of the paired image transform |

## Consequences for the pipeline

- **Splits are grouped by parent acquisition.** The 1200 scenes come from 270 distinct Sentinel-1 products, so splitting by filename would leak crops of the same acquisition across train, validation and test. Splits use the parent product identifier as the grouping key.
- **Mask geometry borrows the image transform.** No mask carries usable georeferencing - most have no geo tags at all, and the few that do store a pixel-space matrix with no CRS. Every geometry calculation therefore reads the transform and CRS from the paired image after confirming both rasters have identical dimensions.
- **Band order is resolved by name, per scene.** VV and VH are located through the embedded DIMAP band names instead of a hard-coded index.
- **Normalisation is per band, from training-split statistics.** The two channels occupy different decibel ranges (valid-sample p0.5-p99.5: `Sigma0_VH_db` -48.6 to -20.4 dB; `Sigma0_VV_db` -29.2 to -11 dB), so a shared scaling would suppress one of them. Clip limits come from those percentiles rather than a fixed guess, and they are computed with no-data excluded so the padding cannot collapse the range.
- **Exact zeros and non-finite samples become an explicit invalid mask.** The DIMAP declares `0.0` as no-data, so those pixels are excluded from normalisation statistics, from the loss and from reported metrics.
- **Drift uses deterministic synthetic forcing, clearly labelled.** The supplied CMEMS product matches the scene footprints in space but not in time, so it cannot drive the drift model. Observed CMEMS speeds over the scene footprints (0.029 to 0.788 m/s) are used only as a plausibility anchor for the magnitude of the synthetic field. The UI reports spatial overlap as valid and time overlap as invalid, and labels the forcing `Drift forcing: Synthetic scenario data`.
- **The CMEMS land pattern is still used.** Cells where `uo`/`vo` are fill values mark land or no-data, and the drift engine flags particles that enter them.
- **The 48 GB of raw imagery stays out of the browser.** Scenes are decoded server-side and the frontend receives only downsampled PNG previews and compact JSON, per the non-functional requirements.

---

Machine-readable form of this report, including a per-scene record for every pair, is written to `data/processed/audit.json`.
