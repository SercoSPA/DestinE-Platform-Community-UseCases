from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

import xarray as xr
import rioxarray as rio
import pandas as pd
import numpy as np
import geopandas as gpd
from regionmask import mask_geopandas
import json
import rasterio
import warnings
from shapely.geometry import Polygon

from hda import search_and_download


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

def calculate_brightness_temperature(Band10: xr.DataArray, filepath: str) -> xr.DataArray:
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


def mask(
    shapepath: str,
    da: xr.DataArray,
    epsg: int = 4326,
    zona: str = "Roma",
    lon_name: str = "lon",
    lat_name: str = "lat",
) -> xr.DataArray:
    shape = gpd.read_file(shapepath).to_crs(epsg = epsg)
    city = shape[shape.COMUNE == zona]
    mask_array = mask_geopandas(city, da, lon_name = lon_name, lat_name = lat_name) * 0 + 1

    return da * mask_array


_NUTS3_GEOJSON_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_2021_4326_LEVL_3.geojson"
)


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

def raster(da: xr.DataArray, tiffname: str) -> None:
    return da.rio.to_raster(tiffname)

def create_geojson(
    ds_mask: xr.DataArray | xr.Dataset,
    geojson_path: str,
    optional_params: Optional[dict] = None,
) -> None:
    try:
        # Create a meshgrid from the coordinate arrays
        x = ds_mask.x.values
        y = ds_mask.y.values
        xx, yy = np.meshgrid(x, y)
        
        # Extract the data, assuming a single band
        # If ds_mask is a DataArray, use .values directly
        # If it is a Dataset, select the variable
        if hasattr(ds_mask, 'band'):
            hw_data = ds_mask.sel(band=1).values
        else:
            hw_data = ds_mask.values
            
        # Create lists for polygons and data
        polygons = []
        data = []
        
        # Iterate through the grid
        for i in range(len(y) - 1):
            for j in range(len(x) - 1):
                value = hw_data[i, j]
                if not np.isnan(value):
                    # Create cell polygon using actual coordinates
                    polygon = Polygon([
                        (float(x[j]), float(y[i])),           # top left
                        (float(x[j+1]), float(y[i])),         # top right
                        (float(x[j+1]), float(y[i+1])),       # bottom right
                        (float(x[j]), float(y[i+1]))          # bottom left
                    ])
                    polygons.append(polygon)
                    data.append({"valore": float(value)})
        
        # Create GeoDataFrame
        gdf = gpd.GeoDataFrame(data, geometry=polygons)
        gdf.set_crs(epsg=4326, inplace=True)
        
        # Convert to GeoJSON
        geojson_dict = json.loads(gdf.to_json())
        
        # Add optional parameters if provided
        if optional_params:
            geojson_dict['altro'] = optional_params
            
        # Save the GeoJSON
        with open(geojson_path, 'w') as f:
            json.dump(geojson_dict, f)
            
        print(f"Number of non-NaN cells included in the GeoJSON: {len(gdf)}")
        print("GeoJSON created successfully!")
        
    except Exception as e:
        print(f"An error occurred: {str(e)}")

