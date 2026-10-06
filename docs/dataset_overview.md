# Dataset overview

E-MIND Data is a reproducible, task-agnostic data layer. A scenario declares how provider signals are composed; it is not a plant or a control task. **DE_CENT (2019–2025) is the only scientifically closed scenario.** Public archival data distribution and DOI issuance are pending; the Git checkout does not contain the scientific payload.

> **DE_CENT is a historically grounded composition of heterogeneous open signals. It is not a measured co-located physical microgrid.**

## Data, scenarios and tasks

**Task-agnostic** means the Data layer does not impose a prediction target, plant, action space, reward or experimental split. **E-MIND Data** provides provider-semantic signals and traceability; a **scenario** specifies their composition, horizon and spatial support. **E-MIND Env** is a future simulation/interaction layer; **Reference Tasks** are future explicit experiment protocols and configurations. Researchers can define their own tasks separately from Data. Europe-wide coverage is an ambition, not a completed dataset claim.

## Signals and spatial support

| Component | Provider / support | Representation |
|---|---|---|
| Weather | ECMWF ERA5 via Copernicus CDS; reference grid point 51.00° N, 10.25° E | Historical reanalysis at one point; hourly aligned weather |
| Load | Bundesnetzagentur / SMARD; German national/system demand | Native quarter-hour MWh summed into hourly `load_energy_mwh` |
| Market | SMARD; DE-LU bidding zone day-ahead wholesale market | `day_ahead_price_eur_mwh`; negative prices retained |
| Fuel | European Commission DG Energy Weekly Oil Bulletin; Germany diesel | Native weekly CSV, both tax variants |

These supports differ; national demand and bidding-zone prices are not local measurements at the ERA5 point. [DE_CENT scenario contents](scenarios/DE_CENT.md) describes exact alignment and limitations.

## Package layout and traceability

For an authorised standalone package, the implemented layout includes:

```text
<package>/
  manifest.json
  identity.json
  checksums.sha256
  metadata/
  scenarios/DE_CENT/
    scenario.json
    hourly.parquet
    metadata.json
    lineage.json
    validation.json
    checksums.sha256
    components/fuel/fuel_de_2019_2025.csv
```

`hourly.parquet` is the authoritative core scenario. Fuel retains native cadence rather than being repeated or interpolated hourly. Global metadata describes sources, licences, acquisition records and contracts; scenario metadata describes fields and supports; lineage and SHA-256 checksums connect artifacts to their origins. Units and availability must be read from metadata, not guessed. The [release schema](release_schema.md) describes the implemented public package contract; the package metadata governs the actual supplied artifact.

## Read and inspect the hourly core

Set `package` to the root of an authorised package. PyArrow is installed by the Quick Start:

```python
from pathlib import Path
import pyarrow.parquet as pq

package = Path("/path/to/E-MIND-package")
core = package / "scenarios" / "DE_CENT" / "hourly.parquet"
table = pq.read_table(core)
print(table.schema)
print(table.column_names)
print(table.num_rows)
times = table["timestamp_utc"]
print(times[0].as_py(), times[-1].as_py())
print(table.slice(0, 5).to_pydict())
```

Expected: **61,368** rows, UTC interval starts from `2019-01-01T00:00:00Z` to `2025-12-31T23:00:00Z`, describing `[t, t+1h)`. Check sorted unique timestamps and hourly continuity before downstream analysis.

Optional Pandas workflow (install with `python -m pip install pandas`):

```python
import pandas as pd

df = pd.read_parquet(core, engine="pyarrow")
df = df.sort_values("timestamp_utc").set_index("timestamp_utc")
assert df.index.is_unique and df.index.is_monotonic_increasing
assert str(df.index.tz) == "UTC"
assert (df.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()
print(df.columns.tolist())
print(df.head())
```

## Verify checksums and package identity

With matching source code and installed requirements, run from the checkout:

```bash
python scripts/release/build_release.py --verify "/path/to/E-MIND-package"
```

This verifies checksums, identities and package contracts without publishing or rebuilding data. A mismatch must be investigated before use. On systems with GNU `sha256sum`, a direct root checksum check is also possible:

```bash
(cd "/path/to/E-MIND-package" && sha256sum -c checksums.sha256)
```

Root checksums include scenario checksum files; the package verifier also checks scenario contents. Checksums establish integrity against a trusted manifest, not independent authenticity of its source.

## Chronological splits and system-load use

Choose training, validation and test windows in temporal order. For example, training 2019–2022, validation 2023, test 2024–2025 is a **user-defined example**, not a canonical Reference Task. Fit normalisation and model parameters only on the declared training/reference period; keep validation/test information out of fitting and document window boundaries.

With the optional Pandas table above, one minimal chronological split is:

```python
train = df.loc[(df.index >= "2019-01-01") & (df.index < "2023-01-01")]
validation = df.loc[(df.index >= "2023-01-01") & (df.index < "2024-01-01")]
test = df.loc[(df.index >= "2024-01-01") & (df.index < "2026-01-01")]
assert train.index.max() < validation.index.min() < test.index.min()
```

The provider load is **German system demand, not microgrid load**. Use the conceptual pattern:

```text
provider system load → dimensionless demand shape → user-configured microgrid load
```

For reference interval R:

```text
mu_R = mean(L_t for t in R)
load_shape_pu(t) = L_t / mu_R
load_power_kw(t) = P_base_kw * load_shape_pu(t)
```

Here `L_t` is provider hourly-average load power; the core stores hourly energy in `load_energy_mwh`. Dividing by the one-hour duration gives MW (numerically equal for these intervals). Compute `mu_R` only on the declared reference/training interval and apply the same scale to later periods. Do not normalise each year independently or use future information to scale earlier demand. `P_base_kw` is an experimental choice, not a provider property.

Using the optional Pandas table above:

```python
system_power_mw = df["load_energy_mwh"] / 1.0  # hourly MWh / 1 hour
reference = system_power_mw.loc["2019-01-01":"2022-12-31"]
mu_R = reference.mean()
assert pd.notna(mu_R) and mu_R > 0
load_shape_pu = system_power_mw / mu_R
P_base_kw = 100.0  # illustrative user choice; no canonical plant implied
load_power_kw = P_base_kw * load_shape_pu
```

Keep these experiment-derived series separate from authoritative Data. See [Research uses](research_uses.md) for information-set and control limitations.
