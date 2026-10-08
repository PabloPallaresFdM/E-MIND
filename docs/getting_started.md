# Getting started

This guide verifies the software without scientific downloads or provider credentials. Python **3.12** is the supported baseline (validated with 3.12.13 and PyArrow 25.0.1). Run from the repository root. [README](../README.md) explains scope and publication status.

## Prerequisites

You need **Git** and **Python 3.12**, or Conda / Miniforge to create a Python 3.12 environment. A virtual environment keeps this project's Python packages separate from other projects and the system Python. The commands below use a Linux / macOS shell; activate the chosen environment in each new shell.

## Clone and select an environment

The repository is public and supports anonymous HTTPS cloning.

```bash
git clone https://github.com/PabloPallaresFdM/E-MIND.git
cd E-MIND
```

Choose **one** option.

Conda / Miniforge:

```bash
conda create -n emind python=3.12 -y
conda activate emind
```

venv (Linux / macOS):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Install:

```bash
python -m pip install -r scripts/release/requirements.txt
```

Verify:

```bash
python --version
python -c "import pyarrow; print(pyarrow.__version__)"
python -m unittest discover -s tests -v
```

Requirements pin PyArrow, PyYAML and jsonschema; transitive dependencies are not fully locked. The software runs from this checkout, without a wheel installation. Pandas is optional for analysis and is not required by the public tests.

## Understand the result

The validated public baseline is **284 tests: 269 passed, 15 skipped, 0 failures, 0 errors**. Eleven tests require **accepted scientific integration inputs**: accepted artifacts and matching component/acquisition provenance. Four tests require **optional frozen provenance fixtures**: two frozen RAW GRIB inputs and two original acquisition-script fixtures. Expected skips do not indicate installation failure. Synthetic scenario tests run with PyArrow installed.

For a clean software-only run in an environment previously used for scientific validation, clear fixture selectors in the current shell before repeating the test command:

```bash
unset EMIND_DATA_ROOT EMIND_SCENARIO_CANDIDATE
unset EMIND_ERA5_MONTH_RAW EMIND_ERA5_BOUNDARY_RAW
unset EMIND_ERA5_MONTH_SCRIPT EMIND_ERA5_BOUNDARY_SCRIPT
python -m unittest discover -s tests -v
```

A standalone dataset package alone does not supply all accepted integration fixtures. The public integration-input distribution is pending. This baseline validates the software on the validation host; it is not certification of every operating system or machine.

## Troubleshooting

| Symptom | Action |
|---|---|
| Wrong Python version | Check `python --version` and `python -c "import sys; print(sys.executable)"`; activate the Python 3.12 environment. |
| Environment not activated | Repeat `conda activate emind` or `source .venv/bin/activate` in each new shell. Use `python -m pip` so installation follows that interpreter. |
| PyArrow missing or extra Parquet skips | Repeat the requirements installation in the active environment; check `python -c "import pyarrow; print(pyarrow.__version__)"`. |
| `python3.12` / pip / venv unavailable | Install Python 3.12 with your platform's venv support, or use the Conda / Miniforge option. |
| HTTPS clone denied / repository not found | Check the exact public URL above and your network/proxy connection to GitHub. Anonymous HTTPS cloning requires no GitHub authentication. |
| 15 expected skips | No scientific fixtures are configured; installation has succeeded if the remaining tests pass with no failures/errors. More skips require investigation, especially PyArrow skips. |

## Next: inspect metadata, then authorised data

Inspect the [scenario registry](../metadata/scenario_registry.json), source terms and [DE_CENT](scenarios/DE_CENT.md) / [ES_MED](scenarios/ES_MED.md) profiles after the software tests. Git contains software and metadata, not the accepted scientific payload. No public archival download is currently available. When an authorised standalone package is supplied, download it through its explicitly authorised distribution channel; there is no public data URL to use today. Follow [Dataset overview](dataset_overview.md) to verify checksums, inspect timestamps and plan chronological splits. Do not enable integration tests using guessed fixture locations.
