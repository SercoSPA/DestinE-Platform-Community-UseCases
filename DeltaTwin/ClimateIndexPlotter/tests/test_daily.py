import numpy as np
import pandas as pd
import xarray as xr

import daily


def _hourly_t2m(days=2, base_per_day=(270.0, 280.0)):
    """Hourly t2m where value = base_for_day + hour_of_day, on a 1x1 grid."""
    time = pd.date_range("2000-01-01", periods=24 * days, freq="h")
    vals = np.empty((len(time), 1, 1), dtype=float)
    for i, t in enumerate(time):
        vals[i, 0, 0] = base_per_day[t.day - 1] + t.hour
    return xr.Dataset(
        {"t2m": (("time", "lat", "lon"), vals)},
        coords={"time": time, "lat": [45.0], "lon": [10.0]},
    )


def test_to_daily_temperature_max_min_mean():
    ds = _hourly_t2m()
    out = daily.to_daily(ds)
    assert list(out["tasmax"].isel(lat=0, lon=0).values) == [293.0, 303.0]
    assert list(out["tasmin"].isel(lat=0, lon=0).values) == [270.0, 280.0]
    # mean of hours 0..23 is 11.5
    np.testing.assert_allclose(
        out["tas"].isel(lat=0, lon=0).values, [281.5, 291.5]
    )


def test_temperature_units_preserved_kelvin():
    out = daily.to_daily(_hourly_t2m())
    assert out["tasmax"].attrs["units"] == "K"
    assert out["tasmin"].attrs["units"] == "K"
    assert out["tas"].attrs["units"] == "K"


def test_pr_from_avg_tprate_converted_to_mm_per_day():
    time = pd.date_range("2000-01-01", periods=24, freq="h")
    # constant rate 1e-4 kg m-2 s-1 -> 1e-4 * 86400 = 8.64 mm/day
    rate = xr.DataArray(
        np.full((24, 1, 1), 1e-4),
        dims=("time", "lat", "lon"),
        coords={"time": time, "lat": [45.0], "lon": [10.0]},
    )
    ds = xr.Dataset(
        {"t2m": (("time", "lat", "lon"), np.zeros((24, 1, 1))), "avg_tprate": rate}
    )
    out = daily.to_daily(ds)
    np.testing.assert_allclose(out["pr"].isel(lat=0, lon=0).values, [8.64])
    assert out["pr"].attrs["units"] == "mm/d"


def test_no_precip_variable_means_no_pr_variable():
    out = daily.to_daily(_hourly_t2m())
    assert "pr" not in out
    assert "tasmax" in out
