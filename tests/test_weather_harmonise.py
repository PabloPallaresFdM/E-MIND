"""Pure synthetic harmonisation contracts; no Scratch, ecCodes or CDS required."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from emind.weather import canonical_index, load_weather_config
from emind.weather.harmonise import (COLUMNS, central_value, collect_provider, construct_rows,
                                     discover_inputs, harmonise_weather)


class HarmonisationTests(unittest.TestCase):
    def setUp(self):
        self.config = load_weather_config(ROOT / 'configs/weather/de_cent_era5.yaml')
        self.start = datetime(2019, 1, 1, tzinfo=timezone.utc)
        self.end = datetime(2025, 12, 31, 23, tzinfo=timezone.utc)

    def synthetic(self, index=None):
        index = [self.start] if index is None else index
        records = []
        for t in index:
            for v in self.config['variables']:
                validity = t + timedelta(hours=v['provider_validity_offset_hours'])
                value = 7200. if v['short_name'] == 'ssrd' else 3. if v['short_name'].endswith('u') else 4.
                records.append((v['short_name'], validity, value, 'monthly.grib'))
        return collect_provider(records, self.config)

    def test_hourly_index(self):
        index = canonical_index(self.config)
        self.assertEqual(len(index), 61368)
        self.assertEqual(index[0], self.start)
        self.assertEqual(index[-1], self.end)
        self.assertTrue(all(b-a == timedelta(hours=1) for a,b in zip(index,index[1:])))

    def test_leap_years(self):
        self.assertEqual(dict(Counter(t.year for t in canonical_index(self.config))),
                         {2019:8760,2020:8784,2021:8760,2022:8760,2023:8760,2024:8784,2025:8760})

    def test_instantaneous_mapping(self):
        values, sources = self.synthetic()
        values['2t'][self.start] = 270.
        data, _ = construct_rows(values, sources, self.config, index=[self.start])
        self.assertEqual(data['air_temperature_2m_k'], [270.])

    def test_ssrd_endpoint_mapping(self):
        values, sources = self.synthetic()
        _, lineage = construct_rows(values, sources, self.config, index=[self.start])
        self.assertEqual(lineage[0]['ssrd_provider_validity_utc'], '2019-01-01T01:00:00Z')

    def test_initial_ssrd_excluded(self):
        values, sources = self.synthetic()
        values['ssrd'][self.start] = 99999.
        data, _ = construct_rows(values, sources, self.config, index=[self.start])
        self.assertEqual(data['surface_solar_radiation_downwards_j_m2'], [7200.])

    def test_final_boundary_required(self):
        values, sources = self.synthetic([self.end])
        endpoint = self.end + timedelta(hours=1)
        sources['ssrd'][endpoint] = 'boundary.grib'
        _, lineage = construct_rows(values, sources, self.config, index=[self.end])
        self.assertEqual(lineage[0]['ssrd_provider_validity_utc'], '2026-01-01T00:00:00Z')
        self.assertEqual(lineage[0]['raw_path'], 'boundary.grib')

    def test_irradiance(self):
        data, _ = construct_rows(*self.synthetic(), self.config, index=[self.start])
        self.assertEqual(data['surface_solar_irradiance_w_m2'], [2.])

    def test_speed_10m(self):
        values, sources = self.synthetic()
        values['10u'][self.start], values['10v'][self.start] = 3.,4.
        data, _ = construct_rows(values, sources, self.config, index=[self.start])
        self.assertEqual(data['wind_speed_10m_m_s'], [5.])

    def test_speed_100m(self):
        values, sources = self.synthetic()
        values['100u'][self.start], values['100v'][self.start] = 5.,12.
        data, _ = construct_rows(values, sources, self.config, index=[self.start])
        self.assertEqual(data['wind_speed_100m_m_s'], [13.])

    def test_column_order(self):
        data, _ = construct_rows(*self.synthetic(), self.config, index=[self.start])
        self.assertEqual(list(data), ['timestamp_utc','air_temperature_2m_k',
          'surface_solar_radiation_downwards_j_m2','surface_solar_irradiance_w_m2',
          'wind_u_10m_m_s','wind_v_10m_m_s','wind_speed_10m_m_s',
          'wind_u_100m_m_s','wind_v_100m_m_s','wind_speed_100m_m_s'])

    def test_central_only(self):
        self.assertEqual(central_value([(51.,10.25,7.),(51.,10.,999.)], self.config),7.)
        with self.assertRaisesRegex(ValueError,'central point'):
            central_value([(51.,10.,999.)], self.config)

    def test_processing_flags(self):
        self.assertEqual(self.config['processing'], dict.fromkeys(
            ('interpolation','clipping','imputation','spatial_aggregation'),False))

    def test_missing_boundary_fails(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            with self.assertRaisesRegex(ValueError,'missing final SSRD boundary'):
                discover_inputs(root,self.config)
        values, sources = self.synthetic([self.end])
        values['ssrd'].clear()
        with self.assertRaisesRegex(ValueError,'required endpoint for ssrd'):
            construct_rows(values,sources,self.config,index=[self.end])

    def test_duplicates_fail(self):
        record = ('2t',self.start,270.,'fixture')
        with self.assertRaisesRegex(ValueError,'duplicate provider timestamp'):
            collect_provider([record,record],self.config)

    def test_missing_hour_fails(self):
        values,sources = self.synthetic()
        with self.assertRaisesRegex(ValueError,'missing canonical hour'):
            construct_rows(values,sources,self.config,index=[self.start,self.start+timedelta(hours=1)])

    def test_all_year_end_mappings(self):
        index=[datetime(y,12,31,23,tzinfo=timezone.utc) for y in range(2019,2026)]
        values,sources=self.synthetic(index)
        _,lineage=construct_rows(values,sources,self.config,index=index)
        self.assertEqual([r['ssrd_provider_validity_utc'] for r in lineage],
                         [f'{y+1}-01-01T00:00:00Z' for y in range(2019,2026)])

    def test_nonfinite_and_negative_fail(self):
        for s,v in [('2t',float('nan')),('ssrd',-1.)]:
            with self.assertRaises(ValueError):
                collect_provider([(s,self.start,v,'fixture')],self.config)


if __name__=='__main__':
    unittest.main()
