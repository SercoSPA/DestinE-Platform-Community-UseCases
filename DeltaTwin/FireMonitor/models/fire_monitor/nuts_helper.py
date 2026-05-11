from __future__ import annotations

import difflib
import logging

from shapely.geometry.base import BaseGeometry
import xarray as xr
import geopandas as gpd


log = logging.getLogger(__name__)

_NUTS2_GEOJSON_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_2021_4326_LEVL_2.geojson"
)


def _load_nuts2_region(nuts2_code: str) -> gpd.GeoDataFrame:
    """Load the NUTS2 GeoDataFrame row for *nuts2_code* (raises on not found)."""
    nuts2 = gpd.read_file(_NUTS2_GEOJSON_URL)
    region = nuts2[nuts2["NUTS_ID"] == nuts2_code]
    if region.empty:
        raise ValueError(
            f"NUTS2 region '{nuts2_code}' not found. "
            "Check the code against the Eurostat NUTS classification."
        )
    return region


def get_nuts2_geom(nuts2_code: str) -> BaseGeometry:
    """Return merged geometry for a NUTS2 region code."""
    return _load_nuts2_region(nuts2_code).geometry.union_all()


def find_nuts2_by_name(name: str) -> tuple[str, str]:
    """Find the NUTS2 code that best matches a region name.

    Performs a case-insensitive substring search across the ``NUTS_NAME`` and
    ``NAME_LATN`` columns of the Eurostat NUTS2 dataset.  When multiple
    candidates are found the one with the highest string similarity (via
    :mod:`difflib`) is returned.  If no substring match exists the function
    falls back to fuzzy matching across all region names.

    Parameters
    ----------
    name : str
        Free-text region name, e.g. ``"Galicia"`` or ``"Ile-de-France"``.

    Returns
    -------
    str
        The NUTS2 code for the best-matching region, e.g. ``"ES11"``.

    Raises
    ------
    ValueError
        When no NUTS2 region with a plausible similarity to *name* can be found.
    """
    nuts2 = gpd.read_file(_NUTS2_GEOJSON_URL)
    name_lower = name.lower()

    # 1. Substring search in both name columns
    mask = (
        nuts2["NUTS_NAME"].str.lower().str.contains(name_lower, na=False)
        | nuts2["NAME_LATN"].str.lower().str.contains(name_lower, na=False)
    )
    candidates = nuts2[mask]

    if candidates.empty:
        # 2. Fuzzy fallback across all region names
        all_names = nuts2["NUTS_NAME"].tolist() + nuts2["NAME_LATN"].fillna("").tolist()
        close = difflib.get_close_matches(name, all_names, n=5, cutoff=0.3)
        if not close:
            raise ValueError(
                f"No NUTS2 region found matching '{name}'. "
                "Check the name against the Eurostat NUTS classification."
            )
        best_match = close[0]
        candidates = nuts2[
            (nuts2["NUTS_NAME"] == best_match) | (nuts2["NAME_LATN"] == best_match)
        ]

    # Among candidates, pick the one with the highest name similarity
    def _similarity(row) -> float:
        n = name.lower()
        return max(
            difflib.SequenceMatcher(None, n, str(row["NUTS_NAME"]).lower()).ratio(),
            difflib.SequenceMatcher(None, n, str(row.get("NAME_LATN") or "").lower()).ratio(),
        )

    best_row = candidates.iloc[candidates.apply(_similarity, axis=1).values.argmax()]
    code = best_row["NUTS_ID"]
    region_name = best_row["NUTS_NAME"]
    log.info(f"Matched NUTS2 region: {region_name} ({code})")
    return code, region_name


def mask_nuts2(
    nuts2_code: str,
    da: xr.DataArray,
) -> xr.DataArray:
    """Mask a DataArray to a NUTS2 region using Eurostat GISCO boundaries.

    Parameters
    ----------
    nuts2_code : str
        NUTS2 region code, e.g. ``"ES11"`` for Galicia.
    da : xr.DataArray
        DataArray to mask (must already be in EPSG:4326, as produced by
        ``rioxarray``'s ``rio.reproject``).

    Returns
    -------
    xr.DataArray
        Input data clipped to the requested NUTS2 region.
    """
    region = _load_nuts2_region(nuts2_code)
    log.info(f"Using NUTS2 region: {region['NUTS_NAME'].iloc[0]} ({nuts2_code})")
    return da.rio.clip(region.geometry.values, crs=region.crs, drop=False, all_touched=True)

