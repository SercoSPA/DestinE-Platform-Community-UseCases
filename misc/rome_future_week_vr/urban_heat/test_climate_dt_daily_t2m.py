"""Offline tests for the Climate DT daily-t2m export script.

Covers the pure geospatial logic and a real GeoTIFF round trip. No network access: the only
networked steps are the EDH open and the NUTS3 download, which are not exercised here.
"""

import numpy as np
import pandas as pd
import pytest
import rasterio
import xarray as xr

import climate_dt_daily_t2m as mod


# --- month range parsing ---


def test_month_bounds_spans_a_full_year_inclusive():
    t0, t1 = mod.month_bounds("2049-01", "2049-12")
    assert t0 == pd.Timestamp("2049-01-01 00:00")
    assert t1 == pd.Timestamp("2049-12-31 23:00")


def test_month_bounds_handles_a_single_month_and_leap_february():
    t0, t1 = mod.month_bounds("2048-02", "2048-02")
    assert t0 == pd.Timestamp("2048-02-01 00:00")
    assert t1 == pd.Timestamp("2048-02-29 23:00")  # 2048 is a leap year


def test_month_bounds_rejects_reversed_range():
    with pytest.raises(ValueError, match="before"):
        mod.month_bounds("2049-12", "2049-01")


@pytest.mark.parametrize("bad", ["2049", "January 2049", "2049-13", ""])
def test_month_bounds_rejects_malformed_months(bad):
    with pytest.raises(ValueError):
        mod.month_bounds(bad, "2049-12")


# --- grid handling ---


def _grid(lats, lons, values=None):
    if values is None:
        values = np.arange(len(lats) * len(lons), dtype="float32").reshape(len(lats), len(lons))
    return xr.DataArray(values, dims=("lat", "lon"), coords={"lat": lats, "lon": lons})


def test_north_up_flips_ascending_latitude():
    da = _grid([41.0, 41.5, 42.0], [11.0, 11.5])
    out = mod._north_up(da)
    assert list(out["lat"].values) == [42.0, 41.5, 41.0]
    # the flip must carry the data, not just the coordinate
    assert out.sel(lat=42.0).values.tolist() == da.sel(lat=42.0).values.tolist()


def test_north_up_leaves_descending_latitude_untouched():
    da = _grid([42.0, 41.5, 41.0], [11.0, 11.5])
    assert list(mod._north_up(da)["lat"].values) == [42.0, 41.5, 41.0]


def test_geotransform_places_origin_half_a_pixel_outside_the_centres():
    """Coordinates are cell centres; the raster origin is the outer corner."""
    da = mod._north_up(_grid([41.0, 41.5, 42.0], [11.0, 11.5, 12.0]))
    t = mod.geotransform(da)
    assert t.a == pytest.approx(0.5)      # x pixel size
    assert t.e == pytest.approx(-0.5)     # y pixel size, negative for north-up
    assert t.c == pytest.approx(11.0 - 0.25)  # west edge, not the first centre
    assert t.f == pytest.approx(42.0 + 0.25)  # north edge, not the first centre


def test_geotransform_round_trips_cell_centres():
    lats, lons = [41.0, 41.5, 42.0], [11.0, 11.5, 12.0]
    da = mod._north_up(_grid(lats, lons))
    t = mod.geotransform(da)
    for row, lat in enumerate(da["lat"].values):
        for col, lon in enumerate(da["lon"].values):
            x, y = t * (col + 0.5, row + 0.5)  # centre of that pixel
            assert x == pytest.approx(lon)
            assert y == pytest.approx(lat)


def test_geotransform_needs_at_least_two_cells_per_axis():
    with pytest.raises(ValueError, match="at least 2x2"):
        mod.geotransform(_grid([41.0], [11.0]))


# --- GeoTIFF output ---


def test_write_day_round_trips_values_and_georeferencing(tmp_path):
    da = mod._north_up(_grid([41.0, 41.5, 42.0], [11.0, 11.5, 12.0]))
    values = da.values.copy()
    values[0, 0] = np.nan
    path = tmp_path / "day.tif"
    mod.write_day(values, mod.geotransform(da), path, {"variable": "t2m", "units": "K"})

    with rasterio.open(path) as d:
        assert d.crs.to_epsg() == 4326
        assert d.dtypes[0] == "float32"
        assert np.isnan(d.nodata)
        assert d.descriptions == ("t2m",)
        assert d.tags()["units"] == "K"
        read = d.read(1)
        assert np.isnan(read[0, 0])
        np.testing.assert_allclose(read[1:], values[1:])
        # implied centres must match the source coordinates
        xs = [d.xy(0, c)[0] for c in range(d.width)]
        ys = [d.xy(r, 0)[1] for r in range(d.height)]
        np.testing.assert_allclose(xs, da["lon"].values)
        np.testing.assert_allclose(ys, da["lat"].values)


def test_export_model_writes_one_file_per_day_with_dated_names(tmp_path):
    times = pd.date_range("2049-03-01", periods=3, freq="D")
    da = xr.DataArray(
        np.zeros((3, 3, 3), dtype="float32"),
        dims=("time", "lat", "lon"),
        coords={"time": times, "lat": [41.0, 41.5, 42.0], "lon": [11.0, 11.5, 12.0]},
    )
    n = mod.export_model("IFS-NEMO", da, tmp_path, "high", "Roma (NUTS3 ITI43)")
    assert n == 3
    names = sorted(p.name for p in (tmp_path / "IFS-NEMO").glob("*.tif"))
    assert names == [
        "t2m_daily_mean_IFS-NEMO_20490301.tif",
        "t2m_daily_mean_IFS-NEMO_20490302.tif",
        "t2m_daily_mean_IFS-NEMO_20490303.tif",
    ]
    with rasterio.open(tmp_path / "IFS-NEMO" / names[1]) as d:
        assert d.tags()["date"] == "2049-03-02"
        assert d.tags()["model"] == "IFS-NEMO"
        assert d.tags()["time_convention"] == "UTC day"


# --- argument validation, before any network use ---


def test_unknown_model_is_rejected_without_touching_the_network():
    with pytest.raises(ValueError, match="Unknown model"):
        mod.main(["--region", "Roma", "--start", "2049-01", "--end", "2049-12",
                  "--models", "IFS-NEMO,NOT-A-MODEL", "--api-key", "dummy"])


def test_region_and_nuts3_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        mod.parse_args(["--region", "Roma", "--nuts3", "ITI43",
                        "--start", "2049-01", "--end", "2049-12"])


def test_an_aoi_is_required():
    with pytest.raises(SystemExit):
        mod.parse_args(["--start", "2049-01", "--end", "2049-12"])
