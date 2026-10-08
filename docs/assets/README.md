# Branding and documentation figures

Stable branding identifies the project independently of its scenarios and future software. Scientific figures explain the current Data architecture and scenario contents. Installation stays in copyable commands.

| File | Purpose / placement |
|---|---|
| `emind_logo.png` | Stable E-MIND project emblem, 1254 × 1254 pixels; branding only |
| `emind_brand.png` | Stable horizontal project lockup, 1672 × 941 pixels; branding only, centred at 800 px above the README textual title |
| `emind_overview.svg` | Authoritative editable scientific/current architecture overview; separate from branding, may evolve with project content |
| `emind_overview.png` | Scientific overview PNG derivative of that same SVG, 1960 × 1512 pixels |
| `de_cent_contents.png` | **DE_CENT scenario overview**, on the DE_CENT page; original 1672 × 941 pixels retained |

The two branding PNGs were supplied by the project owner and are preserved without recompression. They contain no scenario identifiers/count, release status, payload filenames or future-software claims. The emblem is suitable as a source for future web/favicon/social use; it is not itself a favicon-size export. The lockup carries E-MIND, its official expanded name and the stable research descriptor. Both have transparency; their navy lettering is most legible on a light background.

The horizontal lockup can also be reused as GitHub social preview: PNG, 740,267 bytes, 1672 × 941 pixels, with generous vertical margins around the visible branding. No separate social-preview file is needed. Its central artwork fits a centred 2:1 preview without clipping. Upload is manual: Repository → Settings → General → Social preview → Edit → Upload image → `emind_brand.png`. GitHub settings are not changed by adding this file. See [GitHub social preview guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview).

The overview restores the owner-supplied draft's paired amber/blue regional cards, simple signal icons, green Data panel and dashed violet future layers using project-authored SVG primitives. DE_CENT and ES_MED have equal visual weight and preserve separate providers, acquisition/harmonisation and identities. The compact Data panel shows the authorised package format: `hourly.parquet` (weather/load/market, 61,368 rows, 1 h UTC interval starts), a native weekly fuel CSV, metadata/provenance and validation/checksums. Exact filenames and paths remain in the release schema. Heterogeneous spatial supports do not imply a measured co-located microgrid. Research is user-defined. The footer scopes the public checkout to code, docs, tests and safe metadata/configuration, without scientific payloads; archival release is NOT_ISSUED and REData/OMIE redistribution is PENDING. Future Env, Reference Tasks and Configurator are dashed and faded. No external fonts, icons, scripts or assets are used.

Edit the SVG, then regenerate the PNG from it; do not edit the PNG independently. From the repository root, with the optional librsvg command-line renderer installed:

```bash
rsvg-convert --width 1960 --height 1512 \
  --output docs/assets/emind_overview.png docs/assets/emind_overview.svg
```

Rendering tools are for figure maintenance, not required for software installation or tests. The DE_CENT PNG remains a scenario-specific illustration supplied by the project owner. Its caption scopes the package and providers to DE_CENT; textual documentation governs native fuel cadence and current issuance status. No separate ES_MED figure is needed: the shared overview and ES_MED page already explain its composition.
