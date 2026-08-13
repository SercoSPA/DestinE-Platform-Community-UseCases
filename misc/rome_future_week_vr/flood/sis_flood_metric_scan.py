#!/usr/bin/env python3
"""Which flood metric and which period actually separate the RCP pathways over Rome?

The 50-year return level at 2041-2070 does not order with the forcing in a single ensemble
member, which makes it useless for showing "different futures". This script tests whether a
different choice fixes that, by scanning combinations of:

  - metric: less extreme return levels (2, 5, 10 year) and mean annual maximum discharge are
    estimated from far more data than the 50-year level, so they should be more stable
  - period: RCP pathways diverge much more by 2071-2100 than by 2041-2070, so the forced signal
    should be larger late century
  - degree scenarios: 1.5 / 2.0 / 3.0 C warming levels separate by construction

For each combination it reports the spatial median per scenario over the Rome box, whether the
medians order with the forcing, and a separation score:

    separation = (median_high - median_low) / (mean spatial IQR within scenarios)

That is a signal-to-noise ratio. Above about 0.5 the between-pathway difference is visible
against within-map spatial variability; near 0 the three maps would look identical to a visitor.

No plots: this is a decision tool. Plot the winner with sis_hydrology_rome.py.

Usage:
    export DESPAUTH_USER=... DESPAUTH_PASSWORD=...
    python sis_flood_metric_scan.py
    python sis_flood_metric_scan.py --degree-scenarios
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sis_hydrology_rome as sis  # noqa: E402

log = logging.getLogger("sis_flood_metric_scan")

# metrics ordered from most stable to most extreme
METRICS = [
    "maximum_river_discharge",
    "flood_recurrence_2_years_return_period",
    "flood_recurrence_5_years_return_period",
    "flood_recurrence_10_years_return_period",
    "flood_recurrence_50_years_return_period",
]
RCP_PERIODS = ["2041_2070", "2071_2100"]
DEGREE_SCENARIOS = ["1_5_c", "2_0_c", "3_0_c"]


def stats_for(token, variable, scenario, period, bbox, cache) -> tuple[float, float]:
    """Return (spatial median, spatial IQR) of relative change over the bbox."""
    item = sis.find_item(token, variable, scenario, period)
    short = sis.VARIABLES[variable]["short"]
    dest = cache / f"{short}_{scenario}_{period}.zip"
    sis.order_and_download(token, item, dest)
    lat, lon, vals, ncname = sis.load_field(dest)
    lo, la, v = sis.clip_to_bbox(lat, lon, vals, bbox)
    if v.size == 0:
        raise ValueError(f"{variable}/{scenario}/{period}: no valid cells in bbox")
    q25, q75 = np.percentile(v, [25, 75])
    return float(np.median(v)), float(q75 - q25)


def scan(token, metrics, scenarios, periods, bbox, cache):
    rows = []
    for period in periods:
        for variable in metrics:
            meds, iqrs = [], []
            for sc in scenarios:
                m, i = stats_for(token, variable, sc, period, bbox, cache)
                meds.append(m)
                iqrs.append(i)
                log.info("  %-42s %-14s %-10s median %+7.1f%% IQR %5.1f",
                         variable, period, sc, m, i)
            mono = (meds[0] <= meds[1] <= meds[2]) or (meds[0] >= meds[1] >= meds[2])
            noise = float(np.mean(iqrs))
            sep = (meds[-1] - meds[0]) / noise if noise > 0 else 0.0
            rows.append(dict(variable=variable, period=period, meds=meds,
                             mono=mono, noise=noise, sep=sep))
    return rows


def report(rows, scenarios, label_low, label_high):
    print()
    print("Spatial median relative change over the Rome box (%), and pathway separation")
    print(f"{'metric':<42} {'period':<11} "
          + " ".join(f"{s:>9}" for s in scenarios)
          + f" {'IQR':>6} {'sep':>6}  ordered?")
    print("-" * 108)
    for r in sorted(rows, key=lambda x: (x["period"], METRICS.index(x["variable"]))):
        print(f"{r['variable']:<42} {r['period']:<11} "
              + " ".join(f"{m:>+9.1f}" for m in r["meds"])
              + f" {r['noise']:>6.1f} {r['sep']:>+6.2f}  {'yes' if r['mono'] else 'NO'}")
    print()
    print(f"sep = (median[{label_high}] - median[{label_low}]) / mean spatial IQR.")
    print("Larger magnitude means the pathways are more distinguishable on a map.")
    print("|sep| below ~0.2 means the three maps would look essentially the same to a visitor.")
    best = max(rows, key=lambda r: (r["mono"], abs(r["sep"])))
    print()
    print(f"Most separable combination: {best['variable']} at {best['period']} "
          f"(sep {best['sep']:+.2f}, ordered: {'yes' if best['mono'] else 'NO'})")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache-dir", default=str(Path(tempfile.gettempdir()) / "sis_hydrology_cache"))
    p.add_argument("--bbox", default=",".join(str(v) for v in sis.ROME_BBOX))
    p.add_argument("--metrics", default=",".join(METRICS))
    p.add_argument("--degree-scenarios", action="store_true",
                   help="scan 1.5/2.0/3.0 C warming levels instead of the RCP x period grid")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    bbox = tuple(float(v) for v in args.bbox.split(","))
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    token = sis.get_token()

    if args.degree_scenarios:
        # under degree_scenario the "period" axis carries the warming level
        rows = []
        for level in DEGREE_SCENARIOS:
            for variable in metrics:
                m, i = stats_for(token, variable, "degree_scenario", level, bbox, cache)
                log.info("  %-42s %-8s median %+7.1f%% IQR %5.1f", variable, level, m, i)
                rows.append((variable, level, m, i))
        print()
        print("Warming-level scenarios (degree_scenario), Rome box medians (%)")
        print(f"{'metric':<42} " + " ".join(f"{lv:>9}" for lv in DEGREE_SCENARIOS)
              + f" {'IQR':>6} {'sep':>6}  ordered?")
        print("-" * 100)
        for variable in metrics:
            got = [r for r in rows if r[0] == variable]
            meds = [next(r[2] for r in got if r[1] == lv) for lv in DEGREE_SCENARIOS]
            noise = float(np.mean([r[3] for r in got]))
            sep = (meds[-1] - meds[0]) / noise if noise > 0 else 0.0
            mono = (meds[0] <= meds[1] <= meds[2]) or (meds[0] >= meds[1] >= meds[2])
            print(f"{variable:<42} " + " ".join(f"{m:>+9.1f}" for m in meds)
                  + f" {noise:>6.1f} {sep:>+6.2f}  {'yes' if mono else 'NO'}")
        print()
        print("sep = (median[3.0C] - median[1.5C]) / mean spatial IQR.")
        return 0

    rows = scan(token, metrics, sis.SCENARIOS, RCP_PERIODS, bbox, cache)
    report(rows, sis.SCENARIOS, "RCP2.6", "RCP8.5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
