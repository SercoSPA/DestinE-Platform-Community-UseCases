from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

import matplotlib.pyplot as plt
import xarray as xr
import rioxarray as rio
import numpy as np

from eodag import EODataAccessGateway

from lst_helper import calculate_LST_from_bands, get_nuts3_geom


def search_and_download(
    collection_id: str,
    datetime_range: str,
    out_path: Path,
    asset_suffixes: list[str],
    result_index: int = 0,
    limit: int = 20,
    nuts3_code: Optional[str] = None,
) -> list[Path]:
    """Search and download a Landsat product via EODAG/DEDL.

    Credentials are read from the ``DESP_USERNAME`` and ``DESP_PASSWORD``
    environment variables (same as the DestinE Platform notebook pattern).

    Parameters
    ----------
    collection_id:
        STAC ``productType`` identifier, e.g. ``"EO.NASA.DAT.LANDSAT.C2_L2"``.
    datetime_range:
        ISO-8601 interval ``"start/end"``, e.g.
        ``"2025-07-01T00:00:00Z/2025-07-31T23:59:59Z"``.
    out_path:
        Destination directory for the downloaded product.
    asset_suffixes:
        File-name suffixes to locate in the downloaded product (case-insensitive),
        e.g. ``["B4.TIF", "B5.TIF", "B10.TIF", "MTL.TXT"]``.
    result_index:
        Index of the search result to download.
    limit:
        Maximum number of results to request from the STAC search.
    nuts3_code:
        Optional Eurostat NUTS3 region code (e.g. ``"ITI43"`` for the Province
        of Rome).  When provided, the region's geometry is used as a spatial
        filter so that only products intersecting the region are returned.

    Returns
    -------
    list[Path]
        One :class:`~pathlib.Path` per entry in *asset_suffixes* (only those found).
    """
    username = os.environ.get("DESPAUTH_USER", "")
    password = os.environ.get("DESPAUTH_PASSWORD", "")
    if username:
        os.environ["EODAG__DEDL__AUTH__CREDENTIALS__USERNAME"] = username
    if password:
        os.environ["EODAG__DEDL__AUTH__CREDENTIALS__PASSWORD"] = password
    os.environ["EODAG__DEDL__PRIORITY"] = "10"
    os.environ["EODAG__DEDL__SEARCH__TIMEOUT"] = "60"

    dag = EODataAccessGateway()

    start, end = datetime_range.split("/", 1)

    search_kwargs: dict = {}
    if nuts3_code is not None:
        search_kwargs["geom"] = get_nuts3_geom(nuts3_code)

    results = dag.search(
        provider="dedl",
        collection=collection_id,
        start=start,
        end=end,
        limit=limit,
        **search_kwargs,
    )
    if not results:
        raise ValueError("No products found for the given criteria.")
    if result_index >= len(results):
        raise IndexError(
            f"result_index={result_index} out of range for {len(results)} result(s)."
        )

    product = results[result_index]
    print(f"Selected product: {product.properties.get('id', product)}")

    downloaded = Path(dag.download(product, output_dir=str(out_path)))
    all_files = list(downloaded.rglob("*")) if downloaded.is_dir() else [downloaded]

    found: list[Path] = []
    for suffix in asset_suffixes:
        match = next(
            (f for f in all_files if f.is_file() and f.name.upper().endswith(suffix.upper())),
            None,
        )
        if match is not None:
            found.append(match)
        else:
            print(f"Warning: no downloaded file found with suffix '{suffix}' in {downloaded}")
    return found


def plot_rgb(
    band4: xr.DataArray,
    band3: xr.DataArray,
    band2: xr.DataArray,
    png_path: str,
    title: str = "True Colour (B4 / B3 / B2)",
) -> None:
    """Plot a true-colour RGB composite from Landsat bands and save as PNG.

    Parameters
    ----------
    band4, band3, band2:
        Red, Green, Blue band DataArrays (raw DN or surface reflectance;
        values are percentile-stretched to [0, 1] for display).
    png_path:
        Destination file path for the PNG output.
    title:
        Title shown at the top of the figure.
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

    lons = band4.x.values
    lats = band4.y.values
    extent = [float(lons.min()), float(lons.max()), float(lats.min()), float(lats.max())]
    origin = "upper" if lats[0] > lats[-1] else "lower"

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(rgb, extent=extent, origin=origin, aspect="equal")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(title, fontsize=13)
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"RGB plot saved to: {png_path}")


def plot_lst(
    da: xr.DataArray,
    png_path: str,
    title: str = "Land Surface Temperature",
) -> None:
    """Plot a LST DataArray and save it as a PNG file.

    Parameters
    ----------
    da:
        LST DataArray in degrees Celsius (2-D, EPSG:4326).
    png_path:
        Destination file path for the PNG output.
    title:
        Title shown at the top of the figure.
    """
    data = da.squeeze().values
    lons = da.x.values
    lats = da.y.values

    vmin = float(np.nanpercentile(data, 2))
    vmax = float(np.nanpercentile(data, 98))

    fig, ax = plt.subplots(figsize=(10, 8))
    img = ax.pcolormesh(lons, lats, data, cmap="RdYlBu_r", vmin=vmin, vmax=vmax)
    cbar = fig.colorbar(img, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("LST (°C)", fontsize=11)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(title, fontsize=13)
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"LST plot saved to: {png_path}")


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
    Band3 = rio.open_rasterio(str(band3_path)).rio.reproject("EPSG:4326") if band3_path else None
    Band2 = rio.open_rasterio(str(band2_path)).rio.reproject("EPSG:4326") if band2_path else None

    da = calculate_LST_from_bands(Band4, Band5, Band10, mtl_path, nuts3_code)

    filename = str(output_dir / f"{scene_name}_LST")
    print(f"saving LST data to file: {filename}")
    # da.to_dataset(name="LST").to_netcdf(f"{filename}.nc"))
    da.rio.to_raster(f"{filename}.tif")
    if Band2 is not None and Band3 is not None:
        plot_rgb(Band4, Band3, Band2, f"{filename}_RGB.png", title=f"{scene_name} – True Colour")
    else:
        print("Skipping RGB plot: B2 and/or B3 not available.")
    plot_lst(da, f"{filename}_LST.png", title=f"{scene_name} – LST")


def main(
    # download_collection_id: str = "EO.NASA.DAT.LANDSAT.C2_L2",
    download_collection_id: str = "LANDSAT_C2L2",
    download_datetime_range: str = "2026-03-01/2026-04-01",
    download_out_path: str | Path = "./.delta",
    download_asset_suffixes: list[str] = ["B2.TIF", "B3.TIF", "B4.TIF", "B5.TIF", "B10.TIF", "MTL.TXT"],
    download_result_index: int = 0,
    download_limit: int = 10,
    nuts3_code: Optional[str] = "ITI43",
    # nuts3_code: Optional[str] = None,
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
    nuts3_code : str or None
        Eurostat NUTS3 region code used for masking, e.g. ``"ITI43"`` for
        the Province of Rome (Metropolitan City of Rome Capital).
        Pass ``None`` to skip masking and return LST for the full scene.

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
        downloaded_paths = search_and_download(
            collection_id=download_collection_id,
            datetime_range=download_datetime_range,
            out_path=Path(download_out_path),
            asset_suffixes=download_asset_suffixes,
            result_index=download_result_index,
            limit=download_limit,
            nuts3_code=nuts3_code,
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