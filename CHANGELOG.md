# Changelog

All notable changes to this repository are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- ClimateIndexPlotter DeltaTwin component: computes ETCCDI climate indices from the Climate DT
  (streamed from Earth Data Hub) and plots historical-vs-future variation (absolute and
  percentage) per index.
- ClimateIndexPlotter `resolution` input: `standard` (0.35 deg, ~29 km pixels, default) or
  `high` (0.044 deg, ~4 km, Climate DT's native scale).
- ClimateIndexPlotter logs the volume it will stream before reading, and refuses runs above
  500 GB.
- ClimateIndexPlotter `scripts/climate_dt_daily_t2m.py`: exports Climate DT daily-mean `t2m`
  over a NUTS3 region as one GeoTIFF per model per day, reusing the component's EDH layer and
  the LSTPlotter NUTS3 lookup. Has its own `requirements.txt`.

### Changed
- ClimateIndexPlotter example asset is now a real Climate DT run (TXx over Italy) rather than a
  synthetic illustration.

### Fixed
- ClimateIndexPlotter streamed the hourly Climate DT source once per index per panel (measured
  at 12x for three indices) because climatologies were left lazy until plotting. All index
  climatologies for both periods are now evaluated in a single pass, so the source is read
  about once per period regardless of how many indices are selected. Three indices over the
  example Italy box went from 40m56s to 8m01s, with pixel-identical output.
- ClimateIndexPlotter sized its Dask pool by CPU count, but EDH reads are latency-bound;
  reading with a fixed 16-thread pool raised measured throughput from ~23 MB/s to ~176 MB/s.
- ClimateIndexPlotter left the daily aggregation at one Dask chunk per day, which made the task
  graph very large and broke multi-day rolling indices such as Rx5day on chunked input. Daily
  data is now regrouped into yearly chunks.
