#!/usr/bin/env python3
"""Offline load/market extension and append-only INTERIM construction.

Explicit horizons and native market segments; no acquisition or fuel execution.
The superseded combined runner supplies serialization helpers only.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
from execute_de_candidate import save_csv, save_json, serial
from emind.harmonise.coverage import validate_index, validate_target
from emind.harmonise.smard import (aggregate_quarter_hour_load, aggregate_quarter_hour_market,
    canonicalise_market_hours, canonicalise_market_with_lineage, join_market_segments)
from emind.providers.integrity import digest, verify_raw
from emind.providers.smard import read_native, MARKET_QUARTER_HOUR_HEADER

HOUR = timedelta(hours=1)


def utc(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError('Explicit timezone-aware UTC boundary required')
    return result


def refs_json(refs):
    return json.dumps(refs, default=serial, separators=(',', ':'), ensure_ascii=False)


def construct(load_objects, market_segments, *, start, end, transition):
    """Build an exact extension using explicit homogeneous native segments."""
    load_native, load_audit = read_native(load_objects, 'load', allow_nominal_dst_ends=True)
    validate_index([a['timestamp_utc'] for a in load_audit], timedelta(minutes=15))
    load_core = aggregate_quarter_hour_load(load_native)
    validate_target([r.timestamp_utc for r in load_core], start, end)
    load = []
    for i, hour in enumerate(load_core):
        group = [dict(a, native_value=r.energy_mwh, quality_flag='ORIGINAL')
                 for r, a in zip(load_native[4*i:4*i+4], load_audit[4*i:4*i+4])]
        load.append(dict(timestamp_utc=hour.timestamp_utc, interval_minutes=60,
            load_energy_mwh=hour.load_energy_mwh, source_id=hour.source_id,
            source_native_frequency='15 minutes', quality_flag='AGGREGATED_NATIVE',
            scenario_id='DE_CENT', spatial_support='NATIONAL_SYSTEM',
            native_row_refs=refs_json(group), native_interval_minutes=15,
            contributing_row_count=4, canonical_transformation='SUM_FOUR_QUARTERS',
            provenance_segment='PHASE04_EXTENSION'))
    native_energy = sum((r.energy_mwh for r in load_native), Decimal(0))
    canonical_energy = sum((r['load_energy_mwh'] for r in load), Decimal(0))
    if native_energy != canonical_energy:
        raise ValueError('Exact native/canonical energy conservation failed')
    if len(market_segments) != 2 or [s['interval_minutes'] for s in market_segments] != [60, 15]:
        raise ValueError('Explicit native hourly then quarter-hour market segments required')
    market, cores, native_segments = [], [], []
    for segment in market_segments:
        minutes = segment['interval_minutes']
        records, audit = read_native(segment['objects'], 'market', interval_minutes=minutes,
            allow_nominal_dst_ends=True,
            **({'reviewed_market_header': MARKET_QUARTER_HOUR_HEADER} if minutes == 15 else {}))
        core = canonicalise_market_hours(records) if minutes == 60 else aggregate_quarter_hour_market(records)
        cores.append(core); native_segments.append(records)
        rows = canonicalise_market_with_lineage(records, audit, interval_minutes=minutes)
        for row in rows:
            refs = row['native_row_refs']
            row.update(scenario_id='DE_CENT', spatial_support='BIDDING_ZONE', market_zone='DE-LU',
                currency='EUR', snapshot_id=refs[0]['snapshot_id'], source_row_index=refs[0]['source_row_index'],
                native_row_refs=refs_json(refs), provenance_segment='M1' if minutes == 60 else 'M2')
        market.extend(rows)
    joined = join_market_segments(*cores, transition_utc=transition)
    validate_target([r.timestamp_utc for r in joined], start, end)
    if [r['timestamp_utc'] for r in load] != [r['timestamp_utc'] for r in market]:
        raise ValueError('Load/market UTC indices differ')
    m2 = market[len(cores[0]):]
    # Independent rational arithmetic checks every Decimal result and lineage.
    for i, row in enumerate(m2):
        refs = json.loads(row['native_row_refs'])
        group = native_segments[1][4*i:4*i+4]
        if len(refs) != 4 or [utc(a['timestamp_utc']) for a in refs] != [row['timestamp_utc'] + timedelta(minutes=m) for m in (0,15,30,45)]:
            raise ValueError('Incomplete physical M2 lineage')
        if Fraction(row['day_ahead_price_eur_mwh']) != sum((Fraction(r.price_eur_mwh) for r in group), Fraction(0))/4:
            raise ValueError('Independent M2 mean check failed')
        if [Decimal(a['native_value']) for a in refs] != [r.price_eur_mwh for r in group]:
            raise ValueError('M2 native lineage values changed')
    prices = [r['day_ahead_price_eur_mwh'] for r in m2]
    mixed = sum(any(r.price_eur_mwh < 0 for r in native_segments[1][i:i+4])
                and any(r.price_eur_mwh > 0 for r in native_segments[1][i:i+4])
                for i in range(0, len(native_segments[1]), 4))
    audit = dict(load_native_rows=len(load_native), load_rows=len(load), market_rows=len(market),
        native_energy_mwh=native_energy, canonical_energy_mwh=canonical_energy,
        energy_difference_mwh=canonical_energy-native_energy, m1_rows=len(cores[0]), m2_rows=len(cores[1]),
        m2_max_aggregation_error=Decimal(0), m2_negative_hours=sum(v<0 for v in prices),
        m2_zero_hours=sum(v==0 for v in prices), m2_mixed_sign_groups=mixed,
        m2_min=min(prices), m2_max=max(prices), m2_mean=sum(prices,Decimal(0))/len(prices),
        m2_median=statistics.median(prices), native_market_duration_weighted_mean=
            sum((sum((r.price_eur_mwh for r in rows),Decimal(0))*minutes
                 for rows,minutes in zip(native_segments,(60,15))),Decimal(0))/
            sum(len(rows)*minutes for rows,minutes in zip(native_segments,(60,15))))
    return load, market, audit


def read_history(path, kind, *, start, end):
    """Read the frozen canonical artifact; never reconstruct old provider values."""
    with Path(path).open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    value_key = 'load_energy_mwh' if kind == 'load' else 'day_ahead_price_eur_mwh'
    for row in rows:
        row['timestamp_utc'] = utc(row['timestamp_utc'])
        row['interval_minutes'] = int(row['interval_minutes'])
        row[value_key] = Decimal(row[value_key])
    validate_target([r['timestamp_utc'] for r in rows], start, end)
    return rows


def append_history(history, extension, kind, *, start, end):
    """Append without changing any represented historical field/value."""
    if not history or not extension or history[-1]['timestamp_utc'] + HOUR != extension[0]['timestamp_utc']:
        raise ValueError('Historical/extension boundary has a gap or overlap')
    projected = []
    for original in history:
        row = dict(original)
        # This phase publishes MWh energy only; legacy MW remains in its frozen source artifact.
        row.pop('load_power_mw', None)
        minutes = 15 if kind == 'load' else 60
        if kind == 'market':
            row['source_native_frequency'] = '60 minutes'
            row['native_row_refs'] = refs_json([dict(snapshot_id=row['snapshot_id'],
                source_row_index=int(row['source_row_index']), native_value=row['day_ahead_price_eur_mwh'],
                timestamp_utc=row['timestamp_utc'], interval_minutes=60)])
        row.update(native_interval_minutes=minutes, contributing_row_count=4 if kind=='load' else 1,
            canonical_transformation='SUM_FOUR_QUARTERS' if kind=='load' else 'IDENTITY',
            provenance_segment='PHASE03B_HISTORICAL')
        projected.append({k:row[k] for k in extension[0]})
    full = projected + extension
    validate_target([r['timestamp_utc'] for r in full], start, end)
    for original, row in zip(history, full):
        if any(row[k] != value for k,value in original.items() if k != 'load_power_mw'):
            raise ValueError('Historical field/value regression failed')
    return full


def validate_rows(rows, kind, *, start, end):
    validate_target([r['timestamp_utc'] for r in rows], start, end)
    key = 'load_energy_mwh' if kind=='load' else 'day_ahead_price_eur_mwh'
    if any(r['interval_minutes'] != 60 or not r[key].is_finite() or
           (kind=='load' and r[key]<0) or any(v is None or v=='' for v in r.values()) for r in rows):
        raise ValueError('Canonical row duration/null/numeric gate failed')
    return dict(rows=len(rows), first=rows[0]['timestamp_utc'], last=rows[-1]['timestamp_utc'],
                unique=len({r['timestamp_utc'] for r in rows}), gaps=0, duplicates=0,
                missing=0, nonfinite=0, quality_counts=dict(Counter(r['quality_flag'] for r in rows)))


def run(load_sidecars, market_sidecars, history_dir, output, *, start, end, full_start, transition):
    output, history_dir = Path(output), Path(history_dir)
    configured = os.environ.get('EMIND_INTERIM_ROOT')
    if not configured or not Path(configured).is_absolute():
        raise ValueError('Explicit absolute EMIND_INTERIM_ROOT required')
    allowed = Path(configured).resolve()
    if output.exists() or allowed not in output.resolve().parents:
        raise ValueError('New directory under canonical DE_CENT INTERIM required')
    load_objects = [json.loads(Path(p).read_text()) for p in load_sidecars]
    segments = [dict(interval_minutes=int(minutes), objects=[json.loads(Path(p).read_text())])
                for minutes,p in market_sidecars]
    objects = load_objects + [o for s in segments for o in s['objects']]
    verify_raw(objects)
    historical_paths = {kind:history_dir/(kind+'_hourly.csv') for kind in ('load','market')}
    historical_sha = {kind:digest(p) for kind,p in historical_paths.items()}
    load, market, audit = construct(load_objects, segments, start=start, end=end, transition=transition)
    historical = {kind:read_history(p,kind,start=full_start,end=start) for kind,p in historical_paths.items()}
    full_load = append_history(historical['load'],load,'load',start=full_start,end=end)
    full_market = append_history(historical['market'],market,'market',start=full_start,end=end)
    validations = {name:validate_rows(rows,kind,start=begin,end=end) for name,rows,kind,begin in
        [('load_extension',load,'load',start),('market_extension',market,'market',start),
         ('load_full',full_load,'load',full_start),('market_full',full_market,'market',full_start)]}
    if [r['timestamp_utc'] for r in full_load] != [r['timestamp_utc'] for r in full_market]:
        raise ValueError('Full load/market UTC indices differ')
    annual = {}
    for year in sorted({r['timestamp_utc'].year for r in market}):
        prices = [r['day_ahead_price_eur_mwh'] for r in market if r['timestamp_utc'].year==year]
        annual[str(year)] = dict(rows=len(prices),mean=sum(prices,Decimal(0))/len(prices),negative_hours=sum(v<0 for v in prices))
    verify_raw(objects)
    if any(digest(p)!=historical_sha[k] for k,p in historical_paths.items()):
        raise ValueError('Historical artifact changed during execution')
    os.umask(0o077); output.mkdir(mode=0o700)
    files = [save_csv(output/name,rows) for name,rows in
        [('load_de_cent_2024_2025_hourly.csv',load),('market_de_lu_2024_2025_hourly.csv',market),
         ('load_de_cent_2019_2025_hourly.csv',full_load),('market_de_lu_2019_2025_hourly.csv',full_market)]]
    metadata = dict(status='INTERIM_NOT_RELEASE',canonical_interval_minutes=60,
        extension_start=start,full_start=full_start,end_exclusive=end,transition_utc=transition,
        load=dict(unit='MWh per hourly interval',transformation='SUM four physical quarter-hours',
                  scaling=False,normalisation=False,power_conversion=False,
                  legacy_load_power_mw='Not propagated; preserved in immutable historical source artifact'),
        market=dict(unit='EUR/MWh',native_frequency='MIXED',
            native_regimes=[dict(start=full_start,end_exclusive=transition,interval_minutes=60,transformation='IDENTITY',quality='ORIGINAL'),
                           dict(start=transition,end_exclusive=end,interval_minutes=15,transformation='ARITHMETIC_MEAN_FOUR_QUARTERS',quality='AGGREGATED_NATIVE')],
            transition_local=transition.astimezone(ZoneInfo('Europe/Berlin')),
            limitation='Equivalent constant-power/equal-energy exposure; not exact settlement for unequal quarter-hour energies'),
        historical_artifacts={k:dict(path=str(p),sha256=historical_sha[k]) for k,p in historical_paths.items()},
        historical_provenance_schema=dict(path=str(history_dir/'schema.json'),sha256=digest(history_dir/'schema.json')),
        sources=[{k:o[k] for k in ('source_id','snapshot_id','raw_path','sha256','size_bytes')} for o in objects],
        available_at='UNKNOWN',imputation=False,clipping=False,provider_hourly_substitution=False,
        lineage='native_row_refs plus frozen Phase03B schema and original artifact provenance',
        attribution='Bundesnetzagentur | SMARD.de; CC BY 4.0',tables=files)
    files.append(save_json(output/'metadata.json',metadata))
    report = dict(status='PASS',phase='04-A5',git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        implementation_uncommitted=True,implementation_sha256={str(p.relative_to(REPO)):digest(p) for p in
            [Path(__file__).resolve(),REPO/'src/emind/providers/smard.py',REPO/'src/emind/harmonise/smard.py']},
        validation=validations,extension_audit=audit,annual_market=annual,
        historical_regression=dict(rows=len(historical['load']),max_load_difference=Decimal(0),max_market_difference=Decimal(0),represented_fields_identical=True),
        historical_extension_adjacency=True,market_transition_adjacency=True,
        native_sources_unchanged=True,historical_inputs_unchanged=True,fuel_processed=False,network_access=False,
        annual_references=dict(market_2024='78.51',market_2025='89.32',negative_hours_2025=573,role='Reasonableness only'),outputs=files)
    files.append(save_json(output/'validation.json',report))
    checksums=output/'checksums.sha256'
    with checksums.open('x') as f:
        for entry in files:f.write(entry['sha256']+'  '+Path(entry['path']).name+'\n')
    checksums.chmod(0o400)
    report['checksums_sha256']=digest(checksums)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--load-sidecar',type=Path,action='append',required=True)
    parser.add_argument('--market-segment',nargs=2,action='append',required=True,metavar=('MINUTES','SIDECAR'))
    parser.add_argument('--history-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    for name in ('start','end','full-start','transition'):parser.add_argument('--'+name,type=utc,required=True)
    args=parser.parse_args()
    result=run(args.load_sidecar,args.market_segment,args.history_dir,args.output_dir,
        start=args.start,end=args.end,full_start=args.full_start,transition=args.transition)
    print(json.dumps(result,default=serial,indent=2))
