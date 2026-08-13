#!/usr/bin/env python3
"""Plot C3S/SMHI hydrology impact indicators over Rome, per RCP scenario, from the DestinE HDA.

Collection: ``EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS``. The E-HYPE
hydrological model forced by bias-adjusted EURO-CORDEX EUR-11 simulations, published by SMHI for
the Copernicus Climate Change Service. Values are the relative change (%) for a future period
against a 1971-2000 baseline.

For each requested variable, one PNG per RCP scenario is written, all three sharing a colour
scale so they are directly comparable.

Why several variables. Extreme-value indicators such as ``flood_recurrence_50_years_return_period``
are noisy in a single ensemble member: a 50-year return level estimated from a 30-year window
carries large sampling uncertainty, so its ordering across scenarios need not follow the
forcing. Mean-state indicators such as aridity and soil moisture are far more stable. Plotting
both together shows whether an odd scenario ordering is a data-pipeline problem or the expected
behaviour of an extreme statistic. Interpret the mean-state variables first.

Data notes:
  - The ``e_hypegrid`` variant is continuous over land on a 5.0 km grid. Over the Rome box, 1081
    of 1538 cells carry a value (70%), which is the land fraction. ``e_hypecatch_*`` variants are
    the catchment-aggregated alternatives.
  - Cells carry modelled river discharge and land-surface state. This is a hydrological signal,
    not an inundation footprint: it says how flow changes, not where water goes.
  - **The ``flood_recurrence_*`` variables are return LEVELS, not frequencies.** The HDA
    parameter name and the file's ``long_name`` ("50 year flood recurence") both suggest
    frequency, but the NetCDF ``summary`` attribute is explicit: "Calculated as the 2,5,10, and
    50 year return period of annual daily maximum river discharge. This index is given as a
    relative change". So a value of +30% means the 1-in-50-year peak discharge is 30% larger,
    NOT that it occurs 30% more often. A frequency shift can be derived, because the 2, 5, 10
    and 50 year levels together sample the discharge-frequency curve, but that is an extra step
    this script does not do.
  - Every run here is ONE ensemble member (one GCM, one RCM, one hydrological model). The
    dataset offers 4 RCMs and 10 hydrological models. Do not quote a single-cell value from one
    member; use the ensemble.

HDA endpoint note: this collection is searchable on /stac only. /stac/v2 lists it but returns
404 on search. CMIP6 is the reverse (v2 only).

Usage:
    export DESPAUTH_USER=... DESPAUTH_PASSWORD=...
    python sis_hydrology_rome.py                       # default reference variable set
    python sis_hydrology_rome.py --variables aridity_actual,mean_soil_moisture
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import requests  # noqa: E402
import xarray as xr  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402
from rasterio.crs import CRS  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

log = logging.getLogger("sis_hydrology_rome")

HDA = "https://hda.data.destination-earth.eu/stac"  # v1: v2 cannot search this collection
COLLECTION = "EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS"

SCENARIOS = ["rcp_2_6", "rcp_4_5", "rcp_8_5"]
SCENARIO_LABEL = {
    "rcp_2_6": "RCP2.6  (strong mitigation)",
    "rcp_4_5": "RCP4.5  (intermediate)",
    "rcp_8_5": "RCP8.5  (high emissions)",
}

# Reference variables. "kind" flags how much to trust a single member:
#   mean   - mean-state indicator, stable, expected to order with the forcing
#   extreme- extreme-value indicator, noisy in one member
VARIABLES = {
    "aridity_actual": dict(
        short="aridity", kind="mean",
        title="Aridity",
        means_up="drier: higher evaporative demand relative to water available"),
    "mean_soil_moisture": dict(
        short="soilmoist", kind="mean",
        title="Mean soil moisture",
        means_up="wetter soils"),
    "river_discharge": dict(
        short="discharge", kind="mean",
        title="Mean river discharge",
        means_up="more flow"),
    "minimum_river_discharge": dict(
        short="lowflow", kind="mean",
        title="Minimum river discharge (low flows)",
        means_up="higher low flows, i.e. less hydrological drought"),
    "maximum_river_discharge": dict(
        short="maxflow", kind="extreme",
        title="Maximum river discharge (peak flows)",
        means_up="higher peak flows"),
    "mean_runoff": dict(
        short="runoff", kind="mean",
        title="Mean runoff",
        means_up="more runoff"),
    # The lower the return period, the more stable the estimate: a 2-year level is supported by
    # roughly 15 exceedances in a 30-year window, whereas a 50-year level is an extrapolation
    # from 30 annual maxima. Trading extremeness for stability is the main lever available.
    "flood_recurrence_2_years_return_period": dict(
        short="flood2", kind="extreme-low",
        title="Magnitude of the 2-year flood discharge",
        means_up="the 1-in-2-year peak flow is larger"),
    "flood_recurrence_5_years_return_period": dict(
        short="flood5", kind="extreme-low",
        title="Magnitude of the 5-year flood discharge",
        means_up="the 1-in-5-year peak flow is larger"),
    "flood_recurrence_10_years_return_period": dict(
        short="flood10", kind="extreme-low",
        title="Magnitude of the 10-year flood discharge",
        means_up="the 1-in-10-year peak flow is larger"),
    # Despite the HDA parameter name and the file's own long_name ("50 year flood recurence"),
    # the NetCDF summary attribute is explicit that this is the 50-year RETURN LEVEL of annual
    # daily maximum river discharge, given as a relative change. It is a change in flood
    # MAGNITUDE, not in how often that flood occurs. Labelled accordingly.
    "flood_recurrence_50_years_return_period": dict(
        short="flood50", kind="extreme",
        title="Magnitude of the 50-year flood discharge",
        means_up="the 1-in-50-year peak flow is larger"),
}

DEFAULT_VARIABLES = [
    "aridity_actual",
    "mean_soil_moisture",
    "river_discharge",
    "minimum_river_discharge",
    "flood_recurrence_50_years_return_period",
]

BASE_QUERY = {
    "ecmwf:product_type": "climate_impact_indicators",
    "ecmwf:variable_type": "relative_change_from_reference_period",
    "ecmwf:time_aggregation": "annual_mean",
    "ecmwf:period": "2041_2070",
    "ecmwf:hydrological_model": "e_hypegrid",  # 5 km gridded variant
    "ecmwf:rcm": "rca4",
    "ecmwf:gcm": "ec_earth",
    "ecmwf:ensemble_member": "r12i1p1",
}

ROME_BBOX = (11.2, 41.2, 13.8, 42.8)  # W, S, E, N
LANDMARKS = {"Rome (Tiber)": (12.48, 41.89), "Aniene": (12.60, 41.93)}
LABEL_OFFSET = {"Rome (Tiber)": (-10, -16), "Aniene": (10, 10)}

# Diverging: two hues, neutral gray midpoint. Red = increase, blue = decrease.
DIVERGING = LinearSegmentedColormap.from_list("change", ["#2a78d6", "#f0efec", "#d03b3b"])
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"


def get_token() -> str:
    from destinepyauth import get_token as _get

    if not (os.environ.get("DESPAUTH_USER") and os.environ.get("DESPAUTH_PASSWORD")):
        raise ValueError("Set DESPAUTH_USER and DESPAUTH_PASSWORD before running.")
    return _get("hda").access_token


def find_item(token: str, variable: str, scenario: str, period: str) -> dict:
    query = {k: {"eq": v} for k, v in BASE_QUERY.items()}
    query["ecmwf:variable"] = {"eq": variable}
    query["ecmwf:experiment"] = {"eq": scenario}
    query["ecmwf:period"] = {"eq": period}
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
            f"{variable}/{scenario}: expected 1 orderable item, got {len(feats)}. "
            "Check the parameter combination against the queryables endpoint."
        )
    return feats[0]


def order_and_download(token, item, dest: Path, poll_s=15, timeout_s=1800) -> Path:
    """Order, poll to completion, save. HDA answers 202 + polling location, then 200."""
    if dest.exists() and dest.stat().st_size > 0:
        log.info("    cached %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
        return dest
    url = item["assets"]["downloadLink"]["href"]
    headers = {"Authorization": f"Bearer {token}"}
    waited = 0
    while True:
        r = requests.get(url, headers=headers, timeout=300, allow_redirects=False)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            log.info("    downloaded %s (%.1f MB) after %ds", dest.name, len(r.content) / 1e6, waited)
            return dest
        if r.status_code != 202:
            raise RuntimeError(f"HTTP {r.status_code} ordering {dest.name}: {r.text[:300]}")
        body = r.json()
        url = body.get("location", url)
        if waited >= timeout_s:
            raise TimeoutError(f"{dest.name} not ready after {timeout_s}s")
        time.sleep(poll_s)
        waited += poll_s


def load_field(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """Return (lat2d, lon2d, values, internal_nc_name) for the single time step."""
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
    candidates = [v for v in ds.data_vars if v != "lat"]
    if not candidates:
        raise ValueError(f"no data variable in {nc.name}: {list(ds.data_vars)}")
    name = max(candidates, key=lambda v: ds[v].size)
    values = ds[name].squeeze().values.astype("float64")
    lat = np.asarray(ds["lat"].values, dtype="float64")
    lon = np.asarray(ds["lon"].values, dtype="float64")
    lat2d = np.broadcast_to(lat, values.shape).copy()
    lon2d = np.broadcast_to(lon, values.shape).copy()
    return lat2d, lon2d, values, nc.name


def clip_to_bbox(lat, lon, values, bbox):
    w, s, e, n = bbox
    keep = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n) & np.isfinite(values)
    return lon[keep], lat[keep], values[keep]


def native_spacing(lat2d, lon2d, bbox) -> tuple[float, float]:
    """Native cell spacing in degrees near the AOI, measured on the 2D curvilinear grid.

    Taken as the median absolute step between adjacent rows (latitude) and columns (longitude)
    inside the AOI. This must be done on the 2D arrays: flattening to 1D valid points first and
    then taking unique values gives near-duplicates on a curvilinear grid, and the median step
    collapses to almost zero.
    """
    w, s, e, n = bbox
    inside = (lon2d >= w) & (lon2d <= e) & (lat2d >= s) & (lat2d <= n)
    rows = np.any(inside, axis=1)
    cols = np.any(inside, axis=0)
    if rows.sum() < 2 or cols.sum() < 2:
        raise ValueError(f"AOI {bbox} spans fewer than 2 native cells; cannot infer spacing")
    sub_lat = lat2d[np.ix_(rows, cols)]
    sub_lon = lon2d[np.ix_(rows, cols)]
    dlat = float(np.median(np.abs(np.diff(sub_lat, axis=0))))
    dlon = float(np.median(np.abs(np.diff(sub_lon, axis=1))))
    if not (np.isfinite(dlat) and np.isfinite(dlon) and dlat > 0 and dlon > 0):
        raise ValueError(f"could not infer native spacing (got dlat={dlat}, dlon={dlon})")
    return dlat, dlon


def write_geotiff(variable, scenario, lons, lats, vals, bbox, out_path, period, spacing):
    """Resample the curvilinear source cells onto a regular EPSG:4326 grid and write a GeoTIFF.

    The source NetCDF carries only 2D curvilinear ``lat``/``lon``: ``x`` and ``y`` are bare
    dimensions with no coordinate values and the file declares no CRS or ``grid_mapping``. A
    GeoTIFF needs a rectilinear grid and an affine transform, so resampling is unavoidable
    rather than a choice.

    Nearest-neighbour is used so no new values are invented, and any target cell whose nearest
    source cell is further than one native cell away is left as nodata rather than smeared
    across the coast or a data gap. Target spacing is taken from the native spacing so the
    output does not imply more resolution than the input has.
    """
    w, s, e, n = bbox
    dlat, dlon = spacing  # measured on the native 2D grid, see native_spacing()

    # north-up target grid of cell centres
    tlon = np.arange(w + dlon / 2, e, dlon)
    tlat = np.arange(n - dlat / 2, s, -dlat)
    glon, glat = np.meshgrid(tlon, tlat)

    tree = cKDTree(np.column_stack([lons, lats]))
    dist, idx = tree.query(np.column_stack([glon.ravel(), glat.ravel()]), k=1)
    grid = vals[idx].astype("float32")
    # drop anything further than one native cell: keeps sea and gaps as nodata
    grid[dist > max(dlat, dlon)] = np.nan
    grid = grid.reshape(glat.shape)

    transform = from_origin(w, n, dlon, dlat)
    meta = VARIABLES[variable]
    with rasterio.open(
        out_path, "w", driver="GTiff",
        height=grid.shape[0], width=grid.shape[1], count=1, dtype="float32",
        crs=CRS.from_epsg(4326), transform=transform, nodata=np.nan,
        compress="deflate", tiled=False,
    ) as dst:
        dst.write(grid, 1)
        dst.set_band_description(1, meta["short"])
        dst.update_tags(
            variable=variable,
            long_name=meta["title"],
            units="percent",
            quantity="relative change vs 1971-2000 reference period",
            increase_means=meta["means_up"],
            indicator_kind=meta["kind"],
            scenario=scenario,
            period=period.replace("_", "-"),
            model_chain="E-HYPEgrid v1.0 forced by EURO-CORDEX EUR-11 ICHEC-EC-EARTH "
                        "r12i1p1 / SMHI-RCA4-v1, bias adjusted with TimescaleBC v1.02",
            ensemble_note="SINGLE ensemble member, not an ensemble mean",
                native_grid=f"curvilinear ~5.0 km, no CRS declared in source NetCDF; "
                        f"measured spacing {dlat:.4f} deg lat x {dlon:.4f} deg lon",
            resampling="nearest neighbour onto a regular EPSG:4326 grid; cells further than "
                       "one native cell from a source cell are nodata",
            source="DestinE HDA, EO.ECMWF.DAT.SIS_HYDROLOGY_VARIABLES_DERIVED_PROJECTIONS "
                   "(C3S/SMHI hydrology impact indicators)",
        )
    valid = int(np.isfinite(grid).sum())
    log.info("    wrote %s (%d x %d, %d valid cells)", out_path.name,
             grid.shape[1], grid.shape[0], valid)


def plot_one(variable, scenario, lons, lats, vals, vlim, bbox, out_path, period):
    meta = VARIABLES[variable]
    w, s, e, n = bbox
    fig = plt.figure(figsize=(8.4, 8.8), facecolor=SURFACE)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.set_extent([w, e, s, n], crs=ccrs.PlateCarree())  # cartopy wants W, E, S, N
    ax.set_facecolor(SURFACE)

    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#f4f3f0", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#e8eaec", zorder=0)
    ax.add_feature(cfeature.RIVERS.with_scale("10m"), edgecolor="#b9c6d4", linewidth=0.8, zorder=1)
    ax.add_feature(cfeature.COASTLINE.with_scale("10m"), edgecolor="#9a9a94", linewidth=0.6, zorder=2)
    ax.add_feature(cfeature.BORDERS.with_scale("10m"), edgecolor="#c2c2bb", linewidth=0.4, zorder=2)

    norm = TwoSlopeNorm(vmin=-vlim, vcenter=0.0, vmax=vlim)
    pts = ax.scatter(lons, lats, c=vals, cmap=DIVERGING, norm=norm, marker="s",
                     s=46, linewidths=0.25, edgecolors=SURFACE,
                     transform=ccrs.PlateCarree(), zorder=3)

    for name, (lo, la) in LANDMARKS.items():
        ax.plot(lo, la, marker="o", markersize=7, markerfacecolor="none",
                markeredgecolor=INK, markeredgewidth=1.6,
                transform=ccrs.PlateCarree(), zorder=5)
        dx, dy = LABEL_OFFSET.get(name, (8, 8))
        ax.annotate(name, xy=(lo, la), xytext=(dx, dy), textcoords="offset points",
                    fontsize=11, color=INK, zorder=6, ha="right" if dx < 0 else "left",
                    bbox=dict(boxstyle="round,pad=0.22", facecolor=SURFACE,
                              edgecolor="none", alpha=0.85))

    cbar = fig.colorbar(pts, ax=ax, orientation="horizontal", pad=0.04, shrink=0.86,
                        extend="both")
    cbar.set_label(f"Relative change in {meta['title'].lower()} (%)", fontsize=10, color=INK_MUTED)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(labelsize=9, colors=INK_MUTED, length=0)

    kind_note = ("mean-state indicator: stable in a single member"
                 if meta["kind"] == "mean" else
                 "EXTREME-value indicator: noisy in a single member, ordering across scenarios "
                 "is not meaningful")
    ax.set_title(f"{meta['title']}\n{SCENARIO_LABEL[scenario]}", fontsize=14, color=INK,
                 pad=10, loc="left", linespacing=1.5)
    fig.text(0.5, 0.062,
             f"{period.replace('_', '-')} vs 1971-2000  |  E-HYPE on EURO-CORDEX "
             f"(EC-EARTH / RCA4)  |  5.0 km grid, no interpolation\n"
             f"Red = increase ({meta['means_up']}).  {kind_note}.\n"
             "Single ensemble member. Source: DestinE HDA, C3S/SMHI hydrology impact indicators.",
             fontsize=8.5, color=INK_MUTED, va="top", ha="center")

    fig.savefig(out_path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[3]
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", default=str(repo / "docs" / "assets" / "rome-hydrology"),
                   help="where the PNGs go")
    p.add_argument("--cache-dir",
                   default=str(Path(tempfile.gettempdir()) / "sis_hydrology_cache"),
                   help="download cache, kept outside the repo (products are ~11 MB each)")
    p.add_argument("--variables", default=",".join(DEFAULT_VARIABLES),
                   help=f"comma-separated. Available: {', '.join(VARIABLES)}")
    p.add_argument("--period", default=BASE_QUERY["ecmwf:period"])
    p.add_argument("--bbox", default=",".join(str(v) for v in ROME_BBOX))
    p.add_argument("--no-geotiff", action="store_true",
                   help="write only PNGs; by default a matching EPSG:4326 GeoTIFF is written too")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    variables = [v.strip() for v in args.variables.split(",") if v.strip()]
    unknown = [v for v in variables if v not in VARIABLES]
    if unknown:
        raise ValueError(f"Unknown variable(s) {unknown}. Available: {list(VARIABLES)}")

    bbox = tuple(float(v) for v in args.bbox.split(","))
    out_dir, cache = Path(args.out_dir), Path(args.cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    token = get_token()
    log.info("authenticated with HDA; %d variable(s) x %d scenarios", len(variables), len(SCENARIOS))

    summary = {}
    for variable in variables:
        meta = VARIABLES[variable]
        log.info("=== %s (%s) ===", variable, meta["kind"])
        fields = {}
        for sc in SCENARIOS:
            item = find_item(token, variable, sc, args.period)
            dest = cache / f"{meta['short']}_{sc}_{args.period}.zip"
            order_and_download(token, item, dest)
            lat, lon, vals, ncname = load_field(dest)
            # the CDS filename embeds the scenario: assert it matches what we asked for
            tag = sc.replace("_", "").replace("rcp", "rcp")
            if tag not in ncname.replace("-", "").lower():
                raise ValueError(f"scenario mismatch: asked {sc}, file is {ncname}")
            spacing = native_spacing(lat, lon, bbox)
            lo, la, v = clip_to_bbox(lat, lon, vals, bbox)
            if v.size == 0:
                raise ValueError(f"{variable}/{sc}: no valid cells in bbox {bbox}")
            fields[sc] = (lo, la, v, spacing)
            log.info("    %s: %d cells | median %+.1f%% | native %.4f x %.4f deg",
                     sc, v.size, np.median(v), spacing[0], spacing[1])

        allv = np.concatenate([f[2] for f in fields.values()])
        vlim = max(float(np.nanpercentile(np.abs(allv), 98)), 5.0)
        for sc in SCENARIOS:
            lo, la, v, spacing = fields[sc]
            stem = f"{meta['short']}_{sc}_{args.period}"
            plot_one(variable, sc, lo, la, v, vlim, bbox, out_dir / f"{stem}.png", args.period)
            log.info("    wrote %s.png", stem)
            if not args.no_geotiff:
                write_geotiff(variable, sc, lo, la, v, bbox, out_dir / f"{stem}.tif",
                              args.period, spacing)
        summary[variable] = {sc: fields[sc][2] for sc in SCENARIOS}

    print()
    print("Median relative change over the Rome box (%), by scenario")
    print(f"{'variable':<46} {'kind':<8} {'RCP2.6':>9} {'RCP4.5':>9} {'RCP8.5':>9}  ordered?")
    for variable, per_sc in summary.items():
        meta = VARIABLES[variable]
        meds = [float(np.median(per_sc[sc])) for sc in SCENARIOS]
        mono = "yes" if (meds[0] <= meds[1] <= meds[2]) or (meds[0] >= meds[1] >= meds[2]) else "NO"
        print(f"{variable:<46} {meta['kind']:<8} " + " ".join(f"{m:>+9.1f}" for m in meds)
              + f"  {mono}")
    print()
    print("'ordered?' asks only whether the median changes monotonically with forcing. Mean-state")
    print("indicators are expected to; extreme-value indicators in a single member need not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
