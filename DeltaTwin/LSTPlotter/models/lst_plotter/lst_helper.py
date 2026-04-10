from __future__ import annotations

from typing import Optional

import xarray as xr
import pandas as pd
import numpy as np

from nuts_helper import mask_nuts3


def calculate_LST_from_L1_bands(
        band4: xr.DataArray,
        band5: xr.DataArray,
        band10: xr.DataArray,
        mtl_path: str,
        nuts3_code: Optional[str],
    ) -> xr.DataArray:
    """Calculate LST from Landsat L1 bands and metadata.

    Parameters
    ----------
    band4, band5, band10 : xr.DataArray
        Required Landsat bands (red, near-infrared, thermal).
    mtl_path : str
        Path to Landsat metadata text file.
    nuts3_code : Optional[str]
        NUTS3 code used to mask the output if provided.

    Returns
    -------
    xr.DataArray
        LST in degrees Celsius, rounded to one decimal and clipped to region when requested.
    """

    BT = calculate_brightness_temperature_L1(band10, str(mtl_path))
    ndvi = ndvi_calculation(band5, band4)
    Pv = proportion_vegetation(ndvi.squeeze())

    emissivity = calculate_land_emissivity(ndvi.squeeze(), Pv)
    LST = calculate_LST(BT, emissivity)
    LST = LST.rio.write_crs("EPSG:4326")
    da = mask_nuts3(nuts3_code, LST) if nuts3_code is not None else LST
    da = np.round(da, 1)
    da = da.astype(np.float32)
    return da.rio.write_nodata(np.nan)


def mask_clouds(
    da: xr.DataArray,
    qa_pixel: xr.DataArray,
) -> xr.DataArray:
    """Mask cloudy pixels using the Landsat Collection 2 QA_PIXEL band.

    Pixels flagged as dilated cloud (bit 1), cloud (bit 3), or cloud shadow
    (bit 4) are set to NaN.
    """
    qa = qa_pixel.squeeze().astype(np.uint16)
    cloud_bits = np.uint16((1 << 1) | (1 << 3) | (1 << 4))  # 0x001A
    cloudy = (qa & cloud_bits) != 0
    return da.where(~cloudy)


def calculate_LST_from_L2_bands(
        band10: xr.DataArray,
        mtl_path: str,
        nuts3_code: Optional[str],
        qa_pixel: Optional[xr.DataArray] = None,
    ) -> xr.DataArray:
    """Calculate LST from Landsat Collection 2 Level-2 thermal band.

    Parameters
    ----------
    band10 : xr.DataArray
        Landsat L2 ``ST_B10`` thermal band.
    mtl_path : str
        Path to Landsat metadata text file.
    nuts3_code : Optional[str]
        NUTS3 code used to mask the output if provided.
    qa_pixel : Optional[xr.DataArray]
        Optional QA_PIXEL band for cloud masking.

    Returns
    -------
    xr.DataArray
        LST in degrees Celsius, rounded to one decimal and clipped to region when requested.
    """
    # For Landsat Collection 2 Level-2, ST_B10 is the NASA-produced LST product
    # (single-channel algorithm with full atmospheric and emissivity correction).
    # We decode it directly to °C; the manual emissivity pipeline is not needed.
    LST = calculate_brightness_temperature_L2(band10, str(mtl_path))
    LST = LST.rio.write_crs("EPSG:4326")
    if qa_pixel is not None:
        LST = mask_clouds(LST, qa_pixel)
    da = mask_nuts3(nuts3_code, LST) if nuts3_code is not None else LST
    da = np.round(da, 1)
    da = da.astype(np.float32)
    return da.rio.write_nodata(np.nan)


def extract_parameter_value(file_path: str, parameter_name: str) -> Optional[float]:
    """Extract a numeric parameter value from a Landsat metadata text file."""
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


def ndvi_calculation(band5: xr.DataArray, band4: xr.DataArray) -> xr.DataArray:
    """Compute NDVI from near-infrared and red bands."""
    band5 = xr.where(band5 == 0, np.nan, band5)
    band4 = xr.where(band4 == 0, np.nan, band4)
    return (band5 - band4) / (band5 + band4)


