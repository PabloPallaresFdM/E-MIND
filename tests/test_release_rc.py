"""Owner publication gates, failure atomicity and accepted offline RC rebuilds."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_release as development
COMMIT = development.COMMIT
from emind.release import build_release, files, read_json, sha256, verify_release
from emind.release_gates import STATE, CLOSED_ITEMS, validate_citation
from emind.release_inputs import de_cent_rc_spec, ACCEPTED, HOURLY, FUEL

REPO = Path(__file__).resolve().parents[1]


class RCGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = development.GenericBuildTests()
        self.fixture.setUp()
        self.root, self.inputs = self.fixture.root, self.fixture.inputs
        self.spec = copy.deepcopy(self.fixture.spec)
        self.spec['release_version'] = '0.1.0-rc.1'
        self.spec['publication_items'] = []
        licenses = read_json(REPO / 'metadata/licenses.json')
        source = dict(licenses['sources'][0], source_id='synthetic-source')
        licenses['sources'] = [source]
        self.spec['metadata'].update({'licenses.json': licenses,
            'source_registry.json': {'sources': [dict(source)]},
            'snapshot_registry.json': {'snapshots': [{'snapshot_id': 'synthetic-snapshot',
                'source_ids': ['synthetic-source'], 'sha256': 'a' * 64, 'acquisition_config_id': 'synthetic',
                'acquisition_config_path': 'metadata/acquisition/synthetic.json'}]},
            'acquisition/synthetic.json': {'expected_sha256': 'a' * 64, 'acquisition_config_id': 'synthetic'}})
        self.spec['metadata']['dataset.json'].update(STATE, release_version='0.1.0-rc.1',
            authors=[{'given-names': 'Pablo', 'family-names': 'Pallarés'}], authorship_status='FROZEN',
            publication_blockers=[{'item': x, 'status': 'CLOSED'} for x in sorted(CLOSED_ITEMS)]
                + [{'item': 'DOI absent', 'status': 'NON-BLOCKING'}, {'item': 'Zenodo absent', 'status': 'NON-BLOCKING'}])
        self.spec['text_files'] = {n: (REPO / n).read_text() for n in ('LICENSE', 'LICENSE-METADATA', 'CITATION.cff')}
        self.spec['text_files']['README.md'] = 'This is a local release candidate / pre-release artifact.\n'

    def tearDown(self):
        self.fixture.tearDown()

    def build(self, name='rc'):
        return build_release(self.spec, self.inputs, self.root / name, COMMIT, {})

    def rejected(self):
        with self.assertRaises(ValueError): self.build()
        self.assertFalse((self.root / 'rc').exists(), 'Preflight must fail before creating destination')

    def test_missing_code_license(self):
        del self.spec['metadata']['licenses.json']['code_license']; self.rejected()

    def test_missing_metadata_license(self):
        del self.spec['metadata']['licenses.json']['metadata_license']; self.rejected()

    def test_missing_authorship(self):
        self.spec['metadata']['dataset.json']['authors'] = []; self.rejected()

    def test_unfrozen_authorship(self):
        self.spec['metadata']['dataset.json']['authorship_status'] = 'PENDING'; self.rejected()

    def test_missing_citation(self):
        del self.spec['text_files']['CITATION.cff']; self.rejected()

    def test_invalid_citation_schema(self):
        # Semantically matching but schema-invalid mandatory message type.
        self.spec['text_files']['CITATION.cff'] = self.spec['text_files']['CITATION.cff'].replace('message: >-', 'message: 7\nunused: >-')
        self.rejected()

    def test_duplicate_citation_keys(self):
        self.spec['text_files']['CITATION.cff'] += 'version: "0.1.0-rc.1"\n'; self.rejected()

    def test_unresolved_blocking_license(self):
        self.spec['metadata']['licenses.json']['sources'][0]['status'] = 'PENDING'; self.rejected()

    def test_source_attribution_missing(self):
        del self.spec['metadata']['licenses.json']['sources'][0]['attribution']; self.rejected()

    def test_missing_source_license(self):
        self.spec['metadata']['licenses.json']['sources'] = []; self.rejected()

    def test_artifact_hash_mismatch(self):
        self.inputs['core'].write_bytes(b'corruption'); self.rejected()

    def test_scenario_fingerprint_mismatch(self):
        self.spec['scenarios'][0]['scenario']['scenario_fingerprint'] = '0' * 64; self.rejected()

    def test_publication_status_omitted(self):
        del self.spec['metadata']['dataset.json']['publication_status']; self.rejected()

    def test_doi_inconsistent(self):
        self.spec['metadata']['dataset.json']['doi'] = '10.0000/example'; self.rejected()

    def test_doi_omitted(self):
        del self.spec['metadata']['dataset.json']['doi']; self.rejected()

    def test_stale_blocker_status(self):
        self.spec['metadata']['dataset.json']['publication_blockers'][0]['status'] = 'PENDING'; self.rejected()

    def test_stale_acquisition_status(self):
        self.spec['metadata']['acquisition/synthetic.json'] = {'license_status': 'PENDING_REVIEW'}; self.rejected()

    def test_upstream_relicensing_refused(self):
        self.spec['metadata']['licenses.json']['upstream_relicensed'] = True; self.rejected()

    def test_version_mismatch(self):
        self.spec['metadata']['dataset.json']['release_version'] = '0.0.0-dev'; self.rejected()

    def test_citation_version_mismatch(self):
        self.spec['text_files']['CITATION.cff'] = self.spec['text_files']['CITATION.cff'].replace('0.1.0-rc.1', '0.0.0-dev'); self.rejected()

    def test_dev_requires_explicit_opt_in(self):
        with self.assertRaises(ValueError):
            build_release(self.fixture.spec, self.inputs, self.root / 'dev', COMMIT, {})

    def test_gated_rc_determinism_and_states(self):
        with patch('socket.socket', side_effect=AssertionError('network prohibited')):
            a = self.build('a'); b = self.build('separate/b')
        self.assertEqual(a, b)
        for p in files(self.root / 'a'):
            self.assertEqual(sha256(self.root / 'a' / p), sha256(self.root / 'separate/b' / p))
        for name in ('identity.json', 'manifest.json', 'validation.json', 'metadata/dataset.json'):
            doc = read_json(self.root / 'a' / name)
            for k, v in STATE.items(): self.assertEqual(doc[k], v)
        self.assertEqual((self.root / 'a/VERSION').read_text().strip(), '0.1.0-rc.1')
        manifest = read_json(self.root / 'a/manifest.json')
        for name in ('LICENSE', 'LICENSE-METADATA', 'CITATION.cff', 'README.md'):
            self.assertTrue(next(x for x in manifest['files'] if x['path'] == name)['authoritative'])

    def test_publication_text_changes_identity(self):
        a = self.build('a')
        self.spec['text_files']['CITATION.cff'] += '# Citation wording revision\n'
        b = self.build('b')
        self.assertNotEqual(a['release_fingerprint'], b['release_fingerprint'])


@unittest.skipUnless(os.environ.get('EMIND_SCENARIO_CANDIDATE'), 'Accepted local scenario required')
class AcceptedRCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='emind_rc_test_')
        cls.root = Path(cls.temp.name)
        cls.spec, cls.inputs = de_cent_rc_spec(REPO, os.environ['EMIND_DATA_ROOT'], os.environ['EMIND_SCENARIO_CANDIDATE'])
        with patch('socket.socket', side_effect=AssertionError('network prohibited')):
            cls.a = build_release(cls.spec, cls.inputs, cls.root / 'a', COMMIT, {})
            cls.b = build_release(cls.spec, cls.inputs, cls.root / 'b', COMMIT, {})

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_scientific_immutability(self):
        self.assertEqual(sha256(self.root / 'a/scenarios/DE_CENT/hourly.parquet'), HOURLY)
        self.assertEqual(sha256(self.root / 'a/scenarios/DE_CENT/components/fuel/fuel_de_2019_2025.csv'), FUEL)
        self.assertEqual(self.a['scenarios'][0]['scenario_fingerprint'], ACCEPTED)

    def test_entire_offline_rc_identical(self):
        self.assertEqual(self.a, self.b)
        self.assertEqual(files(self.root / 'a'), files(self.root / 'b'))
        for p in files(self.root / 'a'):
            self.assertEqual(sha256(self.root / 'a' / p), sha256(self.root / 'b' / p))
        self.assertEqual(verify_release(self.root / 'a'), self.a)

    def test_attributions_provenance_and_closed_states(self):
        licenses = read_json(self.root / 'a/metadata/licenses.json')
        self.assertEqual(licenses['code_license'], 'MIT')
        self.assertEqual(licenses['metadata_license'], 'CC-BY-4.0')
        self.assertFalse(licenses['upstream_relicensed'])
        text = (self.root / 'a/README.md').read_text()
        for provider in ('Bundesnetzagentur', 'ECMWF', 'Copernicus Climate Change Service', 'European Commission'):
            self.assertIn(provider, text)
        self.assertEqual(len(list((self.root / 'a/metadata/acquisition').glob('*.json'))), 97)
        dataset = read_json(self.root / 'a/metadata/dataset.json')
        validate_citation((self.root / 'a/CITATION.cff').read_text(), dataset['release_version'], dataset['authors'])
        for item in dataset['publication_blockers']:
            self.assertIn(item['status'], ('CLOSED', 'NON-BLOCKING'))
