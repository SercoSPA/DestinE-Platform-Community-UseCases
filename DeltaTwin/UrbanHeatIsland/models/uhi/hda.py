from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any, Optional

import requests
from requests.exceptions import HTTPError
from destinepyauth import get_token
from tqdm import tqdm

HDA_STAC_ENDPOINT = "https://hda.data.destination-earth.eu/stac/v2"
STAC_DT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

logging.getLogger(__name__).addHandler(logging.NullHandler())
log = logging.getLogger("UrbanHeatIsland")
log.setLevel(logging.INFO)


def _get_auth_headers() -> dict[str, str]:
    access_token = get_token("hda").access_token
    return {"Authorization": f"Bearer {access_token}"}


def search_products(
    collection_id: str,
    datetime_range: str | None = None,
    limit: int = 10,
    endpoint: str = HDA_STAC_ENDPOINT,
) -> list[dict[str, Any]]:
    """Search STAC products by collection and datetime range."""
    auth_headers = _get_auth_headers()
    payload = {
        "collections": [collection_id],
        "limit": limit,
    }
    if datetime_range is not None:
        payload["datetime"] = datetime_range

    log.info(f"Search payload: {payload}")
    response = requests.post(f"{endpoint}/search", headers=auth_headers, json=payload, timeout=60)
    try:
        response.raise_for_status()
    except HTTPError:
        raise HTTPError(f"Bad search ({response.status_code}) \n{response.text}")

    results = response.json()
    features = results.get("features", [])
    log.info(f"Found {len(features)} products")

    for idx, feature in enumerate(features):
        log.info(f"--- Product {idx} ---")
        log.info(f"ID: {feature.get('id')}")
        log.info(f"Datetime: {feature.get('properties', {}).get('datetime')}")
        log.info(f"Assets: {list(feature.get('assets', {}).keys())}")

    return features


def list_asset_keys(product: dict[str, Any]) -> list[str]:
    """Return all asset keys available in one STAC product feature."""
    return list(product.get("assets", {}).keys())


def _resolve_asset_url(product: dict[str, Any], asset_key: str) -> str:
    assets = product.get("assets", {})
    if asset_key not in assets or "href" not in assets[asset_key]:
        available = ", ".join(sorted(assets.keys()))
        raise KeyError(
            f"Asset '{asset_key}' not found for product {product.get('id')}. "
            f"Available assets: [{available}]"
        )
    return assets[asset_key]["href"]


def resolve_asset_key_by_suffix(product: dict[str, Any], asset_suffix: str) -> str:
    """Resolve exactly one asset key by suffix match (case-insensitive)."""
    if not asset_suffix:
        raise ValueError("asset_suffix must be a non-empty string")

    suffix_norm = asset_suffix.lower()
    keys = list_asset_keys(product)
    matches = [key for key in keys if key.lower().endswith(suffix_norm)]

    if not matches:
        available = ", ".join(sorted(keys))
        raise KeyError(
            f"No asset key ends with '{asset_suffix}' for product {product.get('id')}. "
            f"Available assets: [{available}]"
        )

    if len(matches) > 1:
        raise ValueError(
            f"Multiple asset keys end with '{asset_suffix}' for product {product.get('id')}: {matches}. "
            "Please provide a more specific suffix."
        )

    return matches[0]


def _open_download_stream(
    asset_url: str,
    auth_headers: dict[str, str],
    max_pointer_hops: int = 2,
) -> tuple[requests.Response, str]:
    """Open stream and resolve JSON pointer payloads containing {'href': ...}."""
    current_url = asset_url
    use_auth = True

    for hop in range(max_pointer_hops + 1):
        headers = auth_headers if use_auth else None
        response = requests.get(current_url, headers=headers, stream=True, timeout=(30, 300))
        response.raise_for_status()

        content_type = (response.headers.get("content-type") or "").lower()
        if "json" in content_type:
            payload = response.json()
            response.close()

            next_url = payload.get("href") if isinstance(payload, dict) else None
            if next_url:
                log.info(f"Resolved asset pointer hop {hop + 1}")
                current_url = str(next_url)
                use_auth = False
                continue

            raise ValueError("JSON response did not contain an 'href' for downloadable content")

        return response, current_url

    raise ValueError(f"Too many pointer hops while resolving URL: {asset_url}")


