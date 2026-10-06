"""Offline coverage of the frozen contract and fail-closed loading."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from emind.weather import WeatherConfigError, load_weather_config, validate_weather_config

CONFIG = ROOT / "configs/weather/de_cent_era5.yaml"


class WeatherConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = load_weather_config(CONFIG)

    def test_frozen_science(self):
        c = self.config
        self.assertEqual(c['scenario_id'], 'DE_CENT')
        self.assertEqual(c['dataset'], 'reanalysis-era5-single-levels')
        self.assertEqual(c['product_type'], 'reanalysis')
        self.assertEqual(c['anchor'], {'latitude': 51.0, 'longitude': 10.25})
        self.assertEqual(c['audit_bbox'], {'north': 51.25, 'west': 10., 'south': 50.75, 'east': 10.5})
        identities = [('2m_temperature', '2t', 167),
                      ('surface_solar_radiation_downwards', 'ssrd', 169),
                      ('10m_u_component_of_wind', '10u', 165),
                      ('10m_v_component_of_wind', '10v', 166),
                      ('100m_u_component_of_wind', '100u', 228246),
                      ('100m_v_component_of_wind', '100v', 228247)]
        self.assertEqual([(v['provider_name'], v['short_name'], v['param_id'])
                          for v in c['variables']], identities)
        self.assertEqual([v['provider_validity_offset_hours'] for v in c['variables']], [0, 1, 0, 0, 0, 0])
        self.assertEqual(c['horizon'], {'start': '2019-01-01T00:00:00Z',
                         'end_inclusive': '2025-12-31T23:00:00Z', 'expected_hours': 61368})
        start = datetime.strptime(c['horizon']['start'], '%Y-%m-%dT%H:%M:%SZ')
        end = datetime.strptime(c['horizon']['end_inclusive'], '%Y-%m-%dT%H:%M:%SZ')
        self.assertEqual((end-start).total_seconds()/3600+1, 61368)
        self.assertEqual(c['canonical_time'], {'frequency_hours': 1, 'timezone': 'UTC', 'timestamp_semantics': 'interval_start'})
        self.assertEqual(c['ssrd']['final_provider_validity'], '2026-01-01T00:00:00Z')
        self.assertEqual(c['ssrd']['accumulation_seconds'], 3600)
        self.assertEqual(c['ssrd']['boundary_sha256'], 'e2269e7d30c2ab42189d9299e3d418ae752475e1602353ab85d929f7a830a8b1')
        self.assertEqual(c['derivations'], {
            'surface_solar_irradiance_w_m2': 'surface_solar_radiation_downwards_j_m2 / 3600',
            'wind_speed_10m_m_s': 'sqrt(wind_u_10m_m_s^2 + wind_v_10m_m_s^2)',
            'wind_speed_100m_m_s': 'sqrt(wind_u_100m_m_s^2 + wind_v_100m_m_s^2)'})
        self.assertEqual(c['processing'], dict.fromkeys(['interpolation', 'clipping', 'imputation', 'spatial_aggregation'], False))
        self.assertEqual(c['availability'], {'available_at': 'UNKNOWN', 'valid_time_is_available_at': False, 'causal_availability_claim': False})

    def test_every_leaf_is_required_and_frozen(self):
        def walk(node, path=()):
            for key, value in (enumerate(node) if isinstance(node, list) else node.items()):
                p = path + (key,)
                if isinstance(value, (dict, list)):
                    yield from walk(value, p)
                else:
                    yield p
        for path in walk(self.config):
            for remove in (False, True):
                with self.subTest(path=path, remove=remove):
                    changed = deepcopy(self.config)
                    parent = changed
                    for key in path[:-1]:
                        parent = parent[key]
                    if remove:
                        del parent[path[-1]]
                    else:
                        parent[path[-1]] = None
                    with self.assertRaises(WeatherConfigError):
                        validate_weather_config(changed)

    def test_duplicate_variables_and_boolean_number_confusion(self):
        for change in ('duplicate', 'boolean'):
            c = deepcopy(self.config)
            if change == 'duplicate':
                c['variables'][1] = deepcopy(c['variables'][0])
            else:
                c['canonical_time']['frequency_hours'] = True
            with self.assertRaises(WeatherConfigError):
                validate_weather_config(c)

    def test_order_normalization_does_not_mutate_input(self):
        c = deepcopy(self.config)
        c['variables'].reverse()
        self.assertEqual(validate_weather_config(c), self.config)
        self.assertEqual(c['variables'][0]['short_name'], '100v')

    def test_no_machine_paths_or_credentials(self):
        text = CONFIG.read_text()
        for forbidden in ('/scratch1/', '/users/fontdemo/', 'credential', 'token', 'api_key', 'password', 'secret', 'https://'):
            self.assertNotIn(forbidden, text.lower())
        for key in ('runtime_path', 'token'):
            c = deepcopy(self.config)
            c[key] = 'synthetic forbidden value'
            with self.assertRaises(WeatherConfigError):
                validate_weather_config(c)

    def test_bad_files_and_duplicate_mapping_keys(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = Path(tmp) / 'config.yaml'
            for text in ('{', '[]', '{"scenario_id":"DE_CENT","scenario_id":"DE_CENT"}'):
                path.write_text(text)
                with self.assertRaises(WeatherConfigError):
                    load_weather_config(path)
            with self.assertRaises(WeatherConfigError):
                load_weather_config(Path(tmp) / 'missing.yaml')


if __name__ == '__main__':
    unittest.main()
