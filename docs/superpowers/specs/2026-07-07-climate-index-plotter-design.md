# ClimateIndexPlotter — Design Spec

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Component name:** `ClimateIndexPlotter` (manifest name `climate-index-plotter`)

## 1. Summary

A DeltaTwin component for the DestinE Service Platform that computes **ETCCDI climate
indices** from the DestinE **Climate Change Adaptation Digital Twin (Climate DT)** and
plots how each index is projected to change between a **historical** period (1999–2014)
and a **future projection** period (2025–2049) over a user-chosen area.

For each selected ETCCDI index the component produces one PNG with four panels (2x2), with
coastlines and country borders overlaid and no gridlines:

- **Top-left** — historical index climatology
- **Top-right** — future index climatology (shared colour scale with top-left)
- **Bottom-left** — absolute variation: `variation = index_future − index_historical`
- **Bottom-right** — percentage variation: `variation_pct = (index_future − index_historical) / index_historical × 100`

It is the fourth use case in this repository, alongside FireMonitor, FireRiskPlotter and
LSTPlotter, and follows the same DeltaTwin component layout. Its distinguishing feature is
that it **streams** only the required data from **Earth Data Hub (EDH)** rather than
downloading whole products.

### Goals

- Demonstrate Climate DT access from a DeltaTwin component via EDH Zarr streaming.
- Compute ETCCDI indices with `xclim` and visualise projected change over an AOI.
- Support a user-chosen model, index set, and area (bbox or shapefile).

### Non-goals

- Not a general climate-analysis toolkit; it targets the ETCCDI list and the two-panel
  variation plot described above.
- No bias correction, downscaling, or multi-model ensembling.
- Fixed comparison periods (historical 1999–2014 vs projection 2025–2049) — not configurable per run.

## 2. Background & key findings

### 2.1 Climate DT on Earth Data Hub

Climate DT data is published on **Earth Data Hub** (collection `climate-dt-2`), **regridded
from the native HEALPix grid to a regular latitude/longitude grid**, as **Zarr**, opened
lazily with `xarray`. This is a much better fit than the HDA order→poll→download→GRIB flow
(used by FireMonitor) because Dask streams only the AOI + time chunks actually needed.

Relevant published datasets (surface, hourly), for **all three models**
(`IFS-NEMO`, `IFS-FESOM`, `ICON`):

| Experiment | Year coverage | Notes |
|---|---|---|
| Historical (`hist`) | 1990–2014 | used for the "historical" period |
| Future (`SSP3-7.0`, ScenarioMIP) | 2015–2049 | used for the "future projection" period (ICON upper years may lag — verify) |

- **Grid:** regular lat/lon; `high` = 0.044°, plus a coarser `standard` variant. Bbox
  subsetting is a trivial `.sel()`. Chunking variants exist (`timeseries` vs `maps`);
  regional multi-year extraction favours time-series chunking.
- **Variables:** `t2m` (2 m temperature, Kelvin) and `avg_tprate` (time-mean total
  precipitation rate, kg m-2 s-1 = mm/s) are present, plus many others. There is no `tp`.
- **Access pattern (verbatim from EDH getting-started):**

  ```python
  import xarray as xr
  ds = xr.open_dataset(
      "https://api.earthdatahub.destine.eu/d1-climate-dt/ScenarioMIP-SSP3-7.0-IFS-NEMO-0001-high-sfc-v0.zarr",
      storage_options={"client_kwargs": {"trust_env": True}},
      chunks={},
      engine="zarr",
  )
  ```

- **Auth:** an **API key** from DESP account settings, via `~/.netrc`
  (`machine api.earthdatahub.destine.eu` / `password <API key>`) or inline
  `https://edh:<API key>@api.earthdatahub.destine.eu/...`. Climate DT is restricted data —
  requires upgraded DESP access. Monthly quota ~500,000 requests.
- **Required packages:** `xarray`, `zarr>3`, `dask`, `aiohttp`.

> **Resolved (verified against the live store, 2026-07-29):** EDH Zarr URLs are
> `https://api.earthdatahub.destine.eu/climate-dt-2/<MODEL>-<EXPERIMENT>-sfc-hourly-<VARIANT>-v0.zarr`,
> e.g. `IFS-NEMO-hist-sfc-hourly-standard-v0.zarr` and
> `IFS-NEMO-SSP3-7.0-sfc-hourly-standard-v0.zarr`.
>
> | variant | global grid | spacing | pixel at 42°N | `t2m` chunks |
> |---|---|---|---|---|
> | `standard` | 512 x 1025 | 0.352° | 39 km N-S, 29 km E-W | 1440 x 64 x 64 |
> | `high-timeseries` | 4096 x 8193 | 0.0440° | 4.9 km N-S, 3.6 km E-W | 6480 x 32 x 32 |
> | `high-maps` | 4096 x 8193 | 0.0440° | as above | 24 x 512 x 512 |
>
> There is **no daily surface product** — only `sfc-hourly` (plus ocean/sea-ice `o2d-daily`),
> so hourly-to-daily aggregation on the client is unavoidable. `resolution` is a run input:
> `standard` (default) or `high`, which resolves to `high-timeseries`; `high-maps` chunking is
> wrong for reading whole periods over a small area.

