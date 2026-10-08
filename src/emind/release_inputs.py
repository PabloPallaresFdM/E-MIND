"""Allowlisted DE_CENT adapter from frozen A7 and provider evidence to public spec.

Scientific scenario specifics belong here, outside the generic release builder.
"""
import csv
from pathlib import Path
from emind.release import SCHEMA_VERSION, CANONICAL_RULE, CONTRACTS, read_json
from emind.scenario import fingerprint, sha256

ACCEPTED = '51cb5960e78676211eff2a52353fcb666ab1b7ea09ad6fd2ff714199cf2e76df'
HOURLY = 'e765ce62b660f54455bb3a31da0d7aefc3172b9be274e0b33f0922a16cf1cf7f'
FUEL = 'fc15a08799d85fd667311c74b15ae0efac20f2bf2d14d1772cf5998f18786d91'


def scenario_release_entry(data_root, candidate, component_sources):
    """Prepare one validated region for the existing generic package builder.

    This is a schema adapter, not publication clearance. Dataset source/license
    registries and publication gates still belong to the separately reviewed
    release specification. Unique artifact IDs allow multiple regions together.
    """
    import pyarrow.parquet as pq
    original = read_json(Path(candidate) / 'scenario.json')
    if set(component_sources) != set(original['components']) or any(not ids for ids in component_sources.values()):
        raise ValueError('Explicit provider source identities required for every component')
    validation = read_json(Path(candidate) / 'validation.json')
    identity = fingerprint(original)
    if validation['status'] != 'PASS' or validation['scenario_fingerprint'] != identity:
        raise ValueError('Candidate identity/validation mismatch')
    sid = original['scenario_id']
    hourly = Path(candidate) / 'hourly.parquet'
    fuel = Path(data_root) / original['components']['fuel']['artifact']
    if sha256(hourly) != validation['hourly_sha256'] or sha256(fuel) != original['components']['fuel']['sha256']:
        raise ValueError('Candidate artifact mismatch')
    with fuel.open(newline='') as f:
        rows = list(csv.DictReader(f))
    components = {}
    lineage = []
    for name, c in original['components'].items():
        p = select(c, ['provider', 'spatial_support', 'native_temporal_resolution', 'canonical_resolution',
            'available_at', 'causal_availability_claim', 'transformation', 'anchor', 'tax_variants',
            'tax_default', 'currency', 'hourly_resampling', 'interpolation', 'forward_fill',
            'zero_order_hold', 'redistribution', 'raw_policy', 'column_mapping', 'native_timezone',
            'transition_delivery_day', 'post_change_note', 'country', 'country_code',
            'observations_per_variant', 'series_id', 'geo_id', 'unit', 'product', 'provider_field',
            'boundary_sha256', 'valid_time_is_available_at'])
        p.update(component_id=name, accepted_artifact_sha256=c['sha256'],
            source_ids=component_sources[name], snapshot_ids=[sid.lower()+'-'+name+'-accepted'],
            transformation_id=sid.lower()+'-'+name+'-transform',
            authoritative_path='components/fuel/'+fuel.name if name=='fuel' else 'hourly.parquet')
        if name == 'fuel':
            p.update(semantic_kind='native_weekly_economic_bulletin', format='CSV',
                columns=[dict(name=k, type='float64' if k in {'source_value','excel_date_serial'} else 'integer' if k=='source_row_index' else 'string',
                    nullable=any(r[k]=='' for r in rows)) for k in rows[0]],
                units={'source_value':'unconverted provider value per 1000 l; currency UNRESOLVED'},
                key_columns=['reference_date','tax_variant'], ordering='Accepted row order; ascending dates per tax variant',
                valid_time_representation='Date-only reference, not publication/availability',
                missingness_policy='Source gaps retained; no interpolation, ZOH or forward fill')
        components[name] = p
        lineage.append(dict(component_id=name, accepted_artifact_sha256=c['sha256'],
            release_artifact_path=p['authoritative_path'], source_ids=p['source_ids'], snapshot_ids=p['snapshot_ids'],
            transformation_id=p['transformation_id'], transformation=p['transformation']))
    scenario = select(original, ['scenario_id','scenario_class','horizon','hourly_columns',
        'interval_semantics','historical_colocated_microgrid','co_location_claim','technical_limitations','assembly_transformation'])
    scenario.update(rows=original['horizon']['rows'], canonical_timebase='1 hour UTC',
        hourly_artifact='hourly.parquet', scenario_fingerprint=identity, accepted_composition_fingerprint=identity,
        accepted_composition_scheme=original['schema_version'], components=components,
        artifact_status='LOCAL_DRY_RUN_NOT_PUBLIC_RELEASE')
    ids = {k:sid.lower()+'-'+k for k in ('descriptor','hourly','fuel')}
    entry = dict(scenario_id=sid, accepted_descriptor_id=ids['descriptor'], scenario=scenario,
        metadata=dict(scenario_id=sid, hourly_columns=[dict(name=f.name,
            type=str(f.type) if f.name=='timestamp_utc' else 'float64', nullable=False,
            unit='UTC' if f.name=='timestamp_utc' else 'MWh' if f.name=='load_energy_mwh' else
                'EUR/MWh' if f.name=='day_ahead_price_eur_mwh' else 'K' if f.name=='air_temperature_2m_k' else
                'J/m2' if f.name=='surface_solar_radiation_downwards_j_m2' else 'W/m2' if f.name=='surface_solar_irradiance_w_m2' else 'm/s')
            for f in pq.read_schema(hourly)],
            side_components=[components['fuel']]),
        lineage=dict(scenario_id=sid, components=lineage, accepted_composition_fingerprint=identity,
            assembly_transformation=original['assembly_transformation']),
        artifacts=[dict(artifact_id=ids['hourly'],path='hourly.parquet',sha256=sha256(hourly),role='authoritative_hourly_core'),
            dict(artifact_id=ids['fuel'],path='components/fuel/'+fuel.name,sha256=sha256(fuel),role='authoritative_native_component')])
    return entry, {ids['descriptor']:Path(candidate)/'scenario.json', ids['hourly']:hourly, ids['fuel']:fuel}


