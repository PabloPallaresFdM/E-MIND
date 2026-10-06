"""Offline, value-preserving DE_CENT scenario composition (A7 schema 1)."""
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = 'phase04-a7-schema-1'
WEATHER = ['air_temperature_2m_k', 'surface_solar_radiation_downwards_j_m2',
           'surface_solar_irradiance_w_m2', 'wind_u_10m_m_s', 'wind_v_10m_m_s',
           'wind_speed_10m_m_s', 'wind_u_100m_m_s', 'wind_v_100m_m_s',
           'wind_speed_100m_m_s']
LOAD = 'load_energy_mwh'
MARKET = 'day_ahead_price_eur_mwh'
START = datetime(2019, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROWS = 61368


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def portable(value, root):
    """Relocate data-root paths; discard runtime-only machine provenance."""
    if isinstance(value, dict):
        return {k: portable(v, root) for k, v in value.items()
                if k not in {'software', 'reproducibility', 'host', 'implementation_sha256'}}
    if isinstance(value, list):
        return [portable(v, root) for v in value]
    if isinstance(value, str) and value.startswith(str(root) + '/'):
        return str(Path(value).relative_to(root))
    if isinstance(value, str) and value.startswith('/'):
        raise ValueError('Unresolved machine-specific path in scientific identity')
    return value


def fingerprint(config):
    payload = json.dumps(config, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def exact_support(supports, expected=None):
    expected = expected if expected is not None else [START + timedelta(hours=i) for i in range(ROWS)]
    for name, times in supports.items():
        if times != expected:
            raise ValueError(f'{name}: timestamps differ from exact ordered UTC backbone')
    return len(expected)


def assemble(weather, load, market, expected=None):
    """Require support equality before combining; never discard rows by joining."""
    import pyarrow as pa
    supports = {'weather': weather['timestamp_utc'].to_pylist(),
                'load': [datetime.fromisoformat(r['timestamp_utc'].replace('Z', '+00:00')) for r in load],
                'market': [datetime.fromisoformat(r['timestamp_utc'].replace('Z', '+00:00')) for r in market]}
    exact_support(supports, expected)
    if weather.column_names != ['timestamp_utc'] + WEATHER:
        raise ValueError('Unexpected weather schema')
    table = weather.select(['timestamp_utc'] + WEATHER)
    for rows, column in [(load, LOAD), (market, MARKET)]:
        values = [float(r[column]) for r in rows]
        if any(not math.isfinite(v) for v in values):
            raise ValueError('Non-finite component values')
        if column == LOAD and any(v < 0 for v in values):
            raise ValueError('Negative load')
        table = table.append_column(column, pa.array(values, type=pa.float64()))
    for column in WEATHER:
        if any(v is None or not math.isfinite(v) for v in table[column].to_pylist()):
            raise ValueError('Missing/non-finite weather')
    return table


def read_csv(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


def validate_fuel(rows):
    counts = Counter(r['tax_variant'] for r in rows)
    if counts != {'WITH_TAX': 356, 'WITHOUT_TAX': 356}:
        raise ValueError('Unexpected native fuel counts')
    support = None
    for variant in counts:
        dates = [r['reference_date'] for r in rows if r['tax_variant'] == variant]
        if dates != sorted(set(dates)) or any(not '2019-01-01' <= d < '2026-01-01' for d in dates):
            raise ValueError('Invalid fuel date support')
        if support is not None and dates != support:
            raise ValueError('Fuel tax variants have different date supports')
        support = dates
    if any(not math.isfinite(float(r['source_value'])) for r in rows):
        raise ValueError('Non-finite fuel')
    return dict(counts)


def build(root, output):
    import pyarrow
    import pyarrow.parquet as pq
    root, output = Path(root).resolve(), Path(output).resolve()
    if root / 'scenarios/DE_CENT' not in output.parents or output.exists():
        raise ValueError('New non-release directory under data-root/scenarios/DE_CENT required')
    wdir = root / 'interim/DE_CENT/weather/phase03c_c2b_2019_2025'
    adir = root / 'interim/DE_CENT/phase04_a5_20261002_validated'
    fdir = root / 'interim/DE_CENT/phase04_a6_fuel_2019_2025'
    paths = {'weather': wdir / 'weather_de_cent_2019_2025.parquet',
             'load': adir / 'load_de_cent_2019_2025_hourly.csv',
             'market': adir / 'market_de_lu_2019_2025_hourly.csv',
             'fuel': fdir / 'fuel_de_2019_2025.csv'}
    metadata_paths = {'weather': wdir / 'weather_de_cent_2019_2025_metadata.json',
                      'load': adir / 'metadata.json', 'market': adir / 'metadata.json',
                      'fuel': fdir / 'metadata.json'}
    source = {k: json.loads(p.read_text()) for k, p in metadata_paths.items()}
    hashes = {k: sha256(p) for k, p in paths.items()}
    expected = {'weather': source['weather']['output_parquet']['sha256']}
    for name in ['load', 'market']:
        expected[name] = next(t['sha256'] for t in source[name]['tables'] if Path(t['path']).name == paths[name].name)
    expected['fuel'] = next(line.split()[0] for line in (fdir / 'checksums.sha256').read_text().splitlines() if line.split()[-1] == paths['fuel'].name)
    if hashes != expected:
        raise ValueError('Accepted artifact checksum mismatch')
    # Audit every existing accepted-directory file before/after, including metadata.
    originals = {p: sha256(p) for directory in [wdir, adir, fdir] for p in directory.iterdir() if p.is_file()}
    weather = pq.read_table(paths['weather'])
    load, market, fuel = (read_csv(paths[k]) for k in ['load', 'market', 'fuel'])
    table = assemble(weather, load, market)
    fuel_counts = validate_fuel(fuel)
    supports = {
        'weather': ('ERA5 single-grid-point atmospheric estimate; local reference anchor 51.00 N, 10.25 E', 'hourly instantaneous states / one-hour SSRD accumulation', '1h UTC'),
        'load': ('German national/system grid load', '15-minute', '1h UTC'),
        'market': ('DE-LU bidding zone', '60 min before local 2025-10-01; 15 min from local 2025-10-01', '1h UTC'),
        'fuel': ('Germany national bulletin', 'weekly / irregular weekly', 'native cadence; no hourly representation')}
    transformations = {'weather': source['weather']['transformations_performed'],
        'load': 'Hourly sum of four physical quarter-hour energies; no scaling',
        'market': source['market']['market'],
        'fuel': 'Germany automotive diesel extraction; both tax variants; no interpolation/resampling'}
    components = {}
    for name in paths:
        components[name] = {
            'artifact': str(paths[name].relative_to(root)), 'sha256': hashes[name],
            'provider': 'ECMWF / CDS / C3S' if name == 'weather' else 'European Commission Weekly Oil Bulletin' if name == 'fuel' else 'Bundesnetzagentur / SMARD',
            'spatial_support': supports[name][0], 'native_temporal_resolution': supports[name][1],
            'canonical_resolution': supports[name][2], 'available_at': 'UNKNOWN',
            'causal_availability_claim': False,
            'quality_status': 'Accepted component; scientific validation passed with documented semantic limits',
            'transformation': transformations[name],
            'metadata_artifact': str(metadata_paths[name].relative_to(root)),
            'source_metadata_sha256': sha256(metadata_paths[name]),
            'lineage': portable(source[name], root)}
    components['weather'].update(anchor={'latitude': 51.0, 'longitude': 10.25}, valid_time_is_available_at=False)
    components['fuel'].update(tax_variants=['WITH_TAX', 'WITHOUT_TAX'], tax_default='UNSELECTED', currency='UNRESOLVED', interpolation=False, hourly_resampling=False, observations_per_variant=fuel_counts)
    components['market']['post_change_note'] = 'Post-change hourly values are derived arithmetic means of four physical quarter-hour prices, not native hourly auction products; negative prices are valid.'
    config = {'schema_version': VERSION, 'scenario_id': 'DE_CENT', 'scenario_class': 'CORE_FULL_MARKET',
        'artifact_status': 'VALIDATED_CANDIDATE_NOT_RELEASE',
        'horizon': {'start': '2019-01-01T00:00:00Z', 'end_exclusive': '2026-01-01T00:00:00Z', 'rows': ROWS, 'complete_calendar_years': '2019-2025'},
        'canonical_timebase': '1h UTC', 'interval_semantics': 'interval_start',
        'scenario_interpretation': 'historically grounded composition of heterogeneous open signals',
        'historical_colocated_microgrid': False, 'co_location_claim': False,
        'controller_information_set': 'UNDEFINED; later Reference Tasks decide I0/I1/I2/I3',
        'demand_scaling': False, 'hourly_columns': table.column_names,
        'hourly_artifact': 'hourly.parquet', 'fuel_storage': 'REFERENCE_ONLY_NATIVE_CADENCE',
        'assembly_transformation': 'Exact ordered UTC support equality, column concatenation; no numeric transformations',
        'components': components}
    identity = fingerprint(config)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    pq.write_table(table, output / 'hourly.parquet', compression='zstd')
    reread = pq.read_table(output / 'hourly.parquet')
    regression = {}
    for name, columns in [('weather', WEATHER), ('load', [LOAD]), ('market', [MARKET])]:
        diffs = [abs(a-b) for c in columns for a,b in zip(table[c].to_pylist(), reread[c].to_pylist())]
        if not reread.select(columns).equals(table.select(columns)):
            raise ValueError('Parquet regression failure')
        regression[name] = max(diffs)
    if reread['timestamp_utc'].to_pylist() != weather['timestamp_utc'].to_pylist():
        raise ValueError('Serialized timestamp regression')
    if read_csv(paths['fuel']) != fuel:
        raise ValueError('Fuel regression failure')
    regression['fuel'] = 0
    unchanged = all(sha256(p) == h for p,h in originals.items())
    if not unchanged or any(regression.values()):
        raise ValueError('Component mutation or regression')
    validation = {'status': 'PASS', 'rows': reread.num_rows, 'exact_hourly_timestamp_equality': True,
        'gaps': 0, 'duplicates': 0, 'join_missing': 0, 'nonfinite': 0,
        'load_nonnegative': True, 'negative_market_hours': sum(float(r[MARKET]) < 0 for r in market),
        'market_negatives_preserved': True, 'fuel_counts': fuel_counts,
        'fuel_reference_dates': {v: {'first': min(r['reference_date'] for r in fuel if r['tax_variant']==v), 'last': max(r['reference_date'] for r in fuel if r['tax_variant']==v)} for v in fuel_counts},
        'regression_max_numeric_difference': regression, 'fuel_resampled': False,
        'demand_scaled': False, 'plant_control_reward_quantities_added': False,
        'accepted_component_directory_files_unchanged': unchanged,
        'scenario_fingerprint': identity, 'fingerprint_algorithm': 'SHA256 sorted compact UTF-8 JSON of scenario.json; allow_nan=false',
        'hourly_sha256': sha256(output / 'hourly.parquet'), 'component_hashes': hashes,
        'network_access': False, 'python_version': __import__('sys').version, 'pyarrow_version': pyarrow.__version__}
    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    save('scenario.json', config)
    save('metadata.json', {'scenario_fingerprint': identity, 'schema': VERSION,
        'timestamp_type': str(reread.schema.field('timestamp_utc').type),
        'numeric_type': 'float64', 'component_reference_base': 'canonical E-MIND data root',
        'reviewer_note': 'No claim is made that weather, load, market and fuel represent measurements from a single physical location or historical microgrid.'})
    save('lineage.json', {'scenario_fingerprint': identity, 'components': components})
    save('validation.json', validation)
    report = '| component | spatial support | temporal support | native cadence | canonical cadence |\n|---|---|---|---|---|\n'
    for name,(spatial,native,canonical) in supports.items():
        temporal = '2019-2025; 61,368 hours' if name != 'fuel' else str(validation['fuel_reference_dates']) + '; 356 per variant'
        report += f'| {name} | {spatial} | {temporal} | {native} | {canonical} |\n'
    report += '\nNo claim is made that weather, load, market and fuel represent measurements from a single physical location or historical microgrid.\n'
    (output / 'support_audit.md').write_text(report)
    (output / 'checksums.sha256').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in sorted(output.iterdir()) if p.is_file()))
    return validation
