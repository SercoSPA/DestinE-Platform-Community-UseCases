import climate_index_plotter as cip


def test_output_filename_format():
    name = cip.output_filename("TXx", (1999, 2014), (2025, 2049), "IFS-NEMO", "SSP3-7.0")
    assert name == "etccdi_TXx_1999-2014_2025-2049_IFS-NEMO_SSP3-7.0.png"


def test_variables_for_temperature_only():
    assert cip.variables_for(["TXx", "FD"]) == ["t2m"]


def test_variables_for_precip_adds_tp():
    assert cip.variables_for(["TXx", "Rx1day"]) == ["t2m", "tp"]


def test_variables_for_precip_only():
    assert cip.variables_for(["Rx1day", "CDD"]) == ["tp"]


def test_fixed_period_constants():
    assert cip.HIST_YEARS == (1999, 2014)
    assert cip.FUTURE_YEARS == (2025, 2049)
    assert cip.SCENARIO == "SSP3-7.0"
