#!/usr/bin/env python3
"""Phase03B-C only: frozen Germany weekly fuel extraction and evidence audit."""
from collections import Counter
from datetime import date,datetime,timezone
from decimal import Decimal
from html.parser import HTMLParser
import argparse
import csv
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'src'))
from emind.harmonise.fuel import extract_germany,NS,_cell
from emind.providers.integrity import digest,verify_raw


class PageText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[]
    def handle_data(self,value):
        if value.strip():self.parts.append(value.strip())


def summarise(rows):
    result={}
    supports={}
    for basis in ('WITH_TAX','WITHOUT_TAX'):
        selected=[r for r in rows if r['tax_basis']==basis]
        dates=sorted(date.fromisoformat(r['date']) for r in selected)
        values=[r['diesel_price_native'] for r in selected if r['diesel_price_native'] is not None]
        if not dates or not values:
            raise ValueError('Missing required fuel variant')
        counts=Counter(dates)
        gaps=[{'previous_reference_date':a.isoformat(),'next_reference_date':b.isoformat(),
               'elapsed_days':(b-a).days,
               'nominal_weekly_observations_apparently_absent':(b-a).days//7-1 if (b-a).days%7==0 else None}
              for a,b in zip(dates,dates[1:]) if (b-a).days>7]
        result[basis]={'first_reference_date':dates[0].isoformat(),'last_reference_date':dates[-1].isoformat(),
            'observations':len(selected),'nulls':len(selected)-len(values),
            'duplicate_dates':{d.isoformat():n for d,n in counts.items() if n>1},
            'minimum_source_value':min(values),'maximum_source_value':max(values),
            'mean_source_value':sum(values,Decimal(0))/len(values),'median_source_value':statistics.median(values),
            'cadence_elapsed_days':dict(Counter((b-a).days for a,b in zip(dates,dates[1:]))),
            'gaps_gt_7_days':gaps}
        supports[basis]=dates
    return result,supports['WITH_TAX']==supports['WITHOUT_TAX']


def candidate_rows(rows,snapshot):
    return [{'reference_date':r['date'],'country':'Germany','country_code':'DE',
             'spatial_support':'COUNTRY_LEVEL','product':'automotive_diesel',
             'tax_variant':r['tax_basis'],'source_value':r['diesel_price_native'],
             'source_unit':'UNKNOWN_CURRENCY_PER_1000_L','source_volume_unit':'1000 l',
             'currency':'UNKNOWN','available_at':None,'availability_status':'UNKNOWN',
             'source_id':r['source_id'],'native_frequency':'weekly','quality_flag':r['quality_flag'],
             'snapshot_id':snapshot['snapshot_id'],'raw_sha256':snapshot['sha256'],
             'worksheet':r['sheet_name'],'source_header':r['source_field'],
             'source_row_index':r['source_row_index'],'date_cell':r['date_cell'],
             'value_cell':r['value_cell'],'excel_date_serial':r['date_serial_raw'],
             'source_value_raw':r['value_raw']} for r in rows]


def validate_schema(path,rows,evidence):
    # More than a column-name inference: explicit product/tax titles and the
    # country-code cell on every selected row corroborate the field identity.
    with zipfile.ZipFile(path) as book:
        shared=[''.join(t.text or '' for t in x.findall('.//s:t',NS))
                for x in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si',NS)]
        for basis,ev in evidence.items():
            expected_title=('Consumer prices of petroleum products inclusive of duties and taxes' if basis=='WITH_TAX'
                            else 'Consumer prices of petroleum products net of duties and taxes')
            if ev['header'][1]['A']!=expected_title or ev['header'][2]['BD']!='Gas oil automobile Automotive gas oil Dieselkraftstoff (I)':
                raise ValueError('Unsupported product/tax evidence')
            root=ET.fromstring(book.read(ev['worksheet_member']))
            cells={c.get('r'):_cell(c,shared)[0] for c in root.findall('s:sheetData/s:row/s:c',NS)
                   if c.get('r','').startswith('BB')}
            if any(cells.get('BB'+str(r['source_row_index']))!='DE_' for r in rows if r['tax_basis']==basis):
                raise ValueError('Germany country-code evidence mismatch')


def run(manifest,provider_page,output):
    if output.exists():raise FileExistsError('Never overwrite an existing fuel audit')
    snapshot=next(o for o in json.loads(manifest.read_text())['objects'] if o['source_id']=='eu_ec_weekly_oil_bulletin_diesel')
    verify_raw([snapshot])
    sidecar=Path(snapshot['raw_path']+'.manifest.json')
    saved=json.loads(sidecar.read_text())
    if saved['sha256']!=snapshot['sha256']:raise ValueError('Frozen sidecar mismatch')
    rows,evidence=extract_germany(snapshot['raw_path'])
    validate_schema(snapshot['raw_path'],rows,evidence)
    page=PageText();page.feed(provider_page.read_text())
    paragraphs=[t for t in page.parts if 'submitted to the Commission on Wednesdays' in t and 'every Thursday' in t]
    if len(paragraphs)!=1 or 'Germany' not in page.parts:
        raise ValueError('Recorded official metadata does not support expected cadence/country context')
    summary,same_dates=summarise(rows)
    output_rows=candidate_rows(rows,snapshot)
    semantics={
        'product':{'value':'automotive diesel','evidence':'WORKBOOK_OBSERVED','basis':'BD2 explicit multilingual product description; (I) denotes pump prices in workbook notes'},
        'country':{'value':'Germany / DE','evidence':['WORKBOOK_OBSERVED','OFFICIAL_PROVIDER_METADATA'],
                   'basis':'BB selected rows = DE_; Germany country-specific entry in archived official catalogue; not inferred from BD1 alone'},
        'tax_variant':{'value':['WITH_TAX','WITHOUT_TAX'],'evidence':'WORKBOOK_OBSERVED','basis':'A1 explicit inclusive/net duties and taxes titles plus sheet/BD1 identities'},
        'currency':{'value':None,'evidence':'UNKNOWN','basis':'No unambiguous currency statement for the selected historic Germany columns in frozen workbook/archived catalogue; EUR aggregate denotes Eurozone'},
        'volume_unit':{'value':'1000 l','evidence':'WORKBOOK_OBSERVED','basis':'BD3'},
        'complete_monetary_unit':{'value':None,'evidence':'UNKNOWN','basis':'Volume denominator supported; currency unresolved; no conversion'},
        'reference_date':{'value':'Workbook Date label, Excel 1900 epoch serial converted to ISO date',
                          'evidence':['WORKBOOK_OBSERVED','E_MIND_DERIVED'],'basis':'A3=Date; workbook epoch flag; original serial retained'},
        'reference_date_economic_meaning':{'value':None,'evidence':'UNKNOWN','basis':'Monday labels do not establish whether observation, effective or publication date'},
        'publication_cadence':{'value':'Weekly; national submissions Wednesdays; bulletin mailed Thursdays',
                               'evidence':'OFFICIAL_PROVIDER_METADATA','basis':'Archived official catalogue; general schedule, not row-level historical availability'},
        'available_at':{'value':None,'evidence':'UNKNOWN','basis':'Precise historical publication timestamp/timezone, revisions and exceptions not supported; never equated with Date'},
        'gaps':{'evidence':'E_MIND_DERIVED','basis':'Consecutive workbook dates; nominal missing weeks only, not a provider-error diagnosis'}}
    audit={'phase':'03B-C','status':'PARTIAL','extraction_status':'PASS_AUDITED_INTERIM',
           'at_utc':datetime.now(timezone.utc).isoformat(),'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
           'workbook':{k:snapshot[k] for k in ('raw_path','sha256','snapshot_id','size_bytes')},
           'workbook_sha256_verified':True,'sidecar_sha256':digest(sidecar),
           'provider_metadata':{'url':snapshot['catalogue_url'],'local_path':str(provider_page),'sha256':digest(provider_page),
                                'used_existing_capture_only':True},
           'variants':summary,'extraction_evidence':evidence,'identical_date_support':same_dates,
           'semantics':semantics,'core_tax_default':'OPEN_NOT_SELECTED','hourly_expansion':False,
           'numeric_conversion':False,'row_count':len(rows),
           'source_value_statistics_unit':'Unconverted workbook numeric value per 1000 l; currency UNKNOWN',
           'required_external_metadata':'Freeze official documentation explicitly identifying currency of historical Germany diesel columns and Date semantics; separately freeze historical publication times/timezone/revision policy before available_at mapping.',
           'implementation_sha256':{str(p.relative_to(REPO)):digest(p) for p in [Path(__file__),REPO/'src/emind/harmonise/fuel.py']}}
    verify_raw([snapshot])
    os.umask(0o077);output.mkdir(mode=0o700)
    csv_path=output/'fuel_weekly_audited_interim.csv'
    with csv_path.open('x',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(output_rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(output_rows)
    csv_path.chmod(0o400)
    with csv_path.open(newline='') as stream:
        restored=list(csv.DictReader(stream))
    if len(restored)!=len(rows) or any(Decimal(a['source_value'])!=b['diesel_price_native'] for a,b in zip(restored,rows) if b['diesel_price_native'] is not None):
        raise ValueError('Lossless extraction read-back failed')
    audit['extraction']={'path':str(csv_path),'sha256':digest(csv_path),'rows':len(rows)}
    audit_path=output/'fuel_audit.json'
    with audit_path.open('x') as stream:json.dump(audit,stream,indent=2,default=str);stream.write('\n')
    audit_path.chmod(0o400)
    checksums=output/'checksums.sha256'
    with checksums.open('x') as stream:
        for path in (csv_path,audit_path):stream.write(digest(path)+'  '+path.name+'\n')
    checksums.chmod(0o400)
    print(json.dumps({'status':audit['status'],'variants':summary,'identical_date_support':same_dates,
                      'outputs':{str(p):digest(p) for p in (csv_path,audit_path,checksums)}},indent=2,default=str))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--provider-page',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();run(args.manifest,args.provider_page,args.output_dir)
