# Rome Future Week: scenario data

Scripts and outputs from assessing whether DestinE data can support the Serco Rome Future Week
control-room exhibit scenarios. Standalone utilities, not DeltaTwin components.

Feasibility findings, verified dataset details and open questions are in
[docs/rome-future-week-scenario-feasibility.md](docs/rome-future-week-scenario-feasibility.md).
Read that first if you are picking this up cold.

```
flood/         hydrology projections from the DestinE data lake (HDA)
urban_heat/    Climate DT daily temperature from Earth Data Hub (EDH)
fire/          CMIP6 Fire Weather Index feasibility test (reference only, not usable)
docs/          feasibility assessment
```

## Setup

```shell
pip install -r requirements.txt      # covers both flood/ and urban_heat/
cp .env.example .env                 # then fill in your credentials
. ./.env                             # values are export-prefixed, so this is enough
```

One dependency list covers everything here. `.env` is gitignored; `.env.example` is not.

### Credentials

Two DestinE services, two mechanisms:

| Variable | Used by | What it is |
| -------- | ------- | ---------- |
| `EDH_API_KEY` | `urban_heat/` | Earth Data Hub API key from your DESP account settings. Climate DT is restricted and needs upgraded DESP access. Can also be passed as `--api-key`. |
| `DESPAUTH_USER`, `DESPAUTH_PASSWORD` | `flood/` | Your destine.eu login. The flood scripts exchange these for an HDA token via `destinepyauth`, so no separate key is needed. |

Scripts fail immediately with a clear message if credentials are missing.

`urban_heat/climate_dt_daily_t2m.py` also reuses the Earth Data Hub access layer from
[ClimateIndexPlotter](../../DeltaTwin/ClimateIndexPlotter/models/climate_index_plotter/edh.py)
and the NUTS3 lookup from
[LSTPlotter](../../DeltaTwin/LSTPlotter/models/lst_plotter/nuts_helper.py), located by walking up
to the repository root. It therefore needs both components in the checkout and is not portable as
a single file. The `flood/` scripts are self-contained.

## flood/

Orders C3S/SMHI hydrology impact indicators from HDA
(`EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS`, E-HYPE on EURO-CORDEX, 5 km) and
plots them over Rome, one PNG plus a matching EPSG:4326 GeoTIFF per variable per RCP scenario.

```shell
cd flood
python sis_hydrology_rome.py                                          # default variable set, 2041-2070
python sis_hydrology_rome.py --variables minimum_river_discharge --period 2071_2100
python sis_flood_metric_scan.py                                       # which metric/period separates the pathways
```

Outputs land in `flood/assets/rome-hydrology/`. Products are ordered on demand from the
Copernicus CDS, so a first run waits 30 to 60 seconds per scenario; downloads cache to
`$TMPDIR/sis_hydrology_cache` (~11 MB each), outside the repo.

`sis_flood_metric_scan.py` is a decision tool, not a plotter: it tabulates whether a given metric
and period actually distinguishes the RCP pathways. `flood_recurrence_rome.py` is an earlier
single-variable version, superseded and kept for reference only.

### Three traps in the flood output

- **`flood_recurrence_*` are return levels, not frequencies.** +30% means the 1-in-50-year peak
  discharge is 30% larger, not that it happens 30% more often.
- **Every run is one ensemble member** of 4 RCMs x 10 hydrological models. Do not quote a
  single-cell value.
- **The flood metrics do not order with the forcing** in any metric or period tested, whereas
  `minimum_river_discharge` does (-15.3 / -18.9 / -55.1 % for RCP2.6 / 4.5 / 8.5 at 2071-2100).
  For Rome the robust signal is drought, not flooding. See §8 of the feasibility doc.

## urban_heat/

Exports Climate DT daily-mean 2 m temperature over a NUTS3 region as one GeoTIFF per model per
day, in EPSG:4326, kelvin, on UTC days.

