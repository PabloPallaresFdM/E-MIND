"""Negative gates with two real Parquet fixtures and independent native CSVs."""
import copy
import csv
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from emind.release import build_release,verify_release,read_json,write_json,checksums,sha256
from emind.scenario import fingerprint
try:
 import pyarrow as pa
 import pyarrow.parquet as pq
except ImportError:
 pa=None

@unittest.skipIf(pa is None,'PyArrow required for semantic release fixtures')
class MultiScenarioGateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.inputs={}
  self.spec=dict(dataset_name='E-MIND',release_version='0.0.0-dev',metadata={'dataset.json':{'schema_version':'0.1.0'}},
   raw_policy='OMIT_R2_PROVIDER_RAW',text_files={'README.md':'Synthetic internal test\n'},scenarios=[])
  start=datetime(2019,1,1,tzinfo=timezone.utc)
  for sid,country in [('DE_CENT','DE'),('ES_MED','ES')]:
   hourly=self.root/(sid+'.parquet');native=self.root/(sid+'.csv');descriptor=self.root/(sid+'.json')
   table=pa.table({'timestamp_utc':pa.array([start,start+timedelta(hours=1)],type=pa.timestamp('us',tz='UTC')),'load_energy_mwh':[1.,2.],'day_ahead_price_eur_mwh':[-1.,3.]})
   pq.write_table(table,hourly)
   with native.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['reference_date','tax_variant','country_code','native_frequency','source_value']);w.writeheader()
    for v in ['WITH_TAX','WITHOUT_TAX']:
     for d in ['2019-01-07','2019-01-21']:w.writerow(dict(reference_date=d,tax_variant=v,country_code=country,native_frequency='weekly',source_value='2'))
   fuel=dict(component_id='fuel',semantic_kind='native_weekly_economic_bulletin',authoritative_path='components/native.csv',accepted_artifact_sha256=sha256(native),
    format='CSV',columns=[],units={},key_columns=['reference_date','tax_variant'],ordering='native',spatial_support=country+' national bulletin',
    native_temporal_resolution='weekly',valid_time_representation='reference date',available_at='UNKNOWN',source_ids=[sid+'-fuel'],snapshot_ids=[sid+'-snapshot'],
    transformation_id='native',missingness_policy='gaps preserved',tax_default='UNSELECTED',hourly_resampling=False,interpolation=False)
   horizon=dict(start=start.isoformat(),end_exclusive=(start+timedelta(hours=2)).isoformat(),rows=2)
   accepted=dict(scenario_id=sid,horizon=horizon,hourly_columns=table.column_names,components={'fuel':{'observations_per_variant':{'WITH_TAX':2,'WITHOUT_TAX':2},'country_code':country}})
   write_json(descriptor,accepted)
   self.inputs.update({sid+'-descriptor':descriptor,sid+'-hourly':hourly,sid+'-fuel':native})
   self.spec['scenarios'].append(dict(scenario_id=sid,accepted_descriptor_id=sid+'-descriptor',
    scenario=dict(scenario_id=sid,scenario_class='CORE_FULL_MARKET',horizon=horizon,rows=2,hourly_columns=table.column_names,components={'fuel':fuel},hourly_artifact='hourly.parquet',
     scenario_fingerprint=fingerprint(accepted),accepted_composition_scheme='test-accepted-hourly'),
    metadata=dict(scenario_id=sid,hourly_columns=table.column_names,side_components=[fuel]),lineage=dict(scenario_id=sid,components=[],assembly_transformation='identity'),
    artifacts=[dict(artifact_id=sid+'-hourly',path='hourly.parquet',role='authoritative_hourly_core',sha256=sha256(hourly)),
     dict(artifact_id=sid+'-fuel',path='components/native.csv',role='authoritative_native_component',sha256=sha256(native))]))
 def build(self):return build_release(self.spec,self.inputs,self.root/'output','a'*40,{},allow_dev=True)
 def rejected(self,reason):
  with self.assertRaisesRegex(ValueError,reason):self.build()
  self.assertFalse((self.root/'output').exists())
 def test_valid_two_scenario_package(self):
  result=self.build();self.assertEqual(len(result['scenarios']),2);self.assertEqual(verify_release(self.root/'output'),result)
 def test_missing_fingerprint(self):
  del self.spec['scenarios'][1]['scenario']['scenario_fingerprint'];self.rejected('fingerprint')
 def test_duplicate_scenario(self):
  self.spec['scenarios'][1]['scenario_id']='DE_CENT';self.rejected('scenario IDs')
 def test_mismatched_fingerprint(self):
  self.spec['scenarios'][1]['scenario']['scenario_fingerprint']='0'*64;self.rejected('fingerprint')
 def test_es_hourly_row_count_mismatch_even_with_updated_hash(self):
  p=self.inputs['ES_MED-hourly'];pq.write_table(pq.read_table(p).slice(0,1),p)
  self.spec['scenarios'][1]['artifacts'][0]['sha256']=sha256(p);self.rejected('row-count')
 def test_hourly_fuel_expansion_even_with_updated_hash(self):
  p=self.inputs['ES_MED-fuel'];p.write_text(p.read_text()+p.read_text().splitlines()[1]+'\n')
  self.spec['scenarios'][1]['artifacts'][1]['sha256']=sha256(p);self.rejected('observation counts')
 def test_missing_spatial_support(self):
  del self.spec['scenarios'][1]['scenario']['components']['fuel']['spatial_support'];self.rejected('spatial support')
 def test_r2_provider_raw_inclusion(self):
  self.spec['scenarios'][1]['artifacts'].append(dict(artifact_id='ES_MED-fuel',path='raw/provider.csv',role='provider_raw',sha256=sha256(self.inputs['ES_MED-fuel'])));self.rejected('RAW')
 def test_manifest_omits_one_region_even_with_updated_checksums(self):
  self.build();root=self.root/'output';m=read_json(root/'manifest.json');m['scenarios'].pop();write_json(root/'manifest.json',m)
  (root/'checksums.sha256').write_bytes(checksums(root))
  with self.assertRaisesRegex(ValueError,'inventory|registry'):verify_release(root)
 def test_cross_scenario_metadata_identity_collision(self):
  self.spec['scenarios'][1]['metadata']['scenario_id']='DE_CENT';self.rejected('metadata identity collision')
 def test_case_colliding_shared_metadata(self):
  self.spec['metadata'].update({'source.json':{},'SOURCE.json':{}});self.rejected('Metadata path collision')
 def test_parquet_declared_rows_mismatch(self):
  self.spec['scenarios'][1]['scenario']['rows']=3;self.rejected('row-count')

if __name__=='__main__':unittest.main()
