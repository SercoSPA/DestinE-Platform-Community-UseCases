"""ClimateIndexPlotter model.

Computes ETCCDI climate indices from the DestinE Climate DT (streamed from Earth Data Hub)
and plots, per index, the projected change between a fixed historical period and a fixed
future-projection period as a four-panel map: the two period climatologies on a shared scale,
plus the absolute and percentage variation.
"""

from __future__ import annotations

import logging
import os
import sys

import dask

import aoi
import daily
import edh
import indices
import plot

log = logging.getLogger(__name__)

# Fixed comparison configuration (see design spec).
HIST_YEARS = (1999, 2014)
FUTURE_YEARS = (2025, 2049)
SCENARIO = "SSP3-7.0"
HIST_EXPERIMENT = "hist"

# Streaming Zarr from EDH is latency-bound rather than CPU-bound: measured throughput over
# the default AOI rises from ~23 MB/s with one worker to ~176 MB/s at 16, and flattens above
# that. Dask's default pool is sized by CPU count, which leaves most of that unused.
IO_THREADS = 16

# Refuse a run that would stream an impractical volume rather than starting a job that takes
# many hours. At the throughput above, 500 GB is roughly 1.5 hours.
MAX_STREAM_GB = 500.0


def output_filename(index_id, hist_years, future_years, model, scenario) -> str:
    """etccdi_<INDEX>_<histperiod>_<futperiod>_<model>_<scenario>.png"""
    hist = f"{hist_years[0]}-{hist_years[1]}"
    fut = f"{future_years[0]}-{future_years[1]}"
    return f"etccdi_{index_id}_{hist}_{fut}_{model}_{scenario}.png"


def variables_for(ids) -> list[str]:
    """EDH variables to stream for the given indices.

    t2m (2 m temperature) covers the temperature indices; avg_tprate (mean total
    precipitation rate) covers the precipitation indices.
    """
    req = indices.required_variables(ids)
    variables = []
    if req & {"tasmin", "tasmax", "tas"}:
        variables.append("t2m")
    if "pr" in req:
        variables.append("avg_tprate")
    return variables


def percent_change(future, historical):
    """Percentage change relative to the historical field.

    Zero-valued historical cells are masked (result NaN) to avoid division by zero.
    """
    denom = historical.where(historical != 0)
    return (future - historical) / denom * 100.0


def check_stream_volume(hourly_periods, variables) -> float:
    """Log the total volume the run will stream, and refuse impractical runs.

    Returns the estimate in GB.
    """
    gb = sum(edh.stream_bytes(ds, variables) for ds in hourly_periods) / 1e9
    log.info("Total to stream across both periods: %.1f GB", gb)
    if gb > MAX_STREAM_GB:
        raise ValueError(
            f"This run would stream {gb:.0f} GB, above the {MAX_STREAM_GB:.0f} GB limit. "
            "Shrink the AOI, use resolution 'standard' instead of 'high', or drop "
            "precipitation indices so avg_tprate is not streamed."
        )
    return gb


def compute_climatologies(ids, daily_hist, daily_fut) -> dict:
    """Compute every index climatology for both periods, reading each period once.

    All indices share one daily-aggregation graph per period, so evaluating them in a single
    ``dask.compute`` lets Dask reuse each hourly chunk across indices instead of re-reading
    the source for every index. Base percentiles are materialised first because both periods
    consume them. Returns ``{index_id: (hist_clim, fut_clim)}`` backed by NumPy.
    """
    base = indices.prepare_base_percentiles(daily_hist, ids)
    if base:
        base = dict(zip(base, dask.compute(*base.values())))

    pending = {
        index_id: (
            indices.compute_climatology(index_id, daily_hist, base),
            indices.compute_climatology(index_id, daily_fut, base),
        )
        for index_id in ids
    }
    # this single call is where the hourly data is actually streamed; expect it to be the bulk
    # of the run time, with no intermediate output
    log.info("Streaming Climate DT and computing %d climatologies for both periods: %s",
             len(ids), ", ".join(ids))
    (computed,) = dask.compute(pending)
    log.info("Climatologies computed")
    return computed


