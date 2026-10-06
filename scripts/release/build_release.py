#!/usr/bin/env python3
"""Build or verify a NEW offline local RC or opted-in dev package; never publish."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from emind.release import build_release, verify_release
from emind.release_inputs import de_cent_spec, de_cent_rc_spec
from emind.scenario import sha256

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--rc', action='store_true', help='Gated local 0.1.0-rc.1 build')
    mode.add_argument('--allow-dev', action='store_true', help='Explicit development dry-run opt-in')
    args = parser.parse_args()
    if args.verify:
        result = verify_release(args.verify)
    else:
        if not all([args.data_root, args.candidate, args.output]):
            parser.error('--data-root, --candidate and --output required for build')
        if not (args.rc or args.allow_dev):
            parser.error('Build requires --rc or --allow-dev')
        repo = Path(__file__).resolve().parents[2]
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
        implementation = {p: sha256(repo / p) for p in ['src/emind/release.py', 'src/emind/release_inputs.py',
                          'scripts/release/build_release.py', 'src/emind/scenario.py',
                          'src/emind/release_gates.py', 'src/emind/cff_schema_1.2.0.json']}
        spec, inputs = (de_cent_rc_spec if args.rc else de_cent_spec)(repo, args.data_root, args.candidate)
        result = build_release(spec, inputs, args.output, commit, implementation, allow_dev=args.allow_dev)
    print(json.dumps(result, indent=2))
