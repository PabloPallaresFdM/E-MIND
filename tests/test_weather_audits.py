"""Synthetic C0/C1 methodology tests; no network, credentials or Scratch."""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from emind.weather import load_weather_config
from emind.weather.structural import (audit_plan, boundary_accounting, temporal_coverage,
                                      structural_audit, compare_structural)
from emind.weather.spatial import (stencil, mean, scalar_metrics, rank_at, active_mask,
    wind_speed, vector_error, wind_from, angular_difference, season, extreme_indices,
    quantile, spatial_diagnostics, compare_spatial, read_stencil)
# C0 delegates GRIB semantics to D3B. Exercise that shared validator's synthetic
# parameter/grid/value/one-hour tests directly instead of inventing another path.
import test_weather_grib_contract as grib_contract


class C0SharedValidatorTests(grib_contract.GribContractTests):
    pass


class StructuralTests(unittest.TestCase):
    def setUp(self):
        self.config=load_weather_config(ROOT/'configs/weather/de_cent_era5.yaml')

    def test_object_counts(self):
        self.assertEqual(len(audit_plan('raw',self.config)),85)
        monthly=audit_plan('raw',self.config,include_boundary=False)
        self.assertEqual(len(monthly),84)
        self.assertTrue(all(y in range(2019,2026) for _,y,_ in monthly))
        self.assertTrue(str(audit_plan('raw',self.config)[-1][0]).endswith('2026_boundary/era5_de_cent_ssrd_2026_01_01_0000_utc.grib'))

    def test_leap_year_counts(self):
        from emind.weather import canonical_index
        index=canonical_index(self.config)
        self.assertEqual(sum(t.year==2020 for t in index),8784)
        self.assertEqual(sum(t.year==2024 for t in index),8784)
        self.assertEqual(sum(t.year==2023 for t in index),8760)

    def test_boundary_accounting(self):
        summary=boundary_accounting(self.config)
        self.assertEqual(summary['arithmetic'],'61368 - 1 + 1 = 61368')
        self.assertEqual(summary['canonical_intervals'],61368)

    def test_missing_duplicate_detection(self):
        t=datetime(2023,1,1,tzinfo=timezone.utc)
        expected=[t,t+timedelta(hours=1)]
        self.assertEqual(temporal_coverage(expected,expected)['hourly_gaps'],0)
        for invalid in ([t], [t,t], expected+[t]):
            with self.assertRaisesRegex(ValueError,'missing/duplicate'):
                temporal_coverage(invalid,expected)

    def test_d3b_validation_delegation(self):
        with patch('emind.weather.structural.validate_grib',side_effect=ValueError('malformed GRIB')) as validator:
            with self.assertRaisesRegex(ValueError,'malformed GRIB'):
                structural_audit('raw',self.config)
        self.assertEqual(validator.call_args.kwargs,dict(year=2019,month=1,boundary=False))

    def test_full_aggregation_includes_separate_boundary_year(self):
        import calendar
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            objects=audit_plan(root,self.config)
            sha=self.config['ssrd']['boundary_sha256']
            inventory=Path(root)/'inventory.json'
            inventory.write_text(json.dumps({'status':'PASS','inventory':[
                {'absolute_path':str(p),'sha256':sha,'byte_size':126} for p,_,_ in objects]}))
            def validated(path,config,*,year,month,boundary):
                count=1 if boundary else calendar.monthrange(year,month)[1]*24
                variables=['ssrd'] if boundary else [v['short_name'] for v in config['variables']]
                first=config['ssrd']['final_provider_validity'] if boundary else f'{year}-{month:02d}-01T00:00:00Z'
                return {'message_count':count*len(variables),'scalar_count':count*len(variables)*9,
                    'bytes':126,'sha256':sha,'ssrd_sign_counts':{'negative':0,'zero':count*9,'positive':0},
                    'validity_coverage':{v:{'first':first,'count':count} for v in variables},'grid':{'status':'PASS'}}
            with patch('emind.weather.structural.validate_grib',side_effect=validated) as validator:
                result=structural_audit(root,self.config,checksum_inventory=inventory)
            self.assertEqual(validator.call_count,85)
            self.assertEqual(result['global_counts']['total_messages_including_boundary'],368209)
            self.assertEqual(result['global_counts']['total_scalar_values_including_boundary'],3313881)
            self.assertEqual(result['per_year_counts']['2026'],dict(objects=1,messages=1,scalar_values=9,bytes=126))
            self.assertEqual(result['per_variable_temporal_coverage']['ssrd']['messages'],61368)

    def test_regression_rejects_mismatch(self):
        fields=('inventory_counts','global_counts','per_year_counts','per_variable_temporal_coverage',
                'ssrd_raw_including_boundary_coverage','quality_counts','ssrd_sign_counts')
        reference={key:{} for key in fields}
        reference.update(ssrd_boundary_accounting=boundary_accounting(self.config),checksum_comparisons={'mismatch_count':0})
        self.assertEqual(compare_structural(reference,reference)['status'],'PASS')
        actual={**reference,'global_counts':{'changed':1}}
        with self.assertRaisesRegex(ValueError,'regression mismatch'):
            compare_structural(actual,reference)


