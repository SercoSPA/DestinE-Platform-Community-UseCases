# Rome Future Week: Scenario Data Feasibility

Handoff summary of a Claude Code session assessing whether DestinE data can support the four
Serco Rome Future Week exhibit scenarios. Every technical fact below was verified live against
the APIs during that session, not recalled from documentation. Dates of verification: 2026-08-04
to 2026-08-07.

## 1. The brief

An interactive control-room experience. The visitor plays an operator managing four critical
scenarios by analysing satellite data and climate projections, choosing an intervention
strategy under time pressure. Proven gaming mechanics: control room, alarms, Operator Control
Panel, scoring, leaderboard.

Four priority scenarios from the Rome Future Week Master Production Table: **Urban Heat, Flood
Risk, Air Quality, Forest Fires**.

Each scenario is built on the same principle:

1. Data observed today (Copernicus satellites)
2. A future projection assuming no intervention (DestinE Climate DT and impact models)
3. Three real-world intervention options: one rapid and localised, one broad and lasting, one
   addressing the root cause

The brief states: "the data itself suggests which one has the most structural impact over time".

### Intervention options as written

**Flood Risk (originally scoped as "FI-03 Northern EU", later relocated to Rome)**
- Barriers, levees, and drainage upgrades
- Restoration of wetlands and floodplains
- Urban planning in exposed areas

Display data: flood risk areas (current perimeter from SAR observations and hydrological
models); exposed infrastructure (roads, bridges, transport hubs); 2050 projection without
intervention (increasing extreme rainfall intensity, expanding flood area).

**Air Quality**
- Electrification of public transportation and reduction of combustion emissions
- Low-emission zones and pedestrian areas
- Expansion of urban green corridors

Display data: NO2 and PM2.5 along traffic corridors; exposed population (residential areas,
schools, hospitals); 2050 projection without intervention (exposure stable or worsening).

**Forest Fires**
- Firebreaks and vegetation management
- Early detection via satellite and drones, rapid response
- Protection of settlements and vulnerable infrastructure

Display data: Fire Weather Index risk class; vegetation status versus seasonal norms; 2050
projection without mitigation (warmer, drier, windier conditions expanding high-risk areas).

**Urban Heat** was already in development in this repository and was not assessed in detail.

## 2. Headline conclusions

| scenario | present (satellite) | future projection | intervention options |
| -------- | ------------------- | ----------------- | -------------------- |
| Forest fires | available, partly already built | **proven working** | **not representable** |
| Flood risk | available | available | **not representable as maps** |
| Air quality | excellent | **does not exist** | not assessed, blocked upstream |

Three conclusions drove the discussion:

1. **Air quality has no future half.** DestinE hosts many climate projections but none carry
   pollutant variables. Verified below.
2. **The intervention options cannot produce different future maps.** This is the central
   finding and it applies to fire and flood alike, probably to urban heat too.
3. **The recommended fix: interventions drive indicators, not geometry.** The map shows real
   observed and projected data and stays fixed. Each option changes a scorecard of numbers
   sourced from literature with stated uncertainty.

### Why interventions cannot redraw the maps

**Fire.** The Fire Weather Index is purely meteorological (temperature, humidity, wind, rain).
None of the three options change it. Firebreaks act on fuel, so on spread and burned area.
Early detection acts on time to containment. Settlement protection acts only on damage, not on
the fire at all. Two of the three do not affect fire behaviour; the third affects only losses.

**Flood.** The options do relate to flood risk, but as written they have no location, no extent
and no design standard. "Barriers, levees, and drainage upgrades" is a category, not a scheme.
There is nothing to put into a model, so any intervention map would be invented rather than
approximated. Separately, street-level urban flooding needs 1 to 2 m terrain and the storm
sewer network; the sewer network is not open data, and it is exactly what option 1 modifies.

The interventions act on different links in the risk chain (hazard, exposure, vulnerability),
which is arguably a better story than implying they all shift the same map, and it matches the
brief's "different intervention logics" framing.

## 3. Verified technical facts

### 3.1 HDA (DestinE data lake) API

- `https://hda.data.destination-earth.eu/stac/collections` returns **208** collections. This is
  an older or partial view.
- `https://hda.data.destination-earth.eu/stac/v2/collections` returns **311** collections.
  **Use v2.** A sweep of the v1 endpoint missed 105 collections including CMIP6.
