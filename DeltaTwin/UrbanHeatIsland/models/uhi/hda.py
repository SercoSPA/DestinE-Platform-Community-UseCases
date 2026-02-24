from __future__ import annotations

import logging
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

import requests
from destinepyauth import get_token
from tqdm import tqdm

HDA_STAC_ENDPOINT = "https://hda.data.destination-earth.eu/stac/v2"
STAC_DT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

logging.getLogger(__name__).addHandler(logging.NullHandler())
log = logging.getLogger("UrbanHeatIsland")
log.setLevel(logging.INFO)

def _get_auth_headers():
    """
    Get authentication headers for HDA STAC API requests.
    
    Uses the destinepyauth library to obtain an access token and formats
    it as a Bearer token authorization header.
    
    Returns:
        dict: Headers dictionary with Authorization bearer token
    """
    access_token = get_token("hda").access_token
    return {"Authorization": f"Bearer {access_token}"}


def _resolve_download_url(product: dict[str, Any], asset_key: Optional[str]) -> str:
    assets = product.get("assets", {})
    if asset_key and asset_key in assets and "href" in assets[asset_key]:
        return assets[asset_key]["href"]

    if "downloadLink" in assets and "href" in assets["downloadLink"]:
        return assets["downloadLink"]["href"]

    for asset in assets.values():
        if isinstance(asset, dict) and "href" in asset:
            return asset["href"]

    raise KeyError(f"No downloadable asset found for product {product.get('id')}")


def _download_product(
    product: dict[str, Any],
    auth_headers: dict[str, str],
    out_path: Path,
    asset_key: Optional[str] = None,
    max_retries: int = 3,
) -> Path:
    """
    Download a product from the HDA catalog.
    
    Downloads the product ZIP file with retry logic and progress tracking.
    
    Args:
        product: STAC product feature dictionary containing ID and assets
        auth_headers: Authentication headers for the download request
        out_path: Directory where the downloaded ZIP file will be saved
    
    Returns:
        Path: Path to the downloaded ZIP file
    
    Raises:
        KeyError: If downloadLink asset is not found in product
        requests.RequestException: If download fails after all retries
    """
    log.info(f"\n=== Downloading: {product['id']} ===")

    download_url = _resolve_download_url(product, asset_key)
    filename = out_path / f"{product['id']}.zip"

    log.info(f"Downloading full product to: {filename}")
    log.info(f"URL: {download_url}")

    for attempt in range(max_retries):
        try:
            response = requests.get(download_url, headers=auth_headers, stream=True, timeout=30)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))

            with tqdm(total=total_size, unit='B', unit_scale=True, desc=str(filename)) as progress_bar:
                with open(filename, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            progress_bar.update(len(chunk))

            log.info(f"\nDownload complete: {filename}")
            return filename
            
        except (requests.RequestException, OSError) as e:
            if attempt < max_retries - 1:
                log.warning(f"Download failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                log.error(f"Download failed after {max_retries} attempts")
                raise


def _print_search_results(response: requests.Response) -> None:
    """
    Log details of the STAC API search response.
    
    Displays the number of products found and basic information about each,
    including product ID, datetime, and available assets.
    
    Args:
        response: HTTP response from the STAC search endpoint
    
    Raises:
        Exception: If response status code is not 200
    """
    # print result info
    log.info(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        results = response.json()
        log.info(f"\nFound {len(results.get('features', []))} products")
    
        # Display results
        for idx, feature in enumerate(results.get('features', [])):
            log.info(f"\n--- Product {idx + 1} ---")
            log.info(f"ID: {feature.get('id')}")
            log.info(f"Datetime: {feature.get('properties', {}).get('datetime')}")
            log.info(f"Assets: {list(feature.get('assets', {}).keys())}")
    else:
        log.info(f"Error: {response.text}")
        response.raise_for_status()


def _extract_zip(zip_filename: Path, out_path: Path) -> Path:
    """
    Extract a ZIP archive to a folder named after the ZIP stem.
    
    Args:
        zip_filename: Path to the ZIP file to extract
        out_path: Base directory where extraction subdirectory will be created
    
    Returns:
        Path: Path to the extraction directory
    """
    log.info(f"\n=== Extracting {zip_filename} ===")
    
    extract_dir = out_path / Path(zip_filename).stem
    extract_dir.mkdir(exist_ok=True, parents=True)
    
    with zipfile.ZipFile(zip_filename, 'r') as zip_ref:
        zip_ref.extractall(path=extract_dir)
    
    return extract_dir


def search_products(
    collection_id: str,
    datetime_range: str,
    limit: int = 10,
    endpoint: str = HDA_STAC_ENDPOINT,
) -> list[dict[str, Any]]:
    """Search STAC products by collection and datetime range."""
    auth_headers = _get_auth_headers()
    payload = {
        "collections": [collection_id],
        "datetime": datetime_range,
        "limit": limit,
    }
    response = requests.post(f"{endpoint}/search", headers=auth_headers, json=payload, timeout=60)
    _print_search_results(response)
    results = response.json()
    return results.get("features", [])


def search_and_download(
    collection_id: str,
    datetime_range: str,
    out_path: Path,
    limit: int = 10,
    max_downloads: Optional[int] = None,
    extract: bool = True,
    asset_key: Optional[str] = None,
    endpoint: str = HDA_STAC_ENDPOINT,
) -> list[Path]:
    """Search STAC items and download matching assets.

    Returns extracted directories if extract=True, otherwise ZIP paths.
    """
    out_path.mkdir(exist_ok=True, parents=True)
    log.info(f"Output directory: {out_path}")

    auth_headers = _get_auth_headers()
    payload = {
        "collections": [collection_id],
        "datetime": datetime_range,
        "limit": limit,
    }
    response = requests.post(f"{endpoint}/search", headers=auth_headers, json=payload, timeout=60)
    _print_search_results(response)

    results = response.json()
    features = results.get("features", [])
    if not features:
        log.info("No products found for the given criteria")
        return []

    if max_downloads is not None:
        features = features[:max_downloads]

    output_paths: list[Path] = []
    for product in features:
        zip_file = _download_product(
            product=product,
            auth_headers=auth_headers,
            out_path=out_path,
            asset_key=asset_key,
        )
        if extract:
            output_paths.append(_extract_zip(zip_file, out_path))
        else:
            output_paths.append(zip_file)

    return output_paths
