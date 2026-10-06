#!/usr/bin/env python3
"""Offline ERA5 harmonisation into a new output directory; no provider access."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from emind.weather import harmonise_weather


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--raw-root', required=True)
    parser.add_argument('--output-dir', required=True, help='must not already exist')
    parser.add_argument('--validated-inventory', help='explicit PASS structural audit; each input hash checked')
    parser.add_argument('--reference', help='frozen complete-horizon Parquet for exact regression')
    parser.add_argument('--pilot', help='independent pilot Parquet for exact subset regression')
    args = parser.parse_args(argv)
    def progress(index, total, path):
        print(f'Read {index}/{total}: {path.name}', file=sys.stderr, flush=True)
    result = harmonise_weather(args.config, args.raw_root, args.output_dir,
                               validated_inventory=args.validated_inventory,
                               reference=args.reference, pilot=args.pilot, progress=progress)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
