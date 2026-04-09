# DeltaTwin Component: Urban Heat Island (UHI)

The Urban Heat Island component downloads Landsat Collection 2 Level-2 products from the Destination Earth HDA service, computes Land Surface Temperature (LST) over a selected NUTS3 area, and exports:

- a GeoTIFF raster (`uhi_lst.tif`)
- a combined LST/RGB plot (`uhi_lst_plot.png`)

## Workflow

The workflow is defined in [workflow.yml](workflow.yml) and connects component inputs to the UHI model and model outputs to component outputs declared in the manifest.

| **Node** | **Kind** | **Description** |
| -------- | -------- | --------------- |
| user | input | DESP auth username passed to the model as the first CLI argument (`user`). |
| password | input | DESP auth password passed to the model as the second CLI argument (`password`). |
| uhi | model | Python model that searches Landsat products, applies cloud filtering, computes LST, and exports standard output files. |
| plot | output | Output node wired to `outputs.uhi-plot` (`uhi_lst_plot.png`). |
| raster | output | Output node wired to `outputs.uhi-raster` (`uhi_lst.tif`). |

## Steps To Build The Component

### Local testing of the model

The model is implemented in [models/uhi/uhi.py](models/uhi/uhi.py) and accepts optional CLI credentials:

```shell
python models/uhi/uhi.py <username> <password>
```

Or, if credentials are already set in environment variables:

```shell
python models/uhi/uhi.py
```

The model writes deterministic files in its working directory:

- `uhi_lst.tif`
- `uhi_lst_plot.png`

An example plot is shown below:

![LST example: Rome (ITI43)](assets/LST_EXAMLE_BRISTOL.png)

### Build and run locally with DeltaTwin

Inputs are provided through [inputs.json](inputs.json), for example:

```json
{
  "user": {
    "type": "string",
    "value": "johnsmith"
  },
  "password": {
    "type": "string",
    "value": "XXXXXX"
  }
}
```

Two manifest variants are provided:

- `manifest-local.json`: for local runs (`password` is `string`).
- `manifest-remote.json`: for service runs (`password` is `secret`).

Before running, copy the desired manifest to `manifest.json`:

```shell
cp manifest-local.json manifest.json
deltatwin run start_local -i inputs.json
```

If this is your first run, DeltaTwin also builds the Docker image and installs model dependencies.

### Publish to the DeltaTwin service

Before publishing, select the remote manifest:

```shell
cp manifest-remote.json manifest.json
```

If needed, make the component name unique in `manifest.json` (for example by appending your username), then publish:

```shell
deltatwin component publish -t urban-heat -t tutorial 0.1.0
```

### Run on the service

After publishing, run from the DeltaTwin UI:

1. Login to https://app.deltatwin.destine.eu
2. Select `DeltaTwins`
3. Open your published `urban-heat-island` component
4. Click `Run`
5. Fill in `user` and `password`
6. Start the run

The run should produce:

- `uhi_lst_plot.png`
- `uhi_lst.tif`
