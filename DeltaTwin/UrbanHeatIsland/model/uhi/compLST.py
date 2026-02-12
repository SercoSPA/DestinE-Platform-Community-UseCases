import xarray as xr
import rioxarray as rio
import pandas as pd
import numpy as np
import geopandas as gpd
from regionmask import mask_geopandas
from glob import glob

def extract_parameter_value(file_path, parameter_name):
    """
    Estrae il valore di un parametro specifico da un file di testo formattato.

    :param file_path: Percorso al file di testo.
    :param parameter_name: Nome del parametro da cercare.
    :return: Valore del parametro come float, oppure None se non trovato.
    """
    try:
        # Legge il file come testo
        with open(file_path, "r") as file:
            lines = file.readlines()
        
        # Crea un DataFrame da ogni riga come singolo elemento
        df = pd.DataFrame(lines, columns=["line"])

        # Trova la riga contenente il parametro
        parameter_line = df[df["line"].str.contains(parameter_name)].iloc[0]["line"]
        
        # Estrae il valore separandolo dal nome del parametro
        _, value = parameter_line.split("=")
        return float(value.strip())
    
    except (IndexError, ValueError):
        # Ritorna None se il parametro non è trovato o non è numerico
        return None


def ndvi_calculation(Band5, Band4):
    Band5 = xr.where(Band5 == 0 , np.nan, Band5)
    Band4 = xr.where(Band4 == 0 , np.nan, Band4)
    return (Band5 - Band4) / (Band5 + Band4)

def proportion_vegetation(NDVI):
    NDVI_max = np.nanmax(NDVI)
    NDVI_min = np.nanmin(NDVI)
    return ((NDVI-NDVI_min)/(NDVI_max - NDVI_min))**2

def calculate_land_emissivity(NDVI, Pv):
    """
    Calculate land surface emissivity based on NDVI values and vegetation proportion.
    
    Parameters:
    NDVI: float - Normalized Difference Vegetation Index
    Pv: float - Vegetation proportion
    NDVIs: float - NDVI value for soil
    NDVIv: float - NDVI value for vegetation
    
    Returns:
    float: Land surface emissivity value
    """
    NDVI_max = np.nanmax(NDVI)
    NDVI_min = np.nanmin(NDVI)
    # Constants from the paper
    C = 0.005  # surface roughness constant
    e_soil = 0.996  # soil emissivity
    e_veg = 0.973  # vegetation emissivity
    e_water = 0.991  # water emissivity
    
    # Initialize output array with same coordinates and dimensions as NDVI
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

def calcBT(Band10, filepath):
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

def calcLST(BT, emissivity):
    lampda = 10.895 # [micron]
    rho = 14388 # [micron K]
    par = (lampda * BT)/rho

    Ts = BT / (1 + par * np.log(emissivity))
    return Ts


def mask(shapepath, da, epsg = 4326, zona = 'Roma', lon_name = 'lon', lat_name = 'lat'):
    shape = gpd.read_file(shapepath).to_crs(epsg = epsg)
    city = shape[shape.COMUNE == zona]
    mask_array = mask_geopandas(city, da, lon_name = lon_name, lat_name = lat_name) * 0 + 1

    return da * mask_array

def raster(da, tiffname):
    return da.rio.to_raster(tiffname)

def creaGeoJson(ds_mask, geojson_path, optional_params=None):
    import json
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import Point, Polygon, box
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize
    try:
        # Create meshgrid from the coordinate arrays
        x = ds_mask.x.values
        y = ds_mask.y.values
        xx, yy = np.meshgrid(x, y)
        
        # Extract the data, assuming it's a single band
        # If ds_mask is a DataArray, use .values directly
        # If it's a Dataset, specify the variable
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
            
        print(f"Numero di celle non-NaN incluse nel GeoJSON: {len(gdf)}")
        print("GeoJSON creato con successo!")
        
    except Exception as e:
        print(f"Si è verificato un errore: {str(e)}")

