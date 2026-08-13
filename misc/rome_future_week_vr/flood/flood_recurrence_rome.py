#!/usr/bin/env python3
"""Plot the projected change in 50-year flood recurrence over Rome, one PNG per RCP scenario.

Data source: the DestinE data lake (HDA), collection
``EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS``. This is the C3S/SMHI
hydrology impact-indicator dataset: the E-HYPE hydrological model forced by bias-adjusted
EURO-CORDEX EUR-11 climate simulations. The variable plotted is the relative change (%) in the
recurrence of the present-day 50-year flood, for a future period against a 1971-2000 baseline.

Three things about this data that the plots make visible on purpose:

1. The ``e_hypegrid`` variant is **continuous over land** on a 5.0 km grid: measured over the
   Rome box, 1081 of 1538 cells carry a value, i.e. 70%, which is the land fraction (the
   south-west of the box is sea). The 34% valid figure for the whole file is an artefact of the
   950 x 1000 domain covering a lot of ocean. The ``e_hypecatch_*`` variants are the
   catchment-aggregated alternatives.
2. The maps draw the actual grid cells as discrete squares rather than interpolating, so the
   real resolution stays visible. What each cell carries is modelled local river discharge, so
   it is a hydrological signal, not an inundation footprint. It says how much more often a
   present-day 50-year discharge occurs, not where water goes.
3. It is **one ensemble member** (one GCM, one RCM, one hydrological model). The dataset offers
   4 RCMs and 10 hydrological models. A defensible number needs the ensemble spread; this script
   is a single-member demonstration.

Products are ordered on demand from the Copernicus CDS through HDA, so the first run waits for
the order to complete (typically under a minute per scenario). Downloads are cached, so re-runs
plot from disk without re-ordering.

Usage:
    export DESPAUTH_USER=... DESPAUTH_PASSWORD=...
    python flood_recurrence_rome.py --out-dir ./rome_flood

Note the HDA endpoint: this collection is searchable on /stac only. The /stac/v2 endpoint lists
it but returns 404 on search, whereas CMIP6 is the reverse (v2 only).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402
import xarray as xr  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

log = logging.getLogger("flood_recurrence_rome")

HDA = "https://hda.data.destination-earth.eu/stac"  # v1: v2 cannot search this collection
COLLECTION = "EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS"

SCENARIOS = ["rcp_2_6", "rcp_4_5", "rcp_8_5"]
SCENARIO_LABEL = {
    "rcp_2_6": "RCP2.6  (strong mitigation)",
    "rcp_4_5": "RCP4.5  (intermediate)",
    "rcp_8_5": "RCP8.5  (high emissions)",
}

# One ensemble member. Values verified as valid via the HDA queryables endpoint.
FIXED_QUERY = {
    "ecmwf:product_type": "climate_impact_indicators",
    "ecmwf:variable": "flood_recurrence_50_years_return_period",
    "ecmwf:variable_type": "relative_change_from_reference_period",
    "ecmwf:time_aggregation": "annual_mean",
    "ecmwf:period": "2041_2070",
    "ecmwf:hydrological_model": "e_hypegrid",  # 5 km gridded variant, not catchment
    "ecmwf:rcm": "rca4",
    "ecmwf:gcm": "ec_earth",
    "ecmwf:ensemble_member": "r12i1p1",
}

# Rome area. Wide enough to show the Tiber and Aniene network, not just the city.
ROME_BBOX = (11.2, 41.2, 13.8, 42.8)  # W, S, E, N

# Points quoted in the feasibility assessment, annotated so the numbers can be checked.
LANDMARKS = {
    "Rome (Tiber)": (12.48, 41.89),
    "Aniene": (12.60, 41.93),
}

# Diverging palette: two hues with a neutral gray midpoint. Red = more frequent flooding.
DIVERGING = LinearSegmentedColormap.from_list(
    "flood_change", ["#2a78d6", "#f0efec", "#d03b3b"]
)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"


def get_token() -> str:
    """DESP token for HDA. Reads DESPAUTH_USER / DESPAUTH_PASSWORD from the environment."""
    from destinepyauth import get_token as _get

    if not (os.environ.get("DESPAUTH_USER") and os.environ.get("DESPAUTH_PASSWORD")):
        raise ValueError("Set DESPAUTH_USER and DESPAUTH_PASSWORD before running.")
    return _get("hda").access_token


def find_item(token: str, scenario: str) -> dict:
    """Search HDA for the single orderable item matching this scenario."""
    query = {k: {"eq": v} for k, v in FIXED_QUERY.items()}
    query["ecmwf:experiment"] = {"eq": scenario}
    r = requests.post(
        f"{HDA}/search",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"collections": [COLLECTION], "limit": 5, "query": query}),
        timeout=120,
    )
    r.raise_for_status()
    feats = r.json().get("features", [])
    if len(feats) != 1:
        raise ValueError(
            f"{scenario}: expected exactly 1 orderable item, got {len(feats)}. "
            "The parameter combination may no longer be valid."
        )
    return feats[0]


def order_and_download(token: str, item: dict, dest: Path, poll_s: int = 15,
                       timeout_s: int = 1800) -> Path:
    """Order the product, poll until ready, and save it. Returns the file path.

    HDA answers the download link with 202 and a polling location while the CDS job runs,
    then 200 with the payload.
    """
    if dest.exists() and dest.stat().st_size > 0:
        log.info("cached: %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
        return dest

    href = item["assets"]["downloadLink"]["href"]
    headers = {"Authorization": f"Bearer {token}"}
    url, waited = href, 0
    while True:
        r = requests.get(url, headers=headers, timeout=300, allow_redirects=False)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            log.info("downloaded %s (%.1f MB) after %ds", dest.name, len(r.content) / 1e6, waited)
            return dest
        if r.status_code != 202:
            raise RuntimeError(f"unexpected HTTP {r.status_code} ordering {dest.name}: {r.text[:300]}")
        body = r.json()
        url = body.get("location", url)
        if waited >= timeout_s:
            raise TimeoutError(f"order for {dest.name} not ready after {timeout_s}s")
        log.info("  order status=%s, waited %ds", body.get("status"), waited)
        time.sleep(poll_s)
        waited += poll_s


def load_field(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Open the product zip/NetCDF and return (lat2d, lon2d, values) for the single time step."""
    import zipfile

    nc = path
    if zipfile.is_zipfile(path):
        outdir = path.with_suffix("")
        outdir.mkdir(exist_ok=True)
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".nc")]
            if not names:
                raise ValueError(f"no .nc inside {path.name}")
            z.extract(names[0], outdir)
            nc = outdir / names[0]

    ds = xr.open_dataset(nc)
    var = [v for v in ds.data_vars if v.startswith("rdisreturn")]
    if not var:
        raise ValueError(f"expected an rdisreturn* variable, found {list(ds.data_vars)}")
    values = ds[var[0]].isel(time=0).values.astype("float64")
    lat = np.asarray(ds["lat"].values, dtype="float64")
    lon = np.asarray(ds["lon"].values, dtype="float64")
    if lat.ndim == 1 or lon.ndim == 1:  # broadcast to the 2D grid if stored 1D
        lon2d, lat2d = np.meshgrid(lon.ravel(), lat.ravel()) if lat.ndim == 1 else (lon, lat)
    else:
        lat2d, lon2d = lat, lon
    lat2d = np.broadcast_to(lat2d, values.shape).copy()
    lon2d = np.broadcast_to(lon2d, values.shape).copy()
    return lat2d, lon2d, values