def select(value, keys):
    return {k: value[k] for k in keys if k in value}


def de_cent_spec(repository, data_root, candidate):
    repository, data_root, candidate = map(Path, (repository, data_root, candidate))
    original = read_json(candidate / 'scenario.json')
    validation = read_json(candidate / 'validation.json')
    if fingerprint(original) != ACCEPTED or validation['scenario_fingerprint'] != ACCEPTED or validation['status'] != 'PASS':
        raise ValueError('Frozen A7 identity/validation mismatch')
    if sha256(candidate / 'hourly.parquet') != HOURLY:
        raise ValueError('Frozen hourly bytes mismatch')
    sources = read_json(repository / 'metadata/source_registry_v0.1.yaml')['sources']
    weather_ids = sorted(s['source_id'] for s in sources if s['source_id'].startswith('era5_'))
    component_sources = {'weather': weather_ids, 'load': ['de_smard_electrical_load'],
                         'market': ['de_smard_day_ahead'], 'fuel': ['eu_ec_weekly_oil_bulletin_diesel']}
    required_ids = set(sum(component_sources.values(), []))
    public_sources = []
    licenses = []
    for s in sources:
        sid = s['source_id']
        if sid not in required_ids:
            continue
        p = select(s, ['source_id', 'domain', 'provider', 'variable_name', 'native_unit',
                       'native_timezone', 'status', 'source_type'])
        smard = sid.startswith('de_smard_'); weather = sid in weather_ids
        p.update(publication_status='CLEARED_RECORDED_SMARD_TERMS' if smard else 'PENDING_REVIEW',
                 redistribution_class='R2', raw_bundled=False,
                 license_status='CONFIRMED_RECORDED' if smard else 'PENDING_REVIEW')
        if weather:
            p.update(dataset_name='reanalysis-era5-single-levels', source_doi='10.24381/cds.adbb2d47',
                     distribution='Copernicus Climate Data Store / C3S', native_frequency='hourly',
                     historical_registry_product='Superseded time-series product; source ID retained under D-0037',
                     native_unit=next(v['unit'] for v in read_json(repository / 'configs/weather/de_cent_era5.yaml')['variables']
                                      if v['provider_name'] == sid.removeprefix('era5_timeseries_')))
        elif smard:
            p.update(select(s, ['dataset_name', 'licence_name', 'licence_url', 'api_or_download_endpoint']))
            p['attribution'] = 'Bundesnetzagentur | SMARD.de; E-MIND hourly aggregation/window composition'
        else:
            p.update(dataset_name='European Commission Weekly Oil Bulletin',
                     licence_url=s['licence_url'],
                     qualified_reuse_basis='Coordinator-qualified Commission reuse / CC BY 4.0 unless specifically indicated otherwise',
                     attribution='European Commission Weekly Oil Bulletin; E-MIND Germany diesel extraction/window extension')
        public_sources.append(p)
        licenses.append({'source_id': sid, 'status': p['license_status'],
                         'publication_status': p['publication_status'],
                         'recorded_basis': p.get('licence_name', p.get('qualified_reuse_basis', 'Exact accepted ERA5 product terms pending; older product clearance not transferred')),
                         'attribution': p.get('attribution', 'ECMWF / Copernicus CDS / C3S; E-MIND reference-point extraction and derivations'),
                         'raw_class': 'R2'})
    snapshots = []; acquisitions = {}; component_snapshot_ids = {k: [] for k in component_sources}
    # All accepted load/market/fuel captures (including historical UTC tails), not internal audits.
    for domain in ['load', 'market', 'fuel']:
        for path in sorted((data_root / f'raw/DE_CENT/{domain}').rglob('*.manifest.json')):
            m = read_json(path)
            if m['source_id'] not in required_ids:
                continue
            sid = m['snapshot_id']; config_id = sid
            request = select(m, ['url', 'method', 'format', 'original_filename'])
            if 'request_body_utf8' in m:
                import json
                request['request_body'] = json.loads(m['request_body_utf8'])
            acquisitions[f'acquisition/{config_id}.json'] = {'schema_version': SCHEMA_VERSION,
                'acquisition_config_id': config_id, 'source_ids': [m['source_id']], 'request': request,
                'expected_sha256': m['sha256'], 'redistribution_class': 'R2',
                'raw_bundled': False, 'license_status': 'CONFIRMED_RECORDED' if domain != 'fuel' else 'PENDING_REVIEW'}
            snapshots.append(dict(select(m, ['snapshot_id', 'source_id', 'provider', 'retrieved_at_utc',
                                             'original_filename', 'size_bytes', 'sha256', 'content_type']),
                                  source_ids=[m['source_id']], redistribution_class='R2', raw_bundled=False,
                                  acquisition_config_id=config_id, acquisition_config_path=f'metadata/acquisition/{config_id}.json',
                                  license_status='CONFIRMED_RECORDED' if domain != 'fuel' else 'PENDING_REVIEW'))
            component_snapshot_ids[domain].append(sid)
    weather = original['components']['weather']['lineage']
    for obj in weather['raw_objects']:
        raw = data_root / obj['raw_path']; sid = raw.stem
        if '2026_boundary' in raw.parts:
            prov = read_json(raw.with_suffix('.provenance.json'))
            request = prov['exact_request']
        else:
            month = '_'.join(raw.stem.split('_')[-2:])
            prov = read_json(raw.parent / f'audit/provenance_{month}.json')
            request = prov['request']
        if prov['sha256'] != obj['sha256']:
            raise ValueError('Weather snapshot identity mismatch')
        acquisitions[f'acquisition/{sid}.json'] = {'schema_version': SCHEMA_VERSION,
            'acquisition_config_id': sid, 'source_ids': weather_ids, 'dataset': 'reanalysis-era5-single-levels',
            'request': request, 'decimal_request_parameters': 'Normalized decimal strings; convert to numeric API values when reacquiring',
            'expected_sha256': obj['sha256'], 'redistribution_class': 'R2', 'raw_bundled': False, 'license_status': 'PENDING_REVIEW'}
        snapshots.append({'snapshot_id': sid, 'snapshot_id_scheme': 'accepted-provider-filename-stem',
            'source_ids': weather_ids, 'provider': 'ECMWF / CDS / C3S',
            'retrieved_at_utc': prov['retrieval_completed_utc'], 'original_filename': raw.name,
            'sha256': obj['sha256'], 'size_bytes': obj['bytes'], 'redistribution_class': 'R2', 'raw_bundled': False,
            'license_status': 'PENDING_REVIEW', 'acquisition_config_id': sid,
            'acquisition_config_path': f'metadata/acquisition/{sid}.json'})
        component_snapshot_ids['weather'].append(sid)
    components = {}; lineage = []
    for name, c in sorted(original['components'].items()):
        p = select(c, ['provider', 'spatial_support', 'native_temporal_resolution', 'canonical_resolution',
                       'available_at', 'causal_availability_claim', 'transformation', 'anchor',
                       'valid_time_is_available_at', 'tax_variants', 'tax_default', 'currency',
                       'interpolation', 'hourly_resampling', 'observations_per_variant', 'post_change_note'])
        p.update(component_id=name, source_ids=component_sources[name],
                 snapshot_ids=sorted(component_snapshot_ids[name]), accepted_artifact_id=f'{name}-accepted',
                 accepted_artifact_sha256=c['sha256'], transformation_id=f'{name}-accepted-transform')
        if name == 'fuel':
            p.update(authoritative_path='components/fuel/fuel_de_2019_2025.csv', format='CSV',
                     semantic_kind='native_weekly_economic_bulletin', key_columns=['reference_date', 'tax_variant'],
                     ordering='Accepted CSV row order; dates ascending per tax variant',
                     valid_time_representation='Date-only Monday consumer-price reference; not publication or availability',
                     missingness_policy='Accepted irregular weeks retained; no interpolation',
                     reference_date_support={'first': '2019-01-07', 'last': '2025-12-29'},
                     units={'source_value': 'unconverted provider value', 'source_volume_unit': '1000 litres', 'currency': 'UNRESOLVED'})
        else:
            p['authoritative_path'] = 'hourly.parquet'
        components[name] = p
        lineage.append({'component_id': name, 'accepted_artifact_id': p['accepted_artifact_id'],
                        'accepted_artifact_sha256': c['sha256'], 'release_artifact_path': p['authoritative_path'],
                        'source_ids': p['source_ids'], 'snapshot_ids': p['snapshot_ids'],
                        'transformation_id': p['transformation_id'], 'transformation': c['transformation']})
    fuel_path = data_root / original['components']['fuel']['artifact']
    if sha256(fuel_path) != FUEL:
        raise ValueError('Accepted fuel bytes mismatch')
    with fuel_path.open(newline='') as f:
        fuel_rows = list(csv.DictReader(f)); fuel_columns = list(fuel_rows[0])
    # Explicit fields, native keys and units; source-cell provenance remains in CSV.
    side_columns = []
    for name in fuel_columns:
        dtype = 'float64' if name in {'source_value', 'excel_date_serial'} else 'integer' if name == 'source_row_index' else 'string'
        side_columns.append({'name': name, 'type': dtype, 'nullable': any(r[name] == '' for r in fuel_rows),
                             'unit': 'provider value per 1000 litres; currency UNRESOLVED' if name == 'source_value' else None})
    components['fuel']['columns'] = side_columns
    hourly_columns = [{'name': 'timestamp_utc', 'type': 'timestamp[us, tz=UTC]', 'nullable': False,
                       'unit': 'UTC', 'semantics': 'interval_start'}]
    for name in original['hourly_columns'][1:]:
        semantic = weather['variable_semantics'].get(name, {'canonical_units': 'MWh' if name == 'load_energy_mwh' else 'EUR/MWh',
                                                            'temporal_semantics': components['load' if name == 'load_energy_mwh' else 'market']['transformation']})
        hourly_columns.append(dict(name=name, type='float64', nullable=False, **semantic))
    scenario = select(original, ['scenario_id', 'scenario_class', 'horizon', 'interval_semantics',
                                'historical_colocated_microgrid', 'co_location_claim', 'assembly_transformation', 'hourly_columns'])
    scenario.update(scenario_fingerprint=ACCEPTED, accepted_composition_fingerprint=ACCEPTED,
                    accepted_composition_scheme=original['schema_version'], interpretation=original['scenario_interpretation'],
                    canonical_timebase='1 hour UTC', rows=61368, hourly_artifact='hourly.parquet', hourly_sha256=HOURLY,
                    components=components, artifact_status='LOCAL_DRY_RUN_NOT_PUBLIC_RELEASE')
    metadata = dict(acquisitions)
    metadata.update({'source_registry.json': {'schema_version': SCHEMA_VERSION, 'sources': sorted(public_sources, key=lambda s: s['source_id'])},
        'snapshot_registry.json': {'schema_version': SCHEMA_VERSION, 'snapshots': sorted(snapshots, key=lambda s: s['snapshot_id'])},
        'licenses.json': {'schema_version': SCHEMA_VERSION, 'code_license': 'PENDING_OWNER_SELECTION',
                          'metadata_license': 'PENDING_OWNER_SELECTION', 'publication_status': 'PENDING_REVIEW',
                          'sources': sorted(licenses, key=lambda s: s['source_id'])},
        'dataset.json': {'schema_version': SCHEMA_VERSION, 'dataset_name': 'E-MIND', 'release_version': '0.0.0-dev',
                         'title': 'European Intelligent Microgrid Energy Decision Benchmark', 'build_mode': 'dry-run',
                         'doi': None, 'citation_status': 'PENDING', 'publication_status': 'PENDING_REVIEW',
                         'scope': 'Data-layer composition only; DE_CENT only'},
        'schema.json': {'schema_version': SCHEMA_VERSION, 'contract_language': 'required-field-types-v1',
                        'documents': CONTRACTS, 'canonical_serialization': CANONICAL_RULE,
                        'side_component_required_fields': ['component_id', 'semantic_kind', 'authoritative_path', 'accepted_artifact_sha256',
                            'format', 'columns', 'units', 'key_columns', 'ordering', 'spatial_support', 'native_temporal_resolution',
                            'valid_time_representation', 'available_at', 'source_ids', 'snapshot_ids', 'transformation_id', 'missingness_policy']}})
    spec = {'dataset_name': 'E-MIND', 'release_version': '0.0.0-dev', 'metadata': metadata,
            'publication_items': ['ERA5 exact-product RAW/canonical rights', 'EC exact-workbook exceptions/third-party rights',
                'owner code/metadata license selection', 'formal citation authorship/date',
                'public version and archive DOI', 'cross-platform certification'],
            'text_files': {'README.md': PUBLIC_README, 'LICENSE': LICENSE_SUMMARY},
            'scenarios': [{'scenario_id': 'DE_CENT', 'accepted_descriptor_id': 'accepted-scenario', 'scenario': scenario,
                'metadata': {'scenario_id': 'DE_CENT', 'hourly_columns': hourly_columns, 'side_components': [components['fuel']],
                             'storage': {'hourly': 'Parquet; accepted bytes', 'side': 'Native accepted CSV; no reserialization'}},
                'lineage': {'scenario_id': 'DE_CENT', 'release_reference': '../../manifest.json',
                            'accepted_composition_fingerprint': ACCEPTED, 'components': lineage,
                            'assembly_transformation': original['assembly_transformation'],
                            'transformation_code_commit_reference': '../../identity.json'},
                'artifacts': [{'artifact_id': 'hourly', 'path': 'hourly.parquet', 'sha256': HOURLY, 'role': 'authoritative_hourly_core'},
                              {'artifact_id': 'native-side', 'path': 'components/fuel/fuel_de_2019_2025.csv', 'sha256': FUEL, 'role': 'authoritative_native_component'}]}]}
    return spec, {'accepted-scenario': candidate / 'scenario.json', 'hourly': candidate / 'hourly.parquet', 'native-side': fuel_path}


