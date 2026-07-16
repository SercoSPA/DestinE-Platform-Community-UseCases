"""Aggregate hourly Climate DT fields to the daily inputs required by ETCCDI indices."""

from __future__ import annotations

import logging

import xarray as xr

log = logging.getLogger(__name__)

_MM_PER_METRE = 1000.0


def to_daily(ds: xr.Dataset) -> xr.Dataset:
    """Resample hourly ``t2m``/``tp`` to daily ETCCDI input variables.

    From hourly 2 m temperature (``t2m``, Kelvin):
      - ``tasmax`` = daily maximum, ``tasmin`` = daily minimum, ``tas`` = daily mean.

    From hourly total precipitation (``tp``, metres of hourly accumulation):
      - ``pr`` = daily sum converted to mm (``mm/d``).

    Only the variables present in ``ds`` are produced. ``t2m`` is required.
    """
    if "t2m" not in ds:
        raise ValueError("Input dataset must contain 't2m' to derive daily temperature.")

    t2m = ds["t2m"]
    daily_max = t2m.resample(time="1D").max()
    daily_min = t2m.resample(time="1D").min()
    daily_mean = t2m.resample(time="1D").mean()

    for da in (daily_max, daily_min, daily_mean):
        da.attrs["units"] = "K"

    data = {
        "tasmax": daily_max,
        "tasmin": daily_min,
        "tas": daily_mean,
    }

    if "tp" in ds:
        pr = ds["tp"].resample(time="1D").sum() * _MM_PER_METRE
        pr.attrs["units"] = "mm/d"
        data["pr"] = pr

    out = xr.Dataset(data)
    log.info("Aggregated to daily: vars=%s, days=%s", list(out.data_vars), out.sizes.get("time"))
    return out