- Collection listing and `queryables` work without authentication. Item search
  (`POST /stac/search`) requires auth.
- Auth in this repo: `destinepyauth`, `get_token("hda").access_token`, used by
  `DeltaTwin/FireMonitor/models/fire_monitor/hda_helper.py`. Endpoint constant there is
  `https://hda.data.destination-earth.eu/stac/v2`.
- Useful trick: `queryables` returns HTTP 400 with the full list of allowed values when given a
  bogus parameter, e.g.
  `?ecmwf:variable=bogus`. This is how the variable lists below were obtained.
- Known bug: `SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS` queryables fail on `/stac/v2`
  ("Collection SIS_HYDRO_VAR_PROJ not found"). Use `/stac` for that collection's queryables.

### 3.2 Climate DT on Earth Data Hub

URL pattern:
```
https://api.earthdatahub.destine.eu/climate-dt-2/<MODEL>-<EXPERIMENT>-sfc-hourly-<VARIANT>-v0.zarr
```
Auth: HTTP basic, user `edh`, password = EDH API key. Models: `IFS-NEMO`, `IFS-FESOM`, `ICON`.
Experiments: `hist`, `SSP3-7.0`.

| variant | global grid | spacing | pixel at 42N | t2m chunks | variables |
| ------- | ----------- | ------- | ------------ | ---------- | --------- |
| `standard` | 512 x 1025 | 0.352 deg | 39 km NS, 29 km EW | 1440 x 64 x 64 | 36 |
| `high-timeseries` | 4096 x 8193 | 0.0440 deg | 4.9 km NS, 3.6 km EW | 6480 x 32 x 32 | 7 |
| `high-maps` | 4096 x 8193 | 0.0440 deg | as above | 24 x 512 x 512 | 7 |

**This is the single most important constraint.** The high-resolution variants carry only 7
variables: `t2m`, `d2m`, `u10`, `v10`, `avg_tprate`, `lsm`, `orog`. Anything city-scale must be
built from those. At 0.352 deg Rome is about 3 x 5 pixels, which is unusable for a city.

Use `high-timeseries`, not `high-maps`: the latter's 24-hour time chunks are wrong for reading
whole multi-decade periods over a small area.

The 36 standard-resolution variables add pressure, radiation fluxes, cloud, snow, wind speed
(`si10`), skin temperature, total column water, and notably surface and sub-surface runoff
(`avg_surfror`, `avg_ssurfror`).

**Temporal coverage, verified:**

| model | SSP3-7.0 coverage |
| ----- | ----------------- |
| IFS-NEMO | 2015-01-01 to **2049-12-31** |
| IFS-FESOM | 2015-01-01 to **2049-12-31** |
| ICON | 2015-01-01 to **2040-12-31** |

The brief says 2050 throughout. Climate DT cannot reach 2050. ICON cannot reach 2049.

There is **no daily surface product**, only `sfc-hourly` plus ocean/sea-ice `o2d-daily`, so
hourly-to-daily aggregation on the client is unavoidable.

### 3.3 Earth Data Hub catalogue

Five collections: ERA5 Zarr mirror, Climate DT generation 2, CMIP6 (only two models,
CMCC-CM2-SR5 and MPI-ESM1-2-HR), Copernicus DEM, Sentinel-1 ARD. **No air quality data.**

Sentinel-1 ARD as Zarr with the same API key as Climate DT is useful for the flood present-day
panel.

### 3.4 CMIP6 in HDA

`EO.ECMWF.DAT.CMIP6_CLIMATE_PROJECTIONS` (only visible on `/stac/v2`).

- Coverage **1860 to 2300**, so unlike Climate DT it genuinely reaches 2050 and beyond
- Experiments: `historical`, `ssp1_1_9`, `ssp1_2_6`, `ssp2_4_5`, `ssp3_7_0`, `ssp4_3_4`,
  `ssp4_6_0`, `ssp5_3_4os`, `ssp5_8_5`
- Temporal resolution: `daily`, `monthly`, `fixed`
- **51 variables, all physical.** No ozone, PM2.5, NO2 or aerosol. Composition fields come from
  AerChemMIP, which DestinE does not host.
- Includes `total_runoff`, `moisture_in_upper_portion_of_soil_column`,
  `capacity_of_soil_to_store_water`, `daily_maximum_near_surface_air_temperature`,
  `near_surface_relative_humidity`, `near_surface_wind_speed`, `precipitation`