LICENSE_SUMMARY = '''E-MIND LOCAL DRY-RUN LICENSING SUMMARY — NOT A PUBLIC LICENSE GRANT
Data sources have heterogeneous terms. Recorded SMARD terms: CC BY 4.0,
Bundesnetzagentur | SMARD.de attribution, with E-MIND aggregation indicated.
ERA5 exact accepted-product terms and EC exact-workbook exceptions/third-party
rights remain PENDING_REVIEW. The qualified Commission reuse basis is retained.
Code and original metadata licenses await owner selection. See metadata/licenses.json.
Omitting RAW does not clear canonical redistribution. Public issuance is blocked.
'''
PUBLIC_README = '''# E-MIND 0.0.0-dev — local dry-run only
This is an offline Data-layer packaging audit, not an issued public release.
Only validated DE_CENT is included. CITATION.cff is omitted: formal authorship,
publication date, public version, DOI and overall licensing remain PENDING.
No DOI is assigned; source DOIs identify providers, not this dataset.

DE_CENT composes heterogeneous open signals over complete calendar years 2019–2025:
61,368 hourly UTC interval starts in [2019-01-01, 2026-01-01).
Weather: ERA5 atmospheric reanalysis at 51.00 N, 10.25 E, not local measured weather
or national-average weather. Load: original German national/system grid demand.
Market: DE-LU bidding zone. Fuel: Germany national weekly diesel bulletin.
These supports do not establish co-location or a historical physical microgrid.

Load uses hourly sums of four physical quarters. Market is native hourly before
local 2025-10-01 and native 15-minute thereafter; later hourly values are arithmetic
means of four physical quarters, not native hourly auction products or exact
settlement for unequal quarter-hour exposure. Hourly abstraction loses sub-hourly
information. Negative prices remain valid. No numerical values are transformed here.

Weather states at validity t map to interval start t; one-hour SSRD endpoint t+1h
maps to t. Irradiance = SSRD/3600; wind speeds derive from u/v components.
All component available_at values remain UNKNOWN. Weather valid time is not
availability time and causal_availability_claim is false. No controller information
set is asserted. Fuel stays weekly/irregular with both tax variants, no hourly
resampling, tax_default UNSELECTED and currency UNRESOLVED. Its Monday reference
date is not guaranteed availability. No plant, control or task layer is included.

Authoritative bytes: scenarios/DE_CENT/hourly.parquet and native components/fuel CSV.
Accepted composition identity is ancestry; sanitized public composition has a
separate identity scheme. Root manifest inventories payloads except itself and root
checksums. Scenario checksums exclude themselves; root checksums exclude themselves
and include manifest and scenario checksums. Paths are relative POSIX; SHA-256.

Verify offline using emind.release.verify_release(package_root) from the matching
code checkout. Rebuild with scripts/release/build_release.py --data-root LOCAL_ROOT
--candidate ACCEPTED_A7 --output NEW_PACKAGE_ROOT. This requires accepted local
artifacts and frozen provenance in the original code checkout, with Python/PyArrow.
No network request is made. identity.json records baseline commit and exact hashes
of the uncommitted packaging implementation. No dynamic build timestamp is used.
RAW is omitted (R2); metadata/acquisition contains safe provider requests and expected
hashes. Reacquisition is conditional on access and provider history stability;
hash mismatches require new captures, never silent expected-hash replacement.
Canonical verification does not require RAW or internal audits. This local audit
is not cross-platform certification. Licensing/citation gates block publication.
'''


