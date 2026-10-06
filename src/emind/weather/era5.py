"""Offline ERA5 requests and ecCodes validation; retrieval requires explicit execution.

Native grib_ls and grib_get_data must be on PATH for validation. Provider bytes
are never transformed. Request syntax follows the validated Scratch acquisition.
"""
import calendar
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import tempfile

from .config import validate_weather_config


def _stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _request(config, stamp, days, variables, times):
    bbox = config['audit_bbox']
    return {'dataset': config['dataset'], 'request': {
        'product_type': [config['product_type']], 'variable': variables,
        'year': [f'{stamp.year:04d}'], 'month': [f'{stamp.month:02d}'],
        'day': days, 'time': times,
        'area': [bbox[k] for k in ('north', 'west', 'south', 'east')],
        'data_format': config['acquisition_format'].lower(),
        'download_format': 'unarchived'}}


def build_monthly_request(year, month, config):
    """Return dataset, normalized provider request and deterministic filename."""
    config = validate_weather_config(config)
    if type(year) is not int or type(month) is not int:
        raise ValueError('Year and month must be integers')
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    days = calendar.monthrange(year, month)[1]
    end = start + timedelta(days=days, hours=-1)
    if start < _stamp(config['horizon']['start']) or end > _stamp(config['horizon']['end_inclusive']):
        raise ValueError('Month outside complete-year acquisition horizon')
    plan = _request(config, start, [f'{d:02d}' for d in range(1, days + 1)],
                    [v['provider_name'] for v in config['variables']],
                    [f'{h:02d}:00' for h in range(24)])
    plan['filename'] = f"era5_{config['scenario_id'].lower()}_{year:04d}_{month:02d}.grib"
    return plan


def build_boundary_request(config):
    """Only the configured final SSRD endpoint; never a horizon extension."""
    config = validate_weather_config(config)
    stamp = _stamp(config['ssrd']['final_provider_validity'])
    ssrd = next(v for v in config['variables'] if v['short_name'] == 'ssrd')
    plan = _request(config, stamp, [f'{stamp.day:02d}'], [ssrd['provider_name']],
                    [stamp.strftime('%H:%M')])
    plan['filename'] = f"era5_{config['scenario_id'].lower()}_ssrd_{stamp:%Y_%m_%d_%H%M}_utc.grib"
    return plan


def dry_run(plan, destination_root):
    """Serialize a request intent without importing CDS or reading credentials."""
    return {**deepcopy(plan), 'path': str(Path(destination_root) / plan['filename'])}


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition, reason):
    if not condition:
        raise ValueError('GRIB validation failed: ' + reason)


def _provider_stamp(date, time):
    return datetime.strptime(str(date) + f'{int(time):04d}', '%Y%m%d%H%M').replace(tzinfo=timezone.utc)


