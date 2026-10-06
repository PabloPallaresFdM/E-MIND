#!/usr/bin/env python3
"""Read-only DE_CENT audit/check CLI; no downloads or scientific output writer.

raw-audit reconstructs immutable provider coverage without claiming canonical
coverage. canonical-check additionally builds/crops in memory to the exact UTC
calendar target and fails on missing boundaries. Both print audit JSON only.
Actual scientific invocation remains a separate, owner-authorised phase.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import platform
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
from emind.harmonise.coverage import (CoverageError, TARGET_START, TARGET_END,
                                     crop_to_target, validate_index)
from emind.harmonise.fuel import extract_germany
from emind.harmonise.smard import aggregate_quarter_hour_load, canonicalise_market_hours
from emind.providers.integrity import digest, verify_raw
from emind.providers.smard import read_native, SOURCE_IDS

FUEL_SOURCE = 'eu_ec_weekly_oil_bulletin_diesel'


def source_commit():
    """Return the actual checkout commit or null for an unversioned source tree."""
    result = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True)
    if result.returncode or Path(result.stdout.strip()).resolve() != REPO.resolve():
        return None
    return subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'],
                                   text=True).strip()


def coverage(audit):
    interval = timedelta(minutes=audit[0]['interval_minutes'])
    times = validate_index([r['timestamp_utc'] for r in audit], interval)
    return {'rows': len(times), 'first_utc': times[0],
            'exclusive_end_utc': times[-1] + interval,
            'endpoint_rules': dict(Counter(r['endpoint_rule'] for r in audit))}


def build_target(load_records, market_records, load_audit, market_audit,
                 start=TARGET_START, end=TARGET_END):
    """Use remote pure APIs, then validate/crop; return rows and audit summary."""
    if len(load_records) != len(load_audit) or len(market_records) != len(market_audit):
        raise ValueError('Native records/audit lengths differ')
    load = crop_to_target(aggregate_quarter_hour_load(load_records), start, end)
    market = crop_to_target(canonicalise_market_hours(market_records), start, end)
    if [r.timestamp_utc for r in load] != [r.timestamp_utc for r in market]:
        raise CoverageError('Load/market UTC index mismatch')
    native_energy = sum((r.energy_mwh for r, a in zip(load_records, load_audit)
                         if start <= a['timestamp_utc'] < end), Decimal(0))
    if native_energy != sum((r.load_energy_mwh for r in load), Decimal(0)):
        raise ValueError('Selected-window energy conservation failure')
    native_prices = [r.price_eur_mwh for r, a in zip(market_records, market_audit)
                     if start <= a['timestamp_utc'] < end]
    if native_prices != [r.day_ahead_price_eur_mwh for r in market]:
        raise ValueError('Selected-window market values changed')
    return load, market, {'status': 'TARGET_CHECK_PASS', 'rows': len(load),
                         'first_utc': load[0].timestamp_utc, 'exclusive_end_utc': end,
                         'native_energy_mwh': native_energy,
                         'negative_market_values': sum(v < 0 for v in native_prices)}


def run(manifest_path, mode, *, allow_nominal_dst_ends=False):
    if mode not in ('raw-audit', 'canonical-check'):
        raise ValueError('Unsupported audit mode')
    manifest_path = Path(manifest_path)
    objects = json.loads(manifest_path.read_text())['objects']
    if not objects or len({o['snapshot_id'] for o in objects}) != len(objects):
        raise ValueError('Empty or duplicate snapshot manifest')
    allowed = set(SOURCE_IDS.values()) | {FUEL_SOURCE}
    if any(o['source_id'] not in allowed for o in objects):
        raise ValueError('Manifest contains an unsupported source')
    verify_raw(objects)
    parsed = {}
    for kind in ('load', 'market'):
        selected = [o for o in objects if o['source_id'] == SOURCE_IDS[kind]]
        # Caller supplies snapshots in chronological order; no civil-row sorting.
        parsed[kind] = read_native(selected, kind, allow_nominal_dst_ends=allow_nominal_dst_ends)
    workbooks = [o for o in objects if o['source_id'] == FUEL_SOURCE]
    if len(workbooks) != 1:
        raise ValueError('Expected exactly one frozen fuel workbook')
    _, fuel_audit = extract_germany(workbooks[0]['raw_path'])
    summary = {
        'mode': mode, 'status': 'RAW_AUDIT_ONLY',
        'at_utc': datetime.now(timezone.utc), 'python': platform.python_version(),
        'git_head': source_commit(),
        'manifest_sha256': digest(manifest_path),
        'implementation_sha256': {str(p.relative_to(REPO)): digest(p)
                                  for p in sorted((REPO / 'src/emind').rglob('*.py'))},
        'cli_sha256': digest(__file__),
        'raw_input_sha256': {o['snapshot_id']: o['sha256'] for o in objects},
        'raw_coverage': {kind: coverage(parsed[kind][1]) for kind in parsed},
        'target': {'start_utc': TARGET_START, 'exclusive_end_utc': TARGET_END},
        'canonical': {'status': 'NOT_CHECKED'}, 'fuel': fuel_audit,
        'allow_nominal_dst_ends': allow_nominal_dst_ends,
        'scientific_outputs_written': False,
        'release_ready': False,
    }
    exit_code = 0
    if mode == 'canonical-check':
        try:
            _, _, summary['canonical'] = build_target(
                parsed['load'][0], parsed['market'][0], parsed['load'][1], parsed['market'][1])
            summary['status'] = 'TARGET_CHECK_PASS_NOT_RELEASE_VALIDATION'
        except CoverageError as exc:
            summary['status'] = 'CANONICAL_COVERAGE_FAILED'
            summary['canonical'] = {'status': 'FAILED', 'reason': str(exc)}
            exit_code = 2
    verify_raw(objects)
    return summary, exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True,
                        help='JSON objects manifest; snapshots ordered physically within each domain')
    parser.add_argument('--mode', choices=('raw-audit', 'canonical-check'), required=True)
    parser.add_argument('--allow-nominal-dst-ends', action='store_true',
                        help='Only after provider end-label convention is explicitly audited')
    args = parser.parse_args(argv)
    try:
        summary, code = run(args.manifest, args.mode,
                            allow_nominal_dst_ends=args.allow_nominal_dst_ends)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'FAILED', 'reason': str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, default=str))
    return code


if __name__ == '__main__':
    sys.exit(main())
