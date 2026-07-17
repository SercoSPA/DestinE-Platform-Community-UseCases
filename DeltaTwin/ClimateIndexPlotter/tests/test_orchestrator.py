import climate_index_plotter as cip


def test_output_filename_format():
    name = cip.output_filename("TXx", (1999, 2014), (2025, 2049), "IFS-NEMO", "SSP3-7.0")
    assert name == "etccdi_TXx_1999-2014_2025-2049_IFS-NEMO_SSP3-7.0.png"


def test_variables_for_temperature_only():
    assert cip.variables_for(["TXx", "FD"]) == ["t2m"]


def test_variables_for_precip_adds_avg_tprate():
    assert cip.variables_for(["TXx", "Rx1day"]) == ["t2m", "avg_tprate"]


def test_variables_for_precip_only():
    assert cip.variables_for(["Rx1day", "CDD"]) == ["avg_tprate"]


def test_fixed_period_constants():
    assert cip.HIST_YEARS == (1999, 2014)
    assert cip.FUTURE_YEARS == (2025, 2049)
    assert cip.SCENARIO == "SSP3-7.0"


def test_percent_change_is_nan_where_historical_is_zero():
    import numpy as np
    import xarray as xr

    hist = xr.DataArray([0.0, 10.0], dims="x")
    fut = xr.DataArray([5.0, 15.0], dims="x")
    pct = cip.percent_change(fut, hist)
    assert np.isnan(float(pct.isel(x=0)))
    assert float(pct.isel(x=1)) == 50.0


def test_percent_change_emits_no_divide_warning():
    import warnings

    import numpy as np
    import xarray as xr

    hist = xr.DataArray([0.0, 0.0, 4.0], dims="x")
    fut = xr.DataArray([1.0, 0.0, 6.0], dims="x")
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        pct = cip.percent_change(fut, hist)
        _ = np.asarray(pct.values)  # force computation
    assert np.isnan(float(pct.isel(x=0)))
    assert float(pct.isel(x=2)) == 50.0
