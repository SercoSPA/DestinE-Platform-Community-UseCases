# Rome Future Week: Scenario Data Feasibility

Can DestinE data support the three additional Serco Rome Future Week exhibit scenarios, alongside
the Urban Heat one already in development? Every fact here was verified live against the APIs,
not taken from documentation. Verified 2026-08-04 to 2026-08-13.

## Summary

Investigated DestinE for all three additional scenarios: **flood risk, forest fires and air
quality**. One is usable with caveats, two are not.

**Flood: usable.** `EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS` on the DestinE data
lake gives hydrology impact indicators over Europe on a **5 km grid**, from the E-HYPE
hydrological model forced by bias-adjusted EURO-CORDEX simulations, produced by SMHI for C3S.
Relative change against a 1971-2000 baseline, for RCP2.6 / RCP4.5 / RCP8.5, in 30-year windows.
Rome and the Tiber are comfortably resolved.

> **Use the 2071-2100 window, and use `minimum_river_discharge`, not the flood variables.** At
> 2041-2070 the scenarios are indistinguishable (pathway separation ~0.1 of the within-map
> spread); at 2071-2100 they separate cleanly (~0.6-0.7). Low flows order correctly with the
> forcing at 2071-2100: **-15.3% / -18.9% / -55.1%** for RCP2.6 / RCP4.5 / RCP8.5. Note this is
> a **drought** signal, not a flood one. The flood metrics themselves never order correctly in
> any metric or period tested, so for Rome the robust, well-separated water signal is scarcity.

**Fire: does not work.** Two routes, both fail. Fire Weather Index is computable from Climate DT
at 4 km and gives plausible values, but Climate DT stops at 2049 and, more fundamentally, **FWI is
purely meteorological so none of the three intervention options change it** (firebreaks act on
fuel, detection on response time, settlement protection only on damage). The CMIP6 alternative
reaches 2050+ but is on a **~1 degree native grid, only 42 land cells over the whole of Italy**,
with weak scenario separation below SSP5-8.5 and a drought-code runaway over arid cells.

**Air quality: does not work.** Present-day data is excellent (CAMS European analysis and 4-day
forecast at 10 km, live, plus Sentinel-5P NO2). But there is **no future air quality projection
anywhere in DestinE**. Checked all 311 HDA v2 collections and the whole Earth Data Hub catalogue.
The CMIP6 collection carries **51 variables, all physical**: no ozone, PM2.5, NO2 or aerosol.
Composition projections come from AerChemMIP, which DestinE does not host, and which is ~100-250 km
anyway. Without a future half the scenario cannot be built.

