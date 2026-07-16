import json

import pytest

import aoi


def test_parse_bbox_string_returns_bbox_and_no_mask():
    bbox, mask = aoi.resolve_aoi("-10,35,5,45", None)
    assert (bbox.west, bbox.south, bbox.east, bbox.north) == (-10.0, 35.0, 5.0, 45.0)
    assert mask is None


def test_bbox_whitespace_tolerated():
    bbox, _ = aoi.resolve_aoi(" -10 , 35 , 5 , 45 ", None)
    assert bbox.east == 5.0


def test_bbox_wrong_field_count_raises():
    with pytest.raises(ValueError):
        aoi.resolve_aoi("1,2,3", None)


def test_bbox_non_numeric_raises():
    with pytest.raises(ValueError):
        aoi.resolve_aoi("a,b,c,d", None)


def test_bbox_west_not_less_than_east_raises():
    with pytest.raises(ValueError):
        aoi.resolve_aoi("5,35,-10,45", None)


def test_bbox_latitude_out_of_range_raises():
    with pytest.raises(ValueError):
        aoi.resolve_aoi("-10,35,5,95", None)


def test_neither_bbox_nor_shapefile_raises():
    with pytest.raises(ValueError):
        aoi.resolve_aoi(None, None)


def test_none_sentinel_strings_treated_as_absent():
    with pytest.raises(ValueError):
        aoi.resolve_aoi("none", "none")


def _write_geojson_polygon(path, coords):
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [coords]},
            }
        ],
    }
    path.write_text(json.dumps(fc))


def test_shapefile_derives_bbox_and_mask(tmp_path):
    gj = tmp_path / "aoi.geojson"
    _write_geojson_polygon(gj, [[0, 40], [4, 40], [4, 44], [0, 44], [0, 40]])
    bbox, mask = aoi.resolve_aoi(None, str(gj))
    assert (bbox.west, bbox.south, bbox.east, bbox.north) == (0.0, 40.0, 4.0, 44.0)
    assert mask is not None
    assert mask.geom_type in ("Polygon", "MultiPolygon")


def test_bbox_and_shapefile_uses_bbox_for_extent_and_shape_for_mask(tmp_path):
    gj = tmp_path / "aoi.geojson"
    _write_geojson_polygon(gj, [[0, 40], [4, 40], [4, 44], [0, 44], [0, 40]])
    bbox, mask = aoi.resolve_aoi("-10,35,5,45", str(gj))
    assert (bbox.west, bbox.south, bbox.east, bbox.north) == (-10.0, 35.0, 5.0, 45.0)
    assert mask is not None


def test_apply_mask_nans_cells_outside_polygon():
    import numpy as np
    import xarray as xr
    from shapely.geometry import box

    da = xr.DataArray(
        np.ones((4, 4)),
        dims=("lat", "lon"),
        coords={"lat": [40.0, 41.0, 42.0, 43.0], "lon": [10.0, 11.0, 12.0, 13.0]},
    )
    # Polygon covering only the lower-left quadrant.
    geom = box(9.5, 39.5, 11.5, 41.5)
    out = aoi.apply_mask(da, geom)
    assert np.isfinite(out.sel(lat=40.0, lon=10.0))  # inside
    assert np.isnan(out.sel(lat=43.0, lon=13.0))     # outside
