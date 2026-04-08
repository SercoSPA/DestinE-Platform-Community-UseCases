from __future__ import annotations

import xarray as xr
import geopandas as gpd

_NUTS3_GEOJSON_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_2021_4326_LEVL_3.geojson"
)


def get_nuts3_geom(
        nuts3_code: str
) -> str:
    nuts3 = gpd.read_file(_NUTS3_GEOJSON_URL)
    region = nuts3[nuts3["NUTS_ID"] == nuts3_code]
    if region.empty:
        raise ValueError(
            f"NUTS3 region '{nuts3_code}' not found. "
            "Check the code against the Eurostat NUTS classification."
        )
    return region.geometry.union_all().wkt


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
    nuts3 = gpd.read_file(_NUTS3_GEOJSON_URL)
    region = nuts3[nuts3["NUTS_ID"] == nuts3_code]
    if region.empty:
        raise ValueError(
            f"NUTS3 region '{nuts3_code}' not found. "
            "Check the code against the Eurostat NUTS classification."
        )
    print(f"Using NUTS3 region: {region['NUTS_NAME'].iloc[0]} ({nuts3_code})")
    return da.rio.clip(region.geometry.values, crs=region.crs, drop=False, all_touched=True)

