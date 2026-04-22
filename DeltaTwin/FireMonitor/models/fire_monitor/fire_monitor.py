from __future__ import annotations

import difflib
import os
import sys
import logging
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
from shapely.ops import unary_union
import xarray as xr

from hda_helper import search_products, download_single_asset, get_auth_headers

logging.getLogger(__name__).addHandler(logging.NullHandler())
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def _load_country_records() -> list:
    shpfilename = shpreader.natural_earth(resolution="10m", category="cultural", name="admin_0_countries")
    return list(shpreader.Reader(shpfilename).records())


_NAME_ATTRS = ["NAME_LONG", "NAME", "ADMIN", "SOVEREIGNT"]


def find_country_by_name(name: str) -> tuple[str, str]:
    """Find the best-matching country name using cartopy's Natural Earth data."""
    records = _load_country_records()
    name_lower = name.lower()

    # Build a mapping: lowercase → display name (first non-empty NAME_LONG or NAME)
    name_map: dict[str, str] = {}
    for rec in records:
        for attr in _NAME_ATTRS:
            val = rec.attributes.get(attr, "")
            if val:
                name_map[val.lower()] = val

    # Exact match
    if name_lower in name_map:
        display = name_map[name_lower]
        log.info(f"Matched country: {display}")
        return display, display

    # Fuzzy fallback
    close = difflib.get_close_matches(name_lower, name_map.keys(), n=1, cutoff=0.5)
    if close:
        display = name_map[close[0]]
        log.info(f"Matched country (fuzzy): {display}")
        return display, display

    raise ValueError(f"Country '{name}' not found in Natural Earth dataset.")


def get_country_geom(country_name: str):
    """Return merged geometry for a country using cartopy's Natural Earth data."""
    records = _load_country_records()
    name_lower = country_name.lower()
    geoms = [
        rec.geometry
        for rec in records
        if any(str(rec.attributes.get(attr, "")).lower() == name_lower for attr in _NAME_ATTRS)
    ]
    if not geoms:
        raise ValueError(f"Country '{country_name}' not found in Natural Earth dataset.")
    return unary_union(geoms)


def _sensing_time_str(ds: xr.Dataset, fallback_name: str) -> str:
    """Return a human-readable sensing time from dataset attributes or filename."""
    for attr in ("time_coverage_start", "sensing_start_time", "start_time", "date_created"):
        val = ds.attrs.get(attr)
        if val:
            try:
                dt = datetime.fromisoformat(str(val).rstrip("Z"))
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
    country_name: str,
) -> tuple[np.ndarray, np.ndarray, Optional[np.ndarray], Optional[np.ndarray], dict, str]:
    """Open a FCI FIR netCDF and extract fire variables, clipped to a country bounding box.

    Returns
    -------
    lons, lats : np.ndarray
        2-D coordinate arrays.
    fire_prob : np.ndarray or None
        Fire probability values (0–1 float, NaN for off-disk).
    fire_result : np.ndarray or None
        Fire classification values (float, NaN for off-disk).
    flag_info : dict
        ``{"values": [...], "meanings": [...]}`` or empty dict.
    sensing_time : str
        Human-readable sensing time string.
    """
    ds = xr.open_dataset(nc_path, decode_cf=True, mask_and_scale=True)
    sensing_time = _sensing_time_str(ds, nc_path.stem)

    fire_prob_var = ds.get("fire_probability")
    fire_result_var = ds.get("fire_result")

    if fire_prob_var is None and fire_result_var is None:
        available = list(ds.data_vars)
        raise ValueError(
            f"Neither 'fire_probability' nor 'fire_result' found in {nc_path.name}. "
            f"Available variables: {available}"
        )

    lons, lats = _compute_lat_lon_from_projection(ds)
    lons = -lons

    # Extract fire variables; fire_result value 4 is the off-disk fill (no _FillValue attr),
    # so mask it using the NaN positions from fire_probability.
    fire_prob = fire_prob_var.values.squeeze().astype(float) if fire_prob_var is not None else None
    if fire_result_var is not None:
        fire_result_raw = fire_result_var.values.squeeze().astype(float)
        if fire_prob is not None:
            fire_result_raw[np.isnan(fire_prob)] = np.nan
    else:
        fire_result_raw = None

    flag_info: dict = {}
    if fire_result_var is not None:
        fv = fire_result_var.attrs.get("flag_values")
        fm = fire_result_var.attrs.get("flag_meanings")
        if fv is not None and fm is not None:
            try:
                values = list(np.atleast_1d(fv))
                meanings = str(fm).split() if isinstance(fm, str) else list(fm)
                flag_info = {"values": values, "meanings": meanings}
            except Exception:
                pass

    ds.close()

    # Crop to country bounding box (rectangular sub-grid to keep arrays 2-D)
    country_geom = get_country_geom(country_name)
    minx, miny, maxx, maxy = country_geom.bounds
    pad_x = (maxx - minx) * 0.05
    pad_y = (maxy - miny) * 0.05

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
        if fire_prob is not None:
            fire_prob = fire_prob[r0:r1, c0:c1]
        if fire_result_raw is not None:
            fire_result_raw = fire_result_raw[r0:r1, c0:c1]

    return lons, lats, fire_prob, fire_result_raw, flag_info, sensing_time


def _build_fire_result_colormap(flag_info: dict) -> tuple[mcolors.Colormap, mcolors.Normalize, list[str]]:
    """Build a discrete colormap for fire classification values.

    Falls back to a sensible default if no flag information is available.
    """
    if flag_info:
        values = [int(v) for v in flag_info["values"]]
        meanings = flag_info["meanings"]
    else:
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


