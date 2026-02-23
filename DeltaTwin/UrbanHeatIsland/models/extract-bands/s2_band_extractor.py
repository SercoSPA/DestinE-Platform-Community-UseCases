import click
import os

import zipfile
import tempfile
import shutil

def find_band_file(granule_path, band, requested_resolution):
    """
    Tries to find a .jp2 file for the specified band at the requested resolution.
    If not found, tries alternative resolutions in priority order.
    Resolution fallback order:
        10 -> 20 -> 60
        20 -> 10 -> 60
        60 -> 20 -> 10
    """
    fallback_order = {
        "10": ["10", "20", "60"],
        "20": ["20", "10", "60"],
        "60": ["60", "20", "10"]
    }

    resolutions_to_try = fallback_order.get(requested_resolution, [requested_resolution])
    band = band.lower()

    for resolution in resolutions_to_try:
        for granule in os.listdir(granule_path):
            img_data_path = os.path.join(granule_path, granule, "IMG_DATA")
            rpath = os.path.join(img_data_path, f"R{resolution}m")
            if not os.path.exists(rpath):
                continue
            for file in os.listdir(rpath):
                if file.endswith(".jp2") and f"_{band}_" in file.lower():
                    if resolution != requested_resolution:
                        click.echo(f"Requested resolution {requested_resolution}m not found, using {resolution}m instead.")
                    return os.path.join(rpath, file)

    return None


@click.command(name="s2-band-extractor")
@click.argument("product", type=click.Path(exists=True))
@click.argument("band", type=str)
@click.argument("resolution", type=str)
def s2_band_extractor(product, band, resolution):
    """
    Extracts a specific .jp2 band from a Sentinel-2 product (SAFE folder or ZIP archive),
    at the requested resolution. If the exact resolution is not found, the function
    will fallback to other resolutions in this order:
       - 10 → 20 → 60
       - 20 → 10 → 60
       - 60 → 20 → 10

    Args:
        product (str): Path to the Sentinel-2 product (.SAFE folder or .zip archive).
        band (str): Band name to extract (e.g. 'B08', 'B04').
        resolution (str): Target resolution in meters ('10', '20' or '60').
    """
    temp_dir = None
    target_file = None

    if resolution not in ["10", "20", "60"]:    
        click.echo("[ERROR] Invalid resolution. Supported resolutions are 10, 20, or 60.", err=True)
        return

    try:
        # Extract .zip archive into temporary folder
        if zipfile.is_zipfile(product):
            click.echo("[INFO] ZIP archive detected. Extracting...")
            temp_dir = tempfile.mkdtemp()
            with zipfile.ZipFile(product, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            safes = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if d.endswith(".SAFE")]
            if not safes:
                click.echo("[ERROR] No .SAFE folder found in ZIP archive.", err=True)
                return
            product_path = safes[0]
            click.echo(f"[INFO] Archive extracted to: {product_path}")
        else:
            product_path = product

        # Check for expected Sentinel-2 structure
        granule_path = os.path.join(product_path, "GRANULE")
        if not os.path.exists(granule_path):
            click.echo("[ERROR] Invalid Sentinel-2 structure: GRANULE folder not found.", err=True)
            return

        click.echo(f"[INFO] Searching for band {band} at resolution {resolution}m...")
        target_file = find_band_file(granule_path, band, resolution)

        if not target_file:
            click.echo(f"[ERROR] Band {band} not found at resolution {resolution}m or fallback resolutions.", err=True)
            return

        # Copy the band to the current working directory
        output_filename = os.path.basename(target_file)
        shutil.copy2(target_file, output_filename)
        click.echo(f"[INFO] Band {band} extracted successfully as {output_filename}")

    finally:
        # Clean up temporary extraction folder if used
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            click.echo("[INFO] Temporary files cleaned up.")


if __name__ == "__main__":
    s2_band_extractor()