def main(
    model: str = "IFS-NEMO",
    index_spec: str = "TXx,FD,Rx1day",
    aoi_bbox: str | None = None,
    aoi_shapefile: str | None = None,
    api_key: str | None = None,
    out_dir: str = ".",
    resolution: str = edh.DEFAULT_RESOLUTION,
) -> int:
    """Run the full pipeline and write one PNG per selected index. Returns 0 on success."""
    ids = indices.resolve_index_ids(index_spec)
    bbox, mask = aoi.resolve_aoi(aoi_bbox, aoi_shapefile)
    variables = variables_for(ids)
    log.info(
        "Indices=%s | model=%s | resolution=%s (%.3f deg) | variables=%s | bbox=%s",
        ids, model, resolution, edh.grid_spacing(resolution), variables, bbox,
    )

    with dask.config.set(scheduler="threads", num_workers=IO_THREADS):
        open_kwargs = {"api_key": api_key, "resolution": resolution}
        hourly_hist = edh.open_period(
            model, HIST_EXPERIMENT, bbox, HIST_YEARS, variables, **open_kwargs
        )
        hourly_fut = edh.open_period(
            model, SCENARIO, bbox, FUTURE_YEARS, variables, **open_kwargs
        )
        check_stream_volume((hourly_hist, hourly_fut), variables)

        climatologies = compute_climatologies(
            ids, daily.to_daily(hourly_hist), daily.to_daily(hourly_fut)
        )

    os.makedirs(out_dir, exist_ok=True)
    for index_id in ids:
        hist_clim, fut_clim = climatologies[index_id]

        if mask is not None:
            hist_clim = aoi.apply_mask(hist_clim, mask)
            fut_clim = aoi.apply_mask(fut_clim, mask)

        variation = fut_clim - hist_clim
        variation.attrs["units"] = hist_clim.attrs.get("units", "")
        variation_pct = percent_change(fut_clim, hist_clim)
        variation_pct.attrs["units"] = "%"

        long_name = indices.INDEX_REGISTRY[index_id].long_name
        subtitle = (
            f"{index_id} - {long_name}\n"
            f"{HIST_YEARS[0]}-{HIST_YEARS[1]} vs {FUTURE_YEARS[0]}-{FUTURE_YEARS[1]}  "
            f"| {model} | {SCENARIO} | {edh.grid_spacing(resolution):.3f} deg"
        )
        out_path = os.path.join(
            out_dir, output_filename(index_id, HIST_YEARS, FUTURE_YEARS, model, SCENARIO)
        )
        plot.plot_variation(
            index_id, hist_clim, fut_clim, variation, variation_pct, out_path, subtitle=subtitle
        )

    log.info("Done: wrote %d plot(s) to %s", len(ids), out_dir)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s:%(funcName)s] %(message)s",
    )

    # CLI: python climate_index_plotter.py <api_key> <model> <indices> <aoi_bbox>
    #                                      <aoi_shapefile> <resolution>
    # 'none' is accepted for optional positional arguments.
    def _arg(i, default=None):
        if len(sys.argv) > i and str(sys.argv[i]).strip().lower() != "none":
            return sys.argv[i]
        return default

    kwargs = {}
    cli_api_key = _arg(1)
    if cli_api_key:
        kwargs["api_key"] = cli_api_key
    if _arg(2):
        kwargs["model"] = _arg(2)
    if _arg(3):
        kwargs["index_spec"] = _arg(3)
    kwargs["aoi_bbox"] = _arg(4)
    kwargs["aoi_shapefile"] = _arg(5)
    if _arg(6):
        kwargs["resolution"] = _arg(6)

    sys.exit(main(**kwargs))