### 2.2 ETCCDI indices and xclim

There are **27 core ETCCDI indices**, grouped by required daily input variable. Climate DT
provides **only hourly** `t2m`/`avg_tprate` (no daily max/min statistics), so daily inputs
are derived by resampling:

- `tasmax` = daily max of `t2m`; `tasmin` = daily min; `tas` = daily mean.
- `pr` = daily-mean `avg_tprate` × 86400 (mm/s → mm/day), units `mm/d`.

`xclim.indices.*` computes every index from daily `xarray.DataArray`s with a resampling
frequency (`freq="YS"` for annual), and handles unit conversion via `pint`. The lower-level
`xclim.indices` functions are used (not the `xclim.atmos` Indicator wrappers).

**Percentile-based indices** (TN10p, TN90p, TX10p, TX90p, WSDI, CSDI, R95pTOT, R99pTOT)
additionally require **day-of-year percentile thresholds** from a **base period**. The base
period is fixed to the **historical slice (1999–2014)** via
`xclim.core.calendar.percentile_doy`, and the thresholds are applied to both periods.

### 2.3 DeltaTwin component conventions (from FireMonitor)

A component is: `manifest-local.json` + `manifest-remote.json` (copied to `manifest.json`
before run/publish), `workflow.yml` (wires inputs → model ports → outputs), `inputs.json`
(local run values), and `models/<name>/` containing the Python model, helpers, and
`requirements.txt`. Secrets are `string` in the local manifest and `secret` in the remote
manifest. Outputs are captured by a `glob`.

## 3. Architecture & data flow

```
AOI (bbox or shapefile), model, indices, edh_api_key (secret)
        │
        ▼
  climate_index_plotter.py (orchestrator)
    1. resolve_aoi() → bbox (+ optional mask polygon)
    2. for period in {historical 1999–2014, future 2025–2049}:
         edh.open_period(model, experiment, bbox, years, vars) → lazy ds
         daily.to_daily(ds) → {tasmax, tasmin, tas, pr}   (streams only needed chunks)
    3. if any percentile index: build day-of-year percentiles from the historical slice
    4. for index in indices:
         hist_clim = compute_climatology(index, daily_hist, base_percentiles)
         fut_clim  = compute_climatology(index, daily_fut,  base_percentiles)
         mask to polygon if shapefile given
         variation = fut_clim − hist_clim;  variation_pct = variation / hist_clim × 100
         plot.plot_variation(...) → etccdi_<INDEX>_<hist>_<fut>_<model>_<scenario>.png
        │
        ▼
  N PNGs  (etccdi_*.png)
```

Only the variables needed by the selected indices are opened (skip `tp` entirely if no
precipitation index is chosen).

## 4. Module breakdown & interfaces

Location: `DeltaTwin/ClimateIndexPlotter/models/climate_index_plotter/`

| Module | Responsibility | Key interface | Depends on |
|---|---|---|---|
| `climate_index_plotter.py` | CLI entry + orchestration (mirrors `fire_monitor.py:main`); single-pass evaluation of all climatologies | `main(model, indices, aoi_bbox, aoi_shapefile, resolution) -> int`; `compute_climatologies(ids, daily_hist, daily_fut) -> dict` | all below |
| `edh.py` | Zarr-URL registry `(model, experiment, resolution) → url`; API-key auth setup; lazy subset open; chunk-aligned volume estimate | `open_period(...) -> xr.Dataset`; `stream_bytes(ds, variables) -> int`; `grid_spacing(resolution) -> float` | xarray, zarr, dask, aiohttp |
| `aoi.py` | Parse bbox string **or** shapefile → `(bbox, mask geometry \| None)` | `resolve_aoi(bbox_str, shapefile) -> tuple[Bbox, BaseGeometry \| None]` | geopandas, shapely |
| `daily.py` | Hourly → daily aggregation | `to_daily(ds) -> xr.Dataset` (`tasmax, tasmin, tas, pr`) | xarray |
| `indices.py` | ETCCDI registry + per-index climatology (incl. percentile prep) | `INDEX_REGISTRY`; `compute_climatology(index_id, daily_ds, base_percentiles) -> xr.DataArray` | xclim |
| `plot.py` | Four-panel (historical \| future ; variation \| variation-%) PNG per index, coastlines/borders overlaid, no gridlines | `plot_variation(index_id, hist_clim, fut_clim, variation, variation_pct, out_path) -> None` | matplotlib, cartopy |
| `requirements.txt` | Pinned deps | — | — |

