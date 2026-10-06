#!/usr/bin/env python3
"""Offline C0/C1 audit. Output JSON is created exclusively, never overwritten."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from emind.weather import load_weather_config
from emind.weather.structural import structural_audit, compare_structural


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit', choices=('structural', 'spatial'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--raw-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--checksum-inventory', help='C0 inventory supplying expected RAW hashes')
    parser.add_argument('--reference', help='historical report for regression after RAW re-read')
    args = parser.parse_args(argv)
    target = Path(args.output)
    if target.exists():
        parser.error('Output already exists')
    config = load_weather_config(args.config)
    def progress(i, n, path):
        print(f'Read {i}/{n}: {path.name}', file=sys.stderr, flush=True)
    if args.audit == 'structural':
        result = structural_audit(args.raw_root, config, checksum_inventory=args.checksum_inventory, progress=progress)
        comparison = compare_structural
    else:
        from emind.weather.spatial import spatial_audit, compare_spatial
        result = spatial_audit(args.raw_root, config, validated_inventory=args.checksum_inventory, progress=progress)
        comparison = compare_spatial
    if args.reference:
        result['regression'] = comparison(result, json.loads(Path(args.reference).read_text()))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(result.get('regression', {'status': 'PASS'})))


if __name__ == '__main__':
    main()
