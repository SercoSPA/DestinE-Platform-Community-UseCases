#!/usr/bin/env python3
"""Export Climate DT daily-mean 2 m temperature over a NUTS3 region as one GeoTIFF per day.

Streams hourly ``t2m`` for the requested models and months from Earth Data Hub, averages each
UTC day, and writes one single-band GeoTIFF per model per day.

Reuses the verified Earth Data Hub access layer from the ClimateIndexPlotter component and the
NUTS3 lookup from the LSTPlotter component rather than duplicating either.

Example (daily mean t2m over Rome for 2049, all models that cover it):

    python climate_dt_daily_t2m.py --region Roma --start 2049-01 --end 2049-12 \
        --models IFS-NEMO,IFS-FESOM --out-dir ./rome_t2m_2049

Notes:
  - Days are UTC days, matching the Climate DT time axis. Rome local time is UTC+1/+2, so a
    local-midnight-to-midnight mean would differ slightly.
  - Values are kelvin, as published; no unit conversion is applied.
  - ICON currently ends 2040-12-31, so it cannot serve 2049. The script checks each model's
    real coverage before streaming and fails loudly rather than writing partial output.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import dask
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from rasterio.crs import CRS
from rasterio.transform import from_origin

def _repo_root() -> Path:
    """Nearest ancestor directory containing ``DeltaTwin``.

    Found by walking up rather than by a fixed number of parents, so the script keeps working
    if it is moved to a different depth in the tree.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "DeltaTwin").is_dir():
            return candidate
    raise RuntimeError(
        f"Could not locate the repository root (no 'DeltaTwin' directory above {__file__}). "
        "Run this script from within the DestinE-Platform-Community-UseCases checkout."
    )


_REPO = _repo_root()
for _dep in (
    _REPO / "DeltaTwin" / "ClimateIndexPlotter" / "models" / "climate_index_plotter",
    _REPO / "DeltaTwin" / "LSTPlotter" / "models" / "lst_plotter",
):
    if not _dep.is_dir():
        raise RuntimeError(f"Required component directory is missing: {_dep}")
    sys.path.insert(0, str(_dep))

import aoi  # noqa: E402
import edh  # noqa: E402
import nuts_helper  # noqa: E402
from aoi import Bbox  # noqa: E402

log = logging.getLogger("climate_dt_daily_t2m")

ALL_MODELS = ("IFS-NEMO", "IFS-FESOM", "ICON")
SCENARIO = "SSP3-7.0"
VARIABLE = "t2m"

# EDH reads are latency-bound; see the ClimateIndexPlotter design notes.
IO_THREADS = 16


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month(value: str) -> pd.Period:
    """Parse a strict ``YYYY-MM`` month.

    pandas accepts looser forms (a bare ``"2049"`` becomes January, and an unparseable string
    becomes ``NaT``), so the format is checked explicitly first.
    """
    if not _MONTH_RE.match(str(value).strip()):
        raise ValueError(f"Month must be YYYY-MM (e.g. 2049-01), got {value!r}")
    return pd.Period(str(value).strip(), freq="M")


def month_bounds(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Convert ``YYYY-MM`` start/end months to inclusive hourly timestamps."""
    first, last = _month(start), _month(end)
    if last < first:
        raise ValueError(f"--end ({end}) is before --start ({start})")
    return first.to_timestamp(how="start"), last.to_timestamp(how="end").floor("h")


def check_coverage(model: str, resolution: str, api_key: str, t0, t1) -> None:
    """Fail loudly if a model's published time axis does not span the requested months."""
    url = edh.authed_url(edh.zarr_url(model, SCENARIO, resolution), api_key)
    ds = xr.open_dataset(
        url, engine="zarr", chunks={}, storage_options={"client_kwargs": {"trust_env": True}}
    )
    available = ds["time"].values
    first, last = available[0], available[-1]
    if np.datetime64(t0) < first or np.datetime64(t1) > last:
        raise ValueError(
            f"{model} {SCENARIO} covers {str(first)[:13]} to {str(last)[:13]}, which does not "
            f"span the requested {str(t0)[:13]} to {str(t1)[:13]}. "
            f"Drop {model} from --models, or choose a range inside its coverage."
        )


def daily_mean(model: str, bbox: Bbox, t0, t1, resolution: str, api_key: str) -> xr.DataArray:
    """Stream hourly t2m for the AOI and month range, returning the daily-mean field."""
    ds = edh.open_period(
        model,
        SCENARIO,
        bbox,
        (t0.year, t1.year),
        [VARIABLE],
        api_key=api_key,
        resolution=resolution,
    )
    ds = ds.sel(time=slice(t0, t1))
    if ds.sizes["time"] == 0:
        raise ValueError(f"{model}: no time steps between {t0} and {t1}.")
    log.info(
        "%s: %d hourly steps, %.2f GB chunk-aligned",
        model,
        ds.sizes["time"],
        edh.stream_bytes(ds, [VARIABLE]) / 1e9,
    )
    out = ds[VARIABLE].resample(time="1D").mean()
    out.attrs["units"] = "K"
    return out


def _north_up(da: xr.DataArray) -> xr.DataArray:
    """Order latitude descending, the conventional GeoTIFF row order."""
    lat = da["lat"].values
    if lat.size >= 2 and lat[0] < lat[-1]:
        da = da.isel(lat=slice(None, None, -1))
    return da


def geotransform(da: xr.DataArray):
    """Affine transform for a cell-centred regular lat/lon grid, north-up.

    Coordinates are cell centres, so the raster origin sits half a pixel outside them.
    """
    lon = da["lon"].values
    lat = da["lat"].values
    if lon.size < 2 or lat.size < 2:
        raise ValueError(
            f"Need at least 2x2 cells to derive a pixel size, got {lat.size}x{lon.size}. "
            "Use --resolution high for a small area."
        )
    xres = float(np.median(np.diff(lon)))
    yres = float(-np.median(np.diff(lat)))  # lat descends, so diffs are negative
    return from_origin(float(lon[0]) - xres / 2, float(lat[0]) + yres / 2, xres, yres)


def write_day(values: np.ndarray, transform, path: Path, tags: dict) -> None:
    """Write a single 2-D float32 band as a compressed EPSG:4326 GeoTIFF."""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs=CRS.from_epsg(4326),
        transform=transform,
        nodata=np.nan,
        compress="deflate",
        tiled=False,
    ) as dst:
        dst.write(values.astype("float32"), 1)
        dst.update_tags(**tags)
        dst.set_band_description(1, tags["variable"])


