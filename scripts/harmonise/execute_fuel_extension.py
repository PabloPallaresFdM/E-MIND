#!/usr/bin/env python3
"""Offline Phase04-A6 fuel audit; existing long-form schema, explicit window."""
import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import statistics
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
from emind.harmonise.fuel import extract_germany
from emind.providers.integrity import digest, verify_raw
from audit_fuel import candidate_rows, summarise, validate_schema

SEMANTICS = dict(source_class='official economic bulletin', provider='European Commission',
    native_frequency='weekly / irregular weekly observations',
    tax_variants=['WITH_TAX', 'WITHOUT_TAX'], tax_default='UNSELECTED',
    precise_available_at='UNKNOWN', currency_semantic_status='UNRESOLVED',
    interpolation=False, hourly_resampling=False,
    reference_date_meaning='Monday consumer-price reference; not publication or availability')


def extract_window(path, start, end_exclusive):
    if start >= end_exclusive:
        raise ValueError('Empty/reversed window')
    return extract_germany(path, start, end_exclusive - timedelta(days=1))


def accepted_summary(rows):
    summary, same = summarise(rows)
    if not same or any(s['nulls'] or s['duplicate_dates'] for s in summary.values()):
        raise ValueError('Unequal support, nulls or duplicate dates')
    return summary


def serialized(rows):
    return [{k: '' if v is None else str(v) for k, v in row.items()} for row in rows]


def regression(rows, historical):
    selected = serialized([r for r in rows if r['reference_date'] < '2024-01-01'])
    if selected != historical:
        raise ValueError('Historical regression differs (values, sequence or provenance)')
    return dict(status='PASS', rows=len(selected), maximum_numeric_difference='0',
                all_fields_and_sequence_identical=True)


def write_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.write('\n')
    path.chmod(0o400)


def write_csv(path, rows):
    with path.open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    path.chmod(0o400)
    with path.open(newline='') as stream:
        if list(csv.DictReader(stream)) != serialized(rows):
            raise ValueError('CSV read-back differs')


def run(manifest, provenance, output, start=date(2019, 1, 1), end_exclusive=date(2026, 1, 1)):
    if (start, end_exclusive) != (date(2019, 1, 1), date(2026, 1, 1)):
        raise ValueError('A6 benchmark audit requires the authorized 2019-2025 window')
    if output.exists():
        raise FileExistsError('Never overwrite an audit')
    snapshots = [o for o in json.loads(manifest.read_text())['objects']
                 if o['source_id'] == 'eu_ec_weekly_oil_bulletin_diesel']
    if len(snapshots) != 1:
        raise ValueError('Ambiguous frozen workbook')
    snapshot = snapshots[0]
    if snapshot['sha256'] != '666a6d642de81789085ebd3bac38d565e183dedc4026a085963abcfad670e090' or snapshot['size_bytes'] != 4459467:
        raise ValueError('Wrong frozen snapshot')
    verify_raw(snapshots)
    sidecar = Path(snapshot['raw_path'] + '.manifest.json')
    if json.loads(sidecar.read_text())['sha256'] != snapshot['sha256']:
        raise ValueError('Sidecar mismatch')
    frozen = json.loads(provenance.read_text())
    historical_paths = [Path(p) for p in frozen['unchanged_artifact_sha256']
                        if Path(p).name == 'fuel_weekly_audited_interim.csv']
    if len(historical_paths) != 1:
        raise ValueError('Ambiguous Phase03B fuel authority')
    historical_path = historical_paths[0]
    for p, sha in frozen['unchanged_artifact_sha256'].items():
        if Path(p).parent == historical_path.parent and digest(Path(p)) != sha:
            raise ValueError('Historical artifact hash mismatch')
    rows, evidence = extract_window(snapshot['raw_path'], start, end_exclusive)
    validate_schema(snapshot['raw_path'], rows, evidence)
    summary = accepted_summary(rows)
    extension = [r for r in rows if r['date'] >= '2024-01-01']
    extension_summary = accepted_summary(extension)
    output_rows = candidate_rows(rows, snapshot)
    with historical_path.open(newline='') as stream:
        historical = list(csv.DictReader(stream))
    reg = regression(output_rows, historical)
    yearly = {str(year): accepted_summary([r for r in rows if r['date'].startswith(str(year))])
              for year in range(2019, 2026)}
    by_basis = {basis: {r['date']: r['diesel_price_native'] for r in rows if r['tax_basis'] == basis}
                for basis in SEMANTICS['tax_variants']}
    differences = {d: v - by_basis['WITHOUT_TAX'][d] for d, v in by_basis['WITH_TAX'].items()}
    diagnostic = dict(minimum=min(differences.values()), maximum=max(differences.values()),
                      mean=sum(differences.values(), Decimal(0))/len(differences),
                      dates_with_tax_below_without_tax=[d for d, v in differences.items() if v < 0])
    validation = dict(status='PASS', schema_status='PASS', extraction_evidence=evidence,
        full=summary, extension=extension_summary, yearly=yearly,
        exact_date_support={basis: list(support) for basis, support in by_basis.items()},
        identical_date_support=True, historical_regression=reg, tax_difference=diagnostic,
        newer_snapshot_required=False, gaps_preserved=True, network_access=False)
    metadata = dict(phase='PHASE04-A6', semantics=SEMANTICS, workbook=snapshot,
        full_window=[start.isoformat(), end_exclusive.isoformat()],
        extension_window=['2024-01-01', end_exclusive.isoformat()],
        date_window_end_exclusive=True, historical_path=str(historical_path),
        historical_sha256=digest(historical_path), provenance_path=str(provenance),
        provenance_sha256=digest(provenance), sidecar_sha256=digest(sidecar),
        schema='Unchanged Phase03B-C long-form fields',
        statistics_unit='Unconverted source numeric value per 1000 l; currency UNKNOWN',
        attribution='European Commission Weekly Oil Bulletin; extraction/window extension by E-MIND; CC BY 4.0 qualified reuse basis retained',
        implementation_sha256={str(p.relative_to(REPO)): digest(p) for p in
            (Path(__file__).resolve(), REPO/'scripts/harmonise/audit_fuel.py', REPO/'src/emind/harmonise/fuel.py')})
    verify_raw(snapshots)
    os.umask(0o077)
    output.mkdir(mode=0o700)
    write_csv(output/'fuel_de_2019_2025.csv', output_rows)
    write_csv(output/'fuel_de_2024_2025.csv', candidate_rows(extension, snapshot))
    write_json(output/'metadata.json', metadata)
    write_json(output/'validation.json', validation)
    with (output/'checksums.sha256').open('x') as stream:
        for name in ('fuel_de_2019_2025.csv', 'fuel_de_2024_2025.csv', 'metadata.json', 'validation.json'):
            stream.write(digest(output/name)+'  '+name+'\n')
    (output/'checksums.sha256').chmod(0o400)
    verify_raw(snapshots)
    return validation


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--start', type=date.fromisoformat, default=date(2019, 1, 1))
    parser.add_argument('--end-exclusive', type=date.fromisoformat, default=date(2026, 1, 1))
    args = parser.parse_args()
    result = run(args.manifest, args.provenance, args.output_dir, args.start, args.end_exclusive)
    print(json.dumps(result, default=str, indent=2))
