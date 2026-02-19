# DeltaTwin Component: Fire Risk Plotter

The Fire Risk plotter is a component designed to generate short-term forecasts of fire risk–related indicators. It retrieves the most recent 5-day forecast from the [Fire Risk Map - Released Energy Based - MSG](https://data.destination-earth.eu/data-portfolio/EO.EUM.DAT.MSG.LSA-FRM) via the Harmonized Data Access (HDA) service and visualizes the outputs as an animated GIF. The underlying product integrates numerical weather prediction (NWP) data with remotely sensed Fire Radiative Power (FRP) observations to estimate fire danger conditions. The forecasts include 24h, 48h, 72h, 96h, and 120h lead times and provide: (i) fire risk levels categorized into five classes and the probability of ignitions exceeding 2000 GJ of released energy over Southern Europe, and (ii) the Fire Weather Index (FWI) and its components over the full MSG disk. In this implementation, the analysis and visualization are spatially constrained to the Italian peninsula.

## Workflow

The component's workflow is the following:

![workflow](assets/workflow.png)

The workflow is defined in [workflow.yml](workflow.yml) and wires workflow inputs to the model and then to the component output declared in [manifest.json](manifest.json).

The workflow defines the graph that connects inputs to the model and then to the output:

- `user` and `password` nodes reference `inputs.user` and `inputs.password`.
- The `fire-risk-plotter` node references `models.fire-risk-plotter`.
- The `plot` node references `outputs.firerisk-plot`.

Edges connect the inputs to the model’s input ports and connect the model’s output port to the output node. The important wiring is:

```yaml
  - from:
      id: fire-risk-plotter
      port: firerisk-plot
    to:
      id: plot
```

This matches the output name declared in the model definition and makes the generated GIF available as the component output.

| **Node** | **Kind** | **Description** |
| -------- | -------- | --------------- |
| user | input | DESP auth username passed to the model as the first CLI argument (`user`). |
| password | input | DESP auth password passed to the model as the second CLI argument (`password`). |
| fire-risk-plotter | model | The Python model that searches the HDA STAC catalog, downloads MSG fire risk products (5-day forecast), and generates an animated GIF with individual PNG frames. |
| plot | output | The output node wired to `outputs.firerisk-plot`, which captures the animated GIF produced by the model. |

## Steps to build the component

### Local testing of the model

The model is implemented in [models/fire-risk-plotter/fire-risk-plotter.py](models/fire-risk-plotter/fire-risk-plotter.py) and expects two CLI arguments.

[models/fire-risk-plotter/fire-risk-plotter.py](models/fire-risk-plotter/fire-risk-plotter.py) expects two CLI arguments: `user` and `password`. These are used to set `DESPAUTH_USER` and `DESPAUTH_PASSWORD` before calling `destinepyauth.get_token()`.

To install the Python dependencies locally:

```shell
pip install -r models/fire-risk-plotter/requirements.txt
```

To run the model locally:

```shell
python models/fire-risk-plotter/fire-risk-plotter.py <username> <password>
```
If the run is successful, the logs will show individual PNG frames being created and finally:
```
INFO FWI: GIF saved: firerisk_forecast.gif
```

### Build the DeltaTwin component and run it locally

DESP auth credentials are provided as input parameters in the format of a JSON file, as below.
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
To ease local and remote usages in this tutorial, 2 manifest files are provided:

- `manifest-local.json`: the one to be used for local run.
- `manifest-remote.json`: the one to be used to publish the component to the service.

The main difference between them is the type of the `password` parameter. For local runs, the "secret" type is not permitted, so we use "string" (as above).

To run it locally, you must first copy the relevant manifest file to the main `manifest.json` file required for the build. For example, using the following command:

```shell
cp manifest-local.json manifest.json
deltatwin run start_local -i inputs.json
```

If this is the first time running the command, it will also build the component’s Docker image. This process may take several minutes because the model requires GDAL packages and Python libraries to be installed in the image (see for instance manifest sections `models/[...]/pipRequirements` and `models/[...]/aptRequirements`).

This will print several log lines related to loading dependencies and building the image, the orchestrator executing workflow's steps, and finally, the status:

```
Status:RunStatus.SUCCESS
Inputs:
     Input name | Type   | Value/Basename                                                                                                         
    ------------+--------+----------------                                                                                                        
     user       | string | johnsmith                                                                                                              
     password   | string | XXXXXX                                                                                                           
Outputs:
     Output name | Type | Value/Basename                                                                                                          
    -------------+------+---------------------------------------------------------------------------                                              
  firerisk-plot | Data | /path/to/home/.deltatwin/runs/<run_id>/fire-risk-plotter/firerisk_forecast.gif 
```
The outputs of the component are stored in a temporary directory associated to the local run ID.

The resulting file `firerisk_forecast.gif` contains an animated 5-day forecast with frames similar to the example plot below.

![plot](assets/fwi_forecast_example.gif)

### Publish component to the DeltaTwin service

Prior to publish the component to the DeltaTwin service, the relevant manifest file shall be used as main `manifest.json` file required for the build, using the following command:

```shell
cp manifest-remote.json manifest.json
```

This manifest references UUIDs of sample resources that are publicly available on the service platform.

> ⚠️ **Warning:** A current limitation of the DeltaTwin service is that all components must have a unique name across all users. Therefore, before publishing this example, you should rename the component by adding a suffix containing your username or another unique identifier, to avoid a duplicate name error.
>
> To do so, edit the `manifest.json` file by changing the value of the "name" attribute, specifically the suffix part (e.g., replacing 'myname' with your own identifier):
>
> ```json
> {
>   "name": "fire-risk-plotter-myname",
>   [...]
> }
> ```

Then, to publish the component to the DeltaTwin service, the following command specifies the version '0.1.0' with the 'fire-risk' and 'tutorial' tags.

```shell
deltatwin component publish -t fire-risk -t tutorial 0.1.0
```

This will print various log lines as the image is built and its layers are pushed to the service repository. The process should complete successfully with the following message:

```log
INFO: The DeltaTwin fire-risk-plotter-myname-0.1.0, has been released.
```

By default, the component is published with private visibility.
To list private components, use the following:

```shell
deltatwin component list -v private
```

To get more information about this specific component, use:

```shell
deltatwin component get fire-risk-plotter-myname
```

### Run the component on the service

There is a known issue: the CLI cannot decrypt `secret` values from `inputs.json`. If you set `type: "secret"` in the inputs file, the run fails with a `fromhex()` error. **Therefore, the fire-risk-plotter model cannot be run on the service using the CLI**. For service runs, publish the component and pass the secret via the UI, which handles encryption for `secret` inputs.

Once the component has been published to the service, it is ready to use. To run the component using the platform's computing resources, navigate to the [DeltaTwin UI](https://app.deltatwin.destine.eu) and follow these steps:
- login
- select `DeltaTwins` from the menu on the left
- select the component you just created `fire-risk-plotter-myname`
- select run in the top right
- a menu should appear as shown below for you to input your DESP credentials
- press `Start Run`

![plot](assets/run_screenshot.png)

You can monitor the status of the run by selecting `Runs` from the left hand menu.
