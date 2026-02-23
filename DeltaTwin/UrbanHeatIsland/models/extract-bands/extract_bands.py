import click

import s2_band_extractor as sbe

@click.command(name="extract-bands")
@click.argument("product", type=click.Path(exists=True))
def extract_bands(product, resolution="60"):
    """
    Extracts only NIR, SWIR, Green and Bluebands from before wildfire Sentinel-2 product.
    """

    try:
        # Extract the NIR and SWIR bands from the before product
        sbe.s2_band_extractor.main([product, "B02", resolution], standalone_mode=False)
        sbe.s2_band_extractor.main([product, "B03", resolution], standalone_mode=False)
        sbe.s2_band_extractor.main([product, "B04", resolution], standalone_mode=False)
        sbe.s2_band_extractor.main([product, "B8A", resolution], standalone_mode=False)
    except click.ClickException as e:
        click.echo(f"[ERROR] Extraction before-fire bands failed: {e}", err=True)
        return
    except Exception as e:
        raise RuntimeError(f"Unexpected error during extraction before-fire bands: {e}")

    click.echo("[INFO] Extraction before-fire bands completed successfully.")

if __name__ == "__main__":
    extract_bands()