def clip_to_bbox(lat, lon, values, bbox):
    """Return 1D arrays of lon, lat, value for valid cells inside the bbox."""
    w, s, e, n = bbox
    inside = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n) & np.isfinite(values)
    return lon[inside], lat[inside], values[inside]


def plot_scenario(scenario, lons, lats, vals, vlim, bbox, out_path):
    """One map: the actual 5 km river-network cells, on a shared diverging scale."""
    w, s, e, n = bbox
    fig = plt.figure(figsize=(8.4, 8.6), facecolor=SURFACE)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent([w, e, s, n], crs=ccrs.PlateCarree())  # cartopy wants W, E, S, N
    ax.set_facecolor(SURFACE)

    # recessive basemap for orientation only
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#f4f3f0", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#e8eaec", zorder=0)
    ax.add_feature(cfeature.RIVERS.with_scale("10m"), edgecolor="#b9c6d4", linewidth=0.8, zorder=1)
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), edgecolor="#9a9a94", linewidth=0.6, zorder=2)
    ax.add_feature(cfeature.BORDERS.with_scale("10m"), edgecolor="#c2c2bb", linewidth=0.4, zorder=2)

    norm = TwoSlopeNorm(vmin=-vlim, vcenter=0.0, vmax=vlim)
    # square markers sized to the ~5 km cell so the sparse network reads as cells, not dots
    pts = ax.scatter(
        lons, lats, c=vals, cmap=DIVERGING, norm=norm, marker="s",
        s=46, linewidths=0.25, edgecolors=SURFACE,
        transform=ccrs.PlateCarree(), zorder=3,
    )

    # offset the two labels in opposite directions: Rome and the Aniene are ~13 km apart
    label_offset = {"Rome (Tiber)": (-10, -16), "Aniene": (10, 10)}
    for name, (lo, la) in LANDMARKS.items():
        ax.plot(lo, la, marker="o", markersize=7, markerfacecolor="none",
                markeredgecolor=INK, markeredgewidth=1.6,
                transform=ccrs.PlateCarree(), zorder=5)
        dx, dy = label_offset.get(name, (8, 8))
        ax.annotate(name, xy=(lo, la), xytext=(dx, dy), textcoords="offset points",
                    fontsize=11, color=INK, zorder=6,
                    ha="right" if dx < 0 else "left",
                    bbox=dict(boxstyle="round,pad=0.22", facecolor=SURFACE,
                              edgecolor="none", alpha=0.85))

    cbar = fig.colorbar(pts, ax=ax, orientation="horizontal", pad=0.04, shrink=0.86,
                        extend="both")
    cbar.set_label("Change in frequency of the present-day 50-year flood (%)",
                   fontsize=10, color=INK_MUTED)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(labelsize=9, colors=INK_MUTED, length=0)

    ax.set_title(
        f"{SCENARIO_LABEL[scenario]}\n"
        "2041-2070 vs 1971-2000  |  E-HYPE on EURO-CORDEX (EC-EARTH / RCA4)",
        fontsize=14, color=INK, pad=10, loc="left", linespacing=1.5,
    )
    fig.text(0.5, 0.055,
             "Squares are the 5.0 km model grid cells, plotted without interpolation. "
             "Cells carry modelled river discharge,\nso this is how much more often a "
             "present-day 50-year discharge occurs, not where water goes. "
             "Single ensemble member.\n"
             "Source: DestinE HDA, C3S/SMHI hydrology impact indicators (E-HYPE).",
             fontsize=8.5, color=INK_MUTED, va="top", ha="center")

    fig.savefig(out_path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    log.info("wrote %s", out_path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", default="./rome_flood", help="output directory")
    p.add_argument("--cache-dir", default=None, help="download cache (default: <out-dir>/cache)")
    p.add_argument("--bbox", default=",".join(str(v) for v in ROME_BBOX),
                   help="W,S,E,N in degrees")
    p.add_argument("--period", default=FIXED_QUERY["ecmwf:period"],
                   help="future period, e.g. 2041_2070")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    FIXED_QUERY["ecmwf:period"] = args.period
    bbox = tuple(float(v) for v in args.bbox.split(","))
    out_dir = Path(args.out_dir)
    cache = Path(args.cache_dir) if args.cache_dir else out_dir / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    token = get_token()
    log.info("authenticated with HDA")

    # fetch all three first, so the colour scale can be shared across scenarios
    fields = {}
    for sc in SCENARIOS:
        log.info("--- %s ---", sc)
        item = find_item(token, sc)
        log.info("  item %s", item["id"])
        zip_path = cache / f"flood50_{sc}_{args.period}.zip"
        order_and_download(token, item, zip_path)
        lat, lon, vals = load_field(zip_path)
        lo, la, v = clip_to_bbox(lat, lon, vals, bbox)
        if v.size == 0:
            raise ValueError(f"{sc}: no valid river-network cells inside bbox {bbox}")
        fields[sc] = (lo, la, v)
        log.info("  %d valid cells in bbox | min %.1f%% median %.1f%% max %.1f%%",
                 v.size, v.min(), np.median(v), v.max())

    # symmetric shared scale, robust to outliers so the three maps stay comparable
    allv = np.concatenate([f[2] for f in fields.values()])
    vlim = float(np.nanpercentile(np.abs(allv), 98))
    vlim = max(vlim, 5.0)
    log.info("shared symmetric colour limit: +/- %.1f%%", vlim)

    for sc in SCENARIOS:
        lo, la, v = fields[sc]
        plot_scenario(sc, lo, la, v, vlim, bbox, out_dir / f"flood50_{sc}_{args.period}.png")

    print()
    print(f"{'scenario':<10} {'cells':>6} {'min %':>8} {'median %':>9} {'max %':>8}   nearest-cell values")
    for sc in SCENARIOS:
        lo, la, v = fields[sc]
        near = []
        for name, (plo, pla) in LANDMARKS.items():
            d = (lo - plo) ** 2 + (la - pla) ** 2
            near.append(f"{name} {v[int(np.argmin(d))]:+.1f}%")
        print(f"{sc:<10} {v.size:>6} {v.min():>8.1f} {np.median(v):>9.1f} {v.max():>8.1f}   "
              + "  ".join(near))
    return 0


if __name__ == "__main__":
    sys.exit(main())
