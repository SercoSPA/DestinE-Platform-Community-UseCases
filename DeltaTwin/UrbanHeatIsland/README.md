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

    - Unable to use band extractor - provide bands instead of downloading entire image (only need 3 bands)
    - Landsat level 2 collection: https://sesameo.destine.eu/collections/EO.NASA.DAT.LANDSAT.C2_L2
    - Feed bands into LST calculation code