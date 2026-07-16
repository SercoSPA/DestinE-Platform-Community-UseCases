"""ETCCDI index registry and climatology computation, built on xclim.indices.

Each index maps to an xclim function (or a small custom routine). ``compute_climatology``
computes the annual index (``freq="YS"``) then averages over years to a 2-D map.

Percentile-based indices additionally need day-of-year (temperature) or wet-day
(precipitation) percentile thresholds derived from a base period; build them once with
``prepare_base_percentiles`` and pass them in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import xarray as xr
import xclim
import xclim.indices as xi
from xclim.core.calendar import percentile_doy

log = logging.getLogger(__name__)

_WET_THRESH_MM = 1.0  # ETCCDI wet-day threshold
_RNN_DEFAULT_MM = 25  # default nn for Rnnmm


@dataclass(frozen=True)
class IndexSpec:
    id: str
    required: tuple[str, ...]
    percentile: bool
    long_name: str
    func: Callable[[xr.Dataset, dict], xr.DataArray]
    base_keys: tuple[str, ...] = field(default=())


# --- annual-index compute functions (return a DataArray with a yearly 'time' dim) ---

def _fd(ds, b):
    return xi.frost_days(ds.tasmin, freq="YS")


def _tr(ds, b):
    return xi.tn_days_above(ds.tasmin, thresh="20 degC", freq="YS")


def _tnx(ds, b):
    return xi.tn_max(ds.tasmin, freq="YS")


def _tnn(ds, b):
    return xi.tn_min(ds.tasmin, freq="YS")


def _su(ds, b):
    return xi.tx_days_above(ds.tasmax, thresh="25 degC", freq="YS")


def _id(ds, b):
    return xi.ice_days(ds.tasmax, freq="YS")


def _txx(ds, b):
    return xi.tx_max(ds.tasmax, freq="YS")


def _txn(ds, b):
    return xi.tx_min(ds.tasmax, freq="YS")


def _dtr(ds, b):
    return xi.daily_temperature_range(ds.tasmin, ds.tasmax, freq="YS")


def _gsl(ds, b):
    return xi.growing_season_length(ds.tas, freq="YS")


def _tn10p(ds, b):
    return xi.tn10p(ds.tasmin, b["tasmin_per10"], freq="YS")


def _tn90p(ds, b):
    return xi.tn90p(ds.tasmin, b["tasmin_per90"], freq="YS")


def _tx10p(ds, b):
    return xi.tx10p(ds.tasmax, b["tasmax_per10"], freq="YS")


def _tx90p(ds, b):
    return xi.tx90p(ds.tasmax, b["tasmax_per90"], freq="YS")


def _wsdi(ds, b):
    return xi.warm_spell_duration_index(ds.tasmax, b["tasmax_per90"], window=6, freq="YS")


def _csdi(ds, b):
    return xi.cold_spell_duration_index(ds.tasmin, b["tasmin_per10"], window=6, freq="YS")


def _rx1day(ds, b):
    return xi.max_1day_precipitation_amount(ds.pr, freq="YS")


def _rx5day(ds, b):
    return xi.max_n_day_precipitation_amount(ds.pr, window=5, freq="YS")


def _sdii(ds, b):
    return xi.daily_pr_intensity(ds.pr, thresh="1 mm/d", freq="YS")


def _r10mm(ds, b):
    return xi.wetdays(ds.pr, thresh="10 mm/d", freq="YS")


def _r20mm(ds, b):
    return xi.wetdays(ds.pr, thresh="20 mm/d", freq="YS")


def _rnnmm(ds, b):
    return xi.wetdays(ds.pr, thresh=f"{_RNN_DEFAULT_MM} mm/d", freq="YS")


def _cdd(ds, b):
    return xi.maximum_consecutive_dry_days(ds.pr, thresh="1 mm/d", freq="YS")


def _cwd(ds, b):
    return xi.maximum_consecutive_wet_days(ds.pr, thresh="1 mm/d", freq="YS")


def _rptot(pr, per):
    """Annual total precipitation on days exceeding a per-cell threshold."""
    annual = pr.where(pr > per).resample(time="YS").sum()
    annual.attrs["units"] = "mm"
    return annual


def _r95ptot(ds, b):
    return _rptot(ds.pr, b["pr_per95"])


def _r99ptot(ds, b):
    return _rptot(ds.pr, b["pr_per99"])


def _prcptot(ds, b):
    return xi.prcptot(ds.pr, thresh="1 mm/d", freq="YS")


_SPECS = [
    IndexSpec("FD", ("tasmin",), False, "Frost days", _fd),
    IndexSpec("TR", ("tasmin",), False, "Tropical nights", _tr),
    IndexSpec("TNx", ("tasmin",), False, "Max of daily min temperature", _tnx),
    IndexSpec("TNn", ("tasmin",), False, "Min of daily min temperature", _tnn),
    IndexSpec("SU", ("tasmax",), False, "Summer days", _su),
    IndexSpec("ID", ("tasmax",), False, "Icing days", _id),
    IndexSpec("TXx", ("tasmax",), False, "Max of daily max temperature", _txx),
    IndexSpec("TXn", ("tasmax",), False, "Min of daily max temperature", _txn),
    IndexSpec("DTR", ("tasmin", "tasmax"), False, "Daily temperature range", _dtr),
    IndexSpec("GSL", ("tas",), False, "Growing season length", _gsl),
    IndexSpec("TN10p", ("tasmin",), True, "Cold nights", _tn10p, ("tasmin_per10",)),
    IndexSpec("TN90p", ("tasmin",), True, "Warm nights", _tn90p, ("tasmin_per90",)),
    IndexSpec("TX10p", ("tasmax",), True, "Cold days", _tx10p, ("tasmax_per10",)),
    IndexSpec("TX90p", ("tasmax",), True, "Warm days", _tx90p, ("tasmax_per90",)),
    IndexSpec("WSDI", ("tasmax",), True, "Warm spell duration index", _wsdi, ("tasmax_per90",)),
    IndexSpec("CSDI", ("tasmin",), True, "Cold spell duration index", _csdi, ("tasmin_per10",)),
    IndexSpec("Rx1day", ("pr",), False, "Max 1-day precipitation", _rx1day),
    IndexSpec("Rx5day", ("pr",), False, "Max 5-day precipitation", _rx5day),
    IndexSpec("SDII", ("pr",), False, "Simple daily intensity index", _sdii),
    IndexSpec("R10mm", ("pr",), False, "Heavy precipitation days (>=10mm)", _r10mm),
    IndexSpec("R20mm", ("pr",), False, "Very heavy precipitation days (>=20mm)", _r20mm),
    IndexSpec("Rnnmm", ("pr",), False, f"Precipitation days (>={_RNN_DEFAULT_MM}mm)", _rnnmm),
    IndexSpec("CDD", ("pr",), False, "Consecutive dry days", _cdd),
    IndexSpec("CWD", ("pr",), False, "Consecutive wet days", _cwd),
    IndexSpec("R95pTOT", ("pr",), True, "Very wet day precipitation total", _r95ptot, ("pr_per95",)),
    IndexSpec("R99pTOT", ("pr",), True, "Extremely wet day precipitation total", _r99ptot, ("pr_per99",)),
    IndexSpec("PRCPTOT", ("pr",), False, "Total wet-day precipitation", _prcptot),
]

INDEX_REGISTRY: dict[str, IndexSpec] = {s.id: s for s in _SPECS}


def index_ids() -> list[str]:
    """All supported ETCCDI index ids, in registry order."""
    return [s.id for s in _SPECS]


def resolve_index_ids(spec: str) -> list[str]:
    """Resolve a comma-separated id list (or ``"all"``) to a validated list of ids."""
    if spec is None or str(spec).strip().lower() == "all":
        return index_ids()
    requested = [p.strip() for p in str(spec).split(",") if p.strip()]
    unknown = [i for i in requested if i not in INDEX_REGISTRY]
    if unknown:
        raise ValueError(
            f"Unknown index id(s): {unknown}. Valid ids: {index_ids()}"
        )
    return requested


def required_variables(ids: Iterable[str]) -> set[str]:
    """Union of daily input variables needed by the given indices."""
    out: set[str] = set()
    for i in ids:
        out.update(INDEX_REGISTRY[i].required)
    return out


def needs_precip(ids: Iterable[str]) -> bool:
    return "pr" in required_variables(ids)


def _percentile_doy(da: xr.DataArray, per: int) -> xr.DataArray:
    return percentile_doy(da, window=5, per=per).sel(percentiles=per)


def _pr_percentile(pr: xr.DataArray, per: int) -> xr.DataArray:
    wet = pr.where(pr >= _WET_THRESH_MM)
    out = wet.quantile(per / 100.0, dim="time")
    return out.drop_vars("quantile", errors="ignore")


def _build_base_key(daily_hist: xr.Dataset, key: str) -> xr.DataArray:
    builders = {
        "tasmin_per10": lambda: _percentile_doy(daily_hist.tasmin, 10),
        "tasmin_per90": lambda: _percentile_doy(daily_hist.tasmin, 90),
        "tasmax_per10": lambda: _percentile_doy(daily_hist.tasmax, 10),
        "tasmax_per90": lambda: _percentile_doy(daily_hist.tasmax, 90),
        "pr_per95": lambda: _pr_percentile(daily_hist.pr, 95),
        "pr_per99": lambda: _pr_percentile(daily_hist.pr, 99),
    }
    return builders[key]()


def prepare_base_percentiles(daily_hist: xr.Dataset, ids: Iterable[str]) -> dict:
    """Build only the base-period percentile thresholds needed by ``ids``."""
    needed: set[str] = set()
    for i in ids:
        needed.update(INDEX_REGISTRY[i].base_keys)
    base = {key: _build_base_key(daily_hist, key) for key in needed}
    log.info("Prepared base percentiles: %s", sorted(base))
    return base


def compute_climatology(
    index_id: str,
    daily_ds: xr.Dataset,
    base_percentiles: Optional[dict] = None,
) -> xr.DataArray:
    """Compute the annual index then average over years to a 2-D climatology map."""
    if index_id not in INDEX_REGISTRY:
        raise ValueError(f"Unknown index id: {index_id!r}. Valid ids: {index_ids()}")
    spec = INDEX_REGISTRY[index_id]
    if spec.percentile and not base_percentiles:
        raise ValueError(
            f"Index {index_id} is percentile-based and requires base_percentiles."
        )

    with xclim.set_options(cf_compliance="log", data_validation="log"):
        annual = spec.func(daily_ds, base_percentiles or {})

    clim = annual.mean("time", keep_attrs=True)
    clim.name = index_id
    clim.attrs.setdefault("long_name", spec.long_name)
    return clim