def creaGeoJsonPerZone(ds_mask, shapefile_path, geojson_path, optional_params=None):
    """
    Crea un GeoJSON contenente i poligoni dello shapefile, dove ogni poligono
    ha associato il valore medio delle celle raster che lo intersecano.
    
    Parameters:
    -----------
    ds_mask : xarray.DataArray o xarray.Dataset
        Dataset raster contenente i valori da mediare
    shapefile_path : str
        Percorso al file shapefile con le zone
    geojson_path : str
        Percorso dove salvare il file GeoJSON risultante
    optional_params : dict, optional
        Parametri opzionali da aggiungere al GeoJSON
    """
    import json
    import geopandas as gpd
    import numpy as np
    import xarray as xr
    from shapely.geometry import box
    import rasterio
    from rasterio.features import rasterize
    import warnings
    
    try:
        # Carica lo shapefile
        zone_gdf = gpd.read_file(shapefile_path)
        print(f"Shapefile caricato: {len(zone_gdf)} zone trovate")
        
        # Assicurati che lo shapefile abbia lo stesso CRS del raster
        # Ottieni il CRS dal DataArray (assumendo che sia 4326 se non specificato)
        raster_crs = getattr(ds_mask, 'crs', 'EPSG:4326')
        if hasattr(raster_crs, 'to_epsg'):
            raster_epsg = raster_crs.to_epsg()
            raster_crs = f"EPSG:{raster_epsg}" if raster_epsg else raster_crs
        
        # Riproietta lo shapefile se necessario
        if zone_gdf.crs is None:
            warnings.warn("Lo shapefile non ha un CRS definito. Assumendo lo stesso CRS del raster.")
            zone_gdf.set_crs(raster_crs, inplace=True)
        elif zone_gdf.crs != raster_crs:
            print(f"Riproiezione dello shapefile da {zone_gdf.crs} a {raster_crs}")
            zone_gdf = zone_gdf.to_crs(raster_crs)
        
        # Estrai i dati dal DataArray
        x = ds_mask.x.values
        y = ds_mask.y.values
        
        # Ottieni i dati raster
        if hasattr(ds_mask, 'band'):
            raster_data = ds_mask.sel(band=1).values
        else:
            raster_data = ds_mask.values
        
        # Risultati per ogni zona
        risultati = []
        
        # Itera su ogni zona dello shapefile
        for idx, zona in zone_gdf.iterrows():
            geom = zona.geometry
            
            # Crea una maschera per questa zona
            # Prima ottieni i limiti della zona
            minx, miny, maxx, maxy = geom.bounds
            
            # Trova gli indici dei punti che sono all'interno di questi limiti
            x_indices = np.where((x >= minx) & (x <= maxx))[0]
            y_indices = np.where((y >= miny) & (y <= maxy))[0]
            
            if len(x_indices) == 0 or len(y_indices) == 0:
                print(f"Zona {idx}: Nessuna cella del raster interseca questa zona")
                continue
            
            # Crea una maschera per questa zona
            mask = np.zeros_like(raster_data, dtype=bool)
            
            # Per ogni cella, controlla se il centro è all'interno del poligono
            for i in y_indices:
                for j in x_indices:
                    if i < raster_data.shape[0] and j < raster_data.shape[1]:
                        punto_centrale = (x[j], y[i])
                        if geom.contains(rasterio.transform.xy_to_point(*punto_centrale)):
                            mask[i, j] = True
            
            # Applica la maschera per ottenere solo i valori all'interno della zona
            valori_zona = raster_data[mask]
            
            # Calcola la media dei valori nella zona (ignorando i NaN)
            if len(valori_zona) > 0 and not np.all(np.isnan(valori_zona)):
                media = float(np.nanmean(valori_zona))
                num_celle = np.sum(~np.isnan(valori_zona))
                print(f"Zona {idx}: Media={media:.4f} (basata su {num_celle} celle valide)")
            else:
                media = np.nan
                print(f"Zona {idx}: Nessun dato valido trovato")
            
            # Aggiungi il risultato
            risultati.append({
                "geometry": geom,
                "properties": {
                    "zona_id": idx,
                    "valore_medio": media if not np.isnan(media) else None,
                    "num_celle": int(np.sum(~np.isnan(valori_zona))) if len(valori_zona) > 0 else 0
                }
            })
        
        # Crea un GeoDataFrame con i risultati
        risultati_gdf = gpd.GeoDataFrame(
            [r["properties"] for r in risultati], 
            geometry=[r["geometry"] for r in risultati],
            crs=zone_gdf.crs
        )
        
        # Converti in GeoJSON
        geojson_dict = json.loads(risultati_gdf.to_json())
        
        # Aggiungi parametri opzionali se forniti
        if optional_params:
            geojson_dict['properties'] = optional_params
        
        # Salva il GeoJSON
        with open(geojson_path, 'w') as f:
            json.dump(geojson_dict, f)
        
        print(f"GeoJSON creato con successo con {len(risultati_gdf)} zone!")
        return risultati_gdf
        
    except Exception as e:
        print(f"Si è verificato un errore: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

files =['C:/Users/giuseppe.giugliano/Downloads/LC08_L1TP_191031_20241130_20241203_02_T1']
for filename in files:
    nome = filename.split('/')[-1]
    print(nome)
    print(f'{filename}/{nome}_B5.TIF')
    try:
        Band5 = rio.open_rasterio(f'{filename}/{nome}_B5.TIF').rio.reproject("EPSG:4326")
        Band4 = rio.open_rasterio(f'{filename}/{nome}_B4.TIF').rio.reproject("EPSG:4326")
        Band3 = rio.open_rasterio(f'{filename}/{nome}_B3.TIF').rio.reproject("EPSG:4326")
        Band10 = rio.open_rasterio(f'{filename}/{nome}_B10.TIF').rio.reproject("EPSG:4326")
    except:
        print('dati non presenti')
        continue

    file_path = (f'{filename}/{nome}_MTL.txt')
    BT = calcBT(Band10, file_path)
    ndvi = ndvi_calculation(Band5, Band4)
    Pv = proportion_vegetation(ndvi.squeeze())

    emissivity = calculate_land_emissivity(ndvi.squeeze(), Pv)
    LST = calcLST(BT, emissivity)
    shapepath = 'C:/Users/giuseppe.giugliano/CMCC Dropbox/Giuseppe Giugliano/shape/italia/Limiti01012022/Com01012022/Com01012022_WGS84.shp'
    # shapepath = "C:/Users/giuseppe.giugliano/Downloads/Municipi_Roma/Municipi_Roma_15_wgs84_1.shp"
    da = mask(shapepath, LST, epsg = 4326, zona = 'Roma', lon_name = 'x', lat_name = 'y')
    da = np.round(da, 1)
    print(da)
    da.to_dataset(name = 'LST').to_netcdf(f'{filename}/{nome}.nc')
    # creaGeoJson(da, 'C:/Users/giuseppe.giugliano/CMCC Dropbox/Giuseppe Giugliano/ROCCIA_privata/9_roma_20250117.geojson', optional_params=None)
    da.rio.to_raster(f'{filename}/{nome}.tif')