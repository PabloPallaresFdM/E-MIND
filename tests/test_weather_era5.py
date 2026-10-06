"""Offline request contracts and safety tests; no native tools or RAW required."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from emind.weather import (load_weather_config, build_monthly_request,
                           build_boundary_request, dry_run, execute_request, build_provenance)
CONFIG = ROOT / 'configs/weather/de_cent_era5.yaml'


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.config = load_weather_config(CONFIG)
        self.plan = build_monthly_request(2023, 1, self.config)
        self.request = self.plan['request']

    def test_january(self):
        self.assertEqual(self.request['day'], [f'{d:02d}' for d in range(1, 32)])

    def test_february(self):
        self.assertEqual(len(build_monthly_request(2023, 2, self.config)['request']['day']), 28)

    def test_leap_year(self):
        self.assertEqual(len(build_monthly_request(2024, 2, self.config)['request']['day']), 29)

    def test_variables(self):
        self.assertEqual(self.request['variable'], ['2m_temperature', 'surface_solar_radiation_downwards',
            '10m_u_component_of_wind', '10m_v_component_of_wind',
            '100m_u_component_of_wind', '100m_v_component_of_wind'])

    def test_hours(self):
        self.assertEqual(self.request['time'], [f'{h:02d}:00' for h in range(24)])

    def test_area(self):
        self.assertEqual(self.request['area'], [51.25, 10., 50.75, 10.5])

    def test_dataset(self):
        self.assertEqual(self.plan['dataset'], 'reanalysis-era5-single-levels')

    def test_product(self):
        self.assertEqual(self.request['product_type'], ['reanalysis'])

    def test_grib(self):
        self.assertEqual(self.request['data_format'], 'grib')
        self.assertEqual(self.request['download_format'], 'unarchived')

    def test_no_grid(self):
        self.assertNotIn('grid', self.request)

    def test_2018_rejected(self):
        with self.assertRaises(ValueError):
            build_monthly_request(2018, 1, self.config)

    def test_2026_rejected(self):
        with self.assertRaises(ValueError):
            build_monthly_request(2026, 1, self.config)

    def test_boundary_variable(self):
        self.assertEqual(build_boundary_request(self.config)['request']['variable'],
                         ['surface_solar_radiation_downwards'])

    def test_boundary_timestamp(self):
        r = build_boundary_request(self.config)['request']
        self.assertEqual([r[k] for k in ('year', 'month', 'day', 'time')],
                         [['2026'], ['01'], ['01'], ['00:00']])

    def test_dry_run_no_cds(self):
        with patch.dict(sys.modules, {'cdsapi': None}), patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
            self.assertEqual(dry_run(self.plan, 'raw')['path'], 'raw/era5_de_cent_2023_01.grib')
            spec = importlib.util.spec_from_file_location('era5_cli', ROOT / 'scripts/weather/era5.py')
            cli = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cli)
            output = io.StringIO()
            with redirect_stdout(output):
                cli.main(['--config', str(CONFIG), '--destination-root', 'raw', '--year', '2023', '--month', '1'])
            self.assertEqual(json.loads(output.getvalue()), dry_run(self.plan, 'raw'))

    def test_filenames(self):
        self.assertEqual(self.plan['filename'], 'era5_de_cent_2023_01.grib')
        self.assertEqual(build_boundary_request(self.config)['filename'],
                         'era5_de_cent_ssrd_2026_01_01_0000_utc.grib')

    def test_invalid_month(self):
        for month in (0, 13, True):
            with self.assertRaises(ValueError):
                build_monthly_request(2023, month, self.config)

    def test_existing_object_refused_before_cds(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            path = Path(root) / self.plan['filename']
            path.write_bytes(b'immutable')
            with patch.dict(sys.modules, {'cdsapi': None}), self.assertRaises(FileExistsError):
                execute_request(self.config, root, year=2023, month=1)
            self.assertEqual(path.read_bytes(), b'immutable')

    def test_provenance_utc_and_summary(self):
        validation = dict(status='PASS', path='raw/object.grib', bytes=1, sha256='abc')
        with patch('emind.weather.era5.subprocess.check_output', return_value='ecCodes 2.49.0'):
            record = build_provenance(self.plan, validation, '2026-10-02T00:00:00Z')
        self.assertEqual(record['request'], self.request)
        self.assertEqual(record['validation'], validation)
        with self.assertRaises(ValueError):
            build_provenance(self.plan, validation, '2026-10-02T01:00:00+01:00')


if __name__ == '__main__':
    unittest.main()
