#!/usr/bin/env python3
"""ERA5 request preview by default; validation is offline; retrieval is opt-in."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from emind.weather import (load_weather_config, build_monthly_request,
                           build_boundary_request, dry_run, validate_grib, execute_request)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--destination-root', required=True)
    parser.add_argument('--year', type=int)
    parser.add_argument('--month', type=int)
    parser.add_argument('--boundary', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='default mode')
    mode.add_argument('--validate', metavar='GRIB', help='read existing provider object')
    mode.add_argument('--execute', action='store_true', help='explicitly enable CDS retrieval')
    args = parser.parse_args(argv)
    if args.boundary and (args.year is not None or args.month is not None):
        parser.error('--boundary cannot be combined with --year/--month')
    if not args.boundary and (args.year is None or args.month is None):
        parser.error('monthly operations require --year and --month')
    config = load_weather_config(args.config)
    plan = build_boundary_request(config) if args.boundary else build_monthly_request(args.year, args.month, config)
    kwargs = dict(year=args.year, month=args.month, boundary=args.boundary)
    if args.validate:
        result = validate_grib(args.validate, config, **kwargs)
    elif args.execute:
        result = execute_request(config, args.destination_root, **kwargs)
    else:
        result = dry_run(plan, args.destination_root)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
