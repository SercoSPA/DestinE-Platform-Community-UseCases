#!/usr/bin/env python3
"""Feasibility test: can CMIP6 on Earth Data Hub separate SSP scenarios by Fire Weather Index?

Mediterranean box, 2045-2054, three SSPs. Not a pipeline: one script, no caching, no retries.

Choices made, stated for the record:
  - Fire season handled by computing FWI over the FULL year and then counting exceedance days
    only in Apr-Sep. FWI's drought codes are recursive accumulations, so masking the input to
    Apr-Sep would reset the memory each spring and understate danger. season_method is left None.
  - Land-only. sftlf (land fraction) > 50% is required. FWI over the sea is meaningless and would
    otherwise dominate a basin mean over a box that is mostly water.
  - Longitude is 0-360 in this store, so the -10..40 box wraps. Converted to -180..180 and sorted
    before selecting.

Known caveat, recorded not fixed: FWI is defined on local-noon conditions but this dataset has
only daily-mean tas and hurs, so absolute FWI is biased low. This test is about the difference
between scenarios.
"""

import os
import time

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import dask
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import xclim
import xclim.indices as xi
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

WORK = "/tmp/claude-1000/fwi_work"
URL = ("https://edh:{key}@data.earthdatahub.destine.eu/cmip6/"
       "CMCC-CM2-SR5-ScenarioMIP-r1i1p1f1-day-gn-v0.zarr")
SCENARIOS = ["ssp126", "ssp370", "ssp585"]
LABEL = {"ssp126": "SSP1-2.6", "ssp370": "SSP3-7.0", "ssp585": "SSP5-8.5"}
BOX = dict(lon=slice(-10, 40), lat=slice(30, 48))
YEARS = ("2045", "2054")
FWI_THRESHOLD = 30
SEASON = [4, 5, 6, 7, 8, 9]

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
DIVERGING = LinearSegmentedColormap.from_list("d", ["#2a78d6", "#f0efec", "#d03b3b"])

timings = {}


def stamp(name, t0):
    timings[name] = time.time() - t0
    print(f"  [{name}] {timings[name]:.1f}s")
    return time.time()


t_start = time.time()
t = t_start

# ---------------------------------------------------------------- open and subset
ds = xr.open_dataset(URL.format(key=os.environ["EDH_API_KEY"]),
                     engine="zarr", zarr_format=3, chunks={})
# 0-360 -> -180..180 so the Mediterranean box does not wrap
ds = ds.assign_coords(lon=(((ds.lon + 180) % 360) - 180)).sortby("lon")
sub = ds[["tas", "hurs", "pr", "sfcWind"]].sel(**BOX).sel(time=slice(*YEARS))
# sftlf is a (lat, lon) coord on ds, so it followed the lon reassign and sort above
land = ds["sftlf"].sel(**BOX) > 50

print("=== SUBSET ===")
print(f"  dims: {dict(sub.sizes)}")
nbytes = sum(v.size * 4 for v in sub.data_vars.values()) * len(SCENARIOS) / sub.sizes["experiment_id"]
print(f"  per-scenario in-memory (4 vars): {nbytes / len(SCENARIOS) / 1e6:.0f} MB")
print(f"  all {len(SCENARIOS)} scenarios: {nbytes / 1e6:.0f} MB")
print(f"  land cells in box: {int(land.sum())} of {land.size}")
t = stamp("open+subset", t)

# ---------------------------------------------------------------- FWI per scenario
results = {}
with dask.config.set(scheduler="threads", num_workers=16):
    for sc in SCENARIOS:
        print(f"=== {sc} ===")
        t_sc = time.time()
        d = sub.sel(experiment_id=sc)
        # FWI is recursive along time, so it needs one chunk in time
        tas = d.tas.chunk({"time": -1})
        hurs = d.hurs.chunk({"time": -1})
        wind = d.sfcWind.chunk({"time": -1})
        pr = (d.pr * 86400.0).chunk({"time": -1})
        pr.attrs["units"] = "mm d-1"
        tas.attrs["units"] = "K"
        hurs.attrs["units"] = "%"
        wind.attrs["units"] = "m s-1"

        with xclim.set_options(cf_compliance="log", data_validation="log"):
            out = xi.cffwis_indices(tas=tas, pr=pr, sfcWind=wind, hurs=hurs,
                                    lat=d.lat, season_method=None)
        fwi = out[-1]  # DC, DMC, FFMC, ISI, BUI, FWI
        fwi = fwi.compute()

        # days per season above threshold, in Apr-Sep only, averaged over years
        season = fwi.sel(time=fwi.time.dt.month.isin(SEASON))
        per_year = (season > FWI_THRESHOLD).groupby("time.year").sum("time")
        days = per_year.mean("year").where(land)
        results[sc] = days
        basin = float(days.mean(skipna=True))  # unweighted; box spans only 18 deg of latitude
        print(f"  FWI mean {float(fwi.mean()):.1f}, max {float(fwi.max()):.1f}")
        print(f"  land-mean days/season above FWI {FWI_THRESHOLD}: {basin:.1f}")
        t = stamp(f"fwi:{sc}", t_sc)

# ---------------------------------------------------------------- figures
t_plot = time.time()
vmax = max(float(v.max()) for v in results.values())


def basemap(ax):
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#e8eaec", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f4f3f0", zorder=0)
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor="#6f6f6a", linewidth=0.7, zorder=4)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor="#c2c2bb", linewidth=0.4, zorder=4)
    ax.set_extent([-10, 40, 30, 48], crs=ccrs.PlateCarree())


