"""Read-only multiyear ERA5 structural audit using the maintained GRIB validator."""
from collections import Counter
from datetime import timedelta
from pathlib import Path
import json

from .config import validate_weather_config
from .era5 import validate_grib, _stamp, build_monthly_request, build_boundary_request
from .harmonise import canonical_index


def audit_plan(raw_root, config, *, include_boundary=True):
    """Pure configured inventory plan; C1 does not require or open the boundary."""
    config = validate_weather_config(config)
    root = Path(raw_root)
    first = _stamp(config['horizon']['start']).year
    last = _stamp(config['horizon']['end_inclusive']).year
    objects = [(root / str(y) / build_monthly_request(y,m,config)['filename'], y, m)
               for y in range(first,last+1) for m in range(1,13)]
    if include_boundary:
        year = _stamp(config['ssrd']['final_provider_validity']).year
        objects.append((root / f'{year}_boundary' / build_boundary_request(config)['filename'], None, None))
    return [(root/p.name if not p.exists() and (root/p.name).exists() else p, y, m) for p,y,m in objects]


def boundary_accounting(config):
    """Conceptual intervals only; the extra initial SSRD stays in immutable RAW."""
    count = len(canonical_index(config))
    return {'complete_year_ssrd_timestamps': count, 'initial_extra_count': 1,
            'initial_extra_provider_validity': config['horizon']['start'],
            'final_boundary_count': 1, 'final_boundary_provider_validity': config['ssrd']['final_provider_validity'],
            'canonical_intervals': count - 1 + 1, 'arithmetic': f'{count} - 1 + 1 = {count}',
            'mapping_is_conceptual_only': True, 'initial_extra_retained_in_raw': True}


def temporal_coverage(stamps, expected):
    """Fail closed on duplicate, missing, unexpected or discontinuous validity."""
    counter = Counter(stamps)
    if counter != Counter(expected):
        raise ValueError('Structural audit: missing/duplicate/unexpected provider timestamp')
    ordered = sorted(counter)
    return {'messages': len(stamps), 'unique_timestamps': len(counter),
            'first_validity': ordered[0].strftime('%Y-%m-%dT%H:%M:%SZ'),
            'last_validity': ordered[-1].strftime('%Y-%m-%dT%H:%M:%SZ'),
            'missing_timestamps': 0, 'unexpected_timestamps': 0, 'duplicate_timestamps': 0, 'hourly_gaps': 0}


def structural_audit(raw_root, config, *, checksum_inventory=None, progress=None):
    """Validate every GRIB afresh; an inventory supplies expected hashes only.

    Without an explicit inventory, adjacent acquisition provenance supplies the
    checksum baseline. Historical validation results never replace decoding.
    """
    config = validate_weather_config(config)
    objects = audit_plan(raw_root, config)
    expected = None
    if checksum_inventory:
        report = json.loads(Path(checksum_inventory).read_text())
        if report['status'] != 'PASS':
            raise ValueError('Structural checksum inventory is not PASS')
        entries = report['inventory']
        expected = {str(Path(e['absolute_path']).resolve()): e for e in entries}
        if len(entries) != len(expected):
            raise ValueError('Duplicate checksum inventory path')
    inventory, summaries = [], []
    times = {v['short_name']: [] for v in config['variables']}
    counts = Counter()
    sign = Counter()
    yearly = {}
    for index, (path, year, month) in enumerate(objects):
        boundary = year is None
        result = validate_grib(path, config, year=year, month=month, boundary=boundary)
        if expected is not None:
            baseline = expected.get(str(path.resolve()))
            if baseline is None:
                raise ValueError('Provider object absent from checksum inventory')
            sha, size = baseline['sha256'], baseline['byte_size']
        else:
            provenance = path.with_suffix('.provenance.json') if boundary else path.parent / 'audit' / f'provenance_{year}_{month:02d}.json'
            baseline = json.loads(provenance.read_text())
            sha = baseline['sha256']
            size = baseline['byte_size'] if boundary else baseline['bytes']
        if result['sha256'] != sha or result['bytes'] != size:
            raise ValueError('Structural audit checksum/size mismatch')
        if boundary and sha != config['ssrd']['boundary_sha256']:
            raise ValueError('Boundary checksum differs from configuration')
        messages, scalars = result['message_count'], result['scalar_count']
        counts['total_messages_including_boundary'] += messages
        counts['total_scalar_values_including_boundary'] += scalars
        object_year = _stamp(config['ssrd']['final_provider_validity']).year if boundary else year
        annual = yearly.setdefault(str(object_year), dict(objects=0, messages=0, scalar_values=0, bytes=0))
        for key, value in [('objects', 1), ('messages', messages), ('scalar_values', scalars), ('bytes', result['bytes'])]:
            annual[key] += value
        if not boundary:
            counts['complete_year_messages'] += messages
            counts['complete_year_scalar_values'] += scalars
            for s, coverage in result['validity_coverage'].items():
                first = _stamp(coverage['first'])
                times[s].extend(first + timedelta(hours=h) for h in range(coverage['count']))
        sign.update(result['ssrd_sign_counts'])
        inventory.append({'absolute_path': str(path), 'sha256': result['sha256'], 'byte_size': result['bytes'],
                          'boundary': boundary, 'messages': messages, 'scalar_values': scalars})
        summaries.append(result)
        if progress:
            progress(index + 1, len(objects), path)
    index = canonical_index(config)
    coverage = {s: temporal_coverage(ts, index) for s, ts in times.items()}
    raw_ssrd = temporal_coverage(times['ssrd'] + [_stamp(config['ssrd']['final_provider_validity'])],
                                 index + [_stamp(config['ssrd']['final_provider_validity'])])
    return {'status': 'PASS', 'inventory_counts': {'complete_year_monthly_objects': len(objects) - 1,
             'boundary_objects': 1, 'total_provider_objects': len(objects), 'audited_objects': len(objects)},
            'global_counts': dict(counts), 'per_year_counts': yearly,
            'per_variable_temporal_coverage': coverage, 'ssrd_raw_including_boundary_coverage': raw_ssrd,
            'checksum_comparisons': {'objects_compared': len(objects), 'mismatch_count': 0},
            'quality_counts': {'missing': 0, 'nan': 0, 'non_finite': 0},
            'ssrd_sign_counts': dict(sign), 'ssrd_boundary_accounting': boundary_accounting(config),
            'parameter_identities': config['variables'], 'grid': summaries[0]['grid'],
            'temporal_semantics': 'All objects passed maintained D3B provider-semantic validation',
            'inventory': inventory, 'object_validation': summaries, 'transformations': False}


def compare_structural(actual, reference):
    """Exact comparison of frozen C0 headlines and variable coverage."""
    keys = ('inventory_counts', 'global_counts', 'per_year_counts', 'per_variable_temporal_coverage',
            'ssrd_raw_including_boundary_coverage', 'quality_counts', 'ssrd_sign_counts')
    for key in keys:
        if actual[key] != reference[key]:
            raise ValueError('C0 regression mismatch: ' + key)
    for key in ('complete_year_ssrd_timestamps', 'initial_extra_count', 'final_boundary_count', 'arithmetic'):
        if actual['ssrd_boundary_accounting'][key] != reference['ssrd_boundary_accounting'][key]:
            raise ValueError('C0 boundary accounting mismatch')
    if actual['checksum_comparisons']['mismatch_count'] != reference['checksum_comparisons']['mismatch_count']:
        raise ValueError('C0 checksum mismatch count differs')
    return {'status': 'PASS', 'exact_headlines': True, 'exact_temporal_coverage': True}