def de_cent_rc_spec(repository, data_root, candidate):
    """A4 adds owner/state/rights gates to A2 without changing numeric inputs."""
    from emind.release_gates import STATE, CLOSED_ITEMS
    repository = Path(repository)
    spec, inputs = de_cent_spec(repository, data_root, candidate)
    version = '0.1.0-rc.1'
    spec['release_version'] = version
    spec['publication_items'] = []
    licenses = read_json(repository / 'metadata/licenses.json')
    spec['metadata']['licenses.json'] = licenses
    by_source = {s['source_id']: s for s in licenses['sources']}
    for source in spec['metadata']['source_registry.json']['sources']:
        legal = by_source[source['source_id']]
        source.update({k: legal[k] for k in ('license_name', 'license_url', 'attribution')})
        source.update(license_status='CLOSED', publication_status='CLEARED_SCOPED_WITH_CONDITIONS')
    for name, value in spec['metadata'].items():
        if name.startswith('acquisition/'):
            value['license_status'] = 'CLOSED'
    for snapshot in spec['metadata']['snapshot_registry.json']['snapshots']:
        snapshot['license_status'] = 'CLOSED'
    dataset = spec['metadata']['dataset.json']
    dataset.update(STATE, release_version=version, build_mode='local-rc', citation_status='VALID',
                   authors=[{'given-names': 'Pablo', 'family-names': 'Pallarés'}], authorship_status='FROZEN',
                   publication_blockers=[{'item': item, 'status': 'CLOSED'} for item in sorted(CLOSED_ITEMS)]
                       + [{'item': 'DOI absent', 'status': 'NON-BLOCKING'},
                          {'item': 'Zenodo absent', 'status': 'NON-BLOCKING'}])
    spec['text_files'] = {name: (repository / name).read_text(encoding='utf-8')
                          for name in ('CITATION.cff', 'LICENSE', 'LICENSE-METADATA')}
    # Retain A2's scientific interpretation and provenance prose, replace its
    # historical development status paragraphs with the owner-approved RC state.
    scientific = PUBLIC_README.split('DE_CENT composes', 1)[1].split('Verify offline', 1)[0]
    spec['text_files']['README.md'] = f'''# E-MIND {version}
This is a local release candidate / pre-release artifact.
Publication status: NOT_ISSUED. DOI: none. Archival status: not_archived.
Author: Pablo Pallarés. Schema version: 0.1.0.

Project code: MIT. Project-authored metadata/documentation: CC BY 4.0.
Upstream data retain source-specific terms; this release does not override
provider licenses. See LICENSE, LICENSE-METADATA and metadata/licenses.json.
Provider credits and change indications:\n''' + '\n\n'.join(dict.fromkeys(s['attribution'] for s in licenses['sources'])) + '''

RAW is omitted (R2); all 97 safe acquisition/hash records are retained.

DE_CENT composes''' + scientific + '''Verify offline using emind.release.verify_release(package_root) from the
matching checkout. Rebuild with scripts/release/build_release.py --rc using
accepted local inputs and a new output directory. No network is needed.
identity.json records baseline commit and exact uncommitted exporter hashes.
No public issuance, archival permanence or peer-reviewed-publication claim is made.
'''
    spec['scenarios'][0]['scenario']['artifact_status'] = 'LOCAL_RC_NOT_ISSUED'
    return spec, inputs