class SpatialTests(unittest.TestCase):
    def setUp(self):
        self.config=load_weather_config(ROOT/'configs/weather/de_cent_era5.yaml')
        self.center,self.neighbours=stencil(self.config)

    def test_neighbours_and_central_exclusion(self):
        self.assertEqual(self.center,(51.,10.25))
        self.assertEqual(len(self.neighbours),8)
        self.assertNotIn(self.center,self.neighbours)
        self.assertEqual(self.neighbours,sorted(self.neighbours))

    def test_mean_median_quantiles(self):
        import statistics
        values=list(range(8))
        self.assertEqual(mean(values),3.5)
        self.assertEqual(statistics.median(values),3.5)
        self.assertEqual(quantile([0.,10.],.95),9.5)

    def test_tie_rank(self):
        values={p:0. for p in [self.center]+self.neighbours}
        self.assertEqual(rank_at(values,self.center),5)
        values[self.center]=-1.
        self.assertEqual(rank_at(values,self.center),1)
        values[self.center]=1.
        self.assertEqual(rank_at(values,self.center),9)

    def test_scalar_metrics(self):
        summary=scalar_metrics([1.,2.,3.],[0.,1.,2.])
        self.assertEqual(summary['pearson_correlation'],1.)
        for key in ('mean_signed_difference','MAE','RMSE','maximum_absolute_difference'):
            self.assertEqual(summary[key],1.)
        self.assertIsNone(scalar_metrics([1.,1.],[2.,2.])['pearson_correlation'])

    def test_ssrd_common_active_mask(self):
        self.assertEqual(active_mask([0.,1.,0.],[0.,0.,1.]),[False,True,True])

    def test_speed_formula(self):
        self.assertEqual(wind_speed(3.,4.),5.)

    def test_vector_formula(self):
        self.assertEqual(vector_error(4.,6.,1.,2.),5.)

    def test_meteorological_direction(self):
        self.assertEqual(wind_from(0.,-2.),0.)
        self.assertEqual(wind_from(-2.,0.),90.)
        self.assertEqual(wind_from(0.,2.),180.)
        self.assertEqual(angular_difference(0.,-2.,-2.,0.),90.)

    def test_low_speed_exclusion(self):
        self.assertIsNone(angular_difference(.5,0.,2.,0.))
        self.assertIsNone(angular_difference(2.,0.,0.,.5))
        self.assertEqual(angular_difference(1.,0.,1.,0.),0.)

    def test_seasons(self):
        self.assertEqual([season(m) for m in range(1,13)],
                         ['DJF','DJF','MAM','MAM','MAM','JJA','JJA','JJA','SON','SON','SON','DJF'])

    def test_extreme_order(self):
        t=datetime(2020,1,1,tzinfo=timezone.utc)
        self.assertEqual(extreme_indices([1.,3.,3.],[t,t+timedelta(hours=2),t+timedelta(hours=1)]),[2,1,0])

    def test_spatial_diagnostics_small_fixture(self):
        index=[datetime(2020,m,1,tzinfo=timezone.utc) for m in (1,4,7,10)]
        columns={v['short_name']:{p:[float(i+j+1) for i in range(4)]
                                 for j,p in enumerate(sorted([self.center]+self.neighbours))}
                 for v in self.config['variables']}
        result=spatial_diagnostics(index,columns,self.config)
        self.assertEqual(result['scalar_diagnostics']['2t']['pooled']['rank_counts']['5'],4)
        self.assertEqual(set(result['scalar_diagnostics']['2t']['seasonal']),{'DJF','MAM','JJA','SON'})
        self.assertIn('neighbour_mean_components',result['wind_vector_and_direction_diagnostics']['10']['pooled']['vector_error'])
        self.assertEqual(result['scalar_diagnostics']['ssrd']['pooled']['comparisons']['neighbour_median']['central_or_neighbour_median_positive']['count'],4)
        self.assertEqual(len(result['extreme_discrepancies']),16)
        self.assertEqual(set(result['scalar_diagnostics']['2t']['seasonal']['DJF']['comparisons']),{'neighbour_median'})
        self.assertEqual(set(result['wind_vector_and_direction_diagnostics']['10']['seasonal']['DJF']['vector_error']),{'neighbour_mean_components'})
        self.assertEqual(result['wind_vector_and_direction_diagnostics']['10']['seasonal']['DJF']['direction'],{})

    def test_numeric_regression_tolerance(self):
        actual={k:{} for k in ('scalar_diagnostics','wind_vector_and_direction_diagnostics','headline_numbers')}
        actual.update(extreme_discrepancies=[],input_object_count=84,timestamp_count=61368,horizon={})
        actual['headline_numbers']={'metric':1.0}
        reference={**actual,'headline_numbers':{'metric':1.+1e-13}}
        self.assertEqual(compare_spatial(actual,reference)['status'],'PASS')
        reference['headline_numbers']['metric']=1.01
        with self.assertRaisesRegex(ValueError,'numeric regression mismatch'):
            compare_spatial(actual,reference)

    def test_validated_input_checksum_mismatch(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            path=Path(root)/'raw.grib';path.write_bytes(b'fixture')
            audit=Path(root)/'audit.json';audit.write_text(json.dumps({'status':'PASS','inventory':[
                {'absolute_path':str(path),'sha256':'wrong','byte_size':7}]}))
            with patch('emind.weather.spatial.audit_plan',return_value=[(path,2019,1)]):
                with self.assertRaisesRegex(ValueError,'checksum/size mismatch'):
                    read_stencil(root,self.config,validated_inventory=audit)


if __name__=='__main__':
    unittest.main()
