"""Offline raw-retrieval tests: synthetic bytes, mocked HTTP, disposable storage.

Official host names exercise the allowlist only; every opener is mocked and no
scientific observations or provider objects are downloaded by these tests.
"""
import contextlib
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'raw_tools'
sys.path.insert(0, str(TOOLS))
import controlled_download as raw


def workbook_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as book:
        book.writestr('[Content_Types].xml',
                      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                      '</Types>')
        book.writestr('xl/workbook.xml',
                      '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                      '<sheets><sheet name="Synthetic history" sheetId="1"/></sheets></workbook>')
        book.writestr('xl/worksheets/sheet1.xml',
                      '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>')
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, payload, url, **headers):
        super().__init__(payload)
        self.url = url
        self.status = 200
        self.headers = Message()
        self.headers['Content-Type'] = 'application/octet-stream'
        self.headers['Content-Length'] = str(len(payload))
        for name, value in headers.items():
            self.headers.replace_header(name, value) if name in self.headers else self.headers.__setitem__(name, value)

    def geturl(self):
        return self.url


class ControlledDownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='emind-raw-offline-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for path in ('tmp', 'raw/DE_CENT/load', 'raw/DE_CENT/market', 'raw/DE_CENT/fuel'):
            (self.root / path).mkdir(parents=True, exist_ok=True)
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        patches = contextlib.ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(mock.patch.object(raw, 'ROOT', self.root))
        patches.enter_context(mock.patch.object(raw, 'EXECUTION_HOST', 'execution.example.invalid'))
        patches.enter_context(mock.patch.object(raw.socket, 'getfqdn', return_value='execution.example.invalid'))
        self.opener_factory = patches.enter_context(mock.patch.object(raw.urllib.request, 'build_opener'))
        patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.config = {
            'snapshot_id': 'synthetic_fixture',
            'source_id': 'eu_ec_weekly_oil_bulletin_diesel',
            'scenario_id': 'DE_CENT',
            'provider': 'Synthetic fixture exercising approved host rules',
            'url': 'https://energy.ec.europa.eu/synthetic-fixture.xlsx',
            'original_filename': 'synthetic-fixture.xlsx',
            'format': 'xlsx',
        }

    def response(self, payload, **headers):
        reply = Response(payload, self.config['url'], **headers)
        self.opener_factory.return_value.open.return_value = reply
        return reply

    def test_workbook_freezes_exact_bytes_with_adjacent_provenance(self):
        payload = workbook_bytes()
        self.response(payload, **{'Content-Disposition': 'attachment; filename="provider-name.xlsx"'})
        result = raw.download(self.config)
        path = Path(result['raw_path'])
        sidecar = Path(str(path) + '.manifest.json')
        saved = json.loads(sidecar.read_text())
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(path.name, 'provider-name.xlsx')
        self.assertEqual(saved, result)
        self.assertEqual(saved['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(saved['size_bytes'], len(payload))
        self.assertEqual(saved['url'], self.config['url'])
        self.assertEqual(saved['source_id'], self.config['source_id'])
        self.assertTrue(saved['immutable'])
        self.assertFalse(saved['transformation_performed'])
        self.assertTrue(saved['retrieved_at_utc'])
        self.assertEqual(saved['format_audit']['sheets'][0]['name'], 'Synthetic history')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o400)
        self.assertEqual(list((self.root / 'tmp').iterdir()), [])

    def test_csv_retains_negative_missing_and_duplicate_labels_byte_for_byte(self):
        self.config.update(source_id='de_smard_day_ahead', url='https://www.smard.de/synthetic.csv',
                           original_filename='synthetic.csv', format='csv', semantic_freeze_status='VERIFIED')
        payload = ('\ufeffDate;Time;Price\r\nsynthetic day;02:00;-17,5\r\n'
                   'synthetic day;02:00;-\r\n').encode('utf-8')
        self.response(payload)
        result = raw.download(self.config)
        self.assertEqual(Path(result['raw_path']).read_bytes(), payload)
        self.assertEqual(result['format_audit']['row_count'], 2)
        self.assertEqual(result['format_audit']['first_row_verbatim_fields'], ['synthetic day', '02:00', '-17,5'])
        self.assertEqual(result['format_audit']['last_row_verbatim_fields'], ['synthetic day', '02:00', '-'])

    def test_html_is_failed_evidence_never_accepted_raw(self):
        payload = b'<!DOCTYPE html><html><body>provider error</body></html>'
        self.response(payload)
        with self.assertRaisesRegex(ValueError, 'HTML'):
            raw.download(self.config)
        directory = self.root / 'raw/DE_CENT/fuel/synthetic_fixture'
        self.assertEqual([p.name for p in directory.iterdir()], ['failure.manifest.json'])
        failure = json.loads((directory / 'failure.manifest.json').read_text())
        self.assertEqual(failure['status'], 'FAILED_NOT_ACCEPTED')
        self.assertEqual(Path(failure['partial_path']).read_bytes(), payload)

    def test_corrupt_xlsx_rejected_without_changing_input(self):
        path = self.root / 'invalid.xlsx'
        for payload in (b'not a workbook', workbook_bytes()[:-30]):
            with self.subTest(size=len(payload)):
                path.write_bytes(payload)
                with self.assertRaises(zipfile.BadZipFile):
                    raw.inspect_format(path, 'xlsx')
                self.assertEqual(path.read_bytes(), payload)

    def test_no_second_request_or_overwrite_for_existing_snapshot(self):
        self.response(workbook_bytes())
        first = raw.download(self.config)
        path = Path(first['raw_path'])
        before = path.read_bytes(), Path(str(path) + '.manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            raw.download(self.config)
        self.assertEqual(self.opener_factory.return_value.open.call_count, 1)
        self.assertEqual(before, (path.read_bytes(), Path(str(path) + '.manifest.json').read_bytes()))

    def test_existing_partial_is_not_overwritten(self):
        partial = self.root / 'tmp/synthetic_fixture.partial'
        partial.write_bytes(b'previous incomplete attempt')
        self.response(workbook_bytes())
        with self.assertRaises(FileExistsError):
            raw.download(self.config)
        self.assertEqual(partial.read_bytes(), b'previous incomplete attempt')
        self.assertFalse((self.root / 'raw/DE_CENT/fuel/synthetic_fixture/synthetic-fixture.xlsx').exists())

    def test_short_response_cannot_be_certified(self):
        self.response(workbook_bytes(), **{'Content-Length': '999999'})
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            raw.download(self.config)
        self.assertFalse((self.root / 'raw/DE_CENT/fuel/synthetic_fixture/synthetic-fixture.xlsx').exists())

    def test_scope_and_semantic_freeze_checked_before_network(self):
        for changes in ({'scenario_id': 'GB'}, {'source_id': 'unapproved'},
                        {'source_id': 'de_smard_electrical_load', 'url': 'https://www.smard.de/synthetic.csv'}):
            with self.subTest(changes=changes):
                config = dict(self.config, **changes)
                with self.assertRaises(ValueError):
                    raw.download(config)
        for field in ('ROOT', 'EXECUTION_HOST'):
            with mock.patch.object(raw, field, None), self.assertRaises(ValueError):
                raw.download(self.config)
        with mock.patch.object(raw.socket, 'getfqdn', return_value='other.example.invalid'), self.assertRaises(ValueError):
            raw.download(self.config)
        self.opener_factory.assert_not_called()

    def test_public_url_rejects_credentials_signed_urls_and_other_hosts(self):
        for url in ('https://name:synthetic@www.smard.de/file',
                    'HTTPS://name:synthetic@www.smard.de/file',
                    'https://www.smard.de/file?token=synthetic',
                    'HTTPS://www.smard.de/file?Signature=synthetic',
                    'https://www.smard.de/file#api_key=synthetic',
                    'https://example.invalid/file', 'http://www.smard.de/file'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    raw.public_url(url)

    def test_redirect_rejects_unapproved_or_secret_destination(self):
        handler = raw.PublicRedirect()
        request = raw.urllib.request.Request(self.config['url'])
        for url in ('https://example.invalid/file', 'https://www.smard.de/file?sig=synthetic'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    handler.redirect_request(request, None, 302, 'Found', {}, url)


if __name__ == '__main__':
    unittest.main()
