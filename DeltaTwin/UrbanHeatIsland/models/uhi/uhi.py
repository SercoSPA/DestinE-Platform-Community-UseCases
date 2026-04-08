from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

import matplotlib.pyplot as plt
import xarray as xr
import rioxarray as rio
import numpy as np
import geopandas as gpd

from lst_helper import calculate_LST_from_L2_bands, _NUTS3_GEOJSON_URL
from hda_helper import search_products, download_single_asset


def _get_nuts3_gdf(nuts3_code: str):
    """Return (GeoDataFrame, region_name) for a NUTS3 code, or (None, nuts3_code) on failure."""
    nuts3 = gpd.read_file(_NUTS3_GEOJSON_URL)
    region = nuts3[nuts3["NUTS_ID"] == nuts3_code]
    if region.empty:
        return None, nuts3_code
    return region, region["NUTS_NAME"].iloc[0]


def _parse_scene_datetime(scene_name: str, mtl_path: Optional[Path] = None) -> str:
    """Return a human-readable acquisition datetime from the MTL file or scene name."""
    if mtl_path is not None:
        date_val = time_val = None
        try:
            with open(mtl_path) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("DATE_ACQUIRED") and "=" in stripped:
                        date_val = stripped.split("=", 1)[1].strip().strip('"')
                    elif stripped.startswith("SCENE_CENTER_TIME") and "=" in stripped:
                        time_val = stripped.split("=", 1)[1].strip().strip('"').split(".")[0]
        except OSError:
            pass
        if date_val:
            return f"{date_val} {time_val}" if time_val else date_val
    for part in scene_name.split("_"):
        if len(part) == 8 and part.isdigit():
            return f"{part[:4]}-{part[4:6]}-{part[6:]}"
    return scene_name


def plot_combined(
    band4: xr.DataArray,
    band3: xr.DataArray,
    band2: xr.DataArray,
    lst_da: xr.DataArray,
    png_path: str,
    nuts3_code: Optional[str] = None,
    scene_datetime: str = "",
) -> None:
    """Plot RGB and LST side-by-side and save as a single PNG.

    Parameters
    ----------
    band4, band3, band2:
        Red, Green, Blue band DataArrays (raw DN or surface reflectance;
        values are percentile-stretched to [0, 1] for display).
    lst_da:
        LST DataArray in degrees Celsius (EPSG:4326).
    png_path:
        Destination file path for the PNG output.
    nuts3_code:
        Eurostat NUTS3 region code used to zoom both panels and draw the
        outline polygon on the RGB panel.
    scene_datetime:
        Human-readable acquisition datetime shown in the figure title.
    """

    def _stretch(arr: np.ndarray) -> np.ndarray:
        lo, hi = np.nanpercentile(arr, 2), np.nanpercentile(arr, 98)
        if hi == lo:
            return np.zeros_like(arr)
        return np.clip((arr - lo) / (hi - lo), 0, 1)

    r = _stretch(band4.squeeze().values.astype(float))
    g = _stretch(band3.squeeze().values.astype(float))
    b = _stretch(band2.squeeze().values.astype(float))
    rgb = np.dstack([r, g, b])

    lons_rgb = band4.x.values
    lats_rgb = band4.y.values
    extent_rgb = [
        float(lons_rgb.min()), float(lons_rgb.max()),
        float(lats_rgb.min()), float(lats_rgb.max()),
    ]
    origin_rgb = "upper" if lats_rgb[0] > lats_rgb[-1] else "lower"

    # Fetch NUTS3 geometry for outline and zoom extent
    nuts3_region = None
    region_name = nuts3_code or ""
    zoom_xlim = (extent_rgb[0], extent_rgb[1])
    zoom_ylim = (extent_rgb[2], extent_rgb[3])
    if nuts3_code is not None:
        nuts3_region, region_name = _get_nuts3_gdf(nuts3_code)
        if nuts3_region is not None:
            minx, miny, maxx, maxy = nuts3_region.geometry.union_all().bounds
            pad_x = (maxx - minx) * 0.1
            pad_y = (maxy - miny) * 0.1
            zoom_xlim = (minx - pad_x, maxx + pad_x)
            zoom_ylim = (miny - pad_y, maxy + pad_y)

    lst_data = lst_da.squeeze().values
    lons_lst = lst_da.x.values
    lats_lst = lst_da.y.values

    title = f"LST from Landsat over {region_name}"
    if scene_datetime:
        title += f" at {scene_datetime}"

    fig, axes = plt.subplots(1, 2, figsize=(18, 8), constrained_layout=True)
    fig.suptitle(title, fontsize=14, fontweight="bold")

    # --- RGB panel ---
    ax_rgb = axes[0]
    ax_rgb.imshow(rgb, extent=extent_rgb, origin=origin_rgb, aspect="auto")
    if nuts3_region is not None:
        nuts3_region.plot(
            ax=ax_rgb,
            facecolor=(0.5, 0.5, 0.5, 0.15),
            edgecolor="grey",
            linewidth=1.5,
        )
    ax_rgb.set_xlim(*zoom_xlim)
    ax_rgb.set_ylim(*zoom_ylim)
    ax_rgb.set_xlabel("Longitude")
    ax_rgb.set_ylabel("Latitude")
    ax_rgb.set_title("RGB image")
    ax_rgb.set_aspect("equal", adjustable="box")

    # --- LST panel ---
    ax_lst = axes[1]
    img = ax_lst.pcolormesh(lons_lst, lats_lst, lst_data, cmap="hot_r", vmin=0, vmax=40)
    fig.colorbar(img, ax=ax_lst, fraction=0.046, pad=0.04, label="LST (°C)")
    ax_lst.set_xlim(*zoom_xlim)
    ax_lst.set_ylim(*zoom_ylim)
    ax_lst.set_xlabel("Longitude")
    ax_lst.set_ylabel("Latitude")
    ax_lst.set_title("LST")
    ax_lst.set_aspect("equal", adjustable="box")

    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Combined plot saved to: {png_path}")


