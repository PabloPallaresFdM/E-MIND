# Contributor instructions

E-MIND is task-agnostic data infrastructure. DE_CENT is currently the only scientifically closed scenario. Preserve provider semantics, temporal/spatial support, source attribution and the distinction between Data and future Env / Reference Tasks.

- Keep accepted scientific artifacts and fingerprints immutable; corrections require explicit versioning and validation.
- Never commit credentials, provider RAW payloads, caches or generated scientific data.
- Do not silently impute, interpolate, rescale demand, select a fuel tax/currency assumption or invent causal availability.
- Fit preprocessing on the declared training/reference interval only.
- Follow README.md and docs/getting_started.md for Python 3.12 installation and the software-only test baseline.
- Run `python -m unittest discover -s tests -v` and `git diff --check` for relevant changes. Expected external-fixture skips are documented in the getting-started guide.
- Changes to code do not authorise data acquisition, publication, archival issuance or a DOI.