# Image 1: absolute, shared sequential scale (single hue, light to dark)
fig, axes = plt.subplots(1, 3, figsize=(20, 3.4), facecolor=SURFACE, constrained_layout=True,
                         subplot_kw={"projection": ccrs.PlateCarree()})
for ax, sc in zip(axes, SCENARIOS):
    basemap(ax)
    m = ax.pcolormesh(results[sc].lon, results[sc].lat, results[sc].values,
                      cmap="Reds", vmin=0, vmax=vmax, shading="nearest",
                      transform=ccrs.PlateCarree(), zorder=2)
    ax.set_title(f"{LABEL[sc]}   land mean {float(results[sc].mean(skipna=True)):.0f} d",
                 fontsize=13, color=INK, loc="left")
cb = fig.colorbar(m, ax=axes, orientation="horizontal", fraction=0.09, pad=0.02,
                  shrink=0.5, extend="max")
cb.set_label(f"Days per Apr-Sep season with FWI > {FWI_THRESHOLD}  (mean of 2045-2054)",
             fontsize=11, color=INK_MUTED)
cb.outline.set_visible(False)
cb.ax.tick_params(labelsize=10, colors=INK_MUTED, length=0)
fig.suptitle("Fire Weather Index exceedance, Mediterranean, mid-century  |  CMIP6 CMCC-CM2-SR5 "
             "r1i1p1f1, 0.94 x 1.25 deg native grid  |  land only (sftlf > 50%)\n"
             "Note the deepest values are Saharan cells where drought codes run away and there "
             "is no fuel; Mediterranean Europe is far lower. See verdict.",
             fontsize=12, color=INK, x=0.006, ha="left", linespacing=1.6)
fig.savefig(f"{WORK}/fwi_days_by_ssp.png", dpi=140, facecolor=SURFACE)
plt.close(fig)

# Image 2: differences vs SSP1-2.6, diverging about zero
diffs = {sc: (results[sc] - results["ssp126"]) for sc in ("ssp370", "ssp585")}
dmax = max(float(np.nanmax(np.abs(d.values))) for d in diffs.values())
fig, axes = plt.subplots(2, 1, figsize=(9.5, 6.4), facecolor=SURFACE, constrained_layout=True,
                         subplot_kw={"projection": ccrs.PlateCarree()})
for ax, sc in zip(axes, ("ssp370", "ssp585")):
    basemap(ax)
    m = ax.pcolormesh(diffs[sc].lon, diffs[sc].lat, diffs[sc].values,
                      cmap=DIVERGING, norm=TwoSlopeNorm(vmin=-dmax, vcenter=0, vmax=dmax),
                      shading="nearest", transform=ccrs.PlateCarree(), zorder=2)
    ax.set_title(f"{LABEL[sc]} minus SSP1-2.6   mean {float(diffs[sc].mean(skipna=True)):+.0f} d",
                 fontsize=13, color=INK, loc="left")
cb = fig.colorbar(m, ax=axes, orientation="horizontal", fraction=0.07, pad=0.02,
                  shrink=0.7, extend="both")
cb.set_label(f"Difference in days per season with FWI > {FWI_THRESHOLD}",
             fontsize=11, color=INK_MUTED)
cb.outline.set_visible(False)
cb.ax.tick_params(labelsize=10, colors=INK_MUTED, length=0)
fig.suptitle("Scenario difference in fire weather exceedance, 2045-2054",
             fontsize=13, color=INK, x=0.01, ha="left")
fig.savefig(f"{WORK}/fwi_days_diff.png", dpi=140, facecolor=SURFACE)
plt.close(fig)
t = stamp("plots", t_plot)

# ---------------------------------------------------------------- verdict numbers
print()
print("=== VERDICT NUMBERS ===")
print(f"  grid: {float(np.median(np.diff(ds.lat.values))):.4f} deg lat x "
      f"{float(np.median(np.diff(ds.lon.values))):.4f} deg lon")
print(f"  Med box: {results['ssp126'].sizes['lat']} x {results['ssp126'].sizes['lon']} cells, "
      f"{int(land.sum())} land")
italy = results["ssp126"].sel(lon=slice(6.5, 18.5), lat=slice(36.5, 47))
print(f"  Italy bbox cells: {italy.sizes['lat']} x {italy.sizes['lon']} = "
      f"{italy.sizes['lat'] * italy.sizes['lon']} "
      f"({int(np.isfinite(italy.values).sum())} with land data)")
print()
print("  full box (includes Sahara, where drought codes run away and there is no fuel):")
for sc in SCENARIOS:
    v = results[sc]
    print(f"    {LABEL[sc]:9s} land-mean {float(v.mean(skipna=True)):6.1f} d   max {float(v.max()):6.1f} d")
means = [float(results[sc].mean(skipna=True)) for sc in SCENARIOS]
print(f"    ordered: {means[0] < means[1] < means[2]}")

# north of 36N drops the Saharan belt and keeps Mediterranean Europe + Anatolia
print("  north of 36N only (Mediterranean Europe + Anatolia):")
north = [float(results[sc].sel(lat=slice(36, 48)).mean(skipna=True)) for sc in SCENARIOS]
for sc, m in zip(SCENARIOS, north):
    print(f"    {LABEL[sc]:9s} land-mean {m:6.1f} d")
print(f"    ordered: {north[0] < north[1] < north[2]}")
print(f"    SSP5-8.5 minus SSP1-2.6: {north[2] - north[0]:+.1f} d "
      f"({100 * (north[2] - north[0]) / north[0]:+.0f}%)")
print()
print(f"  TOTAL WALL CLOCK {time.time() - t_start:.1f}s")
for k, v in timings.items():
    print(f"    {k:16s} {v:6.1f}s")
