import os

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import edh
from aoi import Bbox


def test_zarr_url_standard_future_matches_catalogue():
    assert edh.zarr_url("IFS-NEMO", "SSP3-7.0") == (
        "https://api.earthdatahub.destine.eu/climate-dt-2/"
        "IFS-NEMO-SSP3-7.0-sfc-hourly-standard-v0.zarr"
    )


def test_zarr_url_standard_historical_matches_catalogue():
    assert edh.zarr_url("IFS-NEMO", "hist") == (
        "https://api.earthdatahub.destine.eu/climate-dt-2/"
        "IFS-NEMO-hist-sfc-hourly-standard-v0.zarr"
    )


def test_zarr_url_high_resolution_uses_timeseries_variant():
    """'high' must resolve to high-timeseries: high-maps is chunked 24h x 512 x 512, which
    is the wrong shape for reading whole multi-decade periods over a small area."""
    assert edh.zarr_url("IFS-NEMO", "hist", "high") == (
        "https://api.earthdatahub.destine.eu/climate-dt-2/"
        "IFS-NEMO-hist-sfc-hourly-high-timeseries-v0.zarr"
    )


def test_grid_spacing_matches_published_grids():
    assert edh.grid_spacing("standard") == pytest.approx(0.352, abs=0.002)
    assert edh.grid_spacing("high") == pytest.approx(0.044, abs=0.002)


def test_zarr_url_invalid_model_experiment_and_resolution_raise():
    with pytest.raises(ValueError):
        edh.zarr_url("BAD-MODEL", "hist")
    with pytest.raises(ValueError):
        edh.zarr_url("IFS-NEMO", "rcp85")
    with pytest.raises(ValueError):
        edh.zarr_url("IFS-NEMO", "hist", "ultra")


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


def test_normalize_coords_renames_preferred_chunks():
    """stream_bytes looks up on-disk chunk sizes by dimension name, so the encoding has to
    follow the rename."""
    ds = _grid_ds([40.0, 42.0])
    ds["t2m"].encoding["preferred_chunks"] = {"time": 8, "latitude": 2, "longitude": 3}
    out = edh.normalize_coords(ds)
    assert out["t2m"].encoding["preferred_chunks"] == {"time": 8, "lat": 2, "lon": 3}


def test_stream_bytes_charges_for_whole_chunks_not_the_clipped_selection():
    """Zarr reads a whole chunk at a time: clipping a chunk does not make it cheaper."""
    time = pd.date_range("2000-01-01", periods=100, freq="D")
    lat = np.arange(64.0)
    lon = np.arange(64.0)
    values = np.zeros((len(time), len(lat), len(lon)), dtype="float32")
    ds = xr.Dataset(
        {"t2m": (("time", "lat", "lon"), values)},
        coords={"time": time, "lat": lat, "lon": lon},
    ).chunk({"time": 50, "lat": 64, "lon": 64})
    ds["t2m"].encoding["preferred_chunks"] = {"time": 50, "lat": 64, "lon": 64}

    # a 3 x 3 cell corner still sits inside one 64 x 64 chunk of both time chunks
    clipped = ds.isel(lat=slice(0, 3), lon=slice(0, 3))
    whole_chunks = 2 * 50 * 64 * 64 * 4
    assert edh.stream_bytes(clipped, ["t2m"]) == whole_chunks
    assert clipped["t2m"].nbytes < whole_chunks  # the useful data is far smaller


def test_stream_bytes_scales_with_chunks_touched():
    time = pd.date_range("2000-01-01", periods=100, freq="D")
    ds = xr.Dataset(
        {"t2m": (("time", "lat", "lon"), np.zeros((100, 8, 8), dtype="float32"))},
        coords={"time": time, "lat": np.arange(8.0), "lon": np.arange(8.0)},
    ).chunk({"time": 25, "lat": 4, "lon": 4})
    ds["t2m"].encoding["preferred_chunks"] = {"time": 25, "lat": 4, "lon": 4}

    one_time_chunk = ds.isel(time=slice(0, 25))
    all_time_chunks = ds
    assert edh.stream_bytes(all_time_chunks, ["t2m"]) == 4 * edh.stream_bytes(
        one_time_chunk, ["t2m"]
    )


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


live = pytest.mark.skipif(
    not os.environ.get("EDH_API_KEY"),
    reason="live EDH streaming needs EDH_API_KEY and Climate DT (upgraded DESP) access",
)


@live
def test_open_period_live_smoke():
    bbox = Bbox(west=10.0, south=45.0, east=11.0, north=46.0)
    ds = edh.open_period("IFS-NEMO", "SSP3-7.0", bbox, (2025, 2025), ["t2m"])
    assert "t2m" in ds.data_vars
    assert ds.sizes["time"] > 0
    assert ds.sizes["lat"] > 0 and ds.sizes["lon"] > 0


@live
@pytest.mark.parametrize("resolution", ["standard", "high"])
def test_open_period_live_grid_spacing_matches_declared(resolution):
    """The published grid spacing is what sets the pixel size in the output plots, so pin it
    against the live store rather than trusting the catalogue description."""
    bbox = Bbox(west=10.0, south=45.0, east=13.0, north=48.0)
    ds = edh.open_period(
        "IFS-NEMO", "SSP3-7.0", bbox, (2025, 2025), ["t2m"], resolution=resolution
    )
    expected = edh.grid_spacing(resolution)
    for coord in ("lat", "lon"):
        step = float(np.median(np.diff(ds[coord].values)))
        assert step == pytest.approx(expected, rel=0.02), (
            f"{coord} spacing {step:.4f} deg does not match the declared "
            f"{expected:.4f} deg for resolution {resolution!r}"
        )
