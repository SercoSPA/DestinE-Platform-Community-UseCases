"""Access Climate DT data on Earth Data Hub (EDH) as lazily-streamed Zarr.

The catalogue is regridded to a regular lat/lon grid, so spatial subsetting is a simple
``.sel()``. Only the requested variables, AOI, and time slice are read; Dask streams the
chunks on demand. Network access happens only in :func:`open_period`; URL/auth/subset
helpers are pure and unit-testable offline.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

import xarray as xr

log = logging.getLogger(__name__)

_HOST = "https://api.earthdatahub.destine.eu"
_BASE_PATH = "d1-climate-dt"
_MODELS = {"IFS-NEMO", "IFS-FESOM", "ICON"}
_EXPERIMENT_ACTIVITY = {"hist": "CMIP6", "SSP3-7.0": "ScenarioMIP"}
_DEFAULT_RESOLUTION = "standard"


def zarr_url(model: str, experiment: str, resolution: str = _DEFAULT_RESOLUTION) -> str:
    """Public Zarr URL for a Climate DT surface-hourly dataset.

    Slug pattern (from the EDH catalogue), e.g.
    ``ScenarioMIP-SSP3-7.0-IFS-NEMO-0001-high-sfc-v0.zarr``.
    """
    if model not in _MODELS:
        raise ValueError(f"Unknown model {model!r}. Valid: {sorted(_MODELS)}")
    if experiment not in _EXPERIMENT_ACTIVITY:
        raise ValueError(
            f"Unknown experiment {experiment!r}. Valid: {sorted(_EXPERIMENT_ACTIVITY)}"
        )
    activity = _EXPERIMENT_ACTIVITY[experiment]
    slug = f"{activity}-{experiment}-{model}-0001-{resolution}-sfc-v0.zarr"
    return f"{_HOST}/{_BASE_PATH}/{slug}"


def authed_url(url: str, api_key: str) -> str:
    """Inject EDH HTTP basic auth (user ``edh``, password = API key) into a URL."""
    scheme, rest = url.split("://", 1)
    return f"{scheme}://edh:{api_key}@{rest}"


def normalize_coords(ds: xr.Dataset) -> xr.Dataset:
    """Rename ``latitude``/``longitude`` to ``lat``/``lon`` if present."""
    rename = {}
    if "latitude" in ds.variables:
        rename["latitude"] = "lat"
    if "longitude" in ds.variables:
        rename["longitude"] = "lon"
    return ds.rename(rename) if rename else ds


def subset(ds: xr.Dataset, bbox, year_range, variables: Iterable[str]) -> xr.Dataset:
    """Subset a dataset to the AOI bbox, year range, and requested variables."""
    ds = normalize_coords(ds)

    variables = list(variables)
    missing = [v for v in variables if v not in ds.variables]
    if missing:
        raise ValueError(f"Variables not present in dataset: {missing}")

    lat = ds["lat"].values
    if lat.size >= 2 and lat[0] > lat[-1]:
        lat_slice = slice(bbox.north, bbox.south)
    else:
        lat_slice = slice(bbox.south, bbox.north)

    ds = ds.sel(lat=lat_slice, lon=slice(bbox.west, bbox.east))
    ds = ds.sel(time=slice(f"{year_range[0]}-01-01", f"{year_range[1]}-12-31"))
    ds = ds[variables]

    if ds.sizes.get("lat", 0) == 0 or ds.sizes.get("lon", 0) == 0:
        raise ValueError(
            f"AOI selection is empty: no grid points within bbox {tuple(bbox)}."
        )
    if ds.sizes.get("time", 0) == 0:
        raise ValueError(
            f"No time steps within {year_range} for this dataset."
        )
    return ds


def open_period(
    model: str,
    experiment: str,
    bbox,
    year_range,
    variables: Iterable[str],
    api_key: Optional[str] = None,
    resolution: str = _DEFAULT_RESOLUTION,
) -> xr.Dataset:
    """Open a Climate DT period from EDH and return the lazy AOI/time/variable subset."""
    api_key = api_key or os.environ.get("EDH_API_KEY")
    if not api_key:
        raise ValueError(
            "No EDH API key: pass api_key or set EDH_API_KEY "
            "(get one from your DESP account settings; Climate DT needs upgraded access)."
        )
    url = authed_url(zarr_url(model, experiment, resolution), api_key)
    log.info("Opening EDH dataset: %s (%s)", zarr_url(model, experiment, resolution), experiment)
    ds = xr.open_dataset(
        url,
        engine="zarr",
        chunks={},
        storage_options={"client_kwargs": {"trust_env": True}},
    )
    return subset(ds, bbox, year_range, variables)