def create_geojson_per_zone(
    ds_mask: xr.DataArray | xr.Dataset,
    shapefile_path: str,
    geojson_path: str,
    optional_params: Optional[dict] = None,
) -> Optional[gpd.GeoDataFrame]:
    """
    Create a GeoJSON containing the shapefile polygons, where each polygon
    has the mean value of the raster cells that intersect it.
    
    Parameters:
    -----------
    ds_mask : xarray.DataArray or xarray.Dataset
        Raster dataset containing values to average
    shapefile_path : str
        Path to the shapefile with zones
    geojson_path : str
        Path where the resulting GeoJSON is saved
    optional_params : dict, optional
        Optional parameters to add to the GeoJSON
    """
    
    try:
        # Load the shapefile
        zone_gdf = gpd.read_file(shapefile_path)
        print(f"Shapefile loaded: {len(zone_gdf)} zones found")
        
        # Ensure the shapefile has the same CRS as the raster
        # Get the CRS from the DataArray (assume 4326 if unspecified)
        raster_crs = getattr(ds_mask, 'crs', 'EPSG:4326')
        if hasattr(raster_crs, 'to_epsg'):
            raster_epsg = raster_crs.to_epsg()
            raster_crs = f"EPSG:{raster_epsg}" if raster_epsg else raster_crs
        
        # Reproject the shapefile if needed
        if zone_gdf.crs is None:
            warnings.warn("The shapefile has no CRS defined. Assuming the same CRS as the raster.")
            zone_gdf.set_crs(raster_crs, inplace=True)
        elif zone_gdf.crs != raster_crs:
            print(f"Reprojecting shapefile from {zone_gdf.crs} to {raster_crs}")
            zone_gdf = zone_gdf.to_crs(raster_crs)
        
        # Extract data from the DataArray
        x = ds_mask.x.values
        y = ds_mask.y.values
        
        # Get raster data
        if hasattr(ds_mask, 'band'):
            raster_data = ds_mask.sel(band=1).values
        else:
            raster_data = ds_mask.values
        
        # Results for each zone
        risultati = []
        
        # Iterate over each zone in the shapefile
        for idx, zona in zone_gdf.iterrows():
            geom = zona.geometry
            
            # Create a mask for this zone
            # First get the zone bounds
            minx, miny, maxx, maxy = geom.bounds
            
            # Find indices for points that fall within these bounds
            x_indices = np.where((x >= minx) & (x <= maxx))[0]
            y_indices = np.where((y >= miny) & (y <= maxy))[0]
            
            if len(x_indices) == 0 or len(y_indices) == 0:
                print(f"Zone {idx}: No raster cell intersects this zone")
                continue
            
            # Create a mask for this zone
            mask = np.zeros_like(raster_data, dtype=bool)
            
            # For each cell, check whether the center is inside the polygon
            for i in y_indices:
                for j in x_indices:
                    if i < raster_data.shape[0] and j < raster_data.shape[1]:
                        punto_centrale = (x[j], y[i])
                        if geom.contains(rasterio.transform.xy_to_point(*punto_centrale)):
                            mask[i, j] = True
            
            # Apply the mask to keep only values inside the zone
            valori_zona = raster_data[mask]
            
            # Compute the mean value for the zone (ignoring NaN)
            if len(valori_zona) > 0 and not np.all(np.isnan(valori_zona)):
                media = float(np.nanmean(valori_zona))
                num_celle = np.sum(~np.isnan(valori_zona))
                print(f"Zone {idx}: Mean={media:.4f} (based on {num_celle} valid cells)")
            else:
                media = np.nan
                print(f"Zone {idx}: No valid data found")
            
            # Add the result
            risultati.append({
                "geometry": geom,
                "properties": {
                    "zona_id": idx,
                    "valore_medio": media if not np.isnan(media) else None,
                    "num_celle": int(np.sum(~np.isnan(valori_zona))) if len(valori_zona) > 0 else 0
                }
            })
        
        # Create a GeoDataFrame with the results
        risultati_gdf = gpd.GeoDataFrame(
            [r["properties"] for r in risultati], 
            geometry=[r["geometry"] for r in risultati],
            crs=zone_gdf.crs
        )
        
        # Convert to GeoJSON
        geojson_dict = json.loads(risultati_gdf.to_json())
        
        # Add optional parameters if provided
        if optional_params:
            geojson_dict['properties'] = optional_params
        
        # Save the GeoJSON
        with open(geojson_path, 'w') as f:
            json.dump(geojson_dict, f)
        
        print(f"GeoJSON created successfully with {len(risultati_gdf)} zones!")
        return risultati_gdf
        
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def calculate_LST_from_file(
        files: Sequence[str | Path],
        nuts3_code: str,
    ) -> None:
    file_paths = [Path(item) for item in files]

    def _find_path(suffix: str) -> Optional[Path]:
        for candidate in file_paths:
            candidate_name = candidate.name.upper()
            if candidate_name.endswith(suffix.upper()):
                return candidate
        return None

    band4_path = _find_path("B4.TIF")
    band5_path = _find_path("B5.TIF")
    band10_path = _find_path("B10.TIF")
    mtl_path = _find_path("MTL.TXT")

    missing = []
    if band4_path is None:
        missing.append("B4")
    if band5_path is None:
        missing.append("B5")
    if band10_path is None:
        missing.append("B10")
    if mtl_path is None:
        missing.append("MTL")

    if missing:
        raise ValueError(
            f"Missing required downloaded files: {missing}. "
            f"Got: {[path.name for path in file_paths]}"
        )

    scene_name = band4_path.stem
    for token in ["_SR_B4", "_B4", "_ST_B4"]:
        if scene_name.upper().endswith(token):
            scene_name = scene_name[: -len(token)]
            break

    print(f"Using inputs: {band4_path.name}, {band5_path.name}, {band10_path.name}, {mtl_path.name}")
    output_dir = band4_path.parent

    Band5 = rio.open_rasterio(str(band5_path)).rio.reproject("EPSG:4326")
    Band4 = rio.open_rasterio(str(band4_path)).rio.reproject("EPSG:4326")
    Band10 = rio.open_rasterio(str(band10_path)).rio.reproject("EPSG:4326")

    BT = calculate_brightness_temperature(Band10, str(mtl_path))
    ndvi = ndvi_calculation(Band5, Band4)
    Pv = proportion_vegetation(ndvi.squeeze())

    emissivity = calculate_land_emissivity(ndvi.squeeze(), Pv)
    LST = calculate_LST(BT, emissivity)
    da = mask_nuts3(nuts3_code, LST)
    da = np.round(da, 1)
    print(da)
    da.to_dataset(name="LST").to_netcdf(str(output_dir / f"{scene_name}.nc"))
    da.rio.to_raster(str(output_dir / f"{scene_name}.tif"))


