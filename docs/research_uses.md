# Research uses

E-MIND Data provides task-agnostic historical signals and traceability. DE_CENT and ES_MED are technically validated scientific scenarios; each composes heterogeneous spatial supports and is not a measured physical microgrid. Public archival distribution remains pending, including REData/OMIE rights. [Dataset overview](dataset_overview.md) explains access and interpretation.

| Research use | Possible user-defined experiment |
|---|---|
| Forecasting | Predict demand, weather-related quantities or market prices with chronological evaluation. |
| Temporal representation learning | Learn representations across weather, demand and price series. |
| Anomaly / change detection | Study shifts and unusual patterns, distinguishing provider changes from physical changes. |
| Scheduling | Define assets, tariffs, objectives and admissible information for scheduling experiments. |
| Sizing | Compare user-configured asset sizes under explicit engineering and demand assumptions. |
| Optimisation | Formulate costs, constraints and solvers on a declared experimental plant. |
| EMS / control | Build an energy management experiment with documented observation and actuation semantics. |
| MPC / RL / IL | Supply dynamics, actions, rewards/objectives, forecast assumptions and training/evaluation protocols. |
| Robustness / domain shift | Compare DE_CENT and ES_MED under declared splits, units and spatial supports; study cross-region generalisation, distribution shift and transfer learning using authorised inputs. |

These are uses supported by the data, not promises of implemented tasks or controllers. The repository currently provides **no canonical reward, canonical actions, unique physical plant, definitive Reference Task or complete control benchmark**. E-MIND Env and Reference Tasks are future layers for explicit simulation/interaction and reference experiment contracts. User-defined tasks can be built independently, but must not be presented as official Reference Tasks.

## Available now and future layers

**Available now scientifically:** E-MIND Data and the DE_CENT / ES_MED scenario layer; public archival distribution is pending. **Future:** E-MIND Env, Reference Tasks and Configurator. User-defined tasks supply their own experimental assumptions and implementations. No control algorithms are represented as included software.

## Define an experiment explicitly

Document temporal splits, fitting periods, units, objective, plant configuration and information available at each decision. Fit normalisation on training/reference data only. German or Spanish peninsular system load becomes a dimensionless demand shape before an experiment-selected `P_base_kw` sets microgrid scale; follow the [scaling example](dataset_overview.md#chronological-splits-and-system-load-use). Neither that scale nor the resulting demand is a provider-measured microgrid series.

Historical ERA5 is reanalysis, not a contemporaneously available forecast. UNKNOWN availability does not authorise causal use at valid time. Perfect-foresight scheduling can be studied with an explicitly declared oracle assumption; causal MPC/RL/IL requires an admissible observation/forecast protocol. Fuel reference dates are not exact publication timestamps, tax default is unselected and currency linkage unresolved. Wholesale market prices are not retail tariffs. Derived renewable electrical power and asset dynamics require experimental assumptions.

Keep user-derived artifacts/configurations separate from authoritative Data, record package version/fingerprint and code commit, and retain upstream attribution. Multi-region comparisons require authorised data access and an explicit evaluation protocol; no benchmark results or canonical comparison tasks are supplied.