def _choose_output_filename(
    response: requests.Response,
    resolved_url: str,
    product_id: str,
    asset_key: str,
) -> str:
    content_disposition = response.headers.get("content-disposition") or ""
    if "filename=" in content_disposition:
        filename_part = content_disposition.split("filename=")[-1].strip().strip('"')
        if filename_part:
            return Path(unquote(filename_part)).name

    url_name = Path(unquote(urlparse(resolved_url).path)).name
    if url_name and "." in url_name:
        return url_name

    safe_asset = asset_key.replace("/", "_").replace(" ", "_")
    return f"{product_id}_{safe_asset}.bin"


def _download_asset(
    product: dict[str, Any],
    asset_key: str,
    out_path: Path,
    max_retries: int = 3,
) -> Path:
    product_id = product.get("id", "unknown-product")
    auth_headers = _get_auth_headers()
    asset_url = _resolve_asset_url(product, asset_key)

    log.info(f"=== Downloading product '{product_id}', asset '{asset_key}' ===")
    log.info(f"Asset URL: {asset_url}")

    for attempt in range(max_retries):
        response: Optional[requests.Response] = None
        tmp_path: Optional[Path] = None
        final_path: Optional[Path] = None

        try:
            response, resolved_url = _open_download_stream(asset_url, auth_headers)
            filename = _choose_output_filename(response, resolved_url, product_id, asset_key)

            out_path.mkdir(exist_ok=True, parents=True)
            final_path = out_path / filename
            tmp_path = out_path / f"{filename}.part"

            if final_path.exists():
                final_path.unlink()
            if tmp_path.exists():
                tmp_path.unlink()

            total_size = int(response.headers.get("content-length", 0))
            bytes_written = 0

            with tqdm(total=total_size if total_size > 0 else None, unit="B", unit_scale=True, desc=filename) as progress_bar:
                with open(tmp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            bytes_written += len(chunk)
                            progress_bar.update(len(chunk))

            if total_size > 0 and bytes_written != total_size:
                raise IOError(
                    f"Incomplete download for {product_id}/{asset_key}: expected {total_size}, got {bytes_written}"
                )

            tmp_path.replace(final_path)
            log.info(f"Download complete: {final_path}")
            return final_path

        except Exception as e:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()
            if attempt < max_retries - 1:
                log.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                raise
        finally:
            if response is not None:
                response.close()

    raise RuntimeError("Download failed unexpectedly")


def search_and_download(
    collection_id: str,
    datetime_range: str,
    out_path: Path,
    asset_suffixes: list[str],
    result_index: int = 0,
    limit: int = 1,
    endpoint: str = HDA_STAC_ENDPOINT,
) -> list[Path]:
    """Search products, pick one result by index, and download one asset per suffix.

    Asset selection is based on case-insensitive endswith suffix matching.
    """
    if not asset_suffixes:
        raise ValueError("asset_suffixes must contain at least one suffix")

    features = search_products(
        collection_id=collection_id,
        datetime_range=datetime_range,
        limit=limit,
        endpoint=endpoint,
    )
    if not features:
        raise ValueError("No products found for the given criteria")
    if result_index < 0 or result_index >= len(features):
        raise IndexError(f"result_index={result_index} out of range for {len(features)} results")

    selected = features[result_index]
    log.info(f"Selected result index {result_index}: {selected.get('id')}")
    log.info(f"Available assets: {list_asset_keys(selected)}")

    downloaded_paths: list[Path] = []
    for suffix in asset_suffixes:
        selected_asset_key = resolve_asset_key_by_suffix(selected, suffix)
        log.info(f"Suffix '{suffix}' -> asset key: {selected_asset_key}")
        downloaded_paths.append(
            _download_asset(
                product=selected,
                asset_key=selected_asset_key,
                out_path=out_path,
            )
        )

    return downloaded_paths
