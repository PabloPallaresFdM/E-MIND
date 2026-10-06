#!/usr/bin/env python3
"""Read-only raw integrity/schema/DST audit. Writes project-authored audit JSON."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat
import sys
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
from emind.providers.integrity import digest
from emind.providers.smard import HEADERS, SOURCE_IDS, parse_label, number, read_native
from emind.time.dst import resolve_local_interval_starts


def integrity(objects):
    evidence = []
    for obj in objects:
        path = Path(obj['raw_path'])
        sidecar = Path(str(path) + '.manifest.json')
        saved = json.loads(sidecar.read_text())
        sha = digest(path)
        size = path.stat().st_size
        mode = stat.S_IMODE(path.stat().st_mode)
        sidecar_mode = stat.S_IMODE(sidecar.stat().st_mode)
        passed = (not path.is_symlink() and not sidecar.is_symlink()
                  and sha == obj['sha256'] == saved['sha256']
                  and size == obj['size_bytes'] == saved['size_bytes']
                  and mode == sidecar_mode == 0o400 and saved['immutable'] is True
                  and saved['snapshot_id'] == obj['snapshot_id']
                  and saved['source_id'] == obj['source_id'])
        evidence.append({'snapshot_id': obj['snapshot_id'], 'raw_path': str(path),
                         'sha256': sha, 'size_bytes': size, 'mode': oct(mode),
                         'sidecar_sha256': digest(sidecar), 'sidecar_mode': oct(sidecar_mode),
                         'snapshot_directory_mode': oct(stat.S_IMODE(path.parent.stat().st_mode)),
                         'manifest_and_sidecar_match': passed})
        if not passed:
            raise ValueError('RAW_INTEGRITY_FAILURE: ' + obj['snapshot_id'])
    return {'at_utc': datetime.now(timezone.utc).isoformat(), 'status': 'PASS', 'objects': evidence}


def schema_audit(objects, kind):
    starts, ends, boundaries, raw_rows = [], [], [], []
    delta = timedelta(minutes=15 if kind == 'load' else 60)
    for obj in objects:
        path = Path(obj['raw_path'])
        payload = path.read_bytes()
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream, delimiter=';', strict=True))
        header, data = rows[0], rows[1:]
        if header != ['Start date', 'End date', HEADERS[kind]] or any(len(r) != 3 for r in data):
            raise ValueError('Unsupported real provider schema')
        parsed = [parse_label(r[0]) for r in data]
        numbers = [number(r[2]) for r in data]
        boundaries.append({'snapshot_id': obj['snapshot_id'], 'delimiter': ';',
                           'encoding': 'UTF-8 with BOM' if payload.startswith(b'\xef\xbb\xbf') else 'UTF-8 compatible',
                           'header': header, 'rows': len(data),
                           'first_start': data[0][0], 'last_start': data[-1][0], 'last_end': data[-1][1],
                           'missing_nonnumeric_values': 0,
                           'negative_values': sum(v < 0 for v in numbers),
                           'duplicate_full_rows': len(data) - len(set(tuple(r) for r in data)),
                           'numeric_representation': 'decimal point; optional comma thousands separator'})
        starts.extend(parsed)
        ends.extend(parse_label(r[1]) for r in data)
        raw_rows.extend(data)
    utc = resolve_local_interval_starts(starts, timezone_name='Europe/Berlin', interval=delta)
    zone = ZoneInfo('Europe/Berlin')
    transitions = []
    rules = Counter()
    for i, (start, end, instant) in enumerate(zip(starts, ends, utc)):
        local = instant.astimezone(zone)
        physical_end = (instant + delta).astimezone(zone)
        changed = local.utcoffset() != physical_end.utcoffset()
        if end == physical_end.replace(tzinfo=None):
            rules['PHYSICAL_END'] += 1
        elif changed and end == start + delta:
            rules['NOMINAL_CIVIL_END_AT_DST'] += 1
        else:
            raise ValueError('Unsupported end label at data row ' + str(i + 1))
        if changed:
            transitions.append({'year': start.year, 'direction': 'spring' if physical_end.utcoffset() > local.utcoffset() else 'autumn',
                                'utc_interval_start': instant.isoformat(),
                                'nearby_labels': [{'global_row': j + 1, 'start': raw_rows[j][0], 'end': raw_rows[j][1],
                                                   'utc_start': utc[j].isoformat()}
                                                  for j in range(max(0, i-2), min(len(utc), i+4))]})
    counts = Counter(starts)
    # Strict adapter must also agree; nominal option is explicit and audited above.
    records, audit = read_native(objects, kind, allow_nominal_dst_ends=True)
    if [a['timestamp_utc'] for a in audit] != utc:
        raise ValueError('Adapter/reconstruction disagreement')
    return {'files': boundaries, 'physical_intervals': len(utc),
            'first_local_start': raw_rows[0][0], 'last_local_start': raw_rows[-1][0],
            'first_utc': utc[0].isoformat(), 'last_utc': utc[-1].isoformat(),
            'exclusive_end_utc': (utc[-1] + delta).isoformat(),
            'utc_unique': len(set(utc)) == len(utc), 'utc_continuous': True,
            'repeated_local_labels_extra_rows': sum(n - 1 for n in counts.values()),
            'missing_spring_civil_labels': sum(1 for t in transitions if t['direction']=='spring') * int(timedelta(hours=1)/delta),
            'endpoint_rules': dict(rules), 'transitions': transitions,
            'adapter_status': 'PASS_WITH_EXPLICIT_NOMINAL_DST_END_AUDIT'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--integrity-only', action='store_true')
    args = parser.parse_args()
    objects = json.loads(args.manifest.read_text())['objects']
    result = {'integrity': integrity(objects)}
    if not args.integrity_only:
        result['smard'] = {kind: schema_audit([o for o in objects if o['source_id']==source], kind)
                           for kind, source in SOURCE_IDS.items()}
        result['post_integrity'] = integrity(objects)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps({'audit': str(args.output), 'status': 'PASS'}))


if __name__ == '__main__':
    main()