- Tradeoff: native model grid, roughly 100 to 250 km, so Rome is one or two cells. Fine for a
  headline number or scenario comparison, useless as a city map.

### 3.5 Fire data

**Future projection: proven working.** `xclim.indices.cffwis_indices` computes the full Canadian
Fire Weather Index System from the 7 high-resolution Climate DT variables:

```python
tas     = t2m daily mean
pr      = avg_tprate daily mean * 86400        # mm/d
sfcWind = sqrt(u10**2 + v10**2) daily mean
hurs    = xclim.indices.relative_humidity(tas=tas, tdps=d2m)
out = xclim.indices.cffwis_indices(tas=tas, pr=pr, sfcWind=sfcWind, hurs=hurs,
                                   lat=ds.lat, season_method=None)
# returns DC, DMC, FFMC, ISI, BUI, FWI
```

Implementation notes:
- FWI is recursive over time, so inputs need `.chunk({"time": -1})` or `apply_ufunc` errors
- The function is `xclim.indices.relative_humidity`, not `relative_humidity_from_dewpoint`
- Wrap in `xclim.set_options(cf_compliance="log", data_validation="log")`

Result over Rome NUTS3, April to September 2049, IFS-NEMO, 0.044 deg:

| index | mean | p95 | max |
| ----- | ---- | --- | --- |
| FWI | 13.70 | 35.77 | 52.96 |
| DC | 381.63 | 908.18 | 1033.32 |
| BUI | 110.04 | 327.28 | 452.09 |

About 22 days per season above FWI 30 ("very high danger"). Physically plausible for a warm
Mediterranean summer.

**Present-day sources (all HDA):**
- `EO.ECMWF.DAT.CEMS_FIRE_HISTORICAL` fire danger indices, 1979 to 2022-10-29, global, EFFIS.
  Gives a real FWI baseline to compare the projection against.
- `EO.EUM.DAT.MSG.LSA-FRM` Fire Risk Map, already used by the FireRiskPlotter component
- `EO.EUM.DAT.MTG.FCI-ACTIVE_FIRE-L2-V1` already used by the FireMonitor component
- `EO.EUM.DAT.SENTINEL-3.FRP` SLSTR fire radiative power

**Vegetation stress versus seasonal norm:**
- `EO.CLMS.DAT.SENTINEL-2.HRVPP_ST` Seasonal Trajectories, 10-daily. Built for
  anomaly-versus-norm comparison, which is exactly what the scenario asks for.
- `EO.CLMS.DAT.GLO.NDVI300_V1`, `LAI300_V1`, `FAPAR300_V1` at 300 m as alternatives

### 3.6 Flood data

**The key find.** `EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS` contains
pre-computed flood return-level change, verified by download (see §8):

> **Correction.** An earlier version of this document described the `flood_recurrence_*`
> variables as the "how much more often" quantity. They are not. The NetCDF `summary` attribute
> states: "Calculated as the 2,5,10, and 50 year **return period of annual daily maximum river
> discharge**. This index is given as a relative change". So +30% means the 1-in-50-year peak
> discharge is 30% **larger**, not that it happens 30% more often. The HDA parameter name and the
> file's own `long_name` ("50 year flood recurence") are both misleading on this point.
>
> A frequency shift is still derivable, because the 2, 5, 10 and 50 year return levels together
> sample the discharge-frequency curve, so you can find which future return period matches the
> reference 50-year level. That is an extra computation step, not a direct read, and the
> return-period-swap design in §5 depends on doing it.

- Variables include `flood_recurrence_2_years_return_period`,
  `flood_recurrence_5_years_return_period`, `flood_recurrence_10_years_return_period`,
  `flood_recurrence_50_years_return_period`, `river_discharge`, `maximum_river_discharge`,
  `minimum_river_discharge`, `mean_runoff`, `mean_soil_moisture`, `wetness_actual`,
  `aridity_actual`, plus nitrogen and phosphorus water-quality indicators
- `variable_type`: `relative_change_from_reference_period` or `absolute_values`
- `experiment`: `rcp_2_6`, `rcp_4_5`, `rcp_8_5`, `degree_scenario`
- Other parameters: `product_type`, `time_aggregation`, `hydrological_model`,
  `rcm` (`cclm4_8_17`, `racmo22e`, `rca4`), `gcm`, `ensemble_member`, `period`
