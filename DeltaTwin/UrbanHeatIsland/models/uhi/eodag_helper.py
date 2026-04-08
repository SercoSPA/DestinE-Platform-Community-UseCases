from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from eodag import EODataAccessGateway

from lst_helper import get_nuts3_geom


def search_and_download_eodag(
    collection_id: str,
    datetime_range: str,
    out_path: Path,
    asset_suffixes: list[str],
    result_index: int = 0,
    limit: int = 20,
    nuts3_code: Optional[str] = None,
) -> list[Path]:
    """Search and download a Landsat product via EODAG/DEDL.

    Credentials are read from the ``DESP_USERNAME`` and ``DESP_PASSWORD``
    environment variables (same as the DestinE Platform notebook pattern).

    Parameters
    ----------
    collection_id:
        STAC ``productType`` identifier, e.g. ``"EO.NASA.DAT.LANDSAT.C2_L2"``.
    datetime_range:
        ISO-8601 interval ``"start/end"``, e.g.
        ``"2025-07-01T00:00:00Z/2025-07-31T23:59:59Z"``.
    out_path:
        Destination directory for the downloaded product.
    asset_suffixes:
        File-name suffixes to locate in the downloaded product (case-insensitive),
        e.g. ``["B4.TIF", "B5.TIF", "B10.TIF", "MTL.TXT"]``.
    result_index:
        Index of the search result to download.
    limit:
        Maximum number of results to request from the STAC search.
    nuts3_code:
        Optional Eurostat NUTS3 region code (e.g. ``"ITI43"`` for the Province
        of Rome).  When provided, the region's geometry is used as a spatial
        filter so that only products intersecting the region are returned.

    Returns
    -------
    list[Path]
        One :class:`~pathlib.Path` per entry in *asset_suffixes* (only those found).
    """
    username = os.environ.get("DESPAUTH_USER", "")
    password = os.environ.get("DESPAUTH_PASSWORD", "")
    if username:
        os.environ["EODAG__DEDL__AUTH__CREDENTIALS__USERNAME"] = username
    if password:
        os.environ["EODAG__DEDL__AUTH__CREDENTIALS__PASSWORD"] = password
    os.environ["EODAG__DEDL__PRIORITY"] = "10"
    os.environ["EODAG__DEDL__SEARCH__TIMEOUT"] = "60"

    dag = EODataAccessGateway()

    start, end = datetime_range.split("/", 1)

    search_kwargs: dict = {}
    if nuts3_code is not None:
        search_kwargs["geom"] = get_nuts3_geom(nuts3_code)

    results = dag.search(
        provider="dedl",
        collection=collection_id,
        start=start,
        end=end,
        limit=limit,
        **search_kwargs,
    )
    if not results:
        raise ValueError("No products found for the given criteria.")
    if result_index >= len(results):
        raise IndexError(
            f"result_index={result_index} out of range for {len(results)} result(s)."
        )

    product = results[result_index]
    print(f"Selected product: {product.properties.get('id', product)}")

    downloaded = Path(dag.download(product, output_dir=str(out_path)))
    all_files = list(downloaded.rglob("*")) if downloaded.is_dir() else [downloaded]

    found: list[Path] = []
    for suffix in asset_suffixes:
        match = next(
            (f for f in all_files if f.is_file() and f.name.upper().endswith(suffix.upper())),
            None,
        )
        if match is not None:
            found.append(match)
        else:
            print(f"Warning: no downloaded file found with suffix '{suffix}' in {downloaded}")
    return found
