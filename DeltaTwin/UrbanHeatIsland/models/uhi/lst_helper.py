from __future__ import annotations

from pathlib import Path
from typing import Optional

import xarray as xr
import rioxarray as rio
import pandas as pd
import numpy as np
import geopandas as gpd

_NUTS3_GEOJSON_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_2021_4326_LEVL_3.geojson"
)


def calculate_LST_from_L1_bands(
        Band4,
        Band5,
        Band10,
        mtl_path,
        nuts3_code,
    ) -> xr.DataArray:

    BT = calculate_brightness_temperature_L1(Band10, str(mtl_path))
    ndvi = ndvi_calculation(Band5, Band4)
    Pv = proportion_vegetation(ndvi.squeeze())

    emissivity = calculate_land_emissivity(ndvi.squeeze(), Pv)
    LST = calculate_LST(BT, emissivity)
    LST = LST.rio.write_crs("EPSG:4326")
    da = mask_nuts3(nuts3_code, LST) if nuts3_code is not None else LST
    da = np.round(da, 1)
    da = da.astype(np.float32)
    return da.rio.write_nodata(np.nan)


def calculate_LST_from_L2_bands(
        Band10,
        mtl_path,
        nuts3_code,
    ) -> xr.DataArray:
    # For Landsat Collection 2 Level-2, ST_B10 is the NASA-produced LST product
    # (single-channel algorithm with full atmospheric and emissivity correction).
    # We decode it directly to °C; the manual emissivity pipeline is not needed.
    LST = calculate_brightness_temperature_L2(Band10, str(mtl_path))
    LST = LST.rio.write_crs("EPSG:4326")
    da = mask_nuts3(nuts3_code, LST) if nuts3_code is not None else LST
    da = np.round(da, 1)
    da = da.astype(np.float32)
    return da.rio.write_nodata(np.nan)


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


def extract_parameter_value(file_path: str, parameter_name: str) -> Optional[float]:
    """Extract a specific parameter value from a formatted text file.

    :param file_path: Path to the text file.
    :param parameter_name: Parameter name to search for.
    :return: Parameter value as float, or None if not found.
    """
    try:
        # Read the file as text
        with open(file_path, "r") as file:
            lines = file.readlines()
        
        # Create a DataFrame with each line as a single element
        df = pd.DataFrame(lines, columns=["line"])

        # Find the line containing the parameter
        parameter_line = df[df["line"].str.contains(parameter_name)].iloc[0]["line"]
        
        # Extract the value by splitting it from the parameter name
        _, value = parameter_line.split("=")
        return float(value.strip())
    
    except (IndexError, ValueError):
        # Return None if the parameter is not found or is not numeric
        return None


def ndvi_calculation(Band5: xr.DataArray, Band4: xr.DataArray) -> xr.DataArray:
    Band5 = xr.where(Band5 == 0 , np.nan, Band5)
    Band4 = xr.where(Band4 == 0 , np.nan, Band4)
    return (Band5 - Band4) / (Band5 + Band4)

def proportion_vegetation(NDVI: xr.DataArray) -> xr.DataArray:
    NDVI_max = np.nanmax(NDVI)
    NDVI_min = np.nanmin(NDVI)
    return ((NDVI-NDVI_min)/(NDVI_max - NDVI_min))**2

def calculate_land_emissivity(NDVI: xr.DataArray, Pv: xr.DataArray) -> xr.DataArray:
    """
    Calculate land surface emissivity based on NDVI values and vegetation proportion.
    
    Parameters:
    NDVI: Normalized Difference Vegetation Index
    Pv: Vegetation proportion
    
    Returns:
    Land surface emissivity value
    """
    NDVI_max = np.nanmax(NDVI)
    NDVI_min = np.nanmin(NDVI)
    # Constants from the paper
    C = 0.005  # surface roughness constant
    e_soil = 0.996  # soil emissivity
    e_veg = 0.973  # vegetation emissivity
    e_water = 0.991  # water emissivity
    
    # Initialize output array with the same coordinates and dimensions as NDVI
    emissivity = xr.zeros_like(NDVI)
    
    # Water condition (NDVI < 0)
    water_mask = NDVI < 0
    emissivity = xr.where(water_mask, e_water, emissivity)
    
    # Bare soil condition (NDVI < NDVIs)
    soil_mask = (NDVI >= 0) & (NDVI < NDVI_min)
    emissivity = xr.where(soil_mask, e_soil, emissivity)
    
    # Mixed condition (NDVIs ≤ NDVI ≤ NDVIv)
    mixed_mask = (NDVI >= NDVI_min) & (NDVI <= NDVI_max)
    mixed_value = (e_veg * Pv) + (e_soil * (1 - Pv)) + C
    emissivity = xr.where(mixed_mask, mixed_value, emissivity)
    
    # Full vegetation condition (NDVI > NDVIv)
    veg_mask = NDVI > NDVI_max
    emissivity = xr.where(veg_mask, e_veg + C, emissivity)
    
    emissivity = xr.where(emissivity <= 0.973, np.nan, emissivity)
    emissivity = xr.where(emissivity > 1, 1, emissivity)
    
    return emissivity

def calculate_brightness_temperature_L1(Band10: xr.DataArray, filepath: str) -> xr.DataArray:
    '''
    Band10 : Digital Number from Landsat TIRS instrument Band 10
    filepath : file of metadata file
    '''
    
    Mp_10 =  extract_parameter_value(filepath, "RADIANCE_MULT_BAND_10")
    Ap_10 = extract_parameter_value(filepath, "RADIANCE_ADD_BAND_10")
    K1 = extract_parameter_value(filepath, "K1_CONSTANT_BAND_10")
    K2 = extract_parameter_value(filepath, "K2_CONSTANT_BAND_10")

    TOA_10 = Mp_10 * Band10 + Ap_10 - 0.29
    TOA_10 = xr.where(TOA_10 < 0 , np.nan, TOA_10)
    BT = K2 /  np.log  ( K1 / TOA_10  + 1) - 273.15
    return BT


def calculate_LST(BT: xr.DataArray, emissivity: xr.DataArray) -> xr.DataArray:
    """
    Calculate Land Surface Temperature.
    """
    lampda = 10.895 # [micron]
    rho = 14388 # [micron K]
    par = (lampda * BT)/rho

    Ts = BT / (1 + par * np.log(emissivity))
    return Ts


def calculate_brightness_temperature_L2(Band10: xr.DataArray, filepath: str) -> xr.DataArray:
    '''
    Band10 : Scaled DN from Landsat Collection 2 Level-2 ST_B10 product.
    filepath : path to the MTL metadata file.

    For Collection 2 Level-2 the ST_B10 band stores surface temperature
    as scaled integers; the correct conversion is:
        T(K) = DN * TEMPERATURE_MULT_BAND_ST_B10 + TEMPERATURE_ADD_BAND_ST_B10
    (standard Landsat C2L2 values: mult=0.00341802, add=149.0)
    '''
    T_mult = extract_parameter_value(filepath, "TEMPERATURE_MULT_BAND_ST_B10")
    T_add  = extract_parameter_value(filepath, "TEMPERATURE_ADD_BAND_ST_B10")
    BT = Band10 * T_mult + T_add - 273.15
    BT = xr.where(Band10 == 0, np.nan, BT)  # mask fill pixels
    return BT