- Model: **E-HYPE** forced by eight bias-adjusted EURO-CORDEX EUR-11 simulations, produced and
  quality-assured by SMHI for C3S
- Provided at **catchment scale and on a 5 km grid**. A 5 km grid resolves the Tiber
  comfortably (catchment about 17,000 km2 at Rome).
- Reference period 1971-2000. Future periods 2011-2040, **2041-2070**, 2071-2100, plus degree
  scenarios at 1.5, 2.0 and 3.0 C. The 2041-2070 window brackets 2050 properly, which fixes the
  problem that Climate DT stops in 2049.
- Temporal extent in STAC: 1970-01-01 to 2100-12-31. Spatial bbox [-22, 27, 45, 72].
- Reminder: query its `queryables` on `/stac`, not `/stac/v2`.

Also `EO.ECMWF.DAT.SIS_HYDROLOGY_METEOROLOGY_DERIVED_PROJECTIONS` for temperature and
precipitation impact indicators, 1970 to 2100.

**Present-day and near-term (all HDA):**
- `EO.ECMWF.DAT.EFAS_HISTORICAL`, `EFAS_FORECAST`, `EFAS_REFORECAST`, `EFAS_SEASONAL` European
  Flood Awareness System river discharge
- `EO.ECMWF.DAT.CEMS_GLOFAS_*` the global equivalents
- `EO.ESA.DAT.SENTINEL-1.L1_GRD` SAR, or the Sentinel-1 ARD collection on Earth Data Hub
- `EO.GSW.DAT.*` Global Surface Water occurrence, extent, recurrence, seasonality

**Spatial hazard footprint. Not in DestinE, needs external authoritative sources:**
- **ISPRA IdroGEO** is the best option for Italy. Official national mosaic of flood hazard
  zones compiled from the River Basin District Authorities, refreshed every three years, open
  data with a REST API, multilingual, covers Lazio. Finer and more authoritative for Italy than
  the JRC European maps.
- **JRC river flood hazard maps for Europe and the Mediterranean**, 100 m, return periods 10,
  20, 50, 100, 200, 500 years, cell values are water depth in metres, GeoTIFF, open from the JRC
  Data Catalogue, peer-reviewed in ESSD 2022 (Dottori et al.). Produced with LISFLOOD plus a
  hydrodynamic model. Covers Italy and the Tiber.
- For planned interventions with real design parameters: the PGRA published by the Autorita di
  Bacino Distrettuale dell'Appennino Centrale.

**Rome specifics, important for framing:**
- Central Rome is protected by the **muraglioni**, roughly 18 m embankment walls built after the
  1870 Tiber flood. No major inundation of central Rome since 1937. Do not draw water over the
  historic centre unless explicitly framing defence overtopping or failure.
- Rome does flood. The **Aniene overflowed in January 2026**: red alert for high hydraulic
  criticality, flooding across municipalities III, IV, V and VI (Tiburtino, Ponte Mammolo, Tor
  Cervara), water entering businesses, plus drainage flooding at EUR and Ardeatina, with the
  Tiber embankments closed at over eight metres. This is a real, recent, Sentinel-1-observable
  event that can anchor the present-day panel.
- Rome already implemented intervention option 1 (barriers and levees) in the 1870s and it
  worked. That is a stronger narrative than a generic flood map: show what climate change does
  to the protection level that choice bought, and why options 2 and 3 matter now.
- The realistic present-day Rome hazard is **pluvial flash flooding plus the Aniene in the
  eastern and northeastern periphery**, not the Tiber through the centre. Pluvial is the
  data-poor case: not covered by the 5 km hydrology dataset nor the JRC river maps.

**Why full hydraulic modelling was ruled out.** Urban surface-water flow follows kerbs, streets
and underpasses, needing 1 to 2 m LiDAR; the 30 m Copernicus DEM cannot see a street and
buildings are not obstacles. There is no observed-depth data for calibration. The storm sewer
network dominates urban pluvial flooding and is almost never open data. HAND and bathtub methods
are fluvial techniques and invalid for pluvial. Doing it properly means TUFLOW, SFINCS,
LISFLOOD-FP or HEC-RAS 2D with purchased data and a hydrologist: a multi-month specialist
project, not an exhibit feature.

### 3.7 Air quality data

**Present day is excellent.**

