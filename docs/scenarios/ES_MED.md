# ES_MED

**Status:** technically validated, scientifically closed `CORE_FULL_MARKET`
scenario for complete years **2019–2025**. Its **61,368 hourly UTC intervals**
cover `[2019-01-01T00:00:00Z, 2026-01-01T00:00:00Z)`. Scientific closure does
not clear redistribution: REData and OMIE remain **PENDING**. Public archival
data distribution is **NOT_ISSUED**, not archived, with no E-MIND DOI.

> **ES_MED is a historically grounded composition of heterogeneous open signals.
> It is not a measured co-located physical microgrid.**

| Field | Value |
|---|---|
| Identifier / class | `ES_MED` / `CORE_FULL_MARKET` |
| Period | 2019–2025 |
| Canonical timebase | 1 h UTC, interval-start |
| Weather | ERA5 reference anchor, 39.50° N, −0.75° E |
| Load | Spanish peninsular electricity system, REData Demanda 10297 / 8741 |
| Market | Spanish day-ahead bidding zone, OMIE MARGINALPDBC / MarginalES |
| Fuel | Spain national diesel, European Commission Weekly Oil Bulletin |

## Core and native side component

`hourly.parquet` combines nine weather columns, `load_energy_mwh` and
`day_ahead_price_eur_mwh`, plus `timestamp_utc`. Weather, load and market have
exactly the same ordered 61,368 UTC timestamps; equal row counts alone are
insufficient. These are interval starts, not publication times.

- **Weather:** single ERA5 reference point at 39.50° N, −0.75° E. The surrounding
  3×3 stencil is audit-only. Instantaneous temperature and wind states align at
  `t`; one-hour SSRD for `[t,t+1h)` uses endpoint `t+1h` and is divided by 3600
  for irradiance. Wind speeds derive from vector components. No spatial averaging,
  clipping, interpolation or renewable electrical-power model is applied.
- **Load:** REData peninsular system demand, native hourly MWh. Madrid timestamps
  with explicit offsets are converted to UTC; values remain unscaled.
- **Market:** Spanish OMIE day-ahead price in EUR/MWh, retaining negative values.
  Native delivery resolution changes from 60 to 15 minutes on local **2025-10-01**
  (`2025-09-30T22:00:00Z`). Thereafter each canonical hour is the arithmetic mean
  of four physical quarter-hour prices, calculated with decimal arithmetic.
  The source field `wholesale_market_price_eur_mwh` maps explicitly to
  `day_ahead_price_eur_mwh`. Wholesale prices are not retail tariffs.
- **Fuel:** `components/fuel/fuel_es_2019_2025.csv` retains native weekly reference
  dates and WITH_TAX / WITHOUT_TAX variants: **356 observations each**, from
  2019-01-07 to 2025-12-29. Nine absent nominal Mondays remain absent. The provider
  denominator is 1000 litres; currency linkage is **UNRESOLVED**, tax default
  **UNSELECTED**, and precise availability **UNKNOWN**. No hourly interpolation,
  forward fill, zero-order hold, effective fuel cost or generator model is supplied.

Point weather, peninsular electrical load, bidding-zone market and national fuel
retain independent spatial supports. The weather anchor is neither a common
physical location nor a national climate average.

## Provenance and limitations

The accepted scientific fingerprint is
`d255e6e77c943f7f5d9b0b587e359823f3987b09416e6354f8c4ae33d4153581`.
Safe [scenario metadata](../../metadata/scenario_registry.json) preserves identity,
source lineage and transformations; it contains no scientific payload. Upstream
[Spanish source terms](../../metadata/spanish_sources.json) remain pending;
omitting RAW does not clear derived redistribution rights.

Availability is **UNKNOWN**; valid time does not establish causal availability.
Historical ERA5 is realised reanalysis. Perfect foresight or a causal information
set requires an explicit experiment protocol. No canonical plant, demand scale,
actions, reward or controller is baked in. See [Dataset overview](../dataset_overview.md)
and [Research uses](../research_uses.md) before defining an experiment.
