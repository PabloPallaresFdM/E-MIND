# Documentation figures

Figures explain the Data architecture and scenario contents. Installation stays in copyable commands.

| File | Purpose / placement |
|---|---|
| `emind_overview.svg` | Authoritative editable vector source; multi-region architecture in the main README |
| `emind_overview.png` | PNG derivative of that same SVG, 1960 × 1512 pixels |
| `de_cent_contents.png` | **DE_CENT scenario overview**, on the DE_CENT page; original 1672 × 941 pixels retained |

The overview restores the owner-supplied draft's paired amber/blue regional cards, simple signal icons, green Data panel and dashed violet future layers using project-authored SVG primitives. DE_CENT and ES_MED have equal visual weight and preserve separate providers, acquisition/harmonisation and identities. The compact Data panel shows the authorised package format: `hourly.parquet` (weather/load/market, 61,368 rows, 1 h UTC interval starts), a native weekly fuel CSV, metadata/provenance and validation/checksums. Exact filenames and paths remain in the release schema. Heterogeneous spatial supports do not imply a measured co-located microgrid. Research is user-defined. The footer scopes the public checkout to code, docs, tests and safe metadata/configuration, without scientific payloads; archival release is NOT_ISSUED and REData/OMIE redistribution is PENDING. Future Env, Reference Tasks and Configurator are dashed and faded. No external fonts, icons, scripts or assets are used.

Edit the SVG, then regenerate the PNG from it; do not edit the PNG independently. From the repository root, with the optional librsvg command-line renderer installed:

```bash
rsvg-convert --width 1960 --height 1512 \
  --output docs/assets/emind_overview.png docs/assets/emind_overview.svg
```

Rendering tools are for figure maintenance, not required for software installation or tests. The DE_CENT PNG remains a scenario-specific illustration supplied by the project owner. Its caption scopes the package and providers to DE_CENT; textual documentation governs native fuel cadence and current issuance status. No separate ES_MED figure is needed: the shared overview and ES_MED page already explain its composition.
