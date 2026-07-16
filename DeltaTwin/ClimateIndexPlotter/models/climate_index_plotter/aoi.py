"""Resolve an area of interest from a bounding box string or a shapefile."""

from __future__ import annotations

import logging
from typing import NamedTuple, Optional

import geopandas as gpd
import xarray as xr
from shapely.geometry.base import BaseGeometry

log = logging.getLogger(__name__)

_ABSENT = {"", "none", "null"}


class Bbox(NamedTuple):
    """Geographic bounding box in EPSG:4326 (degrees)."""

    west: float
    south: float
    east: float
    north: float


def _is_absent(value: Optional[str]) -> bool:
    return value is None or str(value).strip().lower() in _ABSENT


def _parse_bbox(bbox_str: str) -> Bbox:
    parts = [p.strip() for p in bbox_str.split(",")]
    if len(parts) != 4:
        raise ValueError(
            f"aoi_bbox must be 'west,south,east,north' (4 values), got {len(parts)}: {bbox_str!r}"
        )
    try:
        west, south, east, north = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"aoi_bbox values must be numeric: {bbox_str!r}") from exc

    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise ValueError(f"aoi_bbox longitudes must be within [-180, 180]: {bbox_str!r}")
    if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        raise ValueError(f"aoi_bbox latitudes must be within [-90, 90]: {bbox_str!r}")
    if west >= east:
        raise ValueError(f"aoi_bbox west must be < east: {bbox_str!r}")
    if south >= north:
        raise ValueError(f"aoi_bbox south must be < north: {bbox_str!r}")

    return Bbox(west, south, east, north)


def _load_mask(shapefile: str) -> BaseGeometry:
    gdf = gpd.read_file(shapefile)
    if gdf.empty:
        raise ValueError(f"Shapefile contains no geometries: {shapefile!r}")
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return gdf.geometry.union_all()


def resolve_aoi(
    bbox_str: Optional[str],
    shapefile: Optional[str],
) -> tuple[Bbox, Optional[BaseGeometry]]:
    """Resolve the AOI to a bounding box and an optional mask polygon.

    Rules:
      - bounding box only  -> bbox from the string, no mask.
      - shapefile only     -> bbox from the shape bounds, mask = shape geometry.
      - both               -> bbox from the string, mask = shape geometry.
      - neither            -> error (fail loudly).
    """
    has_bbox = not _is_absent(bbox_str)
    has_shape = not _is_absent(shapefile)

    if not has_bbox and not has_shape:
        raise ValueError("An AOI is required: provide either aoi_bbox or aoi_shapefile.")

    mask: Optional[BaseGeometry] = _load_mask(shapefile) if has_shape else None

    if has_bbox:
        bbox = _parse_bbox(bbox_str)
    else:
        minx, miny, maxx, maxy = mask.bounds
        bbox = Bbox(float(minx), float(miny), float(maxx), float(maxy))

    log.info("Resolved AOI bbox: %s (mask=%s)", bbox, mask is not None)
    return bbox, mask


def apply_mask(da: xr.DataArray, geometry: BaseGeometry) -> xr.DataArray:
    """Mask a 2-D (lat, lon) DataArray to a polygon, setting outside cells to NaN."""
    import regionmask

    region = regionmask.Regions([geometry])
    grid_mask = region.mask(da["lon"], da["lat"])
    return da.where(grid_mask.notnull())
