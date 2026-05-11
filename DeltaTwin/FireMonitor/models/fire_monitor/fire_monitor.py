from __future__ import annotations

import os
import sys
import logging
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import cartopy.io.shapereader as shpreader
import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.colors as mcolors
import numpy as np
import pyproj
import xarray as xr

from hda_helper import search_products, download_single_asset, get_auth_headers
from nuts_helper import find_nuts2_by_name, get_nuts2_geom

logging.getLogger(__name__).addHandler(logging.NullHandler())
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


_BORDER_RESOLUTION = "10m"


@lru_cache(maxsize=1)
def _load_global_map_layers() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load global political borders and coastlines for map overlays."""
    countries_path = shpreader.natural_earth(
        resolution=_BORDER_RESOLUTION,
        category="cultural",
        name="admin_0_countries",
    )
    coastlines_path = shpreader.natural_earth(
        resolution=_BORDER_RESOLUTION,
        category="physical",
        name="coastline",
    )

    borders = gpd.read_file(countries_path)
    coastlines = gpd.read_file(coastlines_path)
    return borders, coastlines


def _sensing_time_str(ds: xr.Dataset, fallback_name: str) -> str:
    """Return a human-readable sensing time from dataset attributes or filename."""
    for attr in ("time_coverage_start", "sensing_start_time", "start_time", "date_created"):
        val = ds.attrs.get(attr)
        if val:
            try:
                dt = datetime.fromisoformat(str(val).rstrip("Z"))
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    dt = datetime.strptime(str(val), "%Y%m%d%H%M%S")
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return str(val)
    return fallback_name


def _compute_lat_lon_from_projection(ds: xr.Dataset) -> tuple[np.ndarray, np.ndarray]:
    """Convert geostationary x/y scan angles to 2-D lat/lon arrays.

    Reads the CF-compliant ``grid_mapping`` variable to obtain satellite
    orbital parameters, then uses :mod:`pyproj` for the forward/inverse
    projection.

    Returns
    -------
    lons, lats : np.ndarray
        2-D arrays with shape (nrows, ncols).
    """

    # Locate the grid_mapping variable
    gm_attrs: Optional[dict] = None
    for vname in list(ds.data_vars) + list(ds.coords):
        v = ds[vname]
        if v.attrs.get("grid_mapping_name") == "geostationary":
            gm_attrs = dict(v.attrs)
            break

    if gm_attrs is None:
        raise ValueError(
            "No geostationary grid_mapping variable found in the netCDF dataset. "
            "Cannot compute lat/lon coordinates."
        )

    lon_0 = float(gm_attrs.get("longitude_of_projection_origin", 0.0))
    h = float(gm_attrs.get("perspective_point_height", 35785831.0))
    a = float(gm_attrs.get("semi_major_axis", 6378137.0))
    b = float(gm_attrs.get("semi_minor_axis", 6356752.3142))
    sweep = str(gm_attrs.get("sweep_angle_axis", "y"))

    x_coord = ds.coords.get("x")
    y_coord = ds.coords.get("y")
    if x_coord is None or y_coord is None:
        raise ValueError("No 'x'/'y' dimension coordinates found in dataset.")

    x_vals = x_coord.values.astype(float)
    y_vals = y_coord.values.astype(float)

    # Determine scale: radians → metres by multiplying with satellite height.
    # Some files store in microradians; detect from the 'units' attribute.
    x_units = str(x_coord.attrs.get("units", "")).lower()
    if "micro" in x_units:
        scale = h * 1e-6
    else:
        # Assume radians (EUMETSAT convention)
        scale = h

    X = x_vals * scale
    Y = y_vals * scale
    XX, YY = np.meshgrid(X, Y)

    proj = pyproj.Proj(proj="geos", lon_0=lon_0, h=h, sweep=sweep, a=a, b=b)
    lons, lats = proj(XX, YY, inverse=True)

    # Mask off-disk pixels (pyproj returns ±1e30 for points outside the Earth disk)
    off_disk = (np.abs(lons) > 360) | (np.abs(lats) > 90) | (~np.isfinite(lons)) | (~np.isfinite(lats))
    lons = np.where(off_disk, np.nan, lons)
    lats = np.where(off_disk, np.nan, lats)

    return lons, lats


def _load_fire_data(
    nc_path: Path,
    nuts2_geom,
    pad_ratio: float = 0.5,
) -> tuple[np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray], dict, str]:
    """Open a FCI FIR netCDF and extract fire variables, clipped to a NUTS2 bounding box.

    The input arrays are cropped to the NUTS2 bounds expanded by ``pad_ratio``
    on each axis, where ``0`` means no expansion and ``1`` means one extra
    region width/height added on each side.

    Parameters
    ----------
    nc_path : Path
        NetCDF file path.
    nuts2_geom : shapely geometry
        NUTS2 geometry used to compute the crop bounding box.
    pad_ratio : float
        Fractional padding applied to the NUTS2 bbox before cropping.

    Returns
    -------
    lons, lats : np.ndarray
        2-D coordinate arrays.
    fire_result : np.ndarray or None
        Fire classification values (float, NaN for off-disk).
    sensing_time : str
        Human-readable sensing time string.
    """
    ds = xr.open_dataset(nc_path, decode_cf=True, mask_and_scale=True)
    sensing_time = _sensing_time_str(ds, nc_path.stem)

    fire_result_var = ds.get("fire_result")

    if fire_result_var is None:
        available = list(ds.data_vars)
        raise ValueError(
            f"'fire_result' NOT found in {nc_path.name}. "
            f"Available variables: {available}"
        )

    lons, lats = _compute_lat_lon_from_projection(ds)
    lons = -lons

    # Extract fire classification and mask invalid/fill pixels without relying on fire_probability.
    fire_result_raw = fire_result_var.values.squeeze().astype(float)

    fire_result_raw[(fire_result_raw < 0) | (fire_result_raw > 3)] = np.nan

    ds.close()

    # Crop to NUTS2 bounding box (rectangular sub-grid to keep arrays 2-D)
    minx, miny, maxx, maxy = nuts2_geom.bounds
    pad_x = (maxx - minx) * pad_ratio
    pad_y = (maxy - miny) * pad_ratio

    with np.errstate(invalid="ignore"):
        in_bbox = (
            (lons >= minx - pad_x) & (lons <= maxx + pad_x)
            & (lats >= miny - pad_y) & (lats <= maxy + pad_y)
        )

    row_indices = np.where(in_bbox.any(axis=1))[0]
    col_indices = np.where(in_bbox.any(axis=0))[0]
    if row_indices.size > 0 and col_indices.size > 0:
        r0, r1 = int(row_indices[0]), int(row_indices[-1]) + 1
        c0, c1 = int(col_indices[0]), int(col_indices[-1]) + 1
        lons = lons[r0:r1, c0:c1]
        lats = lats[r0:r1, c0:c1]
        if fire_result_raw is not None:
            fire_result_raw = fire_result_raw[r0:r1, c0:c1]

    return lons, lats, fire_result_raw, sensing_time


def _build_fire_result_colormap() -> tuple[mcolors.Colormap, mcolors.Normalize, list[str]]:
    """Build a discrete colormap for fire classification values.
    """
    # EUMETSAT FCI Active Fire L2 classification:
    # 0 = No fire / not processed, 1 = Possible fire, 2 = Probable fire, 3 = Active fire
    # (value 4 is the off-disk fill and is masked to NaN before this point)
    values = [0, 1, 2, 3]
    meanings = ["No fire", "Possible fire", "Probable fire", "Active fire"]

    n = len(values)
    # Colour ramp: grey → yellow → orange → red, roughly
    default_colors = ["#aaaaaa", "#ffdd57", "#ff8c00", "#cc0000"]
    colors = default_colors[:n] if n <= len(default_colors) else plt.cm.YlOrRd(np.linspace(0, 1, n))

    cmap = mcolors.ListedColormap(colors[:n])
    bounds = [v - 0.5 for v in values] + [values[-1] + 0.5]
    norm = mcolors.BoundaryNorm(bounds, cmap.N)
    return cmap, norm, meanings


def _zoom_geometry(country_geom):
    """Return the largest polygonal component for tighter regional zoom."""
    if country_geom.geom_type == "Polygon":
        return country_geom

    if hasattr(country_geom, "geoms"):
        polygons = []
        for geom in country_geom.geoms:
            if geom.geom_type == "Polygon":
                polygons.append(geom)
            elif geom.geom_type == "MultiPolygon":
                polygons.extend(list(geom.geoms))

        if polygons:
            return max(polygons, key=lambda p: p.area)

    return country_geom


def plot_fire_monitor(
    lons: np.ndarray,
    lats: np.ndarray,
    fire_result: Optional[np.ndarray],
    nuts2_geom,
    png_path: str,
    nuts2_region_name: str,
    sensing_time: str = "",
    pad_ratio: float = 0.5,
) -> None:
    """Plot Fire Probability and Active Fire Classification for a NUTS2 region.

    Parameters
    ----------
    lons, lats : np.ndarray
        2-D coordinate arrays.
    fire_result : np.ndarray or None
        Fire classification values.
    nuts2_geom : shapely geometry
        NUTS2 polygon for overlay and zoom extents.
    png_path : str
        Output PNG file path.
    nuts2_region_name : str
        NUTS2 region name used in the plot title.
    sensing_time : str
        Acquisition datetime shown in the figure title.
    pad_ratio : float
        Fractional padding used to expand the NUTS2 plotting extent.
        ``0`` uses the exact region bounds; larger values zoom out.
    """
    _BRAND_PINK   = "#ef2b89"
    _BRAND_PURPLE = "#7B34DB"
    _BG           = "#F8F8F8"
    _SPINE_COLOR  = "#cccccc"

    zoom_geom = _zoom_geometry(nuts2_geom)
    minx, miny, maxx, maxy = zoom_geom.bounds
    # Keep a wider context around NUTS2 regions.
    pad_x = (maxx - minx) * pad_ratio
    pad_y = (maxy - miny) * pad_ratio
    zoom_xlim = (minx - pad_x, maxx + pad_x)
    zoom_ylim = (miny - pad_y, maxy + pad_y)

    title = f"NUTS2 region: {nuts2_region_name}"
    if sensing_time:
        title += f", at {sensing_time}"

    with plt.rc_context({
        "font.size": 13,
        "axes.facecolor": _BG,
        "figure.facecolor": _SPINE_COLOR,
    }):
        fig, axes = plt.subplots(
            1, 1,
            figsize=(8, 6),
            constrained_layout=True,
        )
        axes = [axes]
        fig.suptitle(title, fontsize=15)

        ax_idx = 0

        # --- Fire Classification panel ---
        if fire_result is not None:
            ax = axes[ax_idx]
            cmap, norm, meanings = _build_fire_result_colormap()

            im2 = ax.pcolormesh(lons, lats, fire_result.astype(float), cmap=cmap, norm=norm)
            cbar2 = fig.colorbar(
                im2, ax=ax, fraction=0.046, pad=0.04,
                ticks=list(range(len(meanings))),
                # label="Fire Classification",
            )

            cbar2.ax.set_yticklabels(meanings, fontsize=9)
            cbar2.outline.set_edgecolor(_SPINE_COLOR)
            borders, coastlines = _load_global_map_layers()
            borders.boundary.plot(
                ax=ax,
                color="black",
                linewidth=0.4,
                alpha=0.8,
            )
            coastlines.plot(
                ax=ax,
                color="black",
                linewidth=0.6,
                alpha=0.9,
            )
            gpd.GeoSeries([nuts2_geom], crs="EPSG:4326").plot(
                ax=ax,
                facecolor="none",
                edgecolor=_BRAND_PINK,
                linewidth=1.5,
                aspect=None,
            )
            ax.set_xlim(*zoom_xlim)
            ax.set_ylim(*zoom_ylim)
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")
            ax.set_title("Active Fire Classification", color=_BRAND_PURPLE)
            ax.xaxis.set_major_locator(mticker.MaxNLocator(5))
            ax.tick_params(axis="x", rotation=45)
            ax.grid(True, color="grey", linewidth=0.5, alpha=0.6, zorder=0)

        fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info(f"Fire monitor plot saved to: {png_path}")


def main(
    download_collection_id: str = "EO.EUM.DAT.MTG.FCI-ACTIVE_FIRE-L2-V1",
    download_date: str | None = "2025-08-16",
    download_lookback_days: int = 1,
    download_out_path: str | Path = "./.delta",
    download_limit: int = 20,
    nuts2_region_name: str = "Galicia",
    pad_ratio: float = 1,
) -> int:
    """Download an MTG FCI Active Fire product and produce a fire monitoring plot.

    Searches the HDA STAC for the most recent product within the lookback
    window, downloads the zip archive, extracts the netCDF, and generates a
    side-by-side Fire Probability / Active Fire Classification plot for the
    selected NUTS2 region.

    Parameters
    ----------
    download_collection_id : str
        STAC collection identifier used to search products.
    download_date : str or None
        End date for the search window in ``YYYY-MM-DD`` format (UTC).
        Defaults to today when ``None``.
    download_lookback_days : int
        Number of days before *download_date* to search for products.
    download_out_path : str | Path
        Destination directory where files are written.
    download_limit : int
        Maximum number of products requested from the STAC search endpoint.
    nuts2_region_name : str
        NUTS2 region name used to select the spatial area, e.g. ``"Galicia"``
        or ``"Ile-de-France"``. Resolved via :func:`find_nuts2_by_name`.
    pad_ratio : float
        Fractional padding shared by data cropping and map extent.
        ``0`` keeps tight NUTS2 bounds; larger values provide more context.

    Returns
    -------
    int
        Zero on success, non-zero on failure.
    """
    end_dt = (
        datetime.strptime(download_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if download_date is not None
        else datetime.now(tz=timezone.utc)
    )
    start_dt = end_dt - timedelta(days=download_lookback_days)
    download_datetime_range = (
        f"{start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    log.info(f"Searching for products between {start_dt.date()} and {end_dt.date()}")

    if nuts2_region_name is None:
        raise ValueError("nuts2_region_name must be provided.")
    nuts2_code, display_name = find_nuts2_by_name(nuts2_region_name)
    region_geom = get_nuts2_geom(nuts2_code)

    out_path = Path(download_out_path)

    # Re-use existing download if all required files are already present
    existing_ncs = sorted(out_path.glob("*.nc")) if out_path.exists() else []

    if existing_ncs:
        nc_path = existing_ncs[0]
        log.info(f"Re-using existing netCDF: {nc_path}")
    else:
        auth_headers = get_auth_headers()
        features = search_products(
            collection_id=download_collection_id,
            auth_headers=auth_headers,
            datetime_range=download_datetime_range,
            limit=download_limit,
            country_geom=region_geom,
        )
        if not features:
            raise ValueError("No products found for the given criteria.")

        # Download the most recent product (first result)
        # [log.info(f"product {i} id: {features[i].get('id')}") for i in range(len(features))]
        product = features[0]
        log.info(f"Downloading product: {product.get('id')}")

        nc_path = download_single_asset(product, ".nc", out_path, auth_headers)

    log.info(f"Processing netCDF: {nc_path}")

    try:
        lons, lats, fire_result, sensing_time = (
            _load_fire_data(nc_path, region_geom, pad_ratio)
        )

        plot_path = Path("fire_monitor_plot.png")
        plot_fire_monitor(
            lons=lons,
            lats=lats,
            fire_result=fire_result,
            nuts2_geom=region_geom,
            png_path=str(plot_path),
            nuts2_region_name=display_name,
            sensing_time=sensing_time,
            pad_ratio=pad_ratio,
        )
        log.info(f"Exported output plot: {plot_path}")
        return 0
    except Exception:
        log.exception("Exception processing FCI active fire data")
        return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s:%(funcName)s] %(message)s",
    )

    if len(sys.argv) not in (1, 3, 4, 5):
        log.error("Usage: python fire_monitor.py [<username> <password> [<nuts2_region> [<date>]]]")
        sys.exit(1)

    if len(sys.argv) >= 3:
        os.environ["DESPAUTH_USER"] = sys.argv[1]
        os.environ["DESPAUTH_PASSWORD"] = sys.argv[2]

    kwargs: dict = {}
    if len(sys.argv) >= 4:
        kwargs["nuts2_region_name"] = sys.argv[3]
    if len(sys.argv) >= 5:
        kwargs["download_date"] = sys.argv[4]

    main(**kwargs)
