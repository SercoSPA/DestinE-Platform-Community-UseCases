# UHI use case

    - Use SesamEO to access Sentinel-2 data
    - see tutorial 4 for an example
    - need additional cloud filter

# Notes on bands

    - with landsat was using: red, NIR and TIRS1 (thermal infared)
    - TIRS1 not avail for Sentinel-2
    - calculating LST with Sentinel-2 is not trivial:
        - example: https://www.mdpi.com/2072-4292/14/16/4076 some kind of linear model
    - Could use:
        - Red, NIR from Sentinel
        - TIRS1 from Landsat - interpolated

# LST with Landsat DTC

## Approach

    - Unable to use band extractor (sentinel-2 only)
    - provide bands instead of downloading entire image (only need 3 bands)
    - Use level 2 because it includes atmospheric corrections (not part of code?)
    - Landsat level 2 collections:
        https://sesameo.destine.eu/collections/EO.NASA.DAT.LANDSAT.C2_L2
        https://data.destination-earth.eu/data-portfolio/EO.NASA.DAT.LANDSAT.C2_L2
    - Feed bands into LST calculation code
    - The output from band extractor seems to be a file path. So I'm not sure if band extractor downloads file? Not sure how API key is passed to sesamEO?
    - So for now, stick with HDA / destinepyauth