def proportion_vegetation(ndvi: xr.DataArray) -> xr.DataArray:
    """Estimate vegetation proportion from NDVI."""
    ndvi_max = np.nanmax(ndvi)
    ndvi_min = np.nanmin(ndvi)
    return ((ndvi - ndvi_min) / (ndvi_max - ndvi_min)) ** 2


def calculate_land_emissivity(ndvi: xr.DataArray, pv: xr.DataArray) -> xr.DataArray:
    """Calculate land surface emissivity from NDVI and vegetation proportion."""
    ndvi_max = np.nanmax(ndvi)
    ndvi_min = np.nanmin(ndvi)
    # Constants from the paper
    C = 0.005  # surface roughness constant
    e_soil = 0.996  # soil emissivity
    e_veg = 0.973  # vegetation emissivity
    e_water = 0.991  # water emissivity
    
    # Initialize output array with the same coordinates and dimensions as NDVI
    emissivity = xr.zeros_like(ndvi)
    
    # Water condition (NDVI < 0)
    water_mask = ndvi < 0
    emissivity = xr.where(water_mask, e_water, emissivity)
    
    # Bare soil condition (NDVI < NDVIs)
    soil_mask = (ndvi >= 0) & (ndvi < ndvi_min)
    emissivity = xr.where(soil_mask, e_soil, emissivity)
    
    # Mixed condition (NDVIs ≤ NDVI ≤ NDVIv)
    mixed_mask = (ndvi >= ndvi_min) & (ndvi <= ndvi_max)
    mixed_value = (e_veg * pv) + (e_soil * (1 - pv)) + C
    emissivity = xr.where(mixed_mask, mixed_value, emissivity)
    
    # Full vegetation condition (NDVI > NDVIv)
    veg_mask = ndvi > ndvi_max
    emissivity = xr.where(veg_mask, e_veg + C, emissivity)
    
    emissivity = xr.where(emissivity <= 0.973, np.nan, emissivity)
    emissivity = xr.where(emissivity > 1, 1, emissivity)
    
    return emissivity

def calculate_brightness_temperature_L1(band10: xr.DataArray, filepath: str) -> xr.DataArray:
    """Calculate brightness temperature from Landsat L1 thermal DN values."""

    Mp_10 =  extract_parameter_value(filepath, "RADIANCE_MULT_BAND_10")
    Ap_10 = extract_parameter_value(filepath, "RADIANCE_ADD_BAND_10")
    K1 = extract_parameter_value(filepath, "K1_CONSTANT_BAND_10")
    K2 = extract_parameter_value(filepath, "K2_CONSTANT_BAND_10")

    TOA_10 = Mp_10 * band10 + Ap_10 - 0.29
    TOA_10 = xr.where(TOA_10 < 0, np.nan, TOA_10)
    BT = K2 / np.log(K1 / TOA_10 + 1) - 273.15
    return BT


def calculate_LST(BT: xr.DataArray, emissivity: xr.DataArray) -> xr.DataArray:
    """Calculate land surface temperature from brightness temperature and emissivity."""
    lampda = 10.895 # [micron]
    rho = 14388 # [micron K]
    par = (lampda * BT)/rho

    Ts = BT / (1 + par * np.log(emissivity))
    return Ts


def calculate_brightness_temperature_L2(band10: xr.DataArray, filepath: str) -> xr.DataArray:
    """Calculate LST in Celsius from Landsat Collection 2 Level-2 ``ST_B10``."""
    T_mult = extract_parameter_value(filepath, "TEMPERATURE_MULT_BAND_ST_B10")
    T_add  = extract_parameter_value(filepath, "TEMPERATURE_ADD_BAND_ST_B10")
    BT = band10 * T_mult + T_add - 273.15
    BT = xr.where(band10 == 0, np.nan, BT)  # mask fill pixels
    return BT