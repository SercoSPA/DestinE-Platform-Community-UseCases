"""ClimateIndexPlotter model.

Computes ETCCDI climate indices from the DestinE Climate DT (streamed from Earth Data Hub)
and plots, per index, the projected change between a fixed historical period and a fixed
future-projection period as a two-panel (absolute | percentage) map.
"""

from __future__ import annotations

import logging
import os
import sys

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


def _load_daily(model, experiment, years, bbox, variables, api_key):
    ds = edh.open_period(model, experiment, bbox, years, variables, api_key=api_key)
    return daily.to_daily(ds)


def main(
    model: str = "IFS-NEMO",
    index_spec: str = "TXx,FD,Rx1day",
    aoi_bbox: str | None = None,
    aoi_shapefile: str | None = None,
    api_key: str | None = None,
    out_dir: str = ".",
) -> int:
    """Run the full pipeline and write one PNG per selected index. Returns 0 on success."""
    ids = indices.resolve_index_ids(index_spec)
    bbox, mask = aoi.resolve_aoi(aoi_bbox, aoi_shapefile)
    variables = variables_for(ids)
    log.info("Indices=%s | model=%s | variables=%s | bbox=%s", ids, model, variables, bbox)

    daily_hist = _load_daily(model, HIST_EXPERIMENT, HIST_YEARS, bbox, variables, api_key)
    daily_fut = _load_daily(model, SCENARIO, FUTURE_YEARS, bbox, variables, api_key)

    base = indices.prepare_base_percentiles(daily_hist, ids)

    os.makedirs(out_dir, exist_ok=True)
    for index_id in ids:
        log.info("Computing index %s", index_id)
        hist_clim = indices.compute_climatology(index_id, daily_hist, base)
        fut_clim = indices.compute_climatology(index_id, daily_fut, base)

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
            f"| {model} | {SCENARIO}"
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

    # CLI: python climate_index_plotter.py <api_key> <model> <indices> <aoi_bbox> <aoi_shapefile>
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

    sys.exit(main(**kwargs))
