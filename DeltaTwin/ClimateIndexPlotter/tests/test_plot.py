import numpy as np
import xarray as xr

import plot


def _map2d(values, units="K"):
    da = xr.DataArray(
        np.array(values, dtype=float),
        dims=("lat", "lon"),
        coords={"lat": [40.0, 41.0], "lon": [10.0, 11.0]},
    )
    da.attrs["units"] = units
    return da


def _quad():
    hist = _map2d([[300.0, 301.0], [302.0, 303.0]])
    fut = _map2d([[302.0, 303.0], [304.0, 305.0]])
    var = _map2d([[2.0, 2.0], [2.0, 2.0]])
    varpct = _map2d([[0.7, 0.7], [0.7, 0.7]], units="%")
    return hist, fut, var, varpct


def test_build_figure_has_four_map_panels():
    hist, fut, var, varpct = _quad()
    fig = plot.build_figure("TXx", hist, fut, var, varpct, subtitle="TXx")
    # 4 map axes (plus colorbar axes).
    map_axes = [ax for ax in fig.axes if hasattr(ax, "projection")]
    assert len(map_axes) == 4


def test_plot_variation_writes_png(tmp_path):
    hist, fut, var, varpct = _quad()
    out = tmp_path / "etccdi_TXx.png"
    plot.plot_variation("TXx", hist, fut, var, varpct, str(out))
    assert out.exists()
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_plot_variation_handles_all_nan_without_error(tmp_path):
    nan = _map2d([[np.nan, np.nan], [np.nan, np.nan]])
    nanp = _map2d([[np.nan, np.nan], [np.nan, np.nan]], units="%")
    out = tmp_path / "etccdi_nan.png"
    plot.plot_variation("FD", nan, nan, nan, nanp, str(out))
    assert out.exists()
