"""End-to-end pipeline test with synthetic data injected at the EDH network boundary.

edh.open_period is the only networked call; we replace it with synthetic hourly data so
the full orchestration (open -> daily -> indices -> variation -> plot) runs offline.
"""

import numpy as np
import pandas as pd
import xarray as xr

import climate_index_plotter as cip


def _synthetic_hourly(experiment, variables):
    # ~2 years of hourly data on a 2x2 grid; future is a couple of degrees warmer.
    time = pd.date_range("2000-01-01", "2001-12-31 23:00", freq="h")
    n = len(time)
    lat = [40.0, 41.0]
    lon = [10.0, 11.0]
    doy = time.dayofyear.to_numpy()
    hod = time.hour.to_numpy()
    warming = 2.0 if experiment == cip.SCENARIO else 0.0
    seasonal = 285.0 + 10.0 * np.sin(2 * np.pi * doy / 365.0) + 3.0 * np.sin(2 * np.pi * hod / 24.0)
    t2m = (seasonal + warming)[:, None, None] * np.ones((n, 2, 2))
    data = {"t2m": (("time", "lat", "lon"), t2m)}
    if "avg_tprate" in variables:
        rate = np.where(doy % 5 == 0, 5e-5, 0.0)  # rain on every 5th day (kg m-2 s-1)
        data["avg_tprate"] = (("time", "lat", "lon"), rate[:, None, None] * np.ones((n, 2, 2)))
    ds = xr.Dataset(data, coords={"time": time, "lat": lat, "lon": lon})
    ds["t2m"].attrs["units"] = "K"
    if "avg_tprate" in data:
        ds["avg_tprate"].attrs["units"] = "kg m-2 s-1"
    return ds


def test_full_pipeline_writes_expected_pngs(tmp_path, monkeypatch):
    def fake_open_period(model, experiment, bbox, year_range, variables, api_key=None, **kw):
        return _synthetic_hourly(experiment, list(variables))

    monkeypatch.setattr(cip.edh, "open_period", fake_open_period)

    ids = ["TXx", "FD", "Rx1day", "TN10p", "R95pTOT"]
    rc = cip.main(
        model="IFS-NEMO",
        index_spec=",".join(ids),
        aoi_bbox="9,39,12,42",
        api_key="dummy",
        out_dir=str(tmp_path),
    )
    assert rc == 0
    for i in ids:
        expected = tmp_path / cip.output_filename(
            i, cip.HIST_YEARS, cip.FUTURE_YEARS, "IFS-NEMO", cip.SCENARIO
        )
        assert expected.exists(), expected.name
        assert expected.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_pipeline_temperature_warming_positive_txx_variation(tmp_path, monkeypatch):
    # Future is +2 K, so TXx variation should be positive everywhere.
    captured = {}

    def fake_open_period(model, experiment, bbox, year_range, variables, api_key=None, **kw):
        return _synthetic_hourly(experiment, list(variables))

    monkeypatch.setattr(cip.edh, "open_period", fake_open_period)

    real_plot = cip.plot.plot_variation

    def capture_plot(index_id, hist_clim, fut_clim, variation, variation_pct, out_path, subtitle=""):
        captured[index_id] = float(variation.mean())
        return real_plot(
            index_id, hist_clim, fut_clim, variation, variation_pct, out_path, subtitle=subtitle
        )

    monkeypatch.setattr(cip.plot, "plot_variation", capture_plot)

    cip.main(model="IFS-NEMO", index_spec="TXx", aoi_bbox="9,39,12,42",
             api_key="dummy", out_dir=str(tmp_path))
    assert captured["TXx"] > 1.5  # ~ +2 K warming