Each module has one job and a documented interface, testable on synthetic data without
network access (only `edh.open_period` touches the network).

**Fixed constants (module-level, not run inputs):** `HIST_YEARS = (1999, 2014)`,
`FUTURE_YEARS = (2025, 2049)`, `SCENARIO = "SSP3-7.0"`, `IO_THREADS = 16`,
`MAX_STREAM_GB = 500`; the percentile base period is the historical slice. Resolution started
as a fixed constant and became a run input (see §9.1).

### 4.1 Index registry (`indices.py`)

Single source of truth for all 27 indices: `id → (xclim.indices function, required input
vars, percentile-based?)`. Used to decide which variables to stream and which indices need
percentile prep. Grouped by input variable (xclim function names to be verified against the
pinned xclim version during implementation):

- **tasmin:** FD (`frost_days`), TR (`tropical_nights`), TNx (`tn_max`), TNn (`tn_min`),
  TN10p*, TN90p*, CSDI (`cold_spell_duration_index`)*
- **tasmax:** SU (`tx_days_above`, 25 °C), ID (`ice_days`), TXx (`tx_max`), TXn (`tx_min`),
  TX10p*, TX90p*, WSDI (`warm_spell_duration_index`)*
- **tas:** GSL (`growing_season_length`)
- **tasmax & tasmin:** DTR (`daily_temperature_range`)
- **pr:** Rx1day (`max_1day_precipitation_amount`), Rx5day (`max_n_day_precipitation_amount`,
  n=5), SDII (`daily_pr_intensity`), R10mm (`wetdays`, ≥10 mm), R20mm (`wetdays`, ≥20 mm),
  Rnnmm (`wetdays`, ≥nn mm), CDD (`maximum_consecutive_dry_days`), CWD
  (`maximum_consecutive_wet_days`), R95pTOT*, R99pTOT*, PRCPTOT (`precip_accumulation` over
  wet days)

`*` = percentile-based (needs base-period day-of-year percentiles). `Rnnmm` takes a
user-supplied `nn` threshold (default documented).

## 5. Inputs / outputs

### Inputs (manifest `inputs`, wired in `workflow.yml`)

| Input | Type | Default | Notes |
|---|---|---|---|
| `edh_api_key` | secret | — | EDH API key. `string` in `manifest-local`, `secret` in `manifest-remote`. |
| `model` | string | `IFS-NEMO` | `IFS-NEMO` \| `IFS-FESOM` \| `ICON`. Applied to both periods. |
| `indices` | string | `TXx,FD,Rx1day` | Comma-separated ETCCDI ids, or `all`. |
| `aoi_bbox` | string | `none` | `"W,S,E,N"`. Required unless a shapefile is given. |
| `aoi_shapefile` | string | `none` | Path/URL to shapefile/GeoJSON; enables polygon masking; derives bbox if `aoi_bbox` absent. |
| `resolution` | string | `standard` | `standard` (0.35°, ~29 km) \| `high` (0.044°, ~4 km). See §2.1. |

**Fixed (not run inputs):** historical period `1999–2014`, future period `2025–2049`,
scenario `SSP3-7.0`, percentile base period = historical slice.

### Outputs

- One four-panel PNG per selected index (historical, future, variation, variation-%, with
  coastlines/borders and no gridlines), named
  `etccdi_<INDEX>_<histperiod>_<futperiod>_<model>_<scenario>.png`
  (e.g. `etccdi_TXx_1999-2014_2025-2049_IFS-NEMO_SSP3-7.0.png`).
- Captured by output glob `etccdi_*.png`.

## 6. Error handling (fail loudly — no silent fallbacks)

Explicit errors for:

- Missing API key, or EDH `401`/`403` → error with "check DESP upgraded/DT access" guidance.
- Neither `aoi_bbox` nor `aoi_shapefile` supplied.
- Bbox out of valid range, or an empty selection (AOI outside data / zero grid points).
- Unreadable/invalid shapefile.
- Unknown index id → error listing the valid ids.
- The fixed periods (1999–2014 / 2025–2049) not fully covered by the chosen model → error
  (e.g. ICON future upper years may lag 2049).
- A precipitation index requested but `tp` is unavailable.

## 7. Testing (pytest, minimal mocking)

Real logic exercised on **synthetic in-memory `xarray`** — no network mocks:

