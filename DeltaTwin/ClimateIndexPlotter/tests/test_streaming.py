"""Guard the streaming efficiency of a run.

The hourly Climate DT source dominates run time (tens of GB per period), so the pipeline
must read it once per period no matter how many indices are selected. These tests count how
many source chunks are actually produced, using a dask-backed dataset whose chunks are built
by a counting function, injected at the ``edh.open_period`` network boundary.
"""

import threading

import dask.array as da
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import climate_index_plotter as cip

CHUNK_HOURS = 720
LAT = np.array([40.0, 41.0, 42.0, 43.0])
LON = np.array([10.0, 11.0, 12.0, 13.0])


class ChunkCounter:
    """Thread-safe tally of source chunks produced, per variable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {}

    def block(self, block, var=None, base=0.0):
        with self._lock:
            self.counts[var] = self.counts.get(var, 0) + 1
            n = self.counts[var]
        # vary values per chunk so indices see non-degenerate data
        return np.full(block.shape, base, dtype="float32") * np.float32(1.0 + (n % 5) * 0.02)


def _counting_hourly(counter, experiment, variables, years=("2000", "2001")):
    time = pd.date_range(f"{years[0]}-01-01", f"{years[1]}-12-31 23:00", freq="h")
    shape = (len(time), len(LAT), len(LON))
    warming = 2.0 if experiment == cip.SCENARIO else 0.0
    data = {}
    for var, base in (("t2m", 285.0 + warming), ("avg_tprate", 3e-5)):
        if var not in variables:
            continue
        skeleton = da.zeros(shape, chunks=(CHUNK_HOURS, len(LAT), len(LON)), dtype="float32")
        arr = skeleton.map_blocks(counter.block, var=var, base=base, dtype="float32")
        data[var] = (("time", "lat", "lon"), arr)
    ds = xr.Dataset(data, coords={"time": time, "lat": LAT, "lon": LON})
    ds["t2m"].attrs["units"] = "K"
    if "avg_tprate" in data:
        ds["avg_tprate"].attrs["units"] = "kg m-2 s-1"
    return ds


def _chunks_in_one_pass(years=("2000", "2001")):
    hours = len(pd.date_range(f"{years[0]}-01-01", f"{years[1]}-12-31 23:00", freq="h"))
    return -(-hours // CHUNK_HOURS)


def _run(monkeypatch, tmp_path, index_spec):
    counter = ChunkCounter()

    def fake_open_period(model, experiment, bbox, year_range, variables, api_key=None, **kw):
        return _counting_hourly(counter, experiment, list(variables))

    monkeypatch.setattr(cip.edh, "open_period", fake_open_period)
    rc = cip.main(
        model="IFS-NEMO",
        index_spec=index_spec,
        aoi_bbox="9,39,14,44",
        api_key="dummy",
        out_dir=str(tmp_path),
    )
    assert rc == 0
    return counter.counts


@pytest.mark.parametrize(
    "index_spec, n_indices",
    [
        ("TXx", 1),
        ("TXx,FD,Rx1day", 3),
        ("TXx,TNn,FD,SU,DTR,GSL,CDD,Rx1day,Rx5day,PRCPTOT", 10),
    ],
)
def test_source_is_read_once_per_period_regardless_of_index_count(
    monkeypatch, tmp_path, index_spec, n_indices
):
    counts = _run(monkeypatch, tmp_path, index_spec)
    # both periods are opened, so one pass over the source is two datasets' worth of chunks
    one_pass = 2 * _chunks_in_one_pass()
    for var, n in counts.items():
        assert n <= 1.25 * one_pass, (
            f"{var} source read {n / one_pass:.1f}x for {n_indices} indices; "
            "the hourly source must be streamed about once per period"
        )


def test_percentile_indices_do_not_re_read_the_base_period():
    """Percentile thresholds come from the historical period and are consumed by both, so they
    must be materialised once rather than re-derived for the future period."""
    counter = ChunkCounter()
    hist = _counting_hourly(counter, "hist", ["t2m"])
    fut = _counting_hourly(counter, cip.SCENARIO, ["t2m"])

    import daily

    cip.compute_climatologies(
        ["TX90p", "WSDI", "TXx"], daily.to_daily(hist), daily.to_daily(fut)
    )
    one_pass = 2 * _chunks_in_one_pass()
    assert counter.counts["t2m"] <= 2.25 * one_pass, (
        f"t2m read {counter.counts['t2m'] / one_pass:.1f}x; percentile-based indices should "
        "cost at most one extra pass for building the base thresholds"
    )