**Cross-cutting, and the most important finding:** none of the intervention options in any scenario
can redraw a map. See [Interventions](#the-intervention-problem).

## The intervention problem

The brief says "the data itself suggests which one has the most structural impact over time". It
cannot. No dataset contains the effect of an intervention.

- **Fire.** FWI responds to temperature, humidity, wind and rain only. Two of the three options do
  not affect fire behaviour at all; the third affects only losses.
- **Flood.** The options are categories, not schemes: "barriers, levees and drainage upgrades" has
  no location, extent or design standard, so there is nothing to model. Street-level urban flooding
  additionally needs 1-2 m terrain and the storm sewer network, and the sewer network is not open
  data, which is precisely what option 1 modifies.
- **Urban heat** almost certainly has the same problem: land surface temperature will not respond
  to "plant more trees" either. It is the most tractable because the cooling literature is strong.

**Recommended design: interventions drive indicators, not geometry.** The map stays fixed and fully
evidence-based; each option changes a scorecard of numbers with cited ranges and stated
uncertainty (people moved out of the residual risk zone, return period bought, structures
protected). Nothing is fabricated and the visitor still compares three distinct intervention
logics. If the map must change per option, the only defensible route is real planned schemes with
published design parameters (the PGRA measures for the Tiber and Aniene), which is a literature and
permissions task of weeks.

## Biggest limitations

### Flood

1. **`flood_recurrence_*` are return levels, not frequencies.** The parameter name and the file's
   own `long_name` ("50 year flood recurence") both mislead. The NetCDF `summary` is explicit:
   "the 2,5,10, and 50 year **return period of annual daily maximum river discharge** ... given as
   a relative change". So +30% means the 1-in-50-year peak discharge is 30% **larger**, not 30%
   more frequent. A frequency shift is derivable from the 2/5/10/50-year levels together, but that
   is an extra computation, not a direct read.
2. **The flood metrics never order with the forcing.** Tested five metrics (2, 5, 10, 50-year
   return levels and mean annual maximum) across two periods. All ten combinations put RCP4.5
   above RCP8.5. Metric choice barely affects separability; period choice changes it fivefold.
   Since all five derive from the same annual-maximum series in one realisation, this is one
   result repeated, not ten confirmations.
3. **Everything here is a single ensemble member.** The dataset offers 4 RCMs and 10 hydrological
   models. Do not quote a single-cell value: the 50-year return level at the Rome Tiber cell reads
   -1.7% (RCP2.6), +40.2% (RCP4.5), +5.5% (RCP8.5). Any exhibit number needs the ensemble median
   and spread.
4. **Aridity is less reliable than low flows.** Ordered at 2041-2070 (-12.7 / -3.2 / +36.8) but
   not at 2071-2100 (+5.1 / -5.5 / +67.0). Usable as a low-versus-high contrast only.
5. **No inundation footprint.** This is discharge, not where water goes. The spatial hazard map
   must come from outside DestinE: **ISPRA IdroGEO** (official Italian national flood hazard
   mosaic, open, REST API, covers Lazio) or the JRC European river flood hazard maps (100 m,
   return periods 10-500, peer-reviewed, ESSD 2022).
6. **Full hydraulic modelling was ruled out.** Urban surface flow needs 1-2 m LiDAR, the sewer
   network and calibration data. TUFLOW / SFINCS / LISFLOOD-FP / HEC-RAS 2D with purchased data
   and a hydrologist is a multi-month project, not an exhibit feature.

### Fire

1. **Climate DT stops at 2049** (2040 for ICON), so it cannot say "2050".
2. **Climate DT high resolution carries only 7 variables** (`t2m`, `d2m`, `u10`, `v10`,
   `avg_tprate`, `lsm`, `orog`). Enough for FWI, but nothing else. At `standard` (0.35 deg) Rome is
   about 3 x 5 pixels, so city scale requires the 0.044 deg variant.
3. **CMIP6 is far too coarse for a city.** 0.9424 deg lat x 1.25 deg lon native; an Italy bounding
   box gets 99 cells of which only **42 carry land data**. The catalogue page's 0.044 deg claim is
   wrong for that store.
4. **CMIP6 scenario separation is weak below SSP5-8.5.** Days above FWI 30 north of 36N: 36.4
   (SSP1-2.6), 40.8 (SSP3-7.0), 51.2 (SSP5-8.5). Correctly ordered and SSP5-8.5 is +41%, but
   SSP3-7.0 versus SSP1-2.6 is +3 days and invisible without a difference plot.
5. **Drought codes run away** without a fire-season reset over a multi-year run: DC median 542 but
   max 11,178 against a normal ceiling near 1,000. Needs a season reset, `overwintering=True`, or
   a domain cut at ~35N.

### Air quality

1. **No future projection exists in DestinE.** The only AQ-related items beyond the present-day
   products are observation records, not projections.
2. **The nearest alternatives are unusable.** AerChemMIP is not hosted and is ~100-250 km. A
   Climate DT meteorological driver index (stagnation days from `u10`/`v10`, heat days from `t2m`,
   fewer wet-deposition days from `avg_tprate`) is computable at 4 km and honest, but must be
   labelled as exposure conditions, not a pollutant projection.
3. **Grid mismatch if combined.** CAMS is 0.1 deg, Climate DT high-res is 0.044 deg.

### Cross-cutting

1. **HDA endpoint is inconsistent per collection.** `/stac` returns 208 collections, `/stac/v2`
   returns 311. The CDS-backed ECMWF collections (SIS hydrology, SIS meteorology, EFAS, CMIP6) are
   searchable on **`/stac` only** and 404 on v2; Sentinel-1, MSG LSA-FRM and Copernicus DEM work on
   both. Check per collection rather than assuming either.
2. **Rome specifics matter for framing.** Central Rome is protected by the **muraglioni**, ~18 m
   walls built after the 1870 flood, and has not been inundated since 1937, so do not draw water
   over the historic centre. Rome does flood: the **Aniene overflowed in January 2026** with a red
   alert across municipalities III-VI, a real, recent, Sentinel-1-observable event. The realistic
   hazard is pluvial flash flooding plus the Aniene in the eastern periphery, and pluvial is the
   data-poor case.
3. **Expert review needed** before any intervention number goes in front of visitors: a fire
   behaviour specialist and a hydrologist. Also confirm how the muraglioni appear in ISPRA zones.
4. **Credibility risk.** This is public, under Serco and DestinE branding. Invented inundation
   presented as model output is indefensible if anyone asks how it was produced.
5. **`api.earthdatahub.destine.eu` TLS certificate expired 2026-08-13**, breaking all Climate DT
   access. Server-side; the other DestinE hosts were unaffected. Worth checking if a Climate DT
   script suddenly fails with an aiohttp `Event loop is closed` error, which buries the real SSL
   message about 25 lines down.

## What is in this directory

| path | what |
| ---- | ---- |
| `flood/sis_hydrology_rome.py` | orders any SIS hydrology indicator for the three RCPs, plots Rome, writes PNG + EPSG:4326 GeoTIFF |
| `flood/sis_flood_metric_scan.py` | decision tool: which metric and period actually separate the pathways |
| `flood/flood_recurrence_rome.py` | earlier single-variable version, superseded |
| `flood/assets/rome-hydrology/` | 27 outputs: aridity, soil moisture, mean/low discharge, 50-year flood, for 2041-2070 and 2071-2100 |
| `fire/fwi_cmip6_test.py` | CMIP6 FWI feasibility test, reference only, plus two PNGs |
| `urban_heat/climate_dt_daily_t2m.py` | Climate DT daily-mean t2m over a NUTS3 region as daily GeoTIFFs |

Also reusable: `DeltaTwin/ClimateIndexPlotter` has a verified Earth Data Hub access layer
(`models/climate_index_plotter/edh.py`) and all 27 ETCCDI indices including the extreme-rainfall
ones (`Rx1day`, `Rx5day`, `R10mm`, `R20mm`, `CDD`, `PRCPTOT`).
`DeltaTwin/LSTPlotter/models/lst_plotter/nuts_helper.py` resolves `find_nuts3_by_name("Roma")` to
`ITI43`, bounds `W=11.7338 S=41.4109 E=13.2963 N=42.2960`.

## Ordering data from HDA

The flood products are ordered on demand, which is not obviously documented anywhere:

1. `POST /stac/search` with a fully specified `query` returns one orderable item
   (`order:status: orderable`, provider `cop_cds`).
2. **GET** the item's `assets.downloadLink.href`. POST returns 404. The answer is **HTTP 202** with
   a `location` to poll, and it creates a real CDS job.
3. Poll that location until **HTTP 200**, which returns the zip. Observed 30 to 60 seconds.

`queryables` returns HTTP 400 listing the allowed values when fed a bogus parameter, which is the
fastest way to discover them. Constraints resolve in order, so set `time_aggregation` before
probing the rest. A working set:

```
product_type       climate_impact_indicators
variable           minimum_river_discharge      # or flood_recurrence_50_years_return_period etc
variable_type      relative_change_from_reference_period
time_aggregation   annual_mean
experiment         rcp_2_6 | rcp_4_5 | rcp_8_5 | degree_scenario
period             2071_2100                    # RCPs: 2011_2040, 2041_2070, 2071_2100
                                                # degree_scenario: 1_5_c, 2_0_c, 3_0_c
hydrological_model e_hypegrid                   # 5 km grid; e_hypecatch_m00..m07, vic_wur also exist
rcm                rca4 | csc_remo2009 | racmo22e | cclm4_8_17
gcm                ec_earth                     # only option
ensemble_member    r12i1p1                      # only option
```

Delivered files are ~11 MB NetCDF, 950 x 1000 cells on a projected grid with **no CRS declared**,
only curvilinear 2D lat/lon, so a GeoTIFF requires resampling. Cell size measured at Rome is
5.0 x 5.0 km, continuous over land (1081 of 1538 cells in the Rome box, i.e. the land fraction).
The scenario is embedded in the filename (`rcp26`/`rcp45`/`rcp85`), which the script asserts
against the request as a mislabelling guard.