def calculate_LST_from_file(
        files: Sequence[str | Path],
        nuts3_code: Optional[str],
    ) -> None:
    file_paths = [Path(item) for item in files]

    def _find_path(suffix: str) -> Optional[Path]:
        for candidate in file_paths:
            candidate_name = candidate.name.upper()
            if candidate_name.endswith(suffix.upper()):
                return candidate
        return None

    band2_path = _find_path("B2.TIF")
    band3_path = _find_path("B3.TIF")
    band4_path = _find_path("B4.TIF")
    band10_path = _find_path("B10.TIF")
    mtl_path = _find_path("MTL.TXT")
    qa_pixel_path = _find_path("QA_PIXEL.TIF")

    missing = []
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

    print(f"Using inputs: {band10_path.name}, {mtl_path.name}")
    output_dir = band4_path.parent

    Band4 = rio.open_rasterio(str(band4_path)).rio.reproject("EPSG:4326")
    Band10 = rio.open_rasterio(str(band10_path)).rio.reproject("EPSG:4326")
    Band3 = rio.open_rasterio(str(band3_path)).rio.reproject("EPSG:4326") if band3_path else None
    Band2 = rio.open_rasterio(str(band2_path)).rio.reproject("EPSG:4326") if band2_path else None
    QA_Pixel = rio.open_rasterio(str(qa_pixel_path)).rio.reproject("EPSG:4326") if qa_pixel_path else None
    if QA_Pixel is None:
        print("QA_PIXEL band not found; cloud masking will be skipped.")

    da = calculate_LST_from_L2_bands(Band10, mtl_path, nuts3_code, QA_Pixel)

    scene_datetime = _parse_scene_datetime(scene_name, mtl_path)
    filename = str(output_dir / f"{scene_name}_LST")
    print(f"saving LST data to file: {filename}")
    # da.to_dataset(name="LST").to_netcdf(f"{filename}.nc"))
    da.rio.to_raster(f"{filename}.tif")
    if Band2 is not None and Band3 is not None:
        plot_combined(
            Band4, Band3, Band2, da,
            f"{filename}_plot.png",
            nuts3_code=nuts3_code,
            scene_datetime=scene_datetime,
        )
    else:
        print("Skipping combined plot: B2 and/or B3 not available.")


