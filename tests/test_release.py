"""Offline identity, portability, generic native components and accepted-byte tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from emind.release import (SCHEMA_VERSION, canonical_json, digest, read_json, relative_path,
                           public_scan, validate_public_values, build_release, verify_release,
                           checksums, files, write_json, validate_document)
from emind.release_inputs import de_cent_spec, ACCEPTED, HOURLY, FUEL
from emind.scenario import fingerprint, sha256

COMMIT = 'a' * 40


class CanonicalTests(unittest.TestCase):
    def test_golden_utf8_nfc_decimal_vector(self):
        data = {'z': -0.0, 'e\u0301': 'e\u0301', 'b': [True, None, 3, 1.25]}
        expected = '{"b":[true,null,3,"1.25"],"z":"0","é":"é"}'.encode()
        self.assertEqual(canonical_json(data), expected)
        self.assertEqual(digest(data), hashlib.sha256(expected).hexdigest())

    def test_recursive_key_order(self):
        self.assertEqual(canonical_json({'b': {'y': 1, 'x': 2}, 'a': 1}),
                         canonical_json({'a': 1, 'b': {'x': 2, 'y': 1}}))

    def test_nonfinite_rejected(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            with self.assertRaises(ValueError): canonical_json({'a': value})

    def test_duplicate_normalized_keys_rejected(self):
        with self.assertRaises(ValueError): canonical_json({'é': 1, 'e\u0301': 2})

    def test_duplicate_json_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'data.json'
            for payload in ['{"a":1,"a":2}', '{"a":NaN}']:
                p.write_text(payload)
                with self.assertRaises(ValueError): read_json(p)

    def test_invalid_paths_rejected(self):
        for path in ['/opt/local/data', 'C:/data/file', '../a', 'a/../b', 'a\\b', 'a//b', './a', 'a\nb', 'a b']:
            with self.subTest(path=path), self.assertRaises(ValueError): relative_path(path)
        self.assertEqual(relative_path('components/tariff/data.csv'), 'components/tariff/data.csv')

    def test_machine_paths_and_secrets_rejected(self):
        for data in [b'/scratch1/user', b'/users/private', b'fontdemo', b'shirka',
                     b'C:\\Users\\person\\data', b'api_key=supersecret', b'-----BEGIN PRIVATE KEY']:
            with self.subTest(data=data), self.assertRaises(ValueError): public_scan(data)
        public_scan(b'k:\\')  # A compressed binary prefix is not a path.
        with self.assertRaises(ValueError): validate_public_values({'path': '/opt/hidden'})

    def test_false_scientific_claims_rejected(self):
        for value in [{'causal_availability_claim': True}, {'co_location_claim': True}, {'reward': 1}]:
            with self.assertRaises(ValueError): validate_public_values(value)
        validate_public_values({'available_at': 'UNKNOWN', 'causal_availability_claim': False})


class GenericBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.descriptor = {'scenario_id': 'SYNTH', 'components': {'tariff': 'synthetic'}}
        write_json(self.root / 'accepted.json', self.descriptor)
        (self.root / 'core.bin').write_bytes(b'opaque accepted core')
        (self.root / 'native.csv').write_bytes(b'date,value\n2020-01-01,2\n')
        side = {'component_id': 'tariff', 'semantic_kind': 'native_daily_tariff',
                'authoritative_path': 'components/tariff/native.csv', 'accepted_artifact_sha256': sha256(self.root / 'native.csv'),
                'format': 'CSV', 'columns': [{'name': 'date', 'type': 'string'}, {'name': 'value', 'type': 'float64'}],
                'units': {'value': 'synthetic'}, 'key_columns': ['date'], 'ordering': 'date ascending',
                'spatial_support': 'synthetic region', 'native_temporal_resolution': 'daily',
                'valid_time_representation': 'date only', 'available_at': 'UNKNOWN', 'source_ids': ['synthetic-source'],
                'snapshot_ids': ['synthetic-snapshot'], 'transformation_id': 'identity', 'missingness_policy': 'none'}
        self.spec = {'dataset_name': 'E-MIND', 'release_version': '0.0.0-dev', 'metadata': {'dataset.json': {'schema_version': SCHEMA_VERSION}},
                     'text_files': {'README.md': 'Synthetic fixture\n', 'LICENSE': 'Pending\n'},
                     'scenarios': [{'scenario_id': 'SYNTH', 'accepted_descriptor_id': 'descriptor',
                                   'scenario': {'scenario_id': 'SYNTH', 'scenario_class': 'CORE_CLIMATE_LOAD', 'rows': 1,
                                       'horizon': {'start': '2020-01-01', 'end_exclusive': '2020-01-02'},
                                       'components': {'tariff': side}, 'hourly_artifact': 'hourly.parquet',
                                       'accepted_composition_scheme': 'synthetic-1',
                                       'scenario_fingerprint': fingerprint(self.descriptor)},
                                   'metadata': {'scenario_id': 'SYNTH', 'hourly_columns': ['timestamp'], 'side_components': [side]},
                                   'lineage': {'scenario_id': 'SYNTH', 'components': [], 'assembly_transformation': 'identity'},
                                   'artifacts': [{'artifact_id': 'core', 'path': 'hourly.parquet', 'role': 'authoritative_hourly_core', 'sha256': sha256(self.root / 'core.bin')},
                                                 {'artifact_id': 'native', 'path': 'components/tariff/native.csv', 'role': 'authoritative_native_component', 'sha256': sha256(self.root / 'native.csv')}]}]}
        self.inputs = {'descriptor': self.root / 'accepted.json', 'core': self.root / 'core.bin', 'native': self.root / 'native.csv'}

    def tearDown(self): self.temp.cleanup()

    def build(self, name='package', spec=None):
        output = self.root / name
        result = build_release(spec or self.spec, self.inputs, output, COMMIT, {}, allow_dev=True)
        return output, result

    def test_rebuild_relocation_and_file_order(self):
        a, result_a = self.build('a')
        reordered = copy.deepcopy(self.spec)
        reordered['scenarios'][0]['artifacts'].reverse()
        b, result_b = self.build('another/place/b', reordered)
        self.assertEqual(result_a, result_b)
        self.assertEqual(files(a), files(b))
        for p in files(a): self.assertEqual((a / p).read_bytes(), (b / p).read_bytes())
        manifest = read_json(a / 'manifest.json')
        self.assertEqual([f['path'] for f in manifest['files']], sorted(f['path'] for f in manifest['files']))

    def test_checksum_hierarchy_without_self_reference(self):
        a, _ = self.build()
        for root in [a, a / 'scenarios/SYNTH']:
            lines = (root / 'checksums.sha256').read_text().splitlines()
            names = [line.split('  ')[1] for line in lines]
            self.assertEqual(names, sorted(names)); self.assertNotIn('checksums.sha256', names)
        self.assertIn('manifest.json', (a / 'checksums.sha256').read_text())
        self.assertIn('scenarios/SYNTH/checksums.sha256', (a / 'checksums.sha256').read_text())

    def test_generic_side_contract_and_no_future_placeholders(self):
        a, _ = self.build()
        self.assertEqual([p.name for p in (a / 'scenarios').iterdir()], ['SYNTH'])
        self.assertTrue((a / 'scenarios/SYNTH/components/tariff/native.csv').is_file())
        self.assertNotIn('DE_CENT', (a / 'manifest.json').read_text())
        self.assertEqual((a / 'scenarios/SYNTH/hourly.parquet').read_bytes(), self.inputs['core'].read_bytes())
        self.assertEqual(read_json(a / 'scenarios/SYNTH/scenario.json')['scenario_fingerprint'], fingerprint(self.descriptor))

    def test_missing_side_contract_rejected(self):
        self.spec['scenarios'][0]['metadata']['side_components'][0].pop('key_columns')
        with self.assertRaises(ValueError): self.build()

    def test_doi_null_and_pending_publication(self):
        a, _ = self.build()
        self.assertIsNone(read_json(a / 'identity.json')['doi'])
        self.assertEqual(read_json(a / 'validation.json')['citation_status'], 'PENDING')
        self.assertFalse((a / 'CITATION.cff').exists())

    def test_scientific_hash_change_changes_release_identity(self):
        _, before = self.build('a')
        self.inputs['core'].write_bytes(b'new synthetic revision')
        self.spec['scenarios'][0]['artifacts'][0]['sha256'] = sha256(self.inputs['core'])
        _, after = self.build('b')
        self.assertNotEqual(before['release_fingerprint'], after['release_fingerprint'])

    def test_hash_mismatch_and_fingerprint_mismatch_rejected_before_creation(self):
        self.inputs['core'].write_bytes(b'tampered')
        with self.assertRaises(ValueError): self.build()
        self.assertFalse((self.root / 'package').exists())
        self.spec['scenarios'][0]['scenario']['scenario_fingerprint'] = '0' * 64
        with self.assertRaises(ValueError): self.build()

    def test_existing_output_refused(self):
        a, _ = self.build()
        before = (a / 'checksums.sha256').read_bytes()
        with self.assertRaises(ValueError): self.build()
        self.assertEqual(before, (a / 'checksums.sha256').read_bytes())

    def test_tamper_detected(self):
        a, _ = self.build(); (a / 'scenarios/SYNTH/components/tariff/native.csv').write_bytes(b'changed')
        with self.assertRaises(ValueError): verify_release(a)

    def test_manifest_and_identity_tamper_even_when_checksums_updated(self):
        a, _ = self.build()
        identity = read_json(a / 'identity.json'); identity['projection']['release_version'] = 'wrong'
        write_json(a / 'identity.json', identity)
        (a / 'checksums.sha256').write_bytes(checksums(a))
        with self.assertRaises(ValueError): verify_release(a)

    def test_production_version_refused(self):
        self.spec['release_version'] = '1.0.0'
        with self.assertRaises(ValueError): self.build()

    def test_generic_builder_has_no_scenario_provider_special_cases(self):
        source = (Path(__file__).resolve().parents[1] / 'src/emind/release.py').read_text()
        for marker in ['DE_CENT', 'ERA5', 'SMARD', 'fuel_de_', "components/fuel"]:
            self.assertNotIn(marker, source)

    def test_semantic_change_changes_identity(self):
        _, before = self.build('a')
        self.spec['scenarios'][0]['scenario']['components']['tariff']['spatial_support'] = 'revised synthetic region'
        _, after = self.build('b')
        self.assertNotEqual(before['release_fingerprint'], after['release_fingerprint'])

    def test_symlinks_and_case_collision_rejected(self):
        (self.root / 'alias').symlink_to(self.root / 'native.csv')
        with self.assertRaises(ValueError): files(self.root)
        (self.root / 'alias').unlink(); (self.root / 'NATIVE.csv').write_bytes(b'test')
        with self.assertRaises(ValueError): files(self.root)


@unittest.skipUnless(os.environ.get('EMIND_SCENARIO_CANDIDATE'), 'Accepted local scenario required')
class AcceptedReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='phase05_a2_rebuild_')
        cls.base = Path(cls.temp.name)
        cls.spec, cls.inputs = de_cent_spec(Path(__file__).resolve().parents[1], os.environ['EMIND_DATA_ROOT'], os.environ['EMIND_SCENARIO_CANDIDATE'])
        # Explicitly fail any Python socket attempt during both offline builds.
        with patch('socket.socket', side_effect=AssertionError('network prohibited')):
            cls.a = cls.base / 'a'; cls.b = cls.base / 'separate/b'
            cls.ra = build_release(cls.spec, cls.inputs, cls.a, COMMIT, {}, allow_dev=True)
            cls.rb = build_release(cls.spec, cls.inputs, cls.b, COMMIT, {}, allow_dev=True)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_accepted_hashes_and_fingerprint(self):
        self.assertEqual(sha256(self.a / 'scenarios/DE_CENT/hourly.parquet'), HOURLY)
        self.assertEqual(sha256(self.a / 'scenarios/DE_CENT/components/fuel/fuel_de_2019_2025.csv'), FUEL)
        d = read_json(self.a / 'scenarios/DE_CENT/scenario.json')
        self.assertEqual(d['scenario_fingerprint'], ACCEPTED)
        self.assertNotEqual(d['public_composition_fingerprint'], ACCEPTED)

    def test_entire_package_identical_rebuild(self):
        self.assertEqual(self.ra, self.rb)
        self.assertEqual(files(self.a), files(self.b))
        for p in files(self.a): self.assertEqual(sha256(self.a / p), sha256(self.b / p))

    def test_spatial_native_and_availability_semantics(self):
        d = read_json(self.a / 'scenarios/DE_CENT/scenario.json'); c = d['components']
        self.assertEqual(len({v['spatial_support'] for v in c.values()}), 4)
        self.assertEqual(c['weather']['anchor'], {'latitude': '51', 'longitude': '10.25'})
        self.assertTrue(all(v['available_at'] == 'UNKNOWN' for v in c.values()))
        self.assertFalse(c['weather']['valid_time_is_available_at'])
        self.assertEqual(c['fuel']['currency'], 'UNRESOLVED'); self.assertEqual(c['fuel']['tax_default'], 'UNSELECTED')
        self.assertFalse(c['fuel']['hourly_resampling'])
        self.assertEqual(c['market']['transformation']['native_regimes'][1]['transformation'], 'ARITHMETIC_MEAN_FOUR_QUARTERS')

    def test_sanitized_complete_registries_and_acquisition(self):
        snapshots = read_json(self.a / 'metadata/snapshot_registry.json')['snapshots']
        sources = read_json(self.a / 'metadata/source_registry.json')['sources']
        self.assertEqual(len(snapshots), 97); self.assertEqual(len(sources), 9)
        for snapshot in snapshots:
            self.assertNotIn('raw_path', snapshot); self.assertEqual(snapshot['redistribution_class'], 'R2')
            self.assertFalse(snapshot['raw_bundled'])
            acquisition = read_json(self.a / snapshot['acquisition_config_path'])
            self.assertEqual(snapshot['sha256'], acquisition['expected_sha256'])
        for p in files(self.a): public_scan((self.a / p).read_bytes())
        self.assertFalse((self.a / 'raw').exists()); self.assertFalse((self.a / 'reports').exists())

    def test_machine_readable_contracts(self):
        for kind in ['manifest', 'identity', 'validation']: validate_document(kind, read_json(self.a / f'{kind}.json'))
        for kind in ['scenario', 'metadata', 'lineage', 'validation']: validate_document(kind, read_json(self.a / f'scenarios/DE_CENT/{kind}.json'))
