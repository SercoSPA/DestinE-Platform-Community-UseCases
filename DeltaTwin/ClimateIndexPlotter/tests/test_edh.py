import os

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import edh
from aoi import Bbox


def test_zarr_url_matches_documented_example():
    url = edh.zarr_url("IFS-NEMO", "SSP3-7.0", resolution="high")
    assert url == (
        "https://api.earthdatahub.destine.eu/d1-climate-dt/"
        "ScenarioMIP-SSP3-7.0-IFS-NEMO-0001-high-sfc-v0.zarr"
    )


def test_zarr_url_standard_default_and_historical():
    url = edh.zarr_url("ICON", "hist")
    assert url.startswith("https://api.earthdatahub.destine.eu/d1-climate-dt/")
    assert "CMIP6-hist-ICON-0001-standard-sfc-v0.zarr" in url


def test_zarr_url_invalid_model_and_experiment_raise():
    with pytest.raises(ValueError):
        edh.zarr_url("BAD-MODEL", "hist")
    with pytest.raises(ValueError):
        edh.zarr_url("IFS-NEMO", "rcp85")


def test_authed_url_injects_basic_auth():
    out = edh.authed_url("https://api.earthdatahub.destine.eu/x.zarr", "KEY123")
    assert out == "https://edh:KEY123@api.earthdatahub.destine.eu/x.zarr"


def _grid_ds(lats):
    time = pd.date_range("2000-01-01", "2001-12-31", freq="D")
    lons = [8.0, 10.0, 12.0]
    shape = (len(time), len(lats), len(lons))
    return xr.Dataset(
        {
            "t2m": (("time", "latitude", "longitude"), np.zeros(shape)),
            "tp": (("time", "latitude", "longitude"), np.zeros(shape)),
            "sp": (("time", "latitude", "longitude"), np.zeros(shape)),
        },
        coords={"time": time, "latitude": lats, "longitude": lons},
    )


def test_normalize_coords_renames_latitude_longitude():
    ds = edh.normalize_coords(_grid_ds([40.0, 42.0]))
    assert "lat" in ds.dims and "lon" in ds.dims
    assert "latitude" not in ds.dims


def test_subset_selects_bbox_time_and_variables():
    ds = _grid_ds([38.0, 40.0, 42.0, 44.0, 46.0])
    bbox = Bbox(west=9.0, south=39.0, east=13.0, north=45.0)
    out = edh.subset(ds, bbox, (2000, 2000), ["t2m", "tp"])
    assert list(out["lat"].values) == [40.0, 42.0, 44.0]
    assert list(out["lon"].values) == [10.0, 12.0]
    assert set(out.data_vars) == {"t2m", "tp"}
    assert np.all(pd.DatetimeIndex(out["time"].values).year == 2000)


def test_subset_handles_descending_latitude():
    ds = _grid_ds([46.0, 44.0, 42.0, 40.0, 38.0])
    bbox = Bbox(west=9.0, south=39.0, east=13.0, north=45.0)
    out = edh.subset(ds, bbox, (2000, 2001), ["t2m"])
    assert sorted(out["lat"].values.tolist()) == [40.0, 42.0, 44.0]


def test_subset_empty_selection_raises():
    ds = _grid_ds([38.0, 40.0])
    bbox = Bbox(west=100.0, south=60.0, east=110.0, north=70.0)
    with pytest.raises(ValueError):
        edh.subset(ds, bbox, (2000, 2000), ["t2m"])


def test_subset_missing_variable_raises():
    ds = _grid_ds([40.0, 42.0])
    bbox = Bbox(west=9.0, south=39.0, east=13.0, north=45.0)
    with pytest.raises(ValueError):
        edh.subset(ds, bbox, (2000, 2000), ["t2m", "does_not_exist"])


@pytest.mark.skipif(
    not os.environ.get("EDH_API_KEY"),
    reason="live EDH streaming needs EDH_API_KEY and Climate DT (upgraded DESP) access",
)
def test_open_period_live_smoke():
    bbox = Bbox(west=10.0, south=45.0, east=11.0, north=46.0)
    ds = edh.open_period("IFS-NEMO", "SSP3-7.0", bbox, (2025, 2025), ["t2m"])
    assert "t2m" in ds.data_vars
    assert ds.sizes["time"] > 0
    assert ds.sizes["lat"] > 0 and ds.sizes["lon"] > 0
