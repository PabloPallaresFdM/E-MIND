"""Region-neutral assembly boundaries and packaging with independent regions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from emind.scenario import START,WEATHER,LOAD,MARKET,assemble,mapped_rows,fingerprint
from emind.release import build_release,read_json
import test_release as release_fixture
try:
 import pyarrow as pa
except ImportError:
 pa=None

class RegionMappingTests(unittest.TestCase):
 def test_provider_column_mapping_preserves_signed_decimal(self):
  original=[dict(timestamp_utc=START.isoformat(),wholesale_market_price_eur_mwh='-0.0025')]
  mapped=mapped_rows(original,{MARKET:'wholesale_market_price_eur_mwh'})
  self.assertEqual(mapped[0][MARKET],'-0.0025')
  self.assertNotIn(MARKET,original[0])
 def test_unreviewed_schema_and_collision_rejected(self):
  for rows,mapping in [([{'timestamp_utc':'x','price':'1','extra':'2'}],{MARKET:'price'}),
    ([{'timestamp_utc':'x','price':'1'}],{'fuel_price':'price'})]:
   with self.assertRaises(ValueError):mapped_rows(rows,mapping)
 @unittest.skipIf(pa is None,'PyArrow required')
 def test_different_civil_offsets_match_same_utc_support(self):
  times=[START,START+timedelta(hours=1)]
  weather=pa.table({'timestamp_utc':pa.array(times,type=pa.timestamp('us',tz='UTC')),**{c:[1.,2.] for c in WEATHER}})
  load=[{'timestamp_utc':t.isoformat(),LOAD:'1'} for t in times]
  spanish=mapped_rows([dict(timestamp_utc=t,wholesale_market_price_eur_mwh='-1') for t in ['2019-01-01T01:00:00+01:00','2019-01-01T02:00:00+01:00']],{MARKET:'wholesale_market_price_eur_mwh'})
  german=[{'timestamp_utc':t.isoformat(),MARKET:'-1'} for t in times]
  self.assertTrue(assemble(weather,load,spanish,times).equals(assemble(weather,load,german,times)))
 def test_regional_identity_fields_are_independent(self):
  base={'scenario_id':'DE_CENT','components':{'weather':{'anchor':[51.,10.25]},'load':{'provider':'SMARD'},'market':{'native_timezone':'Europe/Berlin','transition_delivery_day':'2025-10-01'},'fuel':{'country':'Germany'}},'spatial_support':'German system'}
  mutations=[('scenario_id','ES_MED'),('spatial_support','Spanish peninsula')]
  for key,val in mutations:
   changed=copy.deepcopy(base);changed[key]=val;self.assertNotEqual(fingerprint(base),fingerprint(changed))
  for component,key,val in [('weather','anchor',[39.5,-.75]),('load','provider','REData'),('market','native_timezone','Europe/Madrid'),('market','transition_delivery_day','2025-11-01'),('fuel','country','Spain')]:
   changed=copy.deepcopy(base);changed['components'][component][key]=val;self.assertNotEqual(fingerprint(base),fingerprint(changed))

class MultiRegionPackagingTests(unittest.TestCase):
 def setUp(self):
  self.fixture=release_fixture.GenericBuildTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
  self.spec=copy.deepcopy(self.fixture.spec);self.inputs=dict(self.fixture.inputs)
  first=self.spec['scenarios'][0];first['scenario_id']='DE_CENT';first['scenario']['scenario_id']='DE_CENT';first['metadata']['scenario_id']='DE_CENT';first['lineage']['scenario_id']='DE_CENT'
  descriptor={'scenario_id':'DE_CENT','components':{'tariff':'independent German synthetic bulletin'}}
  self.fixture.inputs['descriptor'].write_text(json.dumps(descriptor))
  first['scenario']['scenario_fingerprint']=fingerprint(descriptor)
  second=copy.deepcopy(first);second['scenario_id']='ES_MED'
  for key in ['scenario','metadata','lineage']:second[key]['scenario_id']='ES_MED'
  descriptor={'scenario_id':'ES_MED','components':{'tariff':'independent Spanish synthetic bulletin'}}
  p=self.fixture.root/'es_descriptor.json';p.write_text(json.dumps(descriptor));self.inputs['es-descriptor']=p
  second['accepted_descriptor_id']='es-descriptor';second['scenario']['scenario_fingerprint']=fingerprint(descriptor)
  self.spec['scenarios'].append(second)
 def test_multiple_regions_have_separate_package_identities(self):
  out=self.fixture.root/'multi'
  build_release(self.spec,self.inputs,out,release_fixture.COMMIT,{},allow_dev=True)
  registry=read_json(out/'metadata/scenario_registry.json')['scenarios']
  self.assertEqual([s['scenario_id'] for s in registry],['DE_CENT','ES_MED'])
  self.assertNotEqual(registry[0]['scenario_fingerprint'],registry[1]['scenario_fingerprint'])
  for sid in ['DE_CENT','ES_MED']:
   self.assertTrue((out/f'scenarios/{sid}/hourly.parquet').exists())
   self.assertTrue((out/f'scenarios/{sid}/components/tariff/native.csv').exists())
 def test_duplicate_region_ids_fail_before_output(self):
  self.spec['scenarios'][1]['scenario_id']='DE_CENT';out=self.fixture.root/'bad'
  with self.assertRaises(ValueError):build_release(self.spec,self.inputs,out,release_fixture.COMMIT,{},allow_dev=True)
  self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
