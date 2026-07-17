"""Aggregate hourly Climate DT fields to the daily inputs required by ETCCDI indices."""

from __future__ import annotations

import logging

import xarray as xr

log = logging.getLogger(__name__)

_SECONDS_PER_DAY = 86400.0
_PRECIP_RATE_VAR = "avg_tprate"


def to_daily(ds: xr.Dataset) -> xr.Dataset:
    """Resample hourly ``t2m``/``avg_tprate`` to daily ETCCDI input variables.

    From hourly 2 m temperature (``t2m``, Kelvin):
      - ``tasmax`` = daily maximum, ``tasmin`` = daily minimum, ``tas`` = daily mean.

    From the hourly mean total precipitation rate (``avg_tprate``, kg m-2 s-1 = mm/s):
      - ``pr`` = daily-mean rate times seconds-per-day, i.e. the daily total in ``mm/d``.

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

    if _PRECIP_RATE_VAR in ds:
        pr = ds[_PRECIP_RATE_VAR].resample(time="1D").mean() * _SECONDS_PER_DAY
        pr.attrs["units"] = "mm/d"
        data["pr"] = pr

    out = xr.Dataset(data)
    log.info("Aggregated to daily: vars=%s, days=%s", list(out.data_vars), out.sizes.get("time"))
    return out