`EO.ECMWF.DAT.CAMS_EUROPE_AIR_QUALITY_FORECASTS` is the better choice for a control room:
- Contains **both** `analysis` (assimilates EEA in-situ observations) and `forecast`
- Rolling archive, live to today. The API accepted dates through 2026-08-06.
- 0.1 deg (about 10 km), hourly, seven height levels, ensemble of eleven systems with spread as
  an uncertainty estimate
- Forecast lead time **0 to 96 hours**, confirmed from the API's own allowed values. Four days,
  not decades.
- **28 variables**: `nitrogen_dioxide`, `particulate_matter_2.5um`, `particulate_matter_10um`,
  `ozone`, `nitrogen_monoxide`, `sulphur_dioxide`, `ammonia`, `carbon_monoxide`,
  `peroxyacyl_nitrates`, `formaldehyde`, `glyoxal`, `non_methane_vocs`, `dust`,
  `secondary_inorganic_aerosol`, PM2.5 speciation (`pm2.5_sulphate`, `pm2.5_nitrate`,
  `pm2.5_ammonium`, `pm2.5_total_organic_matter`), `total_elementary_carbon`,
  `residential_elementary_carbon`, `pm10_sea_salt_dry`, **`pm10_wildfires`** (a nice cross-link
  to the fire scenario), and six pollen types

`EO.ECMWF.DAT.CAMS_EUROPE_AIR_QUALITY_REANALYSES`: 0.1 deg, 2013-01-01 to 2023-12-31, nine-model
ensemble median, validated. Better for a climatological baseline, but stops over two years ago.

`EO.ESA.DAT.SENTINEL-5P.TROPOMI.L2` for satellite NO2 columns (L1B also available). Roughly
5.5 x 3.5 km, so it will not resolve individual streets.

Exposed population is not in DestinE at the needed detail; use GHSL plus OpenStreetMap.

**The future does not exist.** Verified across all 311 HDA v2 collections and the Earth Data Hub
catalogue: no gridded air quality projection anywhere in DestinE. CMIP6 there is physical
variables only. The only AQ-related additions found in v2 were observation records, not
projections (`EO.EUM.DAT.METOP.GOMPMA020100` aerosol optical properties,
`EO.EUM.DAT.METOP.MXI-DR-O3` IASI ozone CDR).

Options considered, in order of preference:
1. **Reframe as a meteorological driver index** computed from Climate DT at 4 km: stagnation
   days from `u10`/`v10`, heat days driving photochemistry from `t2m`, fewer wet-deposition days
   from `avg_tprate`. Holds emissions constant and shows dispersion conditions worsening. Honest,
   computable with existing code, and matches the scenario's own wording, which only claims
   exposure will "remain stable or worsen". Must be labelled as exposure conditions, not a
   pollutant forecast.
2. **CMIP6 AerChemMIP from ESGF** gives genuine surface PM2.5 and ozone under SSP3-7.0 but at
   roughly 100 to 250 km. Rome is one or two cells, unusable for a control-room map.
3. **Do not show a projected concentration field.** Present-day CAMS plus a documented
   literature-based scenario statement.

Caveat if combining: CAMS is 0.1 deg and Climate DT high-res is 0.044 deg, so present and future
panels would not share a grid.

### 3.8 Supporting exposure layers, all in HDA v2

- Terrain: `EO.DEM.DAT.COP-DEM_GLO-30-DGED`, `-30-DTED`, `-90-DGED`, `-90-DTED`
- Land cover and fuel: `EO.CLMS.DAT.CORINE`, `ML.EUROSAT.DAT.LULC_10_GEOREF`
- Population and built environment: `EO.GHSL.DAT.POP`, `BUILT-S`, `BUILT-H`, `BUILT-V`,
  `BUILT-C`, `SMOD`, `ESM`, `FUA`, `UCDB-DOMAIN`, `DUC`, `ENACT-POP`
- Statistics: `STAT.EUSTAT.DAT.POP_DENSITY_NUTS3`, `POP_AGE_GROUP_SEX_NUTS3`

Roads, bridges and transport hubs specifically are not in DestinE; OpenStreetMap is the
practical open source.

## 4. Existing assets in this repository

| component | what it does |
| --------- | ------------ |
| `DeltaTwin/FireMonitor` | MTG FCI Active Fire L2 from HDA over a NUTS2 region |
| `DeltaTwin/FireRiskPlotter` | MSG Fire Risk Map 5-day FWI forecast from HDA, animated GIF |
| `DeltaTwin/LSTPlotter` | Landsat Collection 2 L2 land surface temperature over a NUTS3 region |
| `DeltaTwin/ClimateIndexPlotter` | All 27 ETCCDI indices from Climate DT streamed from EDH |

