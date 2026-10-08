"""Region-neutral preflight checks for accepted scientific package inputs."""
import csv
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


def validate_inventory(scenarios, paths, registry, identity_scenarios):
    ids = [s['scenario_id'] for s in scenarios]
    if len(ids) != len(set(x.casefold() for x in ids)):
        raise ValueError('Duplicate/case-colliding scenario IDs')
    expected = {f'scenarios/{sid}/scenario.json' for sid in ids}
    actual = {p for p in paths if p.startswith('scenarios/') and p.endswith('/scenario.json')}
    if actual != expected or [s['path'] for s in scenarios] != [f'scenarios/{sid}/scenario.json' for sid in ids]:
        raise ValueError('Scenario directory/manifest inventory mismatch')
    if registry != scenarios or identity_scenarios != scenarios:
        raise ValueError('Scenario registry/identity/manifest mismatch')


def validate_native(side, path, accepted_component):
    """The declared economic bulletin contract stays native and gap preserving."""
    if side.get('semantic_kind') != 'native_weekly_economic_bulletin':
        return
    with Path(path).open(newline='') as f:
        rows = list(csv.DictReader(f))
    if Counter(r.get('tax_variant') for r in rows) != accepted_component['observations_per_variant']:
        raise ValueError('Native bulletin observation counts changed / hourly expansion')
    if any(r.get('native_frequency') != 'weekly' for r in rows):
        raise ValueError('Native bulletin cadence changed')
    for variant in accepted_component['observations_per_variant']:
        dates = [r['reference_date'] for r in rows if r['tax_variant'] == variant]
        if dates != sorted(set(dates)) or any(datetime.fromisoformat(d).weekday() != 0 for d in dates):
            raise ValueError('Invalid native weekly support')
    if any(side.get(k) is True for k in ('hourly_resampling', 'interpolation', 'forward_fill', 'zero_order_hold')):
        raise ValueError('Native bulletin resampling policy changed')
    if side.get('tax_default') != 'UNSELECTED':
        raise ValueError('Native bulletin default tax selection')
    country = accepted_component.get('country_code')
    if country and any(r.get('country_code') != country for r in rows):
        raise ValueError('Native bulletin country mismatch')


def validate_hourly(scenario, accepted, path):
    """Validate a declared accepted hourly contract; opaque artifacts stay opaque."""
    if 'hourly_columns' not in accepted or 'horizon' not in accepted:
        return
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    horizon = accepted['horizon']
    if (table.num_rows != horizon['rows'] or scenario['rows'] != horizon['rows']
            or scenario['horizon'] != horizon or table.column_names != accepted['hourly_columns']
            or scenario['hourly_columns'] != accepted['hourly_columns']):
        raise ValueError('Hourly row-count/schema/horizon mismatch')
    if table.schema.field('timestamp_utc').type != pa.timestamp('us', tz='UTC'):
        raise ValueError('Hourly UTC timestamp schema mismatch')
    start = datetime.fromisoformat(horizon['start'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(horizon['end_exclusive'].replace('Z', '+00:00'))
    expected = [start + timedelta(hours=i) for i in range(horizon['rows'])]
    if expected[-1] + timedelta(hours=1) != end or table['timestamp_utc'].to_pylist() != expected:
        raise ValueError('Hourly timestamp support mismatch')
    if any(table[c].null_count for c in table.column_names):
        raise ValueError('Hourly required-column nulls')


def validate_preflight(spec, inputs, read_json, fingerprint):
    metadata_paths = list(spec['metadata'])
    if (len({p.casefold() for p in metadata_paths}) != len(metadata_paths)
            or any(p.casefold() == 'scenario_registry.json' for p in metadata_paths)):
        raise ValueError('Metadata path collision with shared/generated metadata')
    scenarios = spec['scenarios']
    ids = [s['scenario_id'] for s in scenarios]
    if len(ids) != len(set(x.casefold() for x in ids)):
        raise ValueError('Duplicate/case-colliding scenario IDs')
    for s in scenarios:
        d = s['scenario']
        identity = d.get('scenario_fingerprint')
        accepted = read_json(inputs[s['accepted_descriptor_id']])
        if not identity or fingerprint(accepted) != identity:
            raise ValueError('Missing/mismatched accepted scenario fingerprint')
        if any(s[k].get('scenario_id') != s['scenario_id'] for k in ('scenario', 'metadata', 'lineage')):
            raise ValueError('Cross-scenario metadata identity collision')
        if accepted.get('scenario_id', s['scenario_id']) != s['scenario_id']:
            raise ValueError('Accepted scenario ID mismatch')
        if any(not c.get('spatial_support') for c in d['components'].values()):
            raise ValueError('Missing component spatial support')
        artifacts = {a['path']: a for a in s['artifacts']}
        reserved = {'scenario.json', 'metadata.json', 'lineage.json', 'validation.json', 'checksums.sha256'}
        if (len(artifacts) != len(s['artifacts'])
                or len({p.casefold() for p in artifacts}) != len(artifacts)
                or reserved.intersection(p.casefold() for p in artifacts)):
            raise ValueError('Scenario artifact/metadata path collision')
        if spec.get('raw_policy') == 'OMIT_R2_PROVIDER_RAW' and any(
                a['role'] not in {'authoritative_hourly_core', 'authoritative_native_component'} for a in s['artifacts']):
            raise ValueError('R2 provider RAW inclusion forbidden')
        hourly = artifacts[d['hourly_artifact']]
        validate_hourly(d, accepted, inputs[hourly['artifact_id']])
        for side in s['metadata']['side_components']:
            if not side.get('spatial_support'):
                raise ValueError('Missing native side-component spatial support')
            artifact = artifacts[side['authoritative_path']]
            component = accepted.get('components', {}).get(side['component_id'], {})
            validate_native(side, inputs[artifact['artifact_id']], component)
