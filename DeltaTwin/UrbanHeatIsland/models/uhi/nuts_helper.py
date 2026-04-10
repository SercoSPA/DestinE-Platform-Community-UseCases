from __future__ import annotations

import difflib

from shapely.geometry.base import BaseGeometry
import xarray as xr
import geopandas as gpd

_NUTS3_GEOJSON_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_2021_4326_LEVL_3.geojson"
)


def _load_nuts3_region(nuts3_code: str) -> gpd.GeoDataFrame:
    """Load the NUTS3 GeoDataFrame row for *nuts3_code* (raises on not found)."""
    nuts3 = gpd.read_file(_NUTS3_GEOJSON_URL)
    region = nuts3[nuts3["NUTS_ID"] == nuts3_code]
    if region.empty:
        raise ValueError(
            f"NUTS3 region '{nuts3_code}' not found. "
            "Check the code against the Eurostat NUTS classification."
        )
    return region


def get_nuts3_geom(nuts3_code: str) -> BaseGeometry:
    return _load_nuts3_region(nuts3_code).geometry.union_all()


def find_nuts3_by_name(name: str) -> tuple[str, str]:
    """Find the NUTS3 code that best matches a city or region name.

    Performs a case-insensitive substring search across the ``NUTS_NAME`` and
    ``NAME_LATN`` columns of the Eurostat NUTS3 dataset.  When multiple
    candidates are found the one with the highest string similarity (via
    :mod:`difflib`) is returned.  If no substring match exists the function
    falls back to fuzzy matching across all region names.

    Parameters
    ----------
    name : str
        Free-text city or region name, e.g. ``"Rome"``, ``"Rotterdam"``, or
        ``"Ile-de-France"``.

    Returns
    -------
    str
        The NUTS3 code for the best-matching region, e.g. ``"ITI43"``.

    Raises
    ------
    ValueError
        When no region with a plausible similarity to *name* can be found.
    """
    nuts3 = gpd.read_file(_NUTS3_GEOJSON_URL)
    name_lower = name.lower()

    # 1. Substring search in both name columns
    mask = (
        nuts3["NUTS_NAME"].str.lower().str.contains(name_lower, na=False)
        | nuts3["NAME_LATN"].str.lower().str.contains(name_lower, na=False)
    )
    candidates = nuts3[mask]

    if candidates.empty:
        # 2. Fuzzy fallback across all region names
        all_names = nuts3["NUTS_NAME"].tolist() + nuts3["NAME_LATN"].fillna("").tolist()
        close = difflib.get_close_matches(name, all_names, n=5, cutoff=0.3)
        if not close:
            raise ValueError(
                f"No NUTS3 region found matching '{name}'. "
                "Check the name against the Eurostat NUTS classification."
            )
        best_match = close[0]
        candidates = nuts3[
            (nuts3["NUTS_NAME"] == best_match) | (nuts3["NAME_LATN"] == best_match)
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
    print(f"Matched NUTS3 region: {region_name} ({code})")
    return code, region_name


def mask_nuts3(
    nuts3_code: str,
    da: xr.DataArray,
) -> xr.DataArray:
    """Mask a DataArray to a NUTS3 region using Eurostat GISCO boundaries.

    Parameters
    ----------
    nuts3_code : str
        NUTS3 region code, e.g. ``"ITI43"`` for the Province of Rome.
    da : xr.DataArray
        DataArray to mask (must already be in EPSG:4326, as produced by
        ``rioxarray``'s ``rio.reproject``).
    """
    region = _load_nuts3_region(nuts3_code)
    print(f"Using NUTS3 region: {region['NUTS_NAME'].iloc[0]} ({nuts3_code})")
    return da.rio.clip(region.geometry.values, crs=region.crs, drop=False, all_touched=True)