def validate_grib(path, config, *, year=None, month=None, boundary=False):
    """Read-only native ecCodes validation. Raise ValueError on invalid contents.

    Coordinate order follows provider scanning. SSRD steps are interpreted in
    their actual units and relative to each message's reference timestamp.
    """
    config = validate_weather_config(config)
    if boundary:
        if year is not None or month is not None:
            raise ValueError('Boundary validation does not accept a month')
        expected_times = {_stamp(config['ssrd']['final_provider_validity'])}
        variables = [v for v in config['variables'] if v['short_name'] == 'ssrd']
    else:
        build_monthly_request(year, month, config)
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        expected_times = {start + timedelta(hours=h)
                          for h in range(calendar.monthrange(year, month)[1] * 24)}
        variables = config['variables']
    keys = ('shortName,paramId,units,dataDate,dataTime,validityDate,validityTime,'
            'startStep,endStep,stepUnits,stepType,timeRangeIndicator,typeOfLevel,level,'
            'gridType,Ni,Nj,iDirectionIncrementInDegrees,jDirectionIncrementInDegrees,'
            'latitudeOfFirstGridPointInDegrees,longitudeOfFirstGridPointInDegrees,'
            'latitudeOfLastGridPointInDegrees,longitudeOfLastGridPointInDegrees,'
            'numberOfDataPoints,numberOfMissing')
    path = Path(path)
    initial = path.stat()
    sha = _digest(path)
    messages = json.loads(subprocess.check_output(
        ['grib_ls', '-j', '-p', keys, str(path)], text=True))['messages']
    _require(len(messages) == len(expected_times) * len(variables), 'message count')
    raw = subprocess.check_output(['grib_get_data', '-F', '%.17g', str(path)], text=True)
    blocks = []
    for line in raw.splitlines():
        if line.startswith('Latitude'):
            blocks.append([])
        elif line.strip():
            _require(bool(blocks), 'decoded header')
            blocks[-1].append(tuple(map(float, line.split())))
    _require(len(blocks) == len(messages), 'decoded message count')
    bbox = config['audit_bbox']
    resolution = config['grid']['resolution_degrees']
    coords = {(bbox['south'] + i * resolution, bbox['west'] + j * resolution)
              for i in range(3) for j in range(3)}
    identities = {v['short_name']: v for v in variables}
    times = {s: Counter() for s in identities}
    units = {'K': 'K', 'J/m2': 'J m**-2', 'm/s': 'm s**-1'}
    step_seconds = {0: 60, 1: 3600, 2: 86400, 10: 10800, 11: 21600, 12: 43200, 13: 1}
    sign = dict(negative=0, zero=0, positive=0)
    for message, rows in zip(messages, blocks):
        s = message['shortName']
        _require(s in identities, 'parameter shortName')
        _require(message['paramId'] == identities[s]['param_id'] and
                 message['units'] == units[identities[s]['unit']], 'parameter identity/units')
        valid = _provider_stamp(message['validityDate'], message['validityTime'])
        _require(valid in expected_times, 'validity outside requested coverage')
        times[s][valid] += 1
        _require(message['numberOfMissing'] == 0, 'missing values')
        _require(message['gridType'] == 'regular_ll' and message['Ni'] == message['Nj'] == 3
                 and message['numberOfDataPoints'] == 9, 'grid geometry')
        _require(message['typeOfLevel'] == 'surface' and message['level'] == 0, 'level encoding')
        _require(all(message[k] == resolution for k in
                     ('iDirectionIncrementInDegrees', 'jDirectionIncrementInDegrees')), 'grid increments')
        _require(len(rows) == 9 and {(a, b) for a, b, _ in rows} == coords, 'coordinate set')
        _require((message['latitudeOfFirstGridPointInDegrees'], message['longitudeOfFirstGridPointInDegrees']) == rows[0][:2]
                 and (message['latitudeOfLastGridPointInDegrees'], message['longitudeOfLastGridPointInDegrees']) == rows[-1][:2], 'grid endpoints/scanning')
        _require(all(math.isfinite(v) for _, _, v in rows), 'non-finite values')
        if s == 'ssrd':
            _require(message['stepType'] == 'accum' and message['timeRangeIndicator'] == 4, 'SSRD accumulation')
            _require(message['stepUnits'] in step_seconds, 'unsupported step units')
            seconds = step_seconds[message['stepUnits']]
            base = _provider_stamp(message['dataDate'], message['dataTime'])
            _require((message['endStep'] - message['startStep']) * seconds == config['ssrd']['accumulation_seconds']
                     and base + timedelta(seconds=message['endStep'] * seconds) == valid, 'SSRD duration/endpoint')
            for _, _, value in rows:
                sign['negative' if value < 0 else 'zero' if value == 0 else 'positive'] += 1
        else:
            _require(message['stepType'] == 'instant', 'instantaneous stepType')
    _require(sign['negative'] == 0, 'negative SSRD')
    for counter in times.values():
        _require(counter == Counter({t: 1 for t in expected_times}), 'duplicate/missing hourly variable')
    final = path.stat()
    _require((initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns, initial.st_ino) ==
             (final.st_size, final.st_mtime_ns, final.st_ctime_ns, final.st_ino) and _digest(path) == sha,
             'object changed during validation')
    iso = lambda t: t.strftime('%Y-%m-%dT%H:%M:%SZ')
    return {'status': 'PASS', 'path': str(path), 'bytes': final.st_size, 'sha256': sha,
            'message_count': len(messages), 'scalar_count': len(messages) * 9,
            'variables': list(identities), 'validity_coverage': {s: {
                'count': sum(c.values()), 'first': iso(min(c)), 'last': iso(max(c))} for s, c in times.items()},
            'grid': {'status': 'PASS', 'type': 'regular_ll', 'coordinates': sorted(coords)},
            'missing_count': 0, 'non_finite_count': 0, 'ssrd_sign_counts': sign,
            'temporal_semantics': {'status': 'PASS', 'instantaneous': 'provider validity',
                                   'ssrd': 'one-hour accumulation ending at provider validity'}}


def build_provenance(plan, validation, retrieval_utc=None):
    """Small credential-free record; retrieval time denotes completed execution."""
    _require(validation['status'] == 'PASS', 'unaccepted validation')
    stamp = _stamp(retrieval_utc) if retrieval_utc else datetime.now(timezone.utc)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError('Retrieval timestamp must be UTC')
    software = {'python': platform.python_version()}
    for package in ('cdsapi', 'eccodes'):
        try:
            software[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    software['native_eccodes'] = subprocess.check_output(
        ['grib_get', '-V'], stderr=subprocess.STDOUT, text=True).strip()
    return {'dataset': plan['dataset'], 'request': deepcopy(plan['request']),
            'retrieval_utc': stamp.isoformat(), 'final_path': validation['path'],
            'bytes': validation['bytes'], 'sha256': validation['sha256'],
            'validation': deepcopy(validation), 'software': software}


def execute_request(config, destination_root, *, year=None, month=None, boundary=False):
    """Explicit future acquisition: partial -> validate -> no-clobber atomic freeze.

    Failed partials stay visibly partial for manual review. Returns provenance;
    caller is responsible for recording it. Never serializes client or errors.
    """
    if boundary and (year is not None or month is not None):
        raise ValueError('Boundary execution does not accept a month')
    plan = build_boundary_request(config) if boundary else build_monthly_request(year, month, config)
    root = Path(destination_root)
    target = root / plan['filename']
    if os.path.lexists(target):
        raise FileExistsError('Provider destination already exists')
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=target.name + '.', suffix='.partial', dir=root)
    os.close(fd)
    partial = Path(name)
    import cdsapi
    try:
        client = cdsapi.Client(quiet=True, debug=False)
        client.retrieve(plan['dataset'], plan['request'], str(partial))
    except Exception:
        raise RuntimeError('CDS retrieval failed; partial object not accepted') from None
    completed = datetime.now(timezone.utc).isoformat()
    validation = validate_grib(partial, config, year=year, month=month, boundary=boundary)
    validation['path'] = str(target)
    record = build_provenance(plan, validation, completed)
    with partial.open('rb') as stream:
        os.fsync(stream.fileno())
    partial.chmod(0o400)
    os.link(partial, target)  # Atomic and refuses replacement, unlike os.replace.
    partial.unlink()
    return record
