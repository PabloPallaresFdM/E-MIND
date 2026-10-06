"""Offline schema/tool checks using synthetic metadata in disposable directories.

These are software fixtures, not proposed scientific source decisions. All URLs
use the reserved example.invalid domain, and no network operations are performed.
"""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'source_tools'
sys.path.insert(0, str(TOOLS))
import registry_schema as schema
import register_source


def source(source_id='fixture-a', **changes):
    entry = dict.fromkeys(schema.FIELDS)
    entry.update(source_id=source_id, source_type='derived', status='PENDING_REVIEW')
    entry.update(changes)
    return entry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='emind-registry-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.registry = self.root / 'registry.yaml'
        self.csv_path = self.root / 'registry.csv'
        self.matrix = self.root / 'matrix.csv'
        self.input = self.root / 'entry.json'
        self.write_pair([])
        self.matrix.write_text(','.join(schema.MATRIX_FIELDS) + '\n', encoding='utf-8')

    def write_pair(self, entries):
        self.registry.write_text(schema.yaml_text(entries), encoding='utf-8')
        self.csv_path.write_text(schema.csv_text(entries), encoding='utf-8')

    def pair_bytes(self):
        return self.registry.read_bytes(), self.csv_path.read_bytes()

    def command(self, script, *arguments):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, str(TOOLS / script), *map(str, arguments)],
                              cwd=str(self.root), env=environment, capture_output=True,
                              text=True, timeout=15)

    def register(self, entry=None, *arguments):
        if entry is not None:
            self.input.write_text(json.dumps(entry), encoding='utf-8')
        return self.command('register_source.py', self.input,
                            '--registry', self.registry, '--csv', self.csv_path, *arguments)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_set_validates_through_cli(self):
        result = self.command('validate_source_registry.py', '--registry', self.registry,
                              '--csv', self.csv_path, '--matrix', self.matrix)
        self.assert_success(result)
        self.assertIn('0 sources', result.stdout)
        self.assertEqual(schema.validate_pair(self.registry, self.csv_path), [])
        self.assertEqual(schema.validate_matrix(self.matrix, []), 0)
        for path in (self.registry, self.csv_path):
            with self.subTest(path=path.name):
                self.assert_success(self.command('validate_source_registry.py', path))

    def test_every_schema_field_is_required(self):
        for field in schema.FIELDS:
            entry = source()
            del entry[field]
            with self.subTest(field=field), self.assertRaises(schema.RegistryError):
                schema.validate_sources([entry])

    def test_enum_values_and_nonempty_source_identity(self):
        for field, value in (('source_type', 'invalid'), ('status', 'accepted_core'),
                             ('source_type', None), ('status', None),
                             ('source_id', None), ('source_id', ''),
                             ('source_id', ' fixture-a ')):
            with self.subTest(field=field, value=value), self.assertRaises(schema.RegistryError):
                schema.validate_sources([source(**{field: value})])
        for value in schema.SOURCE_TYPES:
            schema.validate_sources([source(source_type=value)])
        for value in schema.STATUSES:
            schema.validate_sources([source(status=value, provider='Fixture provider',
                                            dataset_name='Fixture dataset', access_method='Fixture method',
                                            licence_url='https://example.invalid/licence',
                                            reason='Fixture reason')])

    def test_duplicate_source_id_is_rejected(self):
        with self.assertRaises(schema.RegistryError):
            schema.validate_sources([source(), source(notes='Different metadata')])
        document = schema.document([source()])
        document['sources'].append(source())
        self.registry.write_text(json.dumps(document), encoding='utf-8')
        self.assert_failure(self.command('validate_source_registry.py', self.registry))

    def test_accepted_sources_require_four_populated_fields(self):
        for status in ('ACCEPTED_CORE', 'ACCEPTED_EXTENSION'):
            accepted = source(status=status, provider='Fixture provider', dataset_name='Fixture dataset',
                              licence_url='https://example.invalid/licence', access_method='Fixture method')
            schema.validate_sources([accepted])
            for field in ('provider', 'dataset_name', 'licence_url', 'access_method'):
                for empty in (None, '', '  '):
                    with self.subTest(status=status, field=field, empty=empty):
                        invalid = dict(accepted, **{field: empty})
                        with self.assertRaises(schema.RegistryError):
                            schema.validate_sources([invalid])

    def test_blocked_sources_require_reason(self):
        for reason in (None, '', ' \t'):
            with self.subTest(reason=reason), self.assertRaises(schema.RegistryError):
                schema.validate_sources([source(status='BLOCKED', reason=reason)])
        schema.validate_sources([source(status='BLOCKED', reason='Fixture review outstanding')])

    def test_typed_metadata_and_utc_timestamp(self):
        for field in schema.BOOLEAN_FIELDS:
            for invalid in ('true', 'false', 0, 1, [], {}):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(schema.RegistryError):
                    schema.validate_sources([source(**{field: invalid})])
        for invalid in ('2026-09-08', '2026-09-08T00:00:00', '2026-09-08T00:00:00+02:00', 'invalid'):
            with self.subTest(timestamp=invalid), self.assertRaises(schema.RegistryError):
                schema.validate_sources([source(metadata_checked_at_utc=invalid)])
        schema.validate_sources([source(metadata_checked_at_utc='2026-09-08T00:00:00Z')])
        with self.assertRaises(schema.RegistryError):
            schema.validate_sources([source(provider=['Fixture provider'])])

    def test_unknown_and_nested_credential_fields_are_rejected(self):
        invalid_entries = [source(unexpected='Fixture value'), source(api_token='FAKE_TEST_VALUE'),
                           source(notes={'wrapper': {'password': 'FAKE_TEST_VALUE'}}),
                           source(notes=[{'credentials': 'FAKE_TEST_VALUE'}])]
        for entry in invalid_entries:
            with self.subTest(keys=list(entry)), self.assertRaises(schema.RegistryError):
                schema.validate_sources([entry])

    def test_credential_urls_are_rejected_without_echoing_values(self):
        for endpoint in ('https://example.invalid/data?token=FAKE_TEST_VALUE',
                         'https://example.invalid/data?%61pi_key=FAKE_TEST_VALUE',
                         'https://example.invalid/data#access_token=FAKE_TEST_VALUE',
                         'https://fixture:FAKE_TEST_VALUE@example.invalid/data'):
            with self.subTest(endpoint=endpoint):
                result = self.register(source(api_or_download_endpoint=endpoint))
                self.assert_failure(result)
                self.assertNotIn('FAKE_TEST_VALUE', result.stdout + result.stderr)
        schema.validate_sources([source(api_or_download_endpoint='https://example.invalid/data?format=csv')])

    def test_yaml_csv_round_trip_preserves_native_metadata_and_multiline_text(self):
        entry = source(native_unit='Fixture unit', native_frequency='Fixture native interval',
                       native_timezone='Fixture timezone', dataset_version='001.20',
                       coverage_start='2000-01-01', notes='Fixture, quoted "text"\nSecond line: café',
                       redistribution_allowed=False, retrieval_verified=True)
        self.write_pair([entry])
        self.assertEqual(schema.validate_pair(self.registry, self.csv_path), [entry])

    def test_csv_rejects_wrong_headers_and_field_counts(self):
        header = ','.join(schema.FIELDS)
        for text in ('source_id,status\n', header + ',unexpected\n',
                     header + '\nfixture-a\n',
                     header + '\n' + ','.join([''] * (len(schema.FIELDS) + 1)) + '\n'):
            with self.subTest(text=text[:50]):
                self.csv_path.write_text(text, encoding='utf-8')
                with self.assertRaises(schema.RegistryError):
                    schema.read_csv(self.csv_path)

    def test_csv_rejects_invalid_boolean_and_unclosed_quote(self):
        entry = source(retrieval_verified=True)
        text = schema.csv_text([entry]).replace(',true,', ',TRUE,')
        self.csv_path.write_text(text, encoding='utf-8')
        with self.assertRaises(schema.RegistryError):
            schema.read_csv(self.csv_path)
        entry = source(metadata_checked_at_utc='2026-09-08T00:00:00Z')
        text = schema.csv_text([entry]).rstrip('\n')
        text = text.rsplit(',', 1)[0] + ',"2026-09-08T00:00:00Z'
        self.csv_path.write_text(text, encoding='utf-8')
        with self.assertRaises(schema.RegistryError):
            schema.read_csv(self.csv_path)

    def test_parity_detects_a_stale_csv(self):
        self.registry.write_text(schema.yaml_text([source()]), encoding='utf-8')
        with self.assertRaises(schema.RegistryError):
            schema.validate_pair(self.registry, self.csv_path)
        before = self.pair_bytes()
        self.assert_failure(self.register(source('fixture-b')))
        self.assertEqual(self.pair_bytes(), before)

    def test_json_add_and_update_have_deterministic_field_and_row_order(self):
        entry = source('fixture-z', notes='Initial fixture note')
        self.assert_success(self.register(dict(reversed(list(entry.items())))))
        self.assert_success(self.register(source('fixture-a')))
        document = json.loads(self.registry.read_text(encoding='utf-8'))
        self.assertEqual([row['source_id'] for row in document['sources']], ['fixture-a', 'fixture-z'])
        for row in document['sources']:
            self.assertEqual(list(row), list(schema.FIELDS))
        self.assertEqual(list(csv.DictReader(io.StringIO(self.csv_path.read_text(encoding='utf-8'))))[0]['source_id'], 'fixture-a')
        replacement = source('fixture-z', notes=None, known_gaps='Replacement fixture text')
        self.assert_success(self.register(replacement, '--operation', 'update'))
        self.assertEqual(schema.validate_pair(self.registry, self.csv_path)[1], replacement)
        before = self.pair_bytes()
        self.assert_success(self.register(replacement, '--operation', 'update'))
        self.assertEqual(self.pair_bytes(), before)

    def test_flat_yaml_add_and_json_update_are_equivalent(self):
        entry = source('fixture-yaml', provider="Fixture's provider", notes='Fixture # text',
                       dataset_version='001.20', redistribution_allowed=False)
        lines = ['---', '# Synthetic software fixture']
        for field, value in reversed(list(entry.items())):
            if field == 'provider':
                encoded = "'Fixture''s provider'"
            elif field == 'dataset_version':
                encoded = '001.20'
            else:
                encoded = json.dumps(value, ensure_ascii=False)
            lines.append('{}: {} # field comment'.format(field, encoded))
        self.input = self.root / 'entry.yaml'
        self.input.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.assert_success(self.register())
        self.assertEqual(schema.validate_pair(self.registry, self.csv_path), [entry])
        self.input = self.root / 'entry.json'
        self.assert_success(self.register(dict(entry, notes='Replacement'), '--operation', 'update'))
        self.assertEqual(schema.validate_pair(self.registry, self.csv_path)[0]['notes'], 'Replacement')

    def test_dry_run_makes_no_registry_or_lock_changes(self):
        self.input.write_text(json.dumps(source()), encoding='utf-8')
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        self.assert_success(self.register(None, '--dry-run'))
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)

    def test_failed_add_update_and_partial_update_leave_pair_unchanged(self):
        self.assert_success(self.register(source(notes='Retain this fixture note')))
        before = self.pair_bytes()
        cases = [(source(), ()), (source('missing-fixture'), ('--operation', 'update')),
                 ({'source_id': 'fixture-a', 'notes': 'Partial replacement'}, ('--operation', 'update')),
                 (source(status='BLOCKED'), ('--operation', 'update'))]
        for entry, arguments in cases:
            with self.subTest(arguments=arguments, fields=list(entry)):
                self.assert_failure(self.register(entry, *arguments))
                self.assertEqual(self.pair_bytes(), before)

    def test_second_replace_failure_restores_pair(self):
        before = self.pair_bytes()
        original_replace = os.replace

        def fail_csv_replace(original, destination):
            if Path(destination) == self.csv_path:
                raise OSError('Synthetic CSV replace failure')
            return original_replace(original, destination)

        with mock.patch.object(register_source.os, 'replace', side_effect=fail_csv_replace):
            with self.assertRaises(OSError):
                register_source.update_pair(self.registry, self.csv_path, [source()])
        self.assertEqual(self.pair_bytes(), before)
        self.assertFalse(list(self.root.glob('.registry.*')))

    def test_duplicate_mapping_keys_and_unsafe_yaml_are_rejected(self):
        inputs = ['{"source_id":"fixture-a","source_id":"fixture-b"}',
                  'source_id: fixture-a\nsource_id: fixture-b\n',
                  'notes: !!python/object/apply:builtins.str [fixture]\n',
                  'notes: &fixture anchor\n', 'notes: *fixture\n',
                  'notes:\n  nested: fixture\n', 'notes: [fixture, fixture]\n',
                  '---\nsource_id: fixture-a\n---\nsource_id: fixture-b\n']
        for text in inputs:
            with self.subTest(text=text):
                self.input.write_text(text, encoding='utf-8')
                with self.assertRaises(schema.RegistryError):
                    schema.read_entry(self.input)

    def test_country_matrix_requires_registered_source_and_allowed_protocol(self):
        entry = source()
        row = dict.fromkeys(schema.MATRIX_FIELDS, '')
        row.update(country_or_region='Fixture region', source_id=entry['source_id'], status='PENDING_REVIEW')

        def write_rows(rows):
            with self.matrix.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=schema.MATRIX_FIELDS)
                writer.writeheader()
                writer.writerows(rows)

        for protocol in ('', *schema.MARKET_PROTOCOLS):
            write_rows([dict(row, market_protocol=protocol)])
            self.assertEqual(schema.validate_matrix(self.matrix, [entry]), 1)
        for invalid_rows in ([dict(row, source_id='missing-fixture')],
                             [dict(row, market_protocol='UNDECIDED_PROTOCOL')],
                             [dict(row, status='BLOCKED')], [row, row]):
            write_rows(invalid_rows)
            with self.assertRaises(schema.RegistryError):
                schema.validate_matrix(self.matrix, [entry])


if __name__ == '__main__':
    unittest.main()