- `aoi`: bbox string parsing (valid/invalid), shapefile → bbox derivation.
- `daily`: synthetic hourly array with known values → assert `tasmax/tasmin/tas/pr` and unit
  conversion (K retained; m → mm/d).
- `indices`: registry covers all 27; a handful computed against hand-checked answers
  (e.g. FD count, TXx); percentile-prep output shape.
- `plot`: smoke test — a PNG with two axes from small arrays.
- `edh`: URL-registry (incl. resolution → variant) + auth-setup + `stream_bytes` chunk-alignment
  unit tests; **live tests gated on an API-key env var** (skipped without creds) — a streaming
  smoke test plus a grid-spacing assertion that pins the published pixel size per resolution.
- `streaming`: read amplification. A dask-backed source whose chunks are produced by a counting
  function is injected at `edh.open_period`; asserts the source is streamed about once per
  period for 1, 3 and 10 indices, and that percentile indices cost at most one extra pass.
  This is the regression guard for §9.5.

## 8. Documentation & repo conventions

- Follow the existing multi-component pattern: per-model `requirements.txt`, `manifest-*.json`,
  component `README.md`, and an example output under `assets/`.
- Add `tests/` under the component.
- Update the top-level `README.md` (add ClimateIndexPlotter to the use-case list + example
  output) and `CHANGELOG.md`.

## 9. Risks & open items (resolve during implementation)

1. **Exact EDH Zarr URLs** — resolved: `climate-dt-2/<MODEL>-<EXPERIMENT>-sfc-hourly-standard-v0.zarr` (standard grid 0.35 degrees).
2. **Coordinate/dimension names** on the regridded datasets (`lat`/`lon` vs `latitude`/`longitude`, `time` naming, and longitude convention 0-360 vs -180-180) — normalise in `edh.py`; verify against live data.
3. **Precipitation variable** — resolved: Climate DT exposes `avg_tprate` (time-mean rate, kg m-2 s-1), not `tp`. Daily total mm = daily-mean rate x 86400.
4. **Data volume** — resolved. Measured over the example Italy bbox (`7,36,19,47`) for the
   fixed periods: `standard` streams ~24 GB for `t2m` + `avg_tprate`, `high` ~240 GB. Two
   properties drive this and are worth remembering:
   - **Chunk-aligned over-read.** Zarr reads whole chunks, so at `standard` resolution Italy
     (31 x 35 cells) pulls 2 x 1 chunks of 64 x 64 = a 7.6x over-read. Shrinking the AOI below
     one chunk column buys nothing; AOI-size limits are therefore the wrong lever. At `high`
     the 32 x 32 chunks align far better (1.22x).
   - **Reads are latency-bound, not bandwidth-bound.** Measured throughput on cold chunks rose
     from ~23 MB/s at one Dask worker to ~176 MB/s at 16, flat above that. Dask's default pool
     is sized by CPU count, so the component pins `IO_THREADS = 16`.

   The run logs its estimated volume via `edh.stream_bytes` and refuses runs above 500 GB.
5. **Read amplification (found during review, fixed).** Leaving climatologies lazy until
   plotting made every `.values` call re-stream the hourly source: `plot.build_figure` touches
   each array six times, so a 3-index run read `t2m` 12.1x and `avg_tprate` 6.1x (measured with
   a chunk counter). All climatologies for both periods are now evaluated in one
   `dask.compute`, giving 1.03x regardless of index count. Base percentiles are materialised
   first since both periods consume them. `tests/test_streaming.py` guards this.
6. **Daily chunking (found during review, fixed).** `resample(time="1D")` leaves one Dask chunk
   per day, which bloats the graph and makes multi-day rolling indices (Rx5day) fail outright
   on chunked input. `daily.to_daily` now regroups to yearly chunks, which also matches the
   annual `freq="YS"` reduction.
7. **EDH auth in-container** — confirm the API key (secret input) works from the DeltaTwin runtime; check whether a DESP token can be reused to avoid a second credential.
8. **ICON future coverage** upper years may lag 2049 — validate the 2025–2049 range for ICON.

## 10. References

- Climate DT (ECMWF): https://destine.ecmwf.int/climate-change-adaptation-digital-twin-climate-dt/
- Earth Data Hub getting started: https://earthdatahub.destine.eu/getting-started
- EDH Climate DT collection: https://earthdatahub.destine.eu/collections/climate-dt
- DEDL Lab HDA Climate DT notebook (reference for parameters/experiments): https://github.com/destination-earth/DestinE-DataLake-Lab
- ETCCDI 27 indices: https://etccdi.pacificclimate.org/list_27_indices.shtml
- xclim indices: https://xclim.readthedocs.io/en/stable/indices.html
