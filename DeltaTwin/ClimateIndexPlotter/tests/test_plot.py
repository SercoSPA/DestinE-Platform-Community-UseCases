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


def test_build_figure_has_at_least_two_panels():
    var = _map2d([[1.0, -1.0], [2.0, -2.0]])
    varpct = _map2d([[10.0, -10.0], [20.0, -20.0]], units="%")
    fig = plot.build_figure("TXx", var, varpct, subtitle="TXx 1999-2014 vs 2025-2049")
    assert len(fig.axes) >= 2


def test_plot_variation_writes_png(tmp_path):
    var = _map2d([[1.0, -1.0], [2.0, -2.0]])
    varpct = _map2d([[10.0, -10.0], [20.0, -20.0]], units="%")
    out = tmp_path / "etccdi_TXx.png"
    plot.plot_variation("TXx", var, varpct, str(out))
    assert out.exists()
    data = out.read_bytes()
    assert len(data) > 0
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_plot_variation_handles_all_nan_without_error(tmp_path):
    var = _map2d([[np.nan, np.nan], [np.nan, np.nan]])
    varpct = _map2d([[np.nan, np.nan], [np.nan, np.nan]], units="%")
    out = tmp_path / "etccdi_nan.png"
    plot.plot_variation("FD", var, varpct, str(out))
    assert out.exists()
