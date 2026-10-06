#!/usr/bin/env python3
"""Phase03B-B: analyse frozen candidate and its exact native-row lineage only."""
import argparse
import csv
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'src'))
from emind.harmonise.coverage import validate_target
from emind.harmonise.information_loss import detailed_information_loss
from emind.providers.integrity import digest,verify_raw
from emind.providers.smard import HEADERS,number,parse_label

EXPECTED='2550db4661f7838f14bd085cbb384ebd3e651b4edd6fdb1926df5d8eb06a8fe5'


def run(candidate,manifest,output):
    if digest(candidate)!=EXPECTED:
        raise ValueError('Phase03B-A candidate SHA256 mismatch')
    if output.exists():
        raise FileExistsError('Never overwrite a metrics run')
    objects=[o for o in json.loads(manifest.read_text())['objects'] if o['source_id']=='de_smard_electrical_load']
    if len(objects)!=4:
        raise ValueError('Expected three load blocks plus the existing supplementary tail')
    # Only four relevant load inputs; no full raw/market/fuel/DST audit repeated.
    verify_raw(objects)
    raw={}
    for obj in objects:
        with Path(obj['raw_path']).open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.reader(stream,delimiter=';',strict=True)
            if next(reader)!=['Start date','End date',HEADERS['load']]:
                raise ValueError('Unexpected load header')
            raw[obj['snapshot_id']]=list(reader)
    with candidate.open(newline='') as stream:
        rows=list(csv.DictReader(stream))
    times=[datetime.fromisoformat(r['timestamp_utc'].replace('Z','+00:00')) for r in rows]
    validate_target(times)
    if len(rows)!=43824:
        raise ValueError('Incorrect hourly population')
    energies=[]
    seen=set()
    zone=ZoneInfo('Europe/Berlin')
    for t,row in zip(times,rows):
        refs=json.loads(row['native_row_refs'])
        if len(refs)!=4 or row['interval_minutes']!='60':
            raise ValueError('Exactly four physical quarters per hour required')
        for q,ref in enumerate(refs):
            key=(ref['snapshot_id'],ref['source_row_index'])
            if key in seen or key[1]<1:
                raise ValueError('Repeated/invalid native lineage')
            seen.add(key)
            native=raw[key[0]][key[1]-1]
            physical=t+timedelta(minutes=15*q)
            # Reuse Phase03B-A physical lineage, not another DST reconstruction.
            if parse_label(native[0])!=physical.astimezone(zone).replace(tzinfo=None):
                raise ValueError('Native row does not match validated physical quarter')
            energies.append(number(native[2]))
        if Decimal(row['load_energy_mwh'])!=Decimal(row['load_power_mw']):
            raise ValueError('Unexpected hourly energy/power semantics')
    if len(energies)!=175296:
        raise ValueError('Incorrect native population')
    powers=[Decimal(r['load_power_mw']) for r in rows]
    if sum(energies)!=Decimal('2428000532.50') or sum(powers)!=sum(energies):
        raise ValueError('Energy sanity gate failed')
    result=detailed_information_loss(energies,powers,times)
    result['provenance']={'phase':'03B-B','status':'PASS','at_utc':datetime.now(timezone.utc).isoformat(),
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'candidate':str(candidate),'candidate_sha256':EXPECTED,
        'inputs':[{k:o[k] for k in ('snapshot_id','raw_path','sha256')} for o in objects],
        'source_manifest_sha256':digest(manifest),
        'code_sha256':{str(p.relative_to(REPO)):digest(p) for p in
            [Path(__file__),REPO/'src/emind/harmonise/information_loss.py']},
        'native_intervals':len(energies),'hourly_intervals':len(rows),
        'selection':'Exact Phase03B-A native_row_refs including supplementary tail, excluding pre-target hour',
        'variance_convention':'population (ddof=0); equal-duration observations within each resolution',
        'decimal_precision':40,'no_candidate_rebuild':True}
    # Recheck only relevant unchanged inputs and candidate before freezing metrics.
    verify_raw(objects)
    if digest(candidate)!=EXPECTED:
        raise ValueError('Candidate changed during metrics run')
    os.umask(0o077)
    output.mkdir(mode=0o700)
    path=output/'information_loss_metrics.json'
    with path.open('x') as stream:
        json.dump(result,stream,indent=2,default=str);stream.write('\n')
    path.chmod(0o400)
    checksum=output/'checksums.sha256'
    with checksum.open('x') as stream:
        stream.write(digest(path)+'  '+path.name+'\n')
    checksum.chmod(0o400)
    print(json.dumps({'status':'PASS','path':str(path),'sha256':digest(path),
                      'checksum_file_sha256':digest(checksum)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    run(args.candidate,args.manifest,args.output_dir)
