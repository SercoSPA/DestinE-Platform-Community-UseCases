"""Two-panel (absolute | percentage) variation plot for an ETCCDI index."""

from __future__ import annotations

import logging

import matplotlib

matplotlib.use("Agg")  # headless / deterministic

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

log = logging.getLogger(__name__)

_DIVERGING_CMAP = "RdBu_r"


def _symmetric_limit(data: np.ndarray) -> float:
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return 1.0
    m = float(np.max(np.abs(finite)))
    return m if m > 0 else 1.0


def _panel(ax, da: xr.DataArray, title: str, units: str) -> None:
    values = da.values
    limit = _symmetric_limit(values)
    mesh = ax.pcolormesh(
        da["lon"].values,
        da["lat"].values,
        values,
        cmap=_DIVERGING_CMAP,
        vmin=-limit,
        vmax=limit,
        shading="auto",
    )
    ax.set_title(title)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(True, color="grey", linewidth=0.4, alpha=0.5)
    cbar = ax.figure.colorbar(mesh, ax=ax, fraction=0.046, pad=0.04)
    if units:
        cbar.set_label(units)


def build_figure(
    index_id: str,
    variation: xr.DataArray,
    variation_pct: xr.DataArray,
    subtitle: str = "",
) -> "matplotlib.figure.Figure":
    """Build the two-panel figure: absolute variation and percentage variation."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    units = variation.attrs.get("units", "")
    _panel(axes[0], variation, f"{index_id} variation ({units})" if units else f"{index_id} variation", units)
    _panel(axes[1], variation_pct, f"{index_id} variation (%)", "%")
    fig.suptitle(subtitle or index_id, fontsize=14)
    return fig


def plot_variation(
    index_id: str,
    variation: xr.DataArray,
    variation_pct: xr.DataArray,
    out_path: str,
    subtitle: str = "",
) -> None:
    """Render and save the two-panel variation PNG for one index."""
    fig = build_figure(index_id, variation, variation_pct, subtitle=subtitle)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("Saved plot: %s", out_path)
