"""Synthetic assembly guards and optional offline accepted-candidate regression."""
import json
import os
from pathlib import Path
import sys
import unittest
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from emind.scenario import (START, WEATHER, LOAD, MARKET, ROWS, assemble,
                            exact_support, fingerprint, portable, validate_fuel, sha256, read_csv)
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None


class SupportTests(unittest.TestCase):
    def test_complete_row_count(self):
        times = [START + timedelta(hours=i) for i in range(ROWS)]
        self.assertEqual(exact_support({'weather': times, 'load': times, 'market': times}), ROWS)

    def test_shift_equal_count_rejected(self):
        times = [START + timedelta(hours=i) for i in range(ROWS)]
        with self.assertRaises(ValueError):
            exact_support({'load': [t + timedelta(hours=1) for t in times]})

    def test_gap_duplicate_order_rejected(self):
        times = [START + timedelta(hours=i) for i in range(3)]
        for bad in [times[:2], [times[0], times[0], times[2]], list(reversed(times))]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                exact_support({'market': bad}, times)

    def test_fingerprint_order_determinism(self):
        self.assertEqual(fingerprint({'a': 1, 'b': 2}), fingerprint({'b': 2, 'a': 1}))

    def test_relocation_determinism(self):
        a = {'raw_path': '/first/raw/a', 'software': {'python_executable': '/first/python'}}
        b = {'raw_path': '/second/raw/a', 'software': {'python_executable': '/second/python'}}
        self.assertEqual(fingerprint(portable(a, Path('/first'))), fingerprint(portable(b, Path('/second'))))

    def test_fingerprint_sensitive_to_composition(self):
        self.assertNotEqual(fingerprint({'sha256': 'a'}), fingerprint({'sha256': 'b'}))

    def test_unresolved_absolute_path_rejected(self):
        with self.assertRaises(ValueError):
            portable({'raw_path': '/other/raw/a'}, Path('/first'))

    def test_hourly_fuel_rejected(self):
        with self.assertRaises(ValueError):
            validate_fuel([{'tax_variant': 'WITH_TAX'}] * ROWS)


@unittest.skipIf(pa is None, 'PyArrow required for Parquet assembly')
class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.times = [START, START + timedelta(hours=1)]
        self.weather = pa.table({'timestamp_utc': pa.array(self.times, type=pa.timestamp('ns', tz='UTC')),
                                **{c: [1.0, 2.0] for c in WEATHER}})
        self.load = [{'timestamp_utc': t.isoformat(), LOAD: str(i+1)} for i,t in enumerate(self.times)]
        self.market = [{'timestamp_utc': t.isoformat(), MARKET: str(-i-1)} for i,t in enumerate(self.times)]

    def test_regression_and_allowed_columns(self):
        table = assemble(self.weather, self.load, self.market, self.times)
        self.assertEqual(table.column_names, ['timestamp_utc'] + WEATHER + [LOAD, MARKET])
        self.assertTrue(table.select(['timestamp_utc'] + WEATHER).equals(self.weather))
        self.assertEqual(table[LOAD].to_pylist(), [1.0, 2.0])
        self.assertEqual(table[MARKET].to_pylist(), [-1.0, -2.0])

    def test_mismatch_rejected(self):
        self.market[0]['timestamp_utc'] = self.times[1].isoformat()
        with self.assertRaises(ValueError):
            assemble(self.weather, self.load, self.market, self.times)

    def test_nonfinite_and_negative_load_rejected(self):
        for value in ['nan', 'inf', '-1']:
            self.load[0][LOAD] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                assemble(self.weather, self.load, self.market, self.times)

    def test_extra_plant_column_rejected(self):
        with self.assertRaises(ValueError):
            assemble(self.weather.append_column('battery_soc', pa.array([0.5, 0.5])), self.load, self.market, self.times)


@unittest.skipUnless(pa is not None and os.environ.get('EMIND_SCENARIO_CANDIDATE'), 'Set offline candidate and data-root environment variables')
class AcceptedCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = Path(os.environ['EMIND_SCENARIO_CANDIDATE'])
        cls.root = Path(os.environ['EMIND_DATA_ROOT'])
        cls.config = json.loads((cls.output / 'scenario.json').read_text())
        cls.validation = json.loads((cls.output / 'validation.json').read_text())
        cls.table = pq.read_table(cls.output / 'hourly.parquet')

    def test_candidate_support_and_schema(self):
        self.assertEqual(self.table.num_rows, ROWS)
        exact_support({'scenario': self.table['timestamp_utc'].to_pylist()})
        self.assertEqual(self.table.column_names, ['timestamp_utc'] + WEATHER + [LOAD, MARKET])
        self.assertFalse(self.config['historical_colocated_microgrid'])
        self.assertFalse(self.config['co_location_claim'])
        self.assertEqual(len(set(c['spatial_support'] for c in self.config['components'].values())), 4)
        forbidden = {'reward', 'objective_function', 'plant_sizes', 'battery_soc', 'diesel_dispatch',
                     'observation_vector', 'action_vector', 'grid_exchange', 'safety_penalty', 'feasibility_label'}
        def check_keys(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for v in value.values(): check_keys(v)
            elif isinstance(value, list):
                for v in value: check_keys(v)
        check_keys(self.config)

    def test_candidate_identity_and_checksums(self):
        self.assertEqual(fingerprint(self.config), self.validation['scenario_fingerprint'])
        for line in (self.output / 'checksums.sha256').read_text().splitlines():
            digest, name = line.split()
            self.assertEqual(sha256(self.output / name), digest)
        def walk(value):
            if isinstance(value, dict):
                for v in value.values(): walk(v)
            elif isinstance(value, list):
                for v in value: walk(v)
            elif isinstance(value, str): self.assertFalse(value.startswith('/'))
        walk(self.config)

    def test_independent_component_regression(self):
        for name, component in self.config['components'].items():
            path = self.root / component['artifact']
            self.assertEqual(sha256(path), component['sha256'])
            if name == 'weather':
                original = pq.read_table(path)
                self.assertTrue(self.table.select(original.column_names).equals(original))
            elif name in ['load', 'market']:
                column = LOAD if name == 'load' else MARKET
                rows = read_csv(path)
                times = [__import__('datetime').datetime.fromisoformat(r['timestamp_utc'].replace('Z', '+00:00')) for r in rows]
                self.assertEqual(times, self.table['timestamp_utc'].to_pylist())
                self.assertEqual([float(r[column]) for r in rows], self.table[column].to_pylist())
            else:
                self.assertEqual(validate_fuel(read_csv(path)), {'WITH_TAX': 356, 'WITHOUT_TAX': 356})
                self.assertFalse(component['hourly_resampling'])
                self.assertFalse(component['interpolation'])
                self.assertEqual(self.config['fuel_storage'], 'REFERENCE_ONLY_NATIVE_CADENCE')
