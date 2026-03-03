# DestinE-Platform-Community-UseCases
DestinE-Platform-Community-UseCases

## Pre-commit for notebooks

This repository includes a pre-commit hook that strips output cells from Jupyter notebooks (`*.ipynb`) before commit.

Install and enable it locally:

```bash
pip install pre-commit
pre-commit install
```

Optionally run it on all files once:

```bash
pre-commit run --all-files
```