`ClimateIndexPlotter` is the most reusable for these scenarios. It already contains a verified
EDH access layer (`models/climate_index_plotter/edh.py`) with resolution selection, chunk-aware
volume estimation and a hard 500 GB guard, and the extreme-rainfall indices the flood scenario
needs are already implemented (`Rx1day`, `Rx5day`, `R10mm`, `R20mm`, `CDD`, `PRCPTOT`).

Performance reference: three indices over Italy (`7,36,19,47`) at standard resolution takes
**8m01s**, down from **40m56s** before a read-amplification fix, with pixel-identical output.

`DeltaTwin/ClimateIndexPlotter/scripts/climate_dt_daily_t2m.py` exports daily-mean t2m over a
NUTS3 region as one GeoTIFF per model per day. Rome at high resolution is 20 x 35 cells,
0.32 GB per model-year, about 7.5 s to compute a year of daily means.

NUTS3 lookup: `DeltaTwin/LSTPlotter/models/lst_plotter/nuts_helper.py`,
`find_nuts3_by_name("Roma")` gives `ITI43`, bounds
`W=11.7338 S=41.4109 E=13.2963 N=42.2960` (1.562 x 0.885 deg).

## 5. Recommended design

**Interventions drive indicators, not geometry.**

One map, fixed, entirely from real data:
- Present hazard: ISPRA IdroGEO flood zones, plus Sentinel-1 of the January 2026 Aniene event
  for the SAR-observed perimeter the scenario asks for. For fire: MSG FWI plus CEMS fire danger
  baseline plus HRVPP vegetation anomaly.
- Future without intervention: `SIS_HYDROLOGY` change in the 50-year flood return level at 5 km
  for 2041-2070, converted to a frequency shift using the 2/5/10/50-year levels (see §3.6), or
  Climate DT FWI at 4 km for fire.
- Exposure: GHS-POP and GHS-BUILT intersected with the hazard zone, plus OSM for roads and
  bridges.

Three option cards, not three maps. Each option changes numbers with cited ranges and visible
uncertainty:

| flood option | what it changes | indicator |
| ------------ | --------------- | --------- |
| Barriers, levees, drainage | return period absorbed | people and buildings removed from the residual zone at a stated design standard |
| Wetlands, floodplains | upstream peak discharge | percent peak reduction from literature, converted to a return-period equivalent |
| Urban planning | exposure only | assets removed from the zone, hazard unchanged |

If the map must change per option, the only defensible route is to use actual planned schemes
with published design parameters (the PGRA measures), which is a literature and permissions task
of weeks and constrains the exhibit to whatever is genuinely planned.

## 6. Open questions and risks

1. **Does the four-scenario structure survive?** The current recommendation being fed back to
   colleagues is to keep only Urban Heat. The alternative is to keep all four using the
   indicator-not-map design above.
2. **Urban Heat almost certainly has the same problem.** Land surface temperature will not
   respond to "plant more trees" either. It is the most tractable of the four because the
   albedo and green-cover cooling literature is strong, but the principle should be settled now.
3. **Fluvial or pluvial for Rome?** Fluvial (Tiber and Aniene) has a complete data chain.
   Pluvial is the realistic Rome hazard but has no usable model or footprint.
4. **The brief's claim that "the data itself suggests which one has the most structural
   impact"** is not achievable. No dataset contains the effect of the interventions. This needs
   either literature-based effect sizes or a simple parametric model, as an explicit documented
   design decision.
5. **2050 versus 2049.** If the copy must say 2050, use CMIP6 (to 2300) or the hydrology
   projections (2041-2070). Climate DT cannot reach it.
6. **Expert review needed.** A fire behaviour specialist and a hydrologist should sanity-check
   any intervention coefficients before numbers go in front of visitors. Also confirm how the
   muraglioni are represented in the ISPRA hazard zones before drawing anything on a map.
7. **Credibility risk.** This is a public exhibit under Serco and DestinE branding. Presenting
   invented inundation as model output is hard to defend if anyone asks how it was produced.

## 7. Corrections made during the session

Recorded because they affect how much to trust each claim:

