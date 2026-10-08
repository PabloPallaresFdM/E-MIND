# Reproducibility and software scope

The checkout contains source code, synthetic regression tests, scientific request configurations and safe metadata. It contains no accepted scientific payload or public integration-fixture distribution. The [getting-started guide](getting_started.md) verifies software without provider calls or scientific data.

## Retained scientific capabilities

The provider adapters validate immutable input bytes, parse native labels, reconstruct physical UTC intervals and preserve energy/price semantics. Harmonisation modules cover lossless load/market aggregation, mixed native market resolution, native fuel extraction, weather validity/accumulation alignment and information-loss diagnostics. Scenario assembly and release verification retain independent accepted DE_CENT and ES_MED hashes and fingerprints. Multi-scenario packaging validates region identity, exact hourly support and native side components separately. The accepted-input adapters require external matching inputs; no provider payload is bundled.

Tests with historical module names use synthetic fixtures. Those names and artifact identifiers are compatibility/provenance labels; users do not need the project's development history to install or read the data. The obsolete combined execution wrapper is replaced in this distribution by its byte-equivalent serialization functions; hourly construction is exposed as its unchanged scientific function, without local operations or private report dependencies.

## Explicit operational configuration

Scientific processing requires already accepted, matching inputs and new output destinations. It is separate from software installation. The load/market extension wrapper requires an absolute `EMIND_INTERIM_ROOT`; output must be a new directory below it. The optional controlled-download tool requires an absolute `EMIND_DATA_ROOT` and an explicitly selected `EMIND_EXECUTION_HOST`, which must match the current host. No private execution configuration is supplied and no acquisition is performed by the public tests. Provider host allowlisting, semantic freeze, source scope, immutable bytes and no-overwrite checks still apply.

Read-only audit output uses the actual Git checkout commit when available, or null in an unversioned source tree. It never fabricates a code identity. Package building requires a real Git checkout for source provenance; source snapshots support software tests and verification of an authorised standalone package.

## Limits

A full accepted-data rebuild has additional matching component, acquisition and provenance requirements. Expected software-test skips describe their absence, not a successful scientific rebuild. Historical initial-capture registries are not a complete current release manifest. A release package's own contracts, checksums, source lineage and versions govern use. Do not acquire data or infer a public release merely because acquisition/reproduction code is present.