def export_model(
    model: str, daily: xr.DataArray, out_dir: Path, resolution: str, region_label: str
) -> int:
    """Write one GeoTIFF per day for a model. Returns the number of files written."""
    daily = _north_up(daily)
    transform = geotransform(daily)
    model_dir = out_dir / model
    model_dir.mkdir(parents=True, exist_ok=True)

    dates = pd.DatetimeIndex(daily["time"].values)
    values = daily.values
    for i, date in enumerate(dates):
        stamp = date.strftime("%Y%m%d")
        path = model_dir / f"t2m_daily_mean_{model}_{stamp}.tif"
        write_day(
            values[i],
            transform,
            path,
            {
                "variable": VARIABLE,
                "long_name": "2 metre temperature, daily mean",
                "units": "K",
                "date": date.strftime("%Y-%m-%d"),
                "time_convention": "UTC day",
                "model": model,
                "experiment": SCENARIO,
                "resolution": f"{resolution} ({edh.grid_spacing(resolution):.3f} deg)",
                "region": region_label,
                "source": edh.zarr_url(model, SCENARIO, resolution),
            },
        )
    log.info("%s: wrote %d GeoTIFFs to %s", model, len(dates), model_dir)
    return len(dates)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Export Climate DT daily-mean t2m over a NUTS3 region as daily GeoTIFFs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    aoi_group = p.add_mutually_exclusive_group(required=True)
    aoi_group.add_argument("--region", help="Region/city name to look up, e.g. 'Roma'.")
    aoi_group.add_argument("--nuts3", help="NUTS3 code directly, e.g. 'ITI43'.")
    p.add_argument("--start", required=True, help="First month, YYYY-MM.")
    p.add_argument("--end", required=True, help="Last month, YYYY-MM (inclusive).")
    p.add_argument(
        "--models",
        default=",".join(ALL_MODELS),
        help="Comma-separated Climate DT models.",
    )
    p.add_argument(
        "--resolution",
        default="high",
        choices=sorted(edh.RESOLUTIONS),
        help="EDH grid: 'high' is 0.044 deg (~4 km), 'standard' 0.35 deg (~29 km).",
    )
    p.add_argument("--out-dir", default="./climate_dt_t2m", help="Output directory.")
    p.add_argument(
        "--clip-to-region",
        action="store_true",
        help="Mask cells outside the NUTS3 polygon to nodata instead of keeping the full bbox.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="EDH API key. Defaults to the EDH_API_KEY environment variable.",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate and report, write nothing.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in models if m not in ALL_MODELS]
    if unknown:
        raise ValueError(f"Unknown model(s) {unknown}. Valid: {list(ALL_MODELS)}")

    api_key = args.api_key or os.environ.get("EDH_API_KEY")
    if not api_key:
        raise ValueError("No EDH API key: pass --api-key or set EDH_API_KEY.")

    t0, t1 = month_bounds(args.start, args.end)

    if args.nuts3:
        code = args.nuts3
        label = code
    else:
        code, label = nuts_helper.find_nuts3_by_name(args.region)
    geometry = nuts_helper.get_nuts3_geom(code)
    west, south, east, north = geometry.bounds
    bbox = Bbox(float(west), float(south), float(east), float(north))
    region_label = f"{label} (NUTS3 {code})"
    log.info("Region %s -> bbox %s", region_label, tuple(round(v, 4) for v in bbox))
    log.info("Range %s to %s | models=%s | resolution=%s", t0, t1, models, args.resolution)

    # validate every model before streaming anything, so a gap fails before partial output
    for model in models:
        check_coverage(model, args.resolution, api_key, t0, t1)
    log.info("All requested models cover the range")

    out_dir = Path(args.out_dir)
    total = 0
    with dask.config.set(scheduler="threads", num_workers=IO_THREADS):
        for model in models:
            daily = daily_mean(model, bbox, t0, t1, args.resolution, api_key)
            if args.dry_run:
                log.info(
                    "%s: would write %d GeoTIFFs of %d x %d cells",
                    model,
                    daily.sizes["time"],
                    daily.sizes["lat"],
                    daily.sizes["lon"],
                )
                continue
            if args.clip_to_region:
                daily = aoi.apply_mask(daily, geometry)
            daily = daily.compute()
            total += export_model(model, daily, out_dir, args.resolution, region_label)

    if args.dry_run:
        log.info("Dry run: nothing written")
        return 0
    log.info("Done: %d GeoTIFFs across %d model(s) in %s", total, len(models), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
