"""Offline single-point ERA5 harmonisation under the frozen weather contract.

Native ecCodes tools must be on PATH. By default inputs pass D3B validation.
An explicit PASS inventory can instead attest previously validated inputs,
with current size and SHA-256 checked before and after extraction.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import platform
import subprocess

from .config import load_weather_config, validate_weather_config
from .era5 import (build_monthly_request, build_boundary_request, validate_grib,
                   _digest, _provider_stamp, _stamp)

COLUMNS = ('timestamp_utc', 'air_temperature_2m_k',
           'surface_solar_radiation_downwards_j_m2', 'surface_solar_irradiance_w_m2',
           'wind_u_10m_m_s', 'wind_v_10m_m_s', 'wind_speed_10m_m_s',
           'wind_u_100m_m_s', 'wind_v_100m_m_s', 'wind_speed_100m_m_s')
# Output-schema aliases, not scientific parameter defaults.
_PROVIDER_COLUMNS = {'2t': COLUMNS[1], 'ssrd': COLUMNS[2], '10u': COLUMNS[4],
                     '10v': COLUMNS[5], '100u': COLUMNS[7], '100v': COLUMNS[8]}


def _require(condition, reason):
    if not condition:
        raise ValueError('Weather harmonisation failed: ' + reason)


def _iso(stamp):
    return stamp.strftime('%Y-%m-%dT%H:%M:%SZ')


def canonical_index(config):
    """Inclusive hourly UTC index, with calendar/leap years handled by datetime."""
    config = validate_weather_config(config)
    start, end = (_stamp(config['horizon'][key]) for key in ('start', 'end_inclusive'))
    step = timedelta(hours=config['canonical_time']['frequency_hours'])
    count = int((end - start) / step) + 1
    _require(count == config['horizon']['expected_hours'], 'configured index length')
    return [start + i * step for i in range(count)]


def discover_inputs(raw_root, config):
    """Deterministic monthly objects followed by the sole final SSRD boundary."""
    config = validate_weather_config(config)
    root = Path(raw_root)
    start, end = (_stamp(config['horizon'][key]) for key in ('start', 'end_inclusive'))
    objects = [(root / str(year) / build_monthly_request(year, month, config)['filename'], year, month)
               for year in range(start.year, end.year + 1) for month in range(1, 13)]
    boundary = root / f"{_stamp(config['ssrd']['final_provider_validity']).year}_boundary" / build_boundary_request(config)['filename']
    objects = [(root/p.name if not p.exists() and (root/p.name).exists() else p, y, m) for p,y,m in objects]
    if not boundary.exists() and (root/boundary.name).exists(): boundary = root/boundary.name
    _require(boundary.is_file(), 'missing final SSRD boundary object')
    objects.append((boundary, None, None))
    _require(all(p.is_file() for p, _, _ in objects), 'missing monthly provider object')
    return objects


def central_value(rows, config):
    """Select one exact coordinate; never nearest-point selection or averaging."""
    anchor = config['anchor']
    selected = [v for a, b, v in rows if (a, b) == (anchor['latitude'], anchor['longitude'])]
    _require(len(selected) == 1, 'central point absent or duplicated')
    _require(math.isfinite(selected[0]), 'non-finite central provider value')
    return selected[0]


def decode_central(path, config):
    """Yield provider shortName, validity and central value in GRIB message order.

    Called only after D3B validation or explicit hash-bound validated-input mode.
    No second interpretation of GRIB accumulation metadata is introduced here.
    """
    metadata = json.loads(subprocess.check_output(
        ['grib_ls', '-j', '-p', 'shortName,validityDate,validityTime', str(path)], text=True))['messages']
    text = subprocess.check_output(['grib_get_data', '-F', '%.17g', str(path)], text=True)
    blocks = []
    for line in text.splitlines():
        if line.startswith('Latitude'):
            blocks.append([])
        elif line.strip():
            _require(bool(blocks), 'decoded header absent')
            blocks[-1].append(tuple(map(float, line.split())))
    _require(len(metadata) == len(blocks), 'decoded message count')
    for message, rows in zip(metadata, blocks):
        yield (message['shortName'], _provider_stamp(message['validityDate'], message['validityTime']),
               central_value(rows, config))


def collect_provider(records, config):
    """Reject duplicate validity per variable; retain initial RAW SSRD for audit."""
    values = {v['short_name']: {} for v in config['variables']}
    sources = {s: {} for s in values}
    for short_name, validity, value, source in records:
        _require(short_name in values, 'unexpected provider variable')
        _require(validity.tzinfo is not None and validity.utcoffset() == timedelta(0), 'provider timestamp must be UTC')
        _require(validity not in values[short_name], 'duplicate provider timestamp for ' + short_name)
        _require(math.isfinite(value), 'non-finite provider value')
        _require(short_name != 'ssrd' or value >= 0, 'negative SSRD')
        values[short_name][validity] = value
        sources[short_name][validity] = source
    return values, sources


def construct_rows(values, sources, config, *, index=None):
    """Map provider validity via configured offsets and apply frozen arithmetic.

    The optional index supports small pure tests; production uses the full horizon.
    """
    config = validate_weather_config(config)
    index = canonical_index(config) if index is None else list(index)
    offsets = {v['short_name']: timedelta(hours=v['provider_validity_offset_hours'])
               for v in config['variables']}
    data = {c: [] for c in COLUMNS}
    lineage = []
    for t in index:
        data['timestamp_utc'].append(t)
        for s, column in _PROVIDER_COLUMNS.items():
            validity = t + offsets[s]
            _require(validity in values[s], 'missing canonical hour or required endpoint for ' + s + ' at ' + _iso(t))
            data[column].append(values[s][validity])
        data[COLUMNS[3]].append(data[COLUMNS[2]][-1] / config['ssrd']['accumulation_seconds'])
        for u, v, speed in ((COLUMNS[4], COLUMNS[5], COLUMNS[6]), (COLUMNS[7], COLUMNS[8], COLUMNS[9])):
            data[speed].append(math.sqrt(data[u][-1] * data[u][-1] + data[v][-1] * data[v][-1]))
        if t == index[0] or (t.month, t.day, t.hour) == (12, 31, 23):
            valid = t + offsets['ssrd']
            lineage.append({'canonical_timestamp_utc': _iso(t), 'ssrd_provider_validity_utc': _iso(valid),
                            'raw_path': str(sources['ssrd'][valid]), 'mapping_verified': True})
    return data, lineage


def _table(data):
    import pyarrow as pa
    arrays = [pa.array(data[c], type=pa.timestamp('us', tz='UTC') if c == 'timestamp_utc' else pa.float64())
              for c in COLUMNS]
    schema = pa.schema([pa.field(c, a.type, nullable=False) for c, a in zip(COLUMNS, arrays)],
                       metadata={b'artifact_status': b'INTERIM', b'timestamp_semantics': b'interval_start'})
    return pa.Table.from_arrays(arrays, schema=schema)


def validate_table(table, config):
    """Independent re-read audit: index, numeric quality and recomputed formulas."""
    import pyarrow as pa
    _require(table.column_names == list(COLUMNS), 'output column order')
    _require(table.schema.field('timestamp_utc').type == pa.timestamp('us', tz='UTC'), 'UTC timestamp dtype')
    _require(all(table.schema.field(c).type == pa.float64() for c in COLUMNS[1:]), 'numeric dtypes')
    _require(all(table[c].null_count == 0 for c in COLUMNS), 'missing output values')
    data = table.to_pydict()
    stamps = data['timestamp_utc']
    _require(stamps == canonical_index(config), 'canonical index gaps/duplicates/coverage')
    _require(all(math.isfinite(v) for c in COLUMNS[1:] for v in data[c]), 'non-finite output')
    _require(all(v >= 0 for v in data[COLUMNS[2]]), 'negative output SSRD')
    errors = {COLUMNS[3]: max(abs(a - b / config['ssrd']['accumulation_seconds'])
                             for a, b in zip(data[COLUMNS[3]], data[COLUMNS[2]]))}
    for u, v, speed in ((COLUMNS[4], COLUMNS[5], COLUMNS[6]), (COLUMNS[7], COLUMNS[8], COLUMNS[9])):
        errors[speed] = max(abs(s - math.sqrt(a*a + b*b)) for a, b, s in zip(data[u], data[v], data[speed]))
    _require(all(e == 0 for e in errors.values()), 'derived formula error')
    return {'status': 'PASS', 'rows': table.num_rows, 'unique_timestamps': len(set(stamps)),
            'first': _iso(stamps[0]), 'last': _iso(stamps[-1]),
            'yearly_rows': dict(sorted(Counter(str(t.year) for t in stamps).items())),
            'gaps': 0, 'duplicates': 0, 'missing': 0, 'NaN': 0, 'non_finite': 0,
            'negative_ssrd': 0, 'derived_max_errors': errors}


def compare_tables(actual, reference):
    """Scientific exact equality; Parquet metadata/compression need not match."""
    _require(actual.num_rows == reference.num_rows, 'regression row count')
    _require(actual.column_names == reference.column_names == list(COLUMNS), 'regression column order')
    _require(all(actual.schema.field(c).type == reference.schema.field(c).type for c in COLUMNS), 'regression dtypes')
    a, b = actual.to_pydict(), reference.to_pydict()
    _require(a['timestamp_utc'] == b['timestamp_utc'], 'regression timestamp sequence')
    _require(all(math.isfinite(v) for data in (a, b) for c in COLUMNS[1:] for v in data[c]), 'non-finite regression value')
    errors = {c: max(abs(x-y) for x, y in zip(a[c], b[c])) for c in COLUMNS[1:]}
    _require(all(e == 0 for e in errors.values()), 'non-zero numeric regression')
    return {'status': 'PASS', 'rows': actual.num_rows, 'identical_timestamps': True,
            'identical_column_order': True, 'identical_dtypes': True,
            'per_column_max_absolute_difference': errors, 'max_absolute_difference': max(errors.values())}


def build_metadata(config, config_path, inputs, output, validation, lineage):
    """Config identity and input/output provenance without availability claims."""
    import pyarrow as pa
    return {'artifact_status': 'INTERIM', 'created_utc': datetime.now(timezone.utc).isoformat(),
            'config': {'path': str(config_path), 'sha256': _digest(config_path), 'normalized': deepcopy(config)},
            'source_class': 'REANALYSIS', 'provider': config['provider'], 'dataset': config['dataset'],
            'product_type': config['product_type'], 'source_doi': config['source_doi'],
            'anchor': config['anchor'], 'time_semantics': config['canonical_time'],
            'provider_columns': _PROVIDER_COLUMNS, 'derived_columns': list(config['derivations']),
            'derivation_formulas': config['derivations'], 'raw_objects': inputs, 'boundary_object': inputs[-1],
            'transformations': ['exact single-grid-point extraction', 'SSRD endpoint to preceding interval start',
                                'configured deterministic derivations', 'UTC float64 Parquet serialization'],
            'processing': config['processing'], **config['availability'],
            'excluded_initial_ssrd_validity': config['horizon']['start'], 'ssrd_lineage': lineage,
            'output': {'path': str(output), 'sha256': _digest(output), 'bytes': Path(output).stat().st_size,
                       'rows': validation['rows'], 'columns': list(COLUMNS)},
            'software': {'python': platform.python_version(), 'pyarrow': pa.__version__,
                         'native_eccodes': subprocess.check_output(['grib_get', '-V'], stderr=subprocess.STDOUT, text=True).strip()},
            'implementation_sha256': _digest(__file__)}


def harmonise_weather(config_path, raw_root, output_dir, *, validated_inventory=None,
                      reference=None, pilot=None, progress=None):
    """Reproduce weather into a new directory only. Never overwrite any object.

    validated_inventory must be an explicit PASS structural audit with inventory
    entries containing absolute_path, sha256 and byte_size. Its trust is explicit;
    all file contents are hash-bound to that audit, rather than silently assumed.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    config = load_weather_config(config_path)
    objects = discover_inputs(raw_root, config)
    output_dir = Path(output_dir)
    _require(not output_dir.exists(), 'output directory already exists')
    inventory = None
    if validated_inventory:
        audit = json.loads(Path(validated_inventory).read_text())
        _require(audit['status'] == 'PASS', 'input inventory is not PASS')
        entries = audit['inventory']
        inventory = {str(Path(e['absolute_path']).resolve()): e for e in entries}
        _require(len(inventory) == len(entries), 'duplicate input inventory path')
    inputs = []

    def records():
        for i, (path, year, month) in enumerate(objects):
            boundary = year is None
            initial = path.stat()
            digest = _digest(path)
            if boundary:
                _require(digest == config['ssrd']['boundary_sha256'], 'boundary checksum')
            if inventory is not None:
                entry = inventory.get(str(path.resolve()))
                _require(entry is not None and entry['sha256'] == digest and entry['byte_size'] == initial.st_size,
                         'validated inventory checksum/size mismatch')
                validation = {'status': 'PASS', 'mode': 'explicit hash-bound validated inventory'}
            else:
                validation = validate_grib(path, config, year=year, month=month, boundary=boundary)
            times = {v['short_name']: set() for v in config['variables'] if not boundary or v['short_name'] == 'ssrd'}
            start = _stamp(config['ssrd']['final_provider_validity']) if boundary else datetime(year, month, 1, tzinfo=timezone.utc)
            hours = 1 if boundary else len(build_monthly_request(year, month, config)['request']['day']) * 24
            expected = {start + timedelta(hours=h) for h in range(hours)}
            count = 0
            for s, t, value in decode_central(path, config):
                _require(s in times and t in expected and t not in times[s], 'object variable/validity coverage')
                times[s].add(t)
                count += 1
                yield s, t, value, path
            _require(all(ts == expected for ts in times.values()), 'object missing provider hour')
            final = path.stat()
            _require((initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns, initial.st_ino) ==
                     (final.st_size, final.st_mtime_ns, final.st_ctime_ns, final.st_ino) and _digest(path) == digest,
                     'input changed during extraction')
            inputs.append({'path': str(path), 'bytes': initial.st_size, 'sha256': digest,
                           'boundary': boundary, 'messages': count, 'validation': validation})
            if progress:
                progress(i + 1, len(objects), path)

    values, sources = collect_provider(records(), config)
    data, lineage = construct_rows(values, sources, config)
    table = _table(data)
    validate_table(table, config)
    output_dir.mkdir(parents=True, exist_ok=False)
    output = output_dir / f"weather_{config['scenario_id'].lower()}_2019_2025_reproduced.parquet"
    pq.write_table(table, output, compression='snappy', version='2.6')
    actual = pq.read_table(output)
    validation = validate_table(actual, config)
    _require(actual.to_pydict() == data, 'serialized provider/derived values changed')
    validation['ssrd_lineage'] = lineage
    validation['annual_boundary_checks'] = sum(item['canonical_timestamp_utc'][5:] == '12-31T23:00:00Z' for item in lineage)
    _require(sources['ssrd'][_stamp(config['ssrd']['final_provider_validity'])] == objects[-1][0], 'final SSRD boundary source')
    validation['final_boundary_verified'] = True
    if reference:
        validation['regression_c2b'] = {**compare_tables(actual, pq.read_table(reference)),
                                       'path': str(reference), 'sha256': _digest(reference)}
    if pilot:
        target = pq.read_table(pilot)
        years = {t.year for t in target['timestamp_utc'].to_pylist()}
        subset = actual.filter(pa.array([t.year in years for t in actual['timestamp_utc'].to_pylist()]))
        validation['regression_pilot'] = {**compare_tables(subset, target), 'path': str(pilot), 'sha256': _digest(pilot)}
    metadata = build_metadata(config, config_path, inputs, output, validation, lineage)
    if validated_inventory:
        metadata['validated_inventory'] = {'path': str(validated_inventory), 'sha256': _digest(validated_inventory)}
    validation['output_sha256'] = metadata['output']['sha256']
    for name, record in (('reproduction_metadata.json', metadata), ('reproduction_validation.json', validation)):
        with (output_dir / name).open('x') as stream:
            json.dump(record, stream, indent=2, allow_nan=False)
            stream.write('\n')
    return validation
