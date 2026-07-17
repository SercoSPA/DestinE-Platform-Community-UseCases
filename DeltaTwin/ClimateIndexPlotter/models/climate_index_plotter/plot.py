"""Four-panel plot for an ETCCDI index: historical, future, variation, variation-%.

Coastlines and country borders are overlaid; no gridlines are drawn.
"""

from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")  # headless / deterministic

import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

log = logging.getLogger(__name__)

_SEQUENTIAL_CMAP = "viridis"
_DIVERGING_CMAP = "RdBu_r"
_FEATURE_SCALE = "10m"


def _finite(*arrays: np.ndarray) -> np.ndarray:
    values = np.concatenate([a[np.isfinite(a)].ravel() for a in arrays])
    return values


def _shared_limits(*das: xr.DataArray) -> tuple[float, float]:
    values = _finite(*[da.values for da in das])
    if values.size == 0:
        return 0.0, 1.0
    lo, hi = float(values.min()), float(values.max())
    return (lo, hi) if lo < hi else (lo, lo + 1.0)


def _symmetric_limit(da: xr.DataArray) -> float:
    values = _finite(da.values)
    if values.size == 0:
        return 1.0
    m = float(np.max(np.abs(values)))
    return m if m > 0 else 1.0


def _map_panel(ax, da, title, cmap, vmin, vmax, units) -> None:
    mesh = ax.pcolormesh(
        da["lon"].values,
        da["lat"].values,
        da.values,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        shading="auto",
        transform=ccrs.PlateCarree(),
    )
    ax.add_feature(cfeature.COASTLINE.with_scale(_FEATURE_SCALE), linewidth=0.6)
    ax.add_feature(cfeature.BORDERS.with_scale(_FEATURE_SCALE), linewidth=0.4)

    lons = da["lon"].values
    lats = da["lat"].values
    if lons.size and lats.size:
        ax.set_extent(
            [float(lons.min()), float(lons.max()), float(lats.min()), float(lats.max())],
            crs=ccrs.PlateCarree(),
        )
    ax.set_title(title)
    cbar = ax.figure.colorbar(mesh, ax=ax, fraction=0.046, pad=0.04)
    if units:
        cbar.set_label(units)


def build_figure(
    index_id: str,
    hist_clim: xr.DataArray,
    fut_clim: xr.DataArray,
    variation: xr.DataArray,
    variation_pct: xr.DataArray,
    subtitle: str = "",
) -> "matplotlib.figure.Figure":
    """Build the 2x2 figure: historical, future (shared scale), variation, variation-%."""
    fig, axes = plt.subplots(
        2,
        2,
        figsize=(13, 10),
        subplot_kw={"projection": ccrs.PlateCarree()},
        constrained_layout=True,
    )
    units = hist_clim.attrs.get("units", "")

    top_vmin, top_vmax = _shared_limits(hist_clim, fut_clim)
    _map_panel(axes[0, 0], hist_clim, f"{index_id} historical", _SEQUENTIAL_CMAP, top_vmin, top_vmax, units)
    _map_panel(axes[0, 1], fut_clim, f"{index_id} future", _SEQUENTIAL_CMAP, top_vmin, top_vmax, units)

    vlim = _symmetric_limit(variation)
    _map_panel(axes[1, 0], variation, f"{index_id} variation", _DIVERGING_CMAP, -vlim, vlim, units)
    vlim_pct = _symmetric_limit(variation_pct)
    _map_panel(axes[1, 1], variation_pct, f"{index_id} variation (%)", _DIVERGING_CMAP, -vlim_pct, vlim_pct, "%")

    fig.suptitle(subtitle or index_id, fontsize=14)
    return fig


def plot_variation(
    index_id: str,
    hist_clim: xr.DataArray,
    fut_clim: xr.DataArray,
    variation: xr.DataArray,
    variation_pct: xr.DataArray,
    out_path: str,
    subtitle: str = "",
) -> None:
    """Render and save the four-panel PNG for one index."""
    fig = build_figure(index_id, hist_clim, fut_clim, variation, variation_pct, subtitle=subtitle)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("Saved plot: %s", out_path)
