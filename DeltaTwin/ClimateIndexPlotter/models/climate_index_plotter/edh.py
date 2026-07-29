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
_BASE_PATH = "climate-dt-2"
_MODELS = {"IFS-NEMO", "IFS-FESOM", "ICON"}
_EXPERIMENTS = {"hist", "SSP3-7.0"}
DEFAULT_RESOLUTION = "standard"

# Climate DT is published on EDH regridded from its native ~5-10 km HEALPix grid to two
# regular lat/lon variants. 'high' resolves to the high-timeseries variant, whose chunking
# (6480 hours x 32 x 32 cells) suits this component's whole-period, small-area access
# pattern; the high-maps variant (24 hours x 512 x 512) does not.
RESOLUTIONS = {
    "standard": ("standard", 0.352),
    "high": ("high-timeseries", 0.044),
}


def grid_spacing(resolution: str) -> float:
    """Nominal grid spacing in degrees for a resolution keyword."""
    return _resolution_entry(resolution)[1]


def _resolution_entry(resolution: str) -> tuple[str, float]:
    if resolution not in RESOLUTIONS:
        raise ValueError(
            f"Unknown resolution {resolution!r}. Valid: {sorted(RESOLUTIONS)}"
        )
    return RESOLUTIONS[resolution]


def zarr_url(model: str, experiment: str, resolution: str = DEFAULT_RESOLUTION) -> str:
    """Public Zarr URL for a Climate DT surface-hourly dataset on Earth Data Hub.

    Slug pattern (from the EDH ``climate-dt-2`` catalogue), e.g.
    ``IFS-NEMO-hist-sfc-hourly-standard-v0.zarr``.
    """
    if model not in _MODELS:
        raise ValueError(f"Unknown model {model!r}. Valid: {sorted(_MODELS)}")
    if experiment not in _EXPERIMENTS:
        raise ValueError(
            f"Unknown experiment {experiment!r}. Valid: {sorted(_EXPERIMENTS)}"
        )
    variant = _resolution_entry(resolution)[0]
    slug = f"{model}-{experiment}-sfc-hourly-{variant}-v0.zarr"
    return f"{_HOST}/{_BASE_PATH}/{slug}"


def authed_url(url: str, api_key: str) -> str:
    """Inject EDH HTTP basic auth (user ``edh``, password = API key) into a URL."""
    scheme, rest = url.split("://", 1)
    return f"{scheme}://edh:{api_key}@{rest}"


def normalize_coords(ds: xr.Dataset) -> xr.Dataset:
    """Rename ``latitude``/``longitude`` to ``lat``/``lon`` if present.

    ``preferred_chunks`` in each variable's encoding is renamed alongside, so the on-disk
    chunk shape stays discoverable by dimension name (see :func:`stream_bytes`).
    """
    rename = {}
    if "latitude" in ds.variables:
        rename["latitude"] = "lat"
    if "longitude" in ds.variables:
        rename["longitude"] = "lon"
    if not rename:
        return ds

    ds = ds.rename(rename)
    for var in ds.variables.values():
        preferred = var.encoding.get("preferred_chunks")
        if preferred:
            var.encoding["preferred_chunks"] = {
                rename.get(dim, dim): size for dim, size in preferred.items()
            }
    return ds


def stream_bytes(ds: xr.Dataset, variables: Iterable[str]) -> int:
    """Uncompressed bytes that must be fetched to read ``variables`` over all of ``ds``.

    Zarr is read a whole chunk at a time, so a selection that clips a chunk still costs the
    full chunk. This counts the chunks the selection touches at their on-disk size, which is
    what governs run time: a small AOI is no cheaper than one filling the same chunks.
    """
    total = 0
    for name in variables:
        var = ds[name]
        if var.chunks is None:
            total += var.nbytes
            continue
        preferred = var.encoding.get("preferred_chunks", {})
        cells = 1
        for dim, dim_chunks in zip(var.dims, var.chunks):
            store_chunk = preferred.get(dim, max(dim_chunks))
            cells *= len(dim_chunks) * store_chunk
        total += cells * var.dtype.itemsize
    return total


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
    resolution: str = DEFAULT_RESOLUTION,
) -> xr.Dataset:
    """Open a Climate DT period from EDH and return the lazy AOI/time/variable subset."""
    api_key = api_key or os.environ.get("EDH_API_KEY")
    if not api_key:
        raise ValueError(
            "No EDH API key: pass api_key or set EDH_API_KEY "
            "(get one from your DESP account settings; Climate DT needs upgraded access)."
        )
    public_url = zarr_url(model, experiment, resolution)
    log.info("Opening EDH dataset: %s (%s)", public_url, experiment)
    ds = xr.open_dataset(
        authed_url(public_url, api_key),
        engine="zarr",
        chunks={},
        storage_options={"client_kwargs": {"trust_env": True}},
    )
    ds = subset(ds, bbox, year_range, variables)
    log.info(
        "%s %s: grid %d lat x %d lon, %d hourly steps, %.1f GB to stream",
        experiment,
        year_range,
        ds.sizes["lat"],
        ds.sizes["lon"],
        ds.sizes["time"],
        stream_bytes(ds, variables) / 1e9,
    )
    return ds