```shell
cd urban_heat
python climate_dt_daily_t2m.py --region Roma --start 2049-01 --end 2049-12 \
    --models IFS-NEMO,IFS-FESOM --out-dir ./rome_t2m_2049
```

Produces `<out-dir>/<MODEL>/t2m_daily_mean_<MODEL>_<YYYYMMDD>.tif`. Over Rome that is 365 files
per model, about 0.32 GB streamed per model, well under a minute. `--dry-run` validates and
reports volumes without writing. `--clip-to-region` masks outside the NUTS3 polygon instead of
keeping the full bounding box.

Defaults to `--resolution high` (0.044 deg, ~4 km) because at `standard` (0.35 deg) the Rome
bounding box is only about 3 x 5 cells. High resolution gives about 20 x 35.

**Model coverage under SSP3-7.0**, verified against the live store: IFS-NEMO and IFS-FESOM reach
2049-12-31, but **ICON stops at 2040-12-31**. The script validates every requested model's real
time axis before streaming and fails with its actual coverage, so drop `ICON` for a 2049 request.

### Tests

```shell
cd urban_heat && pytest test_climate_dt_daily_t2m.py
```

17 offline tests covering month parsing, the cell-centre-to-corner geotransform, north-up row
order, and a GeoTIFF round trip. Streaming and the NUTS3 download are not exercised.

## fire/

**Reference only. Not usable for the simulations.** A one-off feasibility test asking whether
CMIP6 on Earth Data Hub can separate SSP scenarios by Fire Weather Index over the Mediterranean
at mid-century. Kept for the record; it is not a pipeline and has no CLI.

```shell
cd fire && python fwi_cmip6_test.py     # needs EDH_API_KEY, runs in about 15 s
```

Store `cmip6/CMCC-CM2-SR5-ScenarioMIP-r1i1p1f1-day-gn-v0.zarr` on `data.earthdatahub.destine.eu`
(note: a different host from Climate DT, and it needs `zarr_format=3`). Computes FWI with
`xclim.indices.fire.cffwis_indices` from daily `tas`, `hurs`, `pr`, `sfcWind` and maps days per
Apr-Sep season above FWI 30, averaged over 2045-2054, for SSP1-2.6 / SSP3-7.0 / SSP5-8.5.
Outputs `fwi_days_by_ssp.png` and `fwi_days_diff.png`.

Why it is not good enough:

- **Resolution is ~1 degree, not the 0.044 deg the catalogue page claims.** Native grid is
  0.9424 deg lat x 1.25 deg lon, about 105 x 103 km at 42N. An Italy bounding box gets 99 cells
  of which only **42 carry land data**. Basin-scale at best, useless at city scale.
- **Scenario separation is weak below SSP5-8.5.** Land mean days above FWI 30 north of 36N:
  36.4 (SSP1-2.6), 40.8 (SSP3-7.0), 51.2 (SSP5-8.5). Correctly ordered, and SSP5-8.5 is
  +41%, but SSP3-7.0 versus SSP1-2.6 is only +3 days basin-wide and is not distinguishable by
  eye without the difference plot.
- **The drought codes run away.** With no fire-season reset over a continuous 10-year run, and a
  box reaching to 30N, hyper-arid Saharan cells accumulate DC without bound: median 542 but max
  11,178 against a normal ceiling near 1,000. This inflates the full-box mean from 36 to 67 days
  and dominates the colour scale. Any real use needs a season reset, `overwintering=True`, or a
  domain cut at ~35N.

Useful things it did establish: the four SSPs are a single `experiment_id` dimension in one store
(`ssp126`, `ssp245`, `ssp370`, `ssp585`), longitude is 0-360 so a Mediterranean box wraps, the
calendar is `DatetimeNoLeap`, and FWI is computable from CMIP6 daily fields at all. FWI over
Climate DT at 4 km, which is what an exhibit would actually need, is discussed in the
[feasibility doc](docs/rome-future-week-scenario-feasibility.md).
