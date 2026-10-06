# Release schema and verification

This is the implemented **local release-candidate** contract, not a public archival release announcement. DE_CENT is the only scientifically closed scenario; candidate version `0.1.0-rc.1`, schema `0.1.0`, publication status `NOT_ISSUED`, DOI null and archival status `not_archived`.

## Layout

```text
<package>/
  README.md
  CITATION.cff
  LICENSE
  LICENSE-METADATA
  VERSION
  manifest.json
  identity.json
  validation.json
  checksums.sha256
  metadata/
    dataset.json
    source_registry.json
    snapshot_registry.json
    scenario_registry.json
    schema.json
    licenses.json
    acquisition/<configuration-id>.json
  scenarios/DE_CENT/
    scenario.json
    hourly.parquet
    metadata.json
    lineage.json
    validation.json
    checksums.sha256
    components/fuel/fuel_de_2019_2025.csv
```

Only validated, rights-cleared scenarios may be included. Provider RAW payloads are omitted; safe request parameters, snapshot identifiers and expected SHA-256 hashes retain acquisition traceability. This software checkout contains no accepted data or public integration-fixture distribution.

## Authoritative objects and time

DE_CENT `hourly.parquet` retains accepted bytes: 61,368 rows in `[2019-01-01T00:00:00Z, 2026-01-01T00:00:00Z)`. `timestamp_utc` is `timestamp[us, tz=UTC]`, sorted unique hourly interval starts; eleven numeric columns are float64, non-null and finite for this scenario. Exact order and units are declared in scenario metadata.

Weather uses a single ERA5 reference point, system load uses summed quarter-hour MWh, and market uses DE-LU EUR/MWh. From local 2025-10-01, hourly prices are means of four quarter-hour prices. Native fuel remains a separate weekly CSV with both tax variants, no interpolation, unresolved currency linkage, unselected tax default and unknown precise availability. See [DE_CENT](scenarios/DE_CENT.md) for temporal and spatial interpretation.

A scenario is a heterogeneous composition, not a measured co-located microgrid. No demand scaling, plant, actions, reward or causal controller information set belongs to this contract. Valid time does not imply available-at time.

## Metadata and identities

Global metadata records versions, sources, snapshot/acquisition references, scenario registry, schemas and source-specific terms. Scenario descriptors record horizon, composition and spatial supports; metadata records storage/columns; lineage records derivations; validation records verification results. Identifiers and accepted hashes remain unchanged when host-specific execution records are omitted.

Scientific composition, sanitized public composition and release identity are distinct named identities. The release identity covers the authoritative package inventory and publication metadata; it must not be confused with the accepted scientific fingerprint. Portable relative POSIX paths, deterministic JSON serialization and SHA-256 checksums support verification. Root checksums exclude themselves and include scenario checksum files; scenario checksums exclude themselves. Changing a package's authoritative bytes requires a newly reviewed package identity; retained candidates are not edited in place.

## Verify an authorised standalone package

From the matching source checkout after installing the public requirements:

```bash
python scripts/release/build_release.py --verify "/path/to/E-MIND-package"
```

Verification checks checksums, identities, inventories, contracts and local-candidate publication/licensing gates. It does not build, download, upload, archive or issue a DOI. A standalone package does not enable accepted-source integration tests without their matching external fixtures.

The builder supports gated local `--rc` and explicitly opted-in `--allow-dev` builds into new destinations using existing accepted inputs. Build provenance requires a Git checkout; an unversioned source snapshot can run tests and package verification but cannot claim a source commit for a build.

## Versioning and future capabilities

Dataset and schema versions are separate. Future additions must declare actual variables, currencies, spatial support, cadence and missing/absent capabilities rather than silently applying DE_CENT assumptions to all regions. Additional regions and experiment protocols need their own validation. E-MIND Env, Reference Tasks and Configurator are future layers.

Project code is [MIT](../LICENSE); original project metadata/documentation are [CC BY 4.0](../LICENSE-METADATA); upstream terms remain in [metadata/licenses.json](../metadata/licenses.json). [CITATION.cff](../CITATION.cff) describes the unissued local candidate; no E-MIND DOI is assigned.
