"""Spanish anchor configuration and provider-neutral canonical alignment."""
from pathlib import Path
import sys,unittest
from copy import deepcopy
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from emind.weather import load_weather_config,build_monthly_request
from emind.weather.config import validate_weather_config,WeatherConfigError
from emind.weather.harmonise import canonical_index,construct_rows,COLUMNS
from emind.harmonise.coverage import validate_target,assert_same_timestamps

ROOT=Path(__file__).resolve().parents[1]
class SpanishWeatherTests(unittest.TestCase):
 def setUp(self):self.c=load_weather_config(ROOT/'configs/weather/es_med_era5.yaml')
 def test_selected_anchor_request_has_no_german_coordinates(self):
  plan=build_monthly_request(2019,1,self.c)
  self.assertEqual(self.c['anchor'],{'latitude':39.5,'longitude':-.75})
  self.assertEqual(plan['request']['area'],[39.75,-1.,39.25,-.5])
  self.assertIn('es_med',plan['filename'])
 def test_full_index_exactly_61368_and_continuous(self):
  index=canonical_index(self.c)
  self.assertEqual(len(index),61368)
  validate_target(index,datetime(2019,1,1,tzinfo=timezone.utc),datetime(2026,1,1,tzinfo=timezone.utc))
  self.assertEqual(index,[datetime(2019,1,1,tzinfo=timezone.utc)+timedelta(hours=i) for i in range(61368)])
 def test_ssrd_alignment_derived_wind_and_cross_signal(self):
  t=datetime(2025,12,31,23,tzinfo=timezone.utc)
  values={v['short_name']:{t+timedelta(hours=v['provider_validity_offset_hours']):3600 if v['short_name']=='ssrd' else 3 if v['short_name'].endswith('u') else 4} for v in self.c['variables']}
  sources={s:{k:'synthetic' for k in x} for s,x in values.items()}
  data,lineage=construct_rows(values,sources,self.c,index=[t])
  self.assertEqual(data['surface_solar_irradiance_w_m2'],[1])
  self.assertEqual(data['wind_speed_10m_m_s'],[5]);self.assertEqual(data['wind_speed_100m_m_s'],[5])
  self.assertEqual(data['timestamp_utc'],[t])
  self.assertEqual(lineage[0]['ssrd_provider_validity_utc'],'2026-01-01T00:00:00Z')
 def test_wrong_anchor_and_offset_rejected(self):
  for field in ['anchor','offset']:
   c=deepcopy(self.c)
   if field=='anchor':c['anchor']['longitude']=-.5
   else:c['variables'][1]['provider_validity_offset_hours']=0
   with self.assertRaises(WeatherConfigError):validate_weather_config(c)

 def test_cross_signal_mismatch_is_not_hidden_by_row_count(self):
  self.assertEqual(assert_same_timestamps(['a','b'],['a','b']),2)
  for right in [['b','a'],['a'],['a','c']]:
   with self.assertRaises(ValueError):assert_same_timestamps(['a','b'],right)