def main(
    download_collection_id: str = "EO.NASA.DAT.LANDSAT.C2_L2",
    download_datetime_range: str = "2025-07-01T00:00:00Z/2025-07-31T23:59:59Z",
    download_out_path: str | Path = "./.delta",
    download_asset_suffixes: list[str] = ["B4.TIF", "B5.TIF", "B10.TIF", "MTL.TXT"],
    download_result_index: int = 0,
    download_limit: int = 1,
    nuts3_code: str = "ITI43",
) -> int:
    """Download Landsat products from HDA and compute LST for each product.

    Parameters
    ----------
    download_collection_id : str
        STAC collection identifier used to search products.
    download_datetime_range : str
        STAC datetime interval in the format start/end (UTC ISO-8601).
    download_out_path : str | Path
        Destination directory where ZIP files and extracted folders are written.
    download_limit : int
        Maximum number of products requested from the STAC search endpoint.
    download_asset_suffixes : list[str]
        Suffixes used to select one asset key per suffix from the selected
        search result (case-insensitive endswith match), e.g. ["B4.TIF", "B7.TIF"].
    download_result_index : int
        Index of the search result from which the asset is downloaded.
    nuts3_code : str
        Eurostat NUTS3 region code used for masking, e.g. ``"ITI43"`` for
        the Province of Rome (Metropolitan City of Rome Capital).

    Returns
    -------
    int
        Zero when processing completes.
    """

    if not download_asset_suffixes:
        raise ValueError("download_asset_suffixes must contain at least one suffix.")

    out_path = Path(download_out_path)
    existing_paths: list[Path] = []
    if out_path.exists():
        all_files = list(out_path.rglob("*"))
        for suffix in download_asset_suffixes:
            match = next(
                (f for f in all_files if f.is_file() and f.name.upper().endswith(suffix.upper())),
                None,
            )
            if match is not None:
                existing_paths.append(match)

    if len(existing_paths) == len(download_asset_suffixes):
        print(f"All {len(existing_paths)} required files already exist, skipping download.")
        downloaded_paths = existing_paths
    else:
        downloaded_paths = search_and_download(
            collection_id=download_collection_id,
            datetime_range=download_datetime_range,
            out_path=Path(download_out_path),
            asset_suffixes=download_asset_suffixes,
            result_index=download_result_index,
            limit=download_limit,
        )

    try:
        calculate_LST_from_file(
            downloaded_paths,
            nuts3_code,
        )
    except Exception as e:
        print(f"Exception calculating LST from downloaded files: \n{e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())