# DeltaTwin Component: Land Surface Temperature (LST)

The Land Surface Temperature (LST) component downloads Landsat Collection 2 Level-2 products from the Destination Earth HDA service, computes LST over a selected NUTS3 area, and exports:

- a GeoTIFF raster (`lst.tif`)
- a combined LST/RGB plot (`lst_plot.png`)

## Workflow

The workflow is defined in [workflow.yml](workflow.yml) and connects component inputs to the LST Plotter model and model outputs to component outputs declared in the manifest.

| **Node** | **Kind** | **Description** |
| -------- | -------- | --------------- |
| user | input | DESP auth username passed to the model as the first CLI argument (`user`). |
| password | input | DESP auth password passed to the model as the second CLI argument (`password`). |
| lst_plotter | model | Python model that searches Landsat products, applies cloud filtering, computes LST, and exports standard output files. |
| plot | output | Output node wired to `outputs.lst-plot` (`lst_plot.png`). |
| raster | output | Output node wired to `outputs.lst-raster` (`lst.tif`). |

## Steps To Build The Component

### Local testing of the model

The model is implemented in [models/lst_plotter/lst_plotter.py](models/lst_plotter/lst_plotter.py) and accepts optional CLI credentials:

```shell
python models/lst_plotter/lst_plotter.py <username> <password>
```

Or, if credentials are already set in environment variables:

```shell
python models/lst_plotter/lst_plotter.py
```

The model writes deterministic files in its working directory:

- `lst.tif`
- `lst_plot.png`

An example plot is shown below:

![LST example: Rome (ITI43)](assets/LST_EXAMPLE_NAPOLI.png)

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
deltatwin component publish -t urban-heat-island -t tutorial 0.0
```

### Run on the service

After publishing, run from the DeltaTwin UI:

1. Login to https://app.deltatwin.destine.eu
2. Select `DeltaTwins`
3. Open your published `lst-plotter` component
4. Click `Run`
5. Fill in `user` and `password`
6. Start the run

The run should produce:

- `lst_plot.png`
- `lst.tif`