- An initial sweep used the HDA `/stac` endpoint (208 collections) and concluded no CMIP6 was
  available. Wrong: `/stac/v2` has 311 collections including
  `EO.ECMWF.DAT.CMIP6_CLIMATE_PROJECTIONS`. **But not simply "always use v2":** the CDS-backed
  ECMWF collections (SIS hydrology, SIS meteorology, EFAS, CMIP6) are searchable on `/stac`
  only, and return 404 or "Something went wrong" on `/stac/v2`, while Sentinel-1, MSG LSA-FRM
  and Copernicus DEM search fine on both. Check per collection.
- An initial estimate of "about one week" to build a rainfall-to-inundation proxy was far too
  optimistic and was withdrawn.
- An initial statement that the Tiber "never floods" was too absolute. Central Rome behind the
  muraglioni does not flood; the Aniene and the periphery do, most recently January 2026.
- `flood_recurrence_*` was described as a frequency ("how much more often"). It is a return
  **level** (discharge magnitude). See the correction box in §3.6.
- The `e_hypegrid` field was described as a sparse river network on the basis of 34% valid cells
  file-wide. Measured over the Rome box it is 1081 of 1538 cells, i.e. 70%, which is the land
  fraction. It is continuous over land at 5.0 km; the 34% is ocean and out-of-domain area.

## 8. Retrieval verified end to end

Data was ordered and downloaded, not just catalogued. Scripts:

| script | what it does |
| ------ | ------------ |
| `DeltaTwin/ClimateIndexPlotter/scripts/sis_hydrology_rome.py` | orders any SIS hydrology indicator for the three RCPs and plots Rome, one PNG per variable per scenario |
| `DeltaTwin/ClimateIndexPlotter/scripts/flood_recurrence_rome.py` | the earlier single-variable version, superseded by the above |

Output PNGs: `docs/assets/rome-hydrology/`.

The order flow, which is not documented obviously anywhere:

1. `POST /stac/search` with a fully specified `query` block returns exactly one orderable item
   (`order:status: orderable`, provider `cop_cds`)
2. **GET** the item's `assets.downloadLink.href` (POST returns 404). Answer is **HTTP 202** with a
   `location` to poll, and it creates a real CDS job
   (`processID=sis-hydrology-variables-derived-projections`)
3. Poll that location until **HTTP 200**, which returns the zip. Observed 30 to 60 seconds.

A working parameter set, all values confirmed against the `queryables` endpoint. Note that
`queryables` returns HTTP 400 listing the allowed values when fed a bogus parameter, which is the
fastest way to discover them, and that constraints resolve in order, so set
`time_aggregation` before probing the rest:

```
product_type       climate_impact_indicators
variable           flood_recurrence_50_years_return_period
variable_type      relative_change_from_reference_period
time_aggregation   annual_mean          # only valid value for the flood variables
experiment         rcp_2_6 | rcp_4_5 | rcp_8_5 | degree_scenario
period             2041_2070            # RCPs: 2011_2040, 2041_2070, 2071_2100
                                        # degree_scenario: 1_5_c, 2_0_c, 3_0_c
hydrological_model e_hypegrid           # 5 km grid; e_hypecatch_m00..m07, vic_wur also exist
rcm                rca4 | csc_remo2009 | racmo22e | cclm4_8_17
gcm                ec_earth             # only option
ensemble_member    r12i1p1              # only option
```

The delivered file for RCP8.5 was
`rdisreturnmax50_tmean_rel_E-HYPEgrid-EUR-11_ICHEC-EC-EARTH_rcp85_r12i1p1_SMHI-RCA4-v1_2041-2070_1971-2000_grid5km_v1.nc`,
11.4 MB, 950 x 1000 cells on a projected grid with 2D lat/lon, units %. Cell size measured at
Rome: 5.0 km x 5.0 km. The scenario is embedded in the filename (`rcp26`/`rcp45`/`rcp85`), which
the script asserts against the request as a mislabelling guard.

**Do not quote single-cell values from one ensemble member.** The 50-year return level change at
the Rome Tiber cell reads -1.7% under RCP2.6, +40.2% under RCP4.5 and +5.5% under RCP8.5. That
ordering does not follow the forcing, and the scenario labelling was verified correct, so it is
sampling noise in a 50-year return level estimated from a 30-year window. Mean-state indicators
(aridity, soil moisture, mean and low discharge) are far more stable and should be read first.
With 4 RCMs and 10 hydrological models available, any number for the exhibit needs the ensemble
median and its spread.
