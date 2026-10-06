#!/usr/bin/env python3
"""Validate registry schema, entries, CSV parity and optional country matrix."""
import argparse
import sys

from registry_schema import (DEFAULT_CSV, DEFAULT_MATRIX, DEFAULT_REGISTRY,
                             RegistryError, read_csv, read_registry,
                             validate_matrix, validate_pair)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', help='Validate one YAML registry or CSV; omit to validate the repository set')
    parser.add_argument('--registry', default=DEFAULT_REGISTRY, help='Canonical JSON-compatible YAML registry')
    parser.add_argument('--csv', default=DEFAULT_CSV, help='CSV mirror')
    parser.add_argument('--matrix', default=DEFAULT_MATRIX, help='Country/source matrix')
    args = parser.parse_args()
    try:
        if args.path:
            entries = read_csv(args.path) if str(args.path).endswith('.csv') else read_registry(args.path)
            print('VALID: {} sources'.format(len(entries)))
        else:
            entries = validate_pair(args.registry, args.csv)
            count = validate_matrix(args.matrix, entries)
            print('VALID: {} sources; YAML/CSV agree; {} country/source mappings'.format(len(entries), count))
    except (RegistryError, OSError, UnicodeError) as error:
        print('INVALID: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
