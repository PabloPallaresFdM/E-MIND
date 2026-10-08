"""Safe DE_CENT + ES_MED development projection from frozen provider evidence.

Spanish rights stay PENDING. This adapter cannot produce a gated RC or publish.
"""
from copy import deepcopy
from pathlib import Path

from emind.release import SCHEMA_VERSION, read_json
from emind.release_gates import validate_citation
from emind.release_inputs import de_cent_rc_spec, scenario_release_entry
from emind.scenario import sha256

VERSION = '0.1.0-dev.phase07'


def multi_region_spec(repository, data_root, de_candidate, es_candidate):
    repository, root = Path(repository), Path(data_root)
    spec, inputs = de_cent_rc_spec(repository, root, de_candidate)
    spec = deepcopy(spec)
    original = read_json(Path(es_candidate)/'scenario.json')
    accepted = read_json(repository/'metadata/scenarios/es_med_phase07_accepted.json')
    if original['scenario_id'] != 'ES_MED' or original['scenario_class'] != 'CORE_FULL_MARKET':
        raise ValueError('Accepted Spanish scenario contract mismatch')
    sources = accepted['release_projection_component_sources']
    entry, es_inputs = scenario_release_entry(root, es_candidate, sources)
    if entry['scenario']['scenario_fingerprint'] != accepted['scenario_fingerprint']:
        raise ValueError('Frozen Spanish identity mismatch')
    if set(inputs) & set(es_inputs):
        raise ValueError('Cross-region input artifact ID collision')
    inputs.update(es_inputs)
    spec['scenarios'].append(entry)
    spec['raw_policy'] = 'OMIT_R2_PROVIDER_RAW'
    spec['release_version'] = VERSION
    spec['publication_items'] = ['REData canonical redistribution PENDING', 'OMIE canonical redistribution PENDING',
        'Multi-region public source projection and documentation approval', 'Multi-region cross-platform certification',
        'Final multi-region release version/citation review']
    metadata = spec['metadata']
    licenses = metadata['licenses.json']
    for name, provider, attribution, endpoint in [
        ('es_redata_demanda_10297', 'Red Eléctrica / REData', 'Red Eléctrica / REData; E-MIND peninsular hourly window and UTC conversion', 'https://apidatos.ree.es/es/datos/demanda/evolucion'),
        ('es_omie_marginalpdbc', 'OMIE', 'OMIE; E-MIND Spanish MarginalES extraction and hourly quarter-price aggregation', 'https://www.omie.es/es/file-download')]:
        legal = dict(source_id=name, provider=provider, status='PENDING', license_name='UNRESOLVED', license_url=endpoint,
            attribution=attribution, redistribution_status='PENDING', derived_bundling_status='PENDING', raw_class='R2',
            raw_bundling_status='R2_OMITTED_BY_POLICY', legal_clearance_claim=False)
        licenses['sources'].append(legal)
        metadata['source_registry.json']['sources'].append(dict(legal, license_status='PENDING',
            publication_status='PENDING_REVIEW', raw_bundled=False))
    for records in (licenses['sources'], metadata['source_registry.json']['sources']):
        fuel = next(s for s in records if s['source_id']=='eu_ec_weekly_oil_bulletin_diesel')
        fuel['attribution'] += ' E-MIND also extracts Spain automotive diesel from the same frozen workbook, retaining native weekly observations and both tax variants.'
    # Shared source IDs retain their accepted exact-product/workbook legal basis.
    licenses.update(publication_status='PENDING_REVIEW', publication_authorized=False)
    metadata['dataset.json'].update(release_version=VERSION, release_stage='development_dry_run',
        publication_status='PENDING_REVIEW', archival_status='not_archived', doi=None,
        scope='Multi-region European energy data infrastructure; two validated regions, not Europe-wide coverage',
        build_mode='dry-run', citation_status='VALID_DEVELOPMENT_DRAFT_NOT_ISSUED',
        publication_blockers=[{'item':p,'status':'PENDING'} for p in spec['publication_items']])
    for region in spec['scenarios']:
        region['scenario']['artifact_status'] = 'INTERNAL_DEVELOPMENT_DRY_RUN_NOT_PUBLIC_RELEASE'
    snapshots = metadata['snapshot_registry.json']['snapshots']
    snapshot_by_id = {s['snapshot_id']:s for s in snapshots}

    def snapshot(snapshot_id, source_ids, provider, path, expected_hash, request, acquired, legal):
        if sha256(path) != expected_hash:
            raise ValueError('Frozen provider object differs from accepted lineage')
        config_id = snapshot_id
        config_path = f'acquisition/{config_id}.json'
        acquisition = dict(schema_version=SCHEMA_VERSION, acquisition_config_id=config_id, source_ids=source_ids,
            request=request, expected_sha256=expected_hash, redistribution_class='R2', raw_bundled=False, license_status=legal)
        record = dict(snapshot_id=snapshot_id, source_ids=source_ids, provider=provider, sha256=expected_hash,
            size_bytes=path.stat().st_size, original_filename=path.name, retrieved_at_utc=acquired,
            acquisition_config_id=config_id, acquisition_config_path='metadata/'+config_path,
            redistribution_class='R2', raw_bundled=False, license_status=legal)
        if snapshot_id in snapshot_by_id or config_path in metadata:
            raise ValueError('Provider snapshot/acquisition identity collision')
        snapshot_by_id[snapshot_id] = record; snapshots.append(record); metadata[config_path] = acquisition
        return snapshot_id

    signal_root = root/'phase07_es_full'
    for component, folder in [('load','redata'),('market','omie')]:
        objects = read_json(signal_root/folder/'manifests/candidate_v1.json')['provider_objects']
        ids = []
        for obj in objects:
            path = Path(obj['path'])
            sid = 'es_'+folder+'_'+path.name.replace('.', '_')
            ids.append(snapshot(sid, sources[component], original['components'][component]['provider'], path,
                obj['sha256'], dict(url=obj['url'], parameters=obj.get('parameters', obj.get('request_parameters', {}))),
                obj.get('acquisition_timestamp_utc', obj.get('acquired_at')), 'PENDING'))
        entry['scenario']['components'][component]['snapshot_ids'] = sorted(ids)
    weather = read_json(signal_root/'era5/manifests/acquisition.json')
    if weather['status'] != 'PASS' or len(weather['objects']) != 85:
        raise ValueError('Accepted Spanish weather acquisition incomplete')
    ids = []
    for obj in weather['objects']:
        path = Path(obj['final_path'])
        ids.append(snapshot(path.stem, sources['weather'], original['components']['weather']['provider'], path,
            obj['sha256'], dict(dataset=obj['dataset'], request=obj['request']), obj['retrieval_utc'], 'CLOSED'))
    entry['scenario']['components']['weather']['snapshot_ids'] = sorted(ids)
    workbook = original['components']['fuel']['upstream_workbook']
    if sha256(root/workbook['artifact']) != workbook['sha256']:
        raise ValueError('Frozen shared workbook mismatch')
    fuel_ids = [s['snapshot_id'] for s in snapshots if s['sha256']==workbook['sha256'] and set(s['source_ids'])==set(sources['fuel'])]
    if len(fuel_ids)!=1:
        raise ValueError('Shared frozen EC workbook lineage unresolved or duplicated')
    entry['scenario']['components']['fuel']['snapshot_ids'] = fuel_ids
    for side in entry['metadata']['side_components']:
        side['snapshot_ids'] = entry['scenario']['components'][side['component_id']]['snapshot_ids']
    for lineage in entry['lineage']['components']:
        lineage['snapshot_ids'] = entry['scenario']['components'][lineage['component_id']]['snapshot_ids']
    # Field-to-source-column lineage survives safe projection, without local paths.
    entry['lineage']['fields'] = read_json(Path(es_candidate)/'lineage.json')['fields']
    for name, c in entry['scenario']['components'].items():
        c['accepted_metadata_references'] = [dict(artifact_id=Path(x['artifact']).name, sha256=x['sha256'])
            for x in original['components'][name]['lineage']]
    for name in ['source_registry.json','snapshot_registry.json']:
        key='source_id' if name.startswith('source') else 'snapshot_id'
        metadata[name]['sources' if key=='source_id' else 'snapshots'].sort(key=lambda x:x[key])
    licenses['sources'].sort(key=lambda x:x['source_id'])
    texts = spec['text_files']
    texts['CITATION.cff'] = texts['CITATION.cff'].replace('0.1.0-rc.1', VERSION).replace(
        'local release candidate / pre-release artifact', 'internal development dry-run artifact')
    validate_citation(texts['CITATION.cff'], VERSION, metadata['dataset.json']['authors'])
    texts['README.md'] = f'''# E-MIND {VERSION} — internal development dry-run
Two technically validated regions, DE_CENT and ES_MED, exercise the release schema.
This is multi-region European energy data infrastructure, not Europe-wide coverage.
NOT ISSUED. No DOI. Not archived. Publication is blocked: REData and OMIE canonical
redistribution remains PENDING. R2 RAW omission does not clear derived rights.
Canonical copies are included solely for this explicitly allowed internal dry-run.

Each region retains its independent accepted scientific fingerprint, spatial
supports, source attribution, hourly UTC interval-start semantics, and native
weekly diesel side component. Weather/load/market/fuel do not describe a measured
co-located physical microgrid. No sizing, plant, reward or controller is included.
Fuel: both tax variants, default UNSELECTED, currency linkage UNRESOLVED, no hourly
interpolation/ZOH/forward fill; all available_at UNKNOWN. No causal forecast claim.

DE_CENT: ERA5 51.00 N/10.25 E; German system load; DE-LU market; Germany fuel.
ES_MED: ERA5 39.50 N/-0.75 E; Spanish peninsular load; Spanish market; Spain fuel.
Both horizons: [2019-01-01T00:00Z,2026-01-01T00:00Z), 61,368 hourly rows.
Source snapshots and exact acquisition requests/hashes remain in metadata; provider
RAW is omitted. Shared EC workbook lineage is reused once, not duplicated.
Project code MIT; original metadata CC BY 4.0; upstream terms remain separate.
See metadata/licenses.json for accepted scoped terms and pending Spanish sources.
Verify offline with emind.release.verify_release(package_root). No publication or
reacquisition is performed by this package. The citation is a development draft.
'''
    return spec, inputs