def _parse_cloud_cover_land(mtl_path: Path) -> float:
    """Return the CLOUD_COVER_LAND value from a Landsat MTL metadata file."""
    with open(mtl_path) as f:
        for line in f:
            if "CLOUD_COVER_LAND" in line:
                return float(line.split("=")[1].strip())
    raise ValueError(f"CLOUD_COVER_LAND not found in {mtl_path}")


def main(
    download_collection_id: str = "EO.NASA.DAT.LANDSAT.C2_L2",
    # download_collection_id: str = "LANDSAT_C2L2",
    download_datetime_range: str = "2026-03-01T00:00:00Z/2026-04-01T00:00:00Z",
    download_out_path: str | Path = "./.delta",
    download_asset_suffixes: list[str] = [
        "B2.TIF", "B3.TIF", "B4.TIF", "B10.TIF", "MTL.TXT", "QA_PIXEL.TIF"
        ],
    download_limit: int = 10,
    nuts3_code: Optional[str] = "ITI43",
    # nuts3_code: Optional[str] = None,
    cloud_cover_land_threshold: float = 30.0,
) -> int:
    """Download Landsat products from HDA and compute LST for each product.

    Products are iterated in search-result order. For each, only the MTL file
    is downloaded first; if ``CLOUD_COVER_LAND`` exceeds
    ``cloud_cover_land_threshold`` the product is skipped and the MTL file is
    removed. The remaining assets are only downloaded for the first product
    that passes the cloud check.

    Parameters
    ----------
    download_collection_id : str
        STAC collection identifier used to search products.
    download_datetime_range : str
        STAC datetime interval in the format start/end (UTC ISO-8601).
    download_out_path : str | Path
        Destination directory where files are written.
    download_limit : int
        Maximum number of products requested from the STAC search endpoint.
    download_asset_suffixes : list[str]
        Suffixes used to select one asset key per suffix from the accepted
        search result (case-insensitive endswith match), e.g. ["B4.TIF", "B7.TIF"].
        ``MTL.TXT`` is always fetched first for the cloud check; other suffixes
        may or may not include it.
    nuts3_code : str or None
        Eurostat NUTS3 region code used for masking, e.g. ``"ITI43"`` for
        the Province of Rome (Metropolitan City of Rome Capital).
        Pass ``None`` to skip masking and return LST for the full scene.
    cloud_cover_land_threshold : float
        Maximum acceptable ``CLOUD_COVER_LAND`` percentage (0–100).
        Products above this value are skipped.

    Returns
    -------
    int
        Zero when processing completes.
    """

    if not download_asset_suffixes:
        raise ValueError("download_asset_suffixes must contain at least one suffix.")

    # JUST FOR DEV PURPOSES: avoid downloading files over and over again
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
        features = search_products(
            collection_id=download_collection_id,
            datetime_range=download_datetime_range,
            limit=download_limit,
            nuts3_code=nuts3_code,
        )
        if not features:
            raise ValueError("No products found for the given criteria")

        downloaded_paths = None
        for product in features:
            product_id = product.get("id", "unknown")

            # Download MTL first to check cloud cover before fetching large assets
            mtl_path = download_single_asset(product, "MTL.TXT", out_path)
            cloud_cover = _parse_cloud_cover_land(mtl_path)
            if cloud_cover > cloud_cover_land_threshold:
                print(
                    f"Skipping product '{product_id}': "
                    f"CLOUD_COVER_LAND={cloud_cover:.1f}% > {cloud_cover_land_threshold}%"
                )
                mtl_path.unlink(missing_ok=True)
                continue

            print(
                f"Product '{product_id}' accepted: CLOUD_COVER_LAND={cloud_cover:.1f}%"
            )

            # Download the remaining assets (skip MTL.TXT — already downloaded)
            paths: list[Path] = [mtl_path]
            for suffix in download_asset_suffixes:
                if suffix.upper() == "MTL.TXT":
                    continue
                paths.append(download_single_asset(product, suffix, out_path))
            downloaded_paths = paths
            break

        if downloaded_paths is None:
            raise ValueError(
                f"No product found with CLOUD_COVER_LAND <= {cloud_cover_land_threshold}% "
                f"among {len(features)} search result(s)."
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
    main()