"""Optional frozen-RAW integration tests, enabled with explicit runtime paths.

Set EMIND_ERA5_MONTH_RAW and EMIND_ERA5_BOUNDARY_RAW; native ecCodes must be
on PATH. No provider calls occur. Default offline unit discovery skips RAW.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from emind.weather import load_weather_config, validate_grib, build_monthly_request, build_boundary_request


class FrozenGribTests(unittest.TestCase):
    def setUp(self):
        self.config = load_weather_config(ROOT / 'configs/weather/de_cent_era5.yaml')

    def check_raw(self, env, **kwargs):
        name = os.environ.get(env)
        if not name:
            self.skipTest('Explicit frozen RAW fixture path not supplied')
        path = Path(name)
        before = path.stat()
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.dict(sys.modules, {'cdsapi': None}), patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')):
            result = validate_grib(path, self.config, **kwargs)
        after = path.stat()
        self.assertEqual((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_mode, before.st_ino),
                         (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_mode, after.st_ino))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)
        self.assertEqual(result['sha256'], sha)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['temporal_semantics']['status'], 'PASS')
        self.assertEqual(result['grid']['status'], 'PASS')
        self.assertEqual(result['missing_count'], 0)
        self.assertEqual(result['non_finite_count'], 0)
        self.assertEqual(result['ssrd_sign_counts']['negative'], 0)
        return result

    def test_january_2023(self):
        result = self.check_raw('EMIND_ERA5_MONTH_RAW', year=2023, month=1)
        self.assertEqual(result['message_count'], 4464)
        self.assertEqual(result['scalar_count'], 40176)
        self.assertEqual(result['variables'], ['2t', 'ssrd', '10u', '10v', '100u', '100v'])
        for coverage in result['validity_coverage'].values():
            self.assertEqual(coverage, dict(count=744, first='2023-01-01T00:00:00Z', last='2023-01-31T23:00:00Z'))

    def test_boundary(self):
        result = self.check_raw('EMIND_ERA5_BOUNDARY_RAW', boundary=True)
        self.assertEqual(result['message_count'], 1)
        self.assertEqual(result['scalar_count'], 9)
        self.assertEqual(result['validity_coverage']['ssrd']['first'], '2026-01-01T00:00:00Z')
        self.assertEqual(result['sha256'], self.config['ssrd']['boundary_sha256'])

    def test_original_monthly_request(self):
        name = os.environ.get('EMIND_ERA5_MONTH_SCRIPT')
        if not name:
            self.skipTest('Original acquisition script path not supplied')
        # Extract only the pure request function and literal variable declaration;
        # never import or execute the acquisition script's top-level code.
        tree = ast.parse(Path(name).read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'request')
        variables = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == 'VARIABLES' for t in n.targets))
        import calendar
        namespace = {'calendar': calendar, 'VARIABLES': variables, '__builtins__': {'range': range}}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<pure request>', 'exec'), namespace)
        for month in range(1, 13):
            self.assertEqual(build_monthly_request(2023, month, self.config)['request'], namespace['request'](month))

    def test_original_boundary_request(self):
        name = os.environ.get('EMIND_ERA5_BOUNDARY_SCRIPT')
        if not name:
            self.skipTest('Original acquisition script path not supplied')
        tree = ast.parse(Path(name).read_text())
        literals = {t.id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name) and t.id in ('REQ', 'DATASET')}
        plan = build_boundary_request(self.config)
        self.assertEqual(plan['request'], literals['REQ'])
        self.assertEqual(plan['dataset'], literals['DATASET'])


if __name__ == '__main__':
    unittest.main()