def plot_fire_monitor(
    lons: np.ndarray,
    lats: np.ndarray,
    fire_prob: Optional[np.ndarray],
    fire_result: Optional[np.ndarray],
    country_geom,
    png_path: str,
    country_name: str,
    flag_info: dict,
    sensing_time: str = "",
) -> None:
    """Plot Fire Probability and Active Fire Classification for a country.

    Parameters
    ----------
    lons, lats : np.ndarray
        2-D coordinate arrays.
    fire_prob : np.ndarray or None
        Fire probability values (fraction or %).
    fire_result : np.ndarray or None
        Fire classification values.
    country_geom : shapely geometry
        Country polygon for overlay and zoom extents.
    png_path : str
        Output PNG file path.
    country_name : str
        Country name used in the plot title.
    flag_info : dict
        Fire classification flag metadata.
    sensing_time : str
        Acquisition datetime shown in the figure title.
    """
    _BRAND_PINK   = "#ef2b89"
    _BRAND_PURPLE = "#7B34DB"
    _BG           = "#F8F8F8"
    _SPINE_COLOR  = "#cccccc"

    minx, miny, maxx, maxy = country_geom.bounds
    pad_x = (maxx - minx) * 0.05
    pad_y = (maxy - miny) * 0.05
    zoom_xlim = (minx - pad_x, maxx + pad_x)
    zoom_ylim = (miny - pad_y, maxy + pad_y)

    title = f"Country: {country_name}"
    if sensing_time:
        title += f", at {sensing_time}"

    n_panels = sum([fire_prob is not None, fire_result is not None])
    if n_panels == 0:
        log.warning("No fire data available to plot; skipping.")
        return

    with plt.rc_context({
        "font.size": 13,
        "axes.facecolor": _BG,
        "figure.facecolor": _SPINE_COLOR,
    }):
        fig, axes = plt.subplots(
            1, n_panels,
            figsize=(10 if n_panels == 2 else 6, 4.5),
            constrained_layout=True,
        )
        if n_panels == 1:
            axes = [axes]
        fig.suptitle(title, fontsize=15)

        ax_idx = 0

        # --- Fire Probability panel ---
        if fire_prob is not None:
            ax = axes[ax_idx]
            ax_idx += 1

            # Normalise to percentage if values are in [0, 1]
            prob_display = fire_prob.copy()
            if np.nanmax(prob_display) <= 1.0:
                prob_display = prob_display * 100.0

            im = ax.pcolormesh(lons, lats, prob_display, cmap="YlOrRd", vmin=0, vmax=100)
            cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

            cbar.outline.set_edgecolor(_SPINE_COLOR)
            gpd.GeoSeries([country_geom], crs="EPSG:4326").plot(
                ax=ax, facecolor="none", edgecolor=_BRAND_PINK, linewidth=1.5, aspect=None,
            )
            ax.set_xlim(*zoom_xlim)
            ax.set_ylim(*zoom_ylim)
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")
            ax.set_title("Fire Probability", color=_BRAND_PURPLE)
            ax.xaxis.set_major_locator(mticker.MaxNLocator(5))
            ax.tick_params(axis="x", rotation=45)
            ax.grid(True, color="grey", linewidth=0.5, alpha=0.6, zorder=0)

        # --- Fire Classification panel ---
        if fire_result is not None:
            ax = axes[ax_idx]
            cmap, norm, meanings = _build_fire_result_colormap(flag_info)

            im2 = ax.pcolormesh(lons, lats, fire_result.astype(float), cmap=cmap, norm=norm)
            cbar2 = fig.colorbar(
                im2, ax=ax, fraction=0.046, pad=0.04,
                ticks=list(range(len(meanings))),
                # label="Fire Classification",
            )

            cbar2.ax.set_yticklabels(meanings, fontsize=9)
            cbar2.outline.set_edgecolor(_SPINE_COLOR)
            gpd.GeoSeries([country_geom], crs="EPSG:4326").plot(
                ax=ax, facecolor="none", edgecolor=_BRAND_PINK, linewidth=1.5, aspect=None,
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
    country_name: str = "Spain",
) -> int:
    """Download an MTG FCI Active Fire product and produce a fire monitoring plot.

    Searches the HDA STAC for the most recent product within the lookback
    window, downloads the zip archive, extracts the netCDF, and generates a
    side-by-side Fire Probability / Active Fire Classification plot for the
    selected country.

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
    country_name : str
        Country name used to select the spatial area, e.g. ``"Spain"`` or
        ``"Italy"``. Resolved via :func:`find_country_by_name`.

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

    if country_name is None:
        raise ValueError("country_name must be provided.")
    canonical_name, display_name = find_country_by_name(country_name)

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
            country_geom=get_country_geom(canonical_name),
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
        lons, lats, fire_prob, fire_result, flag_info, sensing_time = (
            _load_fire_data(nc_path, canonical_name)
        )

        plot_path = Path("fire_monitor_plot.png")
        plot_fire_monitor(
            lons=lons,
            lats=lats,
            fire_prob=fire_prob,
            fire_result=fire_result,
            country_geom=get_country_geom(canonical_name),
            png_path=str(plot_path),
            country_name=display_name,
            flag_info=flag_info,
            sensing_time=sensing_time,
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
        log.error("Usage: python fire_monitor.py [<username> <password> [<city> [<date>]]]")
        sys.exit(1)

    if len(sys.argv) >= 3:
        os.environ["DESPAUTH_USER"] = sys.argv[1]
        os.environ["DESPAUTH_PASSWORD"] = sys.argv[2]

    kwargs: dict = {}
    if len(sys.argv) >= 4:
        kwargs["country_name"] = sys.argv[3]
    if len(sys.argv) >= 5:
        kwargs["download_date"] = sys.argv[4]

    main(**kwargs)
