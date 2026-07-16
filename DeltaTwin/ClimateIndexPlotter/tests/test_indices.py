import numpy as np
import pandas as pd
import pytest
import xarray as xr

import indices


def _daily(years=(2000, 2001)):
    """Synthetic daily dataset on a 2x2 grid with hand-controlled properties."""
    time = pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-31", freq="D")
    tv = pd.DatetimeIndex(time)
    shape = (len(time), 2, 2)
    tasmin = np.full(shape, 280.0)
    tasmax = np.full(shape, 300.0)
    tas = np.full(shape, 290.0)
    pr = np.zeros(shape)
    for y in years:
        yr = tv.year == y
        first10 = np.where(yr)[0][:10]
        tasmin[first10] = 270.0  # 10 frost days per year
        d100 = np.where(yr & (tv.dayofyear == 100))[0]
        tasmax[d100] = 310.0  # TXx = 310 per year
        pr[np.where(yr)[0][50:70]] = 10.0  # 20 moderate wet days
        pr[np.where(yr)[0][70:75]] = 50.0  # 5 heavy wet days
    ds = xr.Dataset(
        {
            "tasmin": (("time", "lat", "lon"), tasmin),
            "tasmax": (("time", "lat", "lon"), tasmax),
            "tas": (("time", "lat", "lon"), tas),
            "pr": (("time", "lat", "lon"), pr),
        },
        coords={"time": time, "lat": [40.0, 41.0], "lon": [10.0, 11.0]},
    )
    for v, u in [("tasmin", "K"), ("tasmax", "K"), ("tas", "K"), ("pr", "mm/d")]:
        ds[v].attrs["units"] = u
    return ds


def test_registry_has_all_27_indices():
    assert len(indices.index_ids()) == 27


def test_registry_contains_expected_ids():
    for i in ["FD", "TR", "TNx", "TNn", "SU", "ID", "TXx", "TXn", "DTR", "GSL",
              "TN10p", "TN90p", "TX10p", "TX90p", "WSDI", "CSDI",
              "Rx1day", "Rx5day", "SDII", "R10mm", "R20mm", "Rnnmm",
              "CDD", "CWD", "R95pTOT", "R99pTOT", "PRCPTOT"]:
        assert i in indices.index_ids(), i


def test_resolve_all_and_list_and_unknown():
    assert indices.resolve_index_ids("all") == indices.index_ids()
    assert indices.resolve_index_ids("TXx, FD") == ["TXx", "FD"]
    with pytest.raises(ValueError):
        indices.resolve_index_ids("TXx,NOPE")


def test_required_variables_and_needs_precip():
    assert indices.required_variables(["FD"]) == {"tasmin"}
    assert indices.required_variables(["DTR"]) == {"tasmin", "tasmax"}
    assert indices.needs_precip(["Rx1day"]) is True
    assert indices.needs_precip(["FD", "TXx"]) is False


def test_frost_days_climatology_is_ten():
    out = indices.compute_climatology("FD", _daily())
    assert float(out.isel(lat=0, lon=0)) == 10.0


def test_txx_climatology_is_310():
    out = indices.compute_climatology("TXx", _daily())
    assert float(out.isel(lat=0, lon=0)) == 310.0


def test_climatology_reduces_time_to_2d_map():
    out = indices.compute_climatology("TXx", _daily())
    assert "time" not in out.dims
    assert set(out.dims) == {"lat", "lon"}


def test_percentile_index_needs_base_and_returns_finite_map():
    ds = _daily()
    base = indices.prepare_base_percentiles(ds, ["TN10p"])
    out = indices.compute_climatology("TN10p", ds, base)
    assert set(out.dims) == {"lat", "lon"}
    assert np.isfinite(out.values).all()


def test_r95ptot_returns_nonnegative_finite_map():
    ds = _daily()
    base = indices.prepare_base_percentiles(ds, ["R95pTOT"])
    out = indices.compute_climatology("R95pTOT", ds, base)
    assert np.isfinite(out.values).all()
    assert (out.values >= 0).all()


def test_prepare_base_only_builds_needed_keys():
    ds = _daily()
    base = indices.prepare_base_percentiles(ds, ["TN10p"])
    assert "tasmin_per10" in base
    assert "pr_per95" not in base
