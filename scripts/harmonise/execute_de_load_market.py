"""Lossless German hourly load/market construction from validated native rows."""
from dataclasses import asdict
import json
from run_de_pilot import build_target
from emind.harmonise.coverage import TARGET_START, TARGET_END

def construct(parsed, start=TARGET_START, end=TARGET_END):
    load, market, check=build_target(parsed['load'][0], parsed['market'][0],
                                     parsed['load'][1], parsed['market'][1], start, end)
    native_load=[a for a in parsed['load'][1] if start<=a['timestamp_utc']<end]
    native_market=[a for a in parsed['market'][1] if start<=a['timestamp_utc']<end]
    if len(native_load)!=4*len(load) or len(native_market)!=len(market):
        raise ValueError('Invalid native/canonical lineage cardinality')
    load_rows=[]
    for i,row in enumerate(load):
        group=native_load[4*i:4*i+4]
        refs=[{'snapshot_id':a['snapshot_id'],'source_row_index':a['source_row_index']} for a in group]
        load_rows.append(dict(asdict(row),scenario_id='DE_CENT',spatial_support='NATIONAL_SYSTEM',
                              native_row_refs=json.dumps(refs,separators=(',',':'))))
    market_rows=[dict(asdict(row),scenario_id='DE_CENT',spatial_support='BIDDING_ZONE',
                      market_zone='DE-LU',currency='EUR',snapshot_id=a['snapshot_id'],
                      source_row_index=a['source_row_index']) for row,a in zip(market,native_market)]
    return load_rows,market_rows,check
