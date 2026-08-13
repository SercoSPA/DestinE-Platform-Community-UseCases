# Scripts

Standalone utilities built on the ClimateIndexPlotter model code, run directly rather than as
DeltaTwin components.

## climate_dt_daily_t2m.py

Exports Climate DT daily-mean 2 m temperature (`t2m`) over a NUTS3 region as one GeoTIFF per
model per day.

Hourly `t2m` is streamed from Earth Data Hub, averaged over each UTC day, and written as
single-band float32 GeoTIFFs in EPSG:4326. The script reuses the Earth Data Hub access layer
from [this component](../models/climate_index_plotter/edh.py) and the NUTS3 lookup from
[LSTPlotter](../../LSTPlotter/models/lst_plotter/nuts_helper.py), so it needs both components
present in the checkout.

### Install

```shell
pip install -r requirements.txt
```

These are a subset of the ClimateIndexPlotter model requirements plus `rasterio` for GeoTIFF
output; `xclim`, `matplotlib` and `cartopy` are not needed. If you already have a working
ClimateIndexPlotter environment, adding `rasterio` to it is enough.

### Usage

```shell
export EDH_API_KEY="<your Earth Data Hub API key>"

python climate_dt_daily_t2m.py \
    --region Roma \
    --start 2049-01 --end 2049-12 \
    --models IFS-NEMO,IFS-FESOM \
    --out-dir ./rome_t2m_2049
```

That produces `rome_t2m_2049/<MODEL>/t2m_daily_mean_<MODEL>_<YYYYMMDD>.tif`, 365 files per
model. Over Rome it streams about 0.32 GB per model and takes well under a minute.

Add `--dry-run` to validate the request and report volumes without writing anything.

### Options

| Option | Description |
| ------ | ----------- |
| `--region` | Region or city name, looked up against the Eurostat NUTS3 classification (e.g. `Roma` resolves to `ITI43`). Mutually exclusive with `--nuts3`. |
| `--nuts3` | NUTS3 code directly, e.g. `ITI43`. |
| `--start`, `--end` | First and last month as `YYYY-MM`, both inclusive. |
| `--models` | Comma-separated subset of `IFS-NEMO`, `IFS-FESOM`, `ICON`. Defaults to all three. |
| `--resolution` | `high` (0.044 deg, ~4 km, default) or `standard` (0.35 deg, ~29 km). |
| `--out-dir` | Output directory. One subdirectory per model is created. |
| `--clip-to-region` | Mask cells outside the NUTS3 polygon to nodata. Without it, the full bounding box is written. |
| `--api-key` | Earth Data Hub API key. Defaults to `EDH_API_KEY`. |
| `--dry-run` | Validate and report, write nothing. |

### Model coverage

The three Climate DT models do not all reach the same end year under SSP3-7.0. Verified against
the live Earth Data Hub store:

| Model | SSP3-7.0 coverage |
| ----- | ----------------- |
| IFS-NEMO | 2015-01-01 to 2049-12-31 |
| IFS-FESOM | 2015-01-01 to 2049-12-31 |
| ICON | 2015-01-01 to **2040-12-31** |

ICON therefore cannot serve any month in 2049. The script checks every requested model's real
time axis before streaming and fails with the model's actual coverage rather than writing
partial output, so drop `ICON` from `--models` for a 2049 request.

### Output conventions

- CRS EPSG:4326, north-up (first raster row is the northernmost latitude).
- Cell-centred coordinates: the raster origin sits half a pixel outside the first centre.
- `float32`, nodata `NaN`, DEFLATE compressed, band described as `t2m`.
- Values are kelvin, as published; no unit conversion is applied.
- Days are UTC days, matching the Climate DT time axis. Rome is UTC+1/+2, so a local-midnight
  daily mean would differ slightly.
- Each file carries `date`, `model`, `experiment`, `units`, `resolution`, `region`,
  `time_convention` and `source` GeoTIFF tags.

### Choosing a resolution

At `standard` resolution the Rome NUTS3 bounding box is only about 3 x 5 cells, which is too
coarse to be useful for a city. `high` gives about 20 x 35 cells and is the default for that
reason. See the ClimateIndexPlotter README for the full resolution and data-volume discussion.

### Tests

```shell
pip install -r requirements.txt pytest
pytest test_climate_dt_daily_t2m.py
```

The tests are offline: they cover month parsing, the cell-centre-to-corner geotransform, the
north-up row order, and a real GeoTIFF round trip. Streaming and the NUTS3 download are not
exercised.
