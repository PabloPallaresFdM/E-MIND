#!/usr/bin/env python3
"""Add or fully replace one coordinator-approved entry; no network or downloads."""
import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sys
import tempfile

from registry_schema import (DEFAULT_CSV, DEFAULT_REGISTRY, RegistryError,
                             csv_text, read_entry, validate_pair, validate_sources, yaml_text)


@contextmanager
def lock(registry):
    # Stable lock-file inode is kept intentionally; never unlink an active lock.
    with registry.with_suffix(registry.suffix + '.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def stage(path, text):
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(name)
    except BaseException:
        Path(name).unlink()
        raise


def update_pair(registry, csv_path, entries):
    # Stage both before touching either destination. Individual renames are atomic;
    # cross-file atomicity is impossible. The validator detects interruption drift.
    old_yaml = registry.read_text(encoding='utf-8')
    staged = []
    replaced = False
    try:
        staged.append(stage(registry, yaml_text(entries)))
        staged.append(stage(csv_path, csv_text(entries)))
        os.replace(staged[0], registry)
        replaced = True
        os.replace(staged[1], csv_path)
    except OSError:
        if replaced:
            recovery = stage(registry, old_yaml)
            os.replace(recovery, registry)
        raise
    finally:
        for path in staged:
            if path.exists():
                path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='One full entry as JSON or documented flat YAML')
    parser.add_argument('--operation', choices=('add', 'update'), default='add')
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument('--csv', type=Path, default=DEFAULT_CSV)
    parser.add_argument('--dry-run', action='store_true', help='Validate the proposed change without writing files')
    args = parser.parse_args()
    try:
        registry, csv_path = args.registry.resolve(), args.csv.resolve()
        if registry == csv_path or Path(args.input).resolve() in (registry, csv_path):
            raise RegistryError('Input, registry and CSV paths must be distinct')
        entry = read_entry(args.input)
        validate_sources([entry])

        def prepare():
            entries = validate_pair(registry, csv_path)
            positions = [i for i, existing in enumerate(entries) if existing['source_id'] == entry['source_id']]
            if args.operation == 'add' and positions:
                raise RegistryError('source_id already exists; use explicit --operation update')
            if args.operation == 'update' and not positions:
                raise RegistryError('Cannot update an unknown source_id')
            if positions:
                entries[positions[0]] = entry
            else:
                entries.append(entry)
            validate_sources(entries)
            return entries

        if args.dry_run:
            prepare()
            print('VALID dry run; no files changed')
        else:
            with lock(registry):
                entries = prepare()
                update_pair(registry, csv_path, entries)
            print('Registered entry; {} total sources; YAML/CSV updated'.format(len(entries)))
    except (RegistryError, OSError, UnicodeError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
