# Rotterdam Use Cases

## Step 1

Create new virtual environment.
Below is for Mac / Linux (syntax varies depending on OS)
```
python -m venv .venv
source .venv/bin/activate
```
Now install dependencies
```
pip install -r requirements.txt
```
Create ipython kernel
```
python -m ipykernel install --user --name rotterdam --display-name "Python (Rotterdam)"
```