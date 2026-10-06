"""Provider/time/serialization regression tests; CSV/XLSX bytes are synthetic."""
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from emind.harmonise.coverage import (CoverageError, TARGET_START, TARGET_END,
                                     crop_to_target, validate_target)
from emind.harmonise.fuel import extract_germany, excel_date, VARIANTS, NS, REL
from emind.harmonise.smard import aggregate_quarter_hour_load, canonicalise_market_hours, CanonicalMarketHour
from emind.providers.integrity import digest, verify_raw
from emind.providers.smard import HEADERS, SOURCE_IDS, read_native, parse_label, number
from emind.time.dst import AmbiguousSequenceError, resolve_local_interval_starts

spec = importlib.util.spec_from_file_location('de_pilot', REPO / 'scripts/harmonise/run_de_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
UTC = timezone.utc
HOUR = timedelta(hours=1)


def label(day):
    # Independent English fixture encoder, including midnight/noon.
    months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
    return '{} {}, {} {}:{:02d} {}'.format(months[day.month - 1], day.day, day.year,
                                          day.hour % 12 or 12, day.minute,
                                          'PM' if day.hour >= 12 else 'AM')


def workbook(path, *, wrong_header=False, value_type=None, formula=False,
             missing=False, duplicate=False, shared=False):
    ns = NS['s']
    def tag(name):
        return '{' + ns + '}' + name
    wb = ET.Element(tag('workbook'))
    ET.SubElement(wb, tag('workbookPr'), date1904='0')
    sheets = ET.SubElement(wb, tag('sheets'))
    rels = ET.Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
    strings = []
    with zipfile.ZipFile(path, 'w') as book:
        for n, (name, basis, field) in enumerate(VARIANTS, 1):
            ET.SubElement(sheets, tag('sheet'), {'name': name, 'sheetId': str(n), REL: 'r' + str(n)})
            ET.SubElement(rels, 'Relationship', Id='r' + str(n), Target='worksheets/sheet{}.xml'.format(n))
            sheet = ET.Element(tag('worksheet'))
            data = ET.SubElement(sheet, tag('sheetData'))
            for index, values in [(1, {'BD': 'wrong' if wrong_header else field}),
                                  (3, {'A': 'Date', 'BD': '1000 l'}),
                                  (4, {'A': '43472', 'BD': '1200.125' if n == 1 else '700.5'}),
                                  (5, {'A': '43472' if duplicate else '43486', 'BD': None if missing else '999'})]:
                row = ET.SubElement(data, tag('row'), r=str(index))
                for column, value in values.items():
                    if value is None:
                        continue
                    cell = ET.SubElement(row, tag('c'), r=column + str(index))
                    if index <= 3:
                        if shared:
                            cell.set('t', 's')
                            strings.append(value)
                            ET.SubElement(cell, tag('v')).text = str(len(strings) - 1)
                        else:
                            cell.set('t', 'inlineStr')
                            ET.SubElement(ET.SubElement(cell, tag('is')), tag('t')).text = value
                    else:
                        if column == 'BD' and value_type:
                            cell.set('t', value_type)
                        if column == 'BD' and formula:
                            ET.SubElement(cell, tag('f')).text = '1+1'
                        ET.SubElement(cell, tag('v')).text = value
            book.writestr('xl/worksheets/sheet{}.xml'.format(n), ET.tostring(sheet))
        book.writestr('xl/workbook.xml', ET.tostring(wb))
        book.writestr('xl/_rels/workbook.xml.rels', ET.tostring(rels))
        if shared:
            sst = ET.Element(tag('sst'))
            for text in strings:
                ET.SubElement(ET.SubElement(sst, tag('si')), tag('t')).text = text
            book.writestr('xl/sharedStrings.xml', ET.tostring(sst))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='emind-recovery-synthetic-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def obj(self, path, source_id):
        return {'raw_path': str(path), 'sha256': digest(path), 'size_bytes': path.stat().st_size,
                'snapshot_id': path.stem, 'source_id': source_id}

    def csv(self, starts, kind='market', *, ends=None, values=None, header=None, name=None):
        step = timedelta(minutes=15 if kind == 'load' else 60)
        ends = ends or [s + step for s in starts]
        values = values or ['-17.50' if kind == 'market' else '1,234.125'] * len(starts)
        path = self.root / ((name or kind) + '.csv')
        lines = ['Start date;End date;' + (header or HEADERS[kind])]
        lines += [';'.join((label(s), label(e), v)) for s, e, v in zip(starts, ends, values)]
        path.write_text('\ufeff' + '\r\n'.join(lines) + '\r\n', encoding='utf-8')
        return self.obj(path, SOURCE_IDS[kind])

    def test_csv_to_remote_core_retains_units_values_and_traceability(self):
        obj = self.csv([datetime(2019, 1, 1, 1, m) for m in (0, 15, 30, 45)], 'load')
        records, audit = read_native([obj], 'load')
        hour = aggregate_quarter_hour_load(records)[0]
        self.assertEqual(hour.load_energy_mwh, Decimal('4936.500'))
        self.assertEqual(hour.load_power_mw, Decimal('4936.500'))
        self.assertEqual(hour.timestamp_utc, TARGET_START)
        self.assertEqual(audit[0]['value_raw'], '1,234.125')
        self.assertEqual(audit[0]['raw_sha256'], obj['sha256'])
        self.assertEqual(audit[-1]['source_row_index'], 4)
        self.assertEqual(audit[-1]['snapshot_id'], 'load')
        self.assertEqual(digest(obj['raw_path']), obj['sha256'])

    def test_spring_missing_civil_hour_is_not_a_physical_gap(self):
        starts = [datetime(2023, 3, 26, h) for h in (1, 3, 4)]
        obj = self.csv(starts, ends=[starts[1], starts[2], datetime(2023, 3, 26, 5)])
        records, audit = read_native([obj], 'market')
        self.assertEqual([r['timestamp_utc'].hour for r in audit], [0, 1, 2])
        self.assertEqual(canonicalise_market_hours(records)[0].day_ahead_price_eur_mwh, Decimal('-17.50'))

    def test_autumn_repeated_labels_remain_distinct(self):
        starts = [datetime(2023, 10, 29, h) for h in (1, 2, 2, 3)]
        ends = [datetime(2023, 10, 29, h) for h in (2, 2, 3, 4)]
        records, audit = read_native([self.csv(starts, ends=ends)], 'market')
        self.assertEqual(len(set(r['timestamp_utc'] for r in audit)), 4)
        self.assertEqual([r['fold'] for r in audit], [0, 0, 1, 0])
        self.assertEqual(len(canonicalise_market_hours(records)), 4)

    def test_nominal_dst_end_is_opt_in_and_audited(self):
        obj = self.csv([datetime(2023, 3, 26, h) for h in (1, 3)])
        with self.assertRaisesRegex(ValueError, 'end label'):
            read_native([obj], 'market')
        _, audit = read_native([obj], 'market', allow_nominal_dst_ends=True)
        self.assertEqual(audit[0]['endpoint_rule'], 'NOMINAL_CIVIL_END_AT_DST')

    def test_wrong_ordinary_end_fails_even_with_nominal_option(self):
        obj = self.csv([datetime(2019, 1, 1)], ends=[datetime(2019, 1, 1, 2)])
        with self.assertRaisesRegex(ValueError, 'end label'):
            read_native([obj], 'market', allow_nominal_dst_ends=True)

    def test_ambiguous_first_label_keeps_remote_fail_closed_contract(self):
        # Old solver could use the later 03:00 to choose fold=1 for 02:00.
        # Remote API deliberately requires an unambiguous anchor; no guessing.
        for labels in ([datetime(2023, 10, 29, 2)],
                       [datetime(2023, 10, 29, h) for h in (2, 3)]):
            with self.assertRaisesRegex(AmbiguousSequenceError, 'first timestamp'):
                resolve_local_interval_starts(labels, timezone_name='Europe/Berlin', interval=HOUR)

    def test_nonexistent_start_rejected(self):
        with self.assertRaises(AmbiguousSequenceError):
            read_native([self.csv([datetime(2023, 3, 26, 2)])], 'market')

    def test_unsupported_header_rejected(self):
        with self.assertRaisesRegex(ValueError, 'schema'):
            read_native([self.csv([datetime(2019, 1, 1)], header='Price')], 'market')

    def test_bad_width_rejected(self):
        obj = self.csv([datetime(2019, 1, 1)])
        path = Path(obj['raw_path'])
        path.write_text(path.read_text() + 'too;many;columns;here\n')
        with self.assertRaisesRegex(ValueError, 'row width'):
            read_native([self.obj(path, SOURCE_IDS['market'])], 'market')

    def test_number_and_label_grammar_fail_explicitly(self):
        for value in ('-', '', '1.234,5', '12,34', 'NaN', 'Infinity', '1e3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value)
        self.assertEqual(number('-1,234.50'), Decimal('-1234.50'))
        for value in ('2019-01-01 00:00', 'Jan 1, 2019 0:00 AM', 'Jan 1, 2019 12:60 PM'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_label(value)
        self.assertEqual(parse_label('Jan 1, 2019 12:00 AM').hour, 0)
        self.assertEqual(parse_label('Jan 1, 2019 12:00 PM').hour, 12)

    def test_snapshot_integrity_and_source_identity_fail_closed(self):
        obj = self.csv([datetime(2019, 1, 1)])
        with self.assertRaisesRegex(ValueError, 'integrity'):
            verify_raw([dict(obj, sha256='0' * 64)])
        with self.assertRaisesRegex(ValueError, 'identity'):
            read_native([dict(obj, source_id='wrong')], 'market')
        link = self.root / 'link.csv'
        link.symlink_to(obj['raw_path'])
        with self.assertRaisesRegex(ValueError, 'integrity'):
            verify_raw([dict(obj, raw_path=str(link))])

    def test_snapshot_order_and_duplicate_ids_are_not_silently_repaired(self):
        first = self.csv([datetime(2019, 1, 1)], name='first')
        second = self.csv([datetime(2019, 1, 1, 1)], name='second')
        _, audit = read_native([first, second], 'market')
        self.assertEqual([a['source_row_index'] for a in audit], [1, 1])
        self.assertEqual([a['global_row_index'] for a in audit], [1, 2])
        with self.assertRaises(AmbiguousSequenceError):
            read_native([second, first], 'market')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            read_native([first, first], 'market')

    def test_43824_rows_do_not_certify_shifted_coverage(self):
        correct = [TARGET_START + i * HOUR for i in range(43824)]
        validate_target(correct)
        with self.assertRaisesRegex(CoverageError, 'boundaries'):
            validate_target([t - HOUR for t in correct])

    def test_target_rejects_gap_duplicate_naive_and_incomplete_tail(self):
        for values in ([TARGET_START, TARGET_START],
                       [TARGET_START, TARGET_START + 2 * HOUR],
                       [TARGET_START.replace(tzinfo=None)], [TARGET_START]):
            with self.subTest(values=values), self.assertRaises(CoverageError):
                validate_target(values, TARGET_START, TARGET_START + 2 * HOUR)

    def test_crop_requires_complete_target_not_full_raw_row_count(self):
        rows = [CanonicalMarketHour(TARGET_START + i * HOUR, 60, Decimal('-1')) for i in range(-1, 3)]
        result = crop_to_target(rows, TARGET_START, TARGET_START + 2 * HOUR)
        self.assertEqual([r.timestamp_utc for r in result], [TARGET_START, TARGET_START + HOUR])
        with self.assertRaises(CoverageError):
            crop_to_target(rows[:2], TARGET_START, TARGET_START + 2 * HOUR)

    def test_fuel_variants_stay_weekly_missing_and_gaps_are_not_filled(self):
        path = self.root / 'fuel.xlsx'
        workbook(path, missing=True)
        before = path.read_bytes()
        rows, audit = extract_germany(path)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r['tax_basis'] for r in rows}, {'WITH_TAX', 'WITHOUT_TAX'})
        self.assertEqual(rows[0]['diesel_price_native'], Decimal('1200.125'))
        self.assertEqual(rows[2]['diesel_price_native'], Decimal('700.5'))
        self.assertIsNone(rows[1]['diesel_price_native'])
        self.assertEqual(rows[1]['quality_flag'], 'MISSING')
        self.assertTrue(all(r['source_native_frequency'] == 'weekly' for r in rows))
        self.assertTrue(all('timestamp_utc' not in r for r in rows))
        self.assertEqual(audit['WITH_TAX']['nonseven_day_gaps'][0]['days'], 14)
        self.assertIsNone(audit['WITH_TAX']['currency'])
        self.assertEqual(path.read_bytes(), before)

    def test_fuel_shared_strings_and_duplicate_dates_remain_auditable(self):
        path = self.root / 'fuel.xlsx'
        workbook(path, shared=True, duplicate=True)
        rows, audit = extract_germany(path)
        self.assertEqual(len(rows), 4)
        self.assertEqual(audit['WITH_TAX']['duplicate_dates'], {'2019-01-07': 2})

    def test_fuel_unsupported_schema_or_cached_formula_fails(self):
        path = self.root / 'fuel.xlsx'
        for args in ({'wrong_header': True}, {'value_type': 'e'},
                     {'value_type': 'b'}, {'formula': True}):
            with self.subTest(args=args):
                workbook(path, **args)
                with self.assertRaises(ValueError):
                    extract_germany(path)

    def test_exact_notes_footer_is_not_a_missing_observation(self):
        path = self.root / 'fuel.xlsx'
        workbook(path)
        with zipfile.ZipFile(path) as book:
            members = {n: book.read(n) for n in book.namelist()}
        for name in ('xl/worksheets/sheet1.xml', 'xl/worksheets/sheet2.xml'):
            root = ET.fromstring(members[name])
            data = root.find('s:sheetData', NS)
            ns = '{' + NS['s'] + '}'
            row = ET.SubElement(data, ns + 'row', r='1088')
            cell = ET.SubElement(row, ns + 'c', r='A1088', t='inlineStr')
            ET.SubElement(ET.SubElement(cell, ns + 'is'), ns + 't').text = 'Notes:'
            members[name] = ET.tostring(root)
        with zipfile.ZipFile(path, 'w') as book:
            for name, payload in members.items():
                book.writestr(name, payload)
        rows, audit = extract_germany(path)
        self.assertEqual(len(rows), 4)
        self.assertEqual(audit['WITH_TAX']['notes_footer_row'], 1088)
        # An arbitrary text date still fails; the exception is exact.
        with zipfile.ZipFile(path, 'w') as book:
            for name, payload in members.items():
                book.writestr(name, payload.replace(b'Notes:', b'Unreviewed footer'))
        with self.assertRaisesRegex(ValueError, 'date representation'):
            extract_germany(path)

    def test_excel_epochs_and_invalid_dates(self):
        self.assertEqual(excel_date('0', True), date(1904, 1, 1))
        self.assertEqual(excel_date('1'), date(1900, 1, 1))
        self.assertEqual(excel_date('61'), date(1900, 3, 1))
        for value in ('60', '43472.5', 'NaN', 'Infinity'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                excel_date(value)

    def test_pilot_build_uses_remote_core_then_crops_and_checks_conservation(self):
        load_obj = self.csv([datetime(2019, 1, 1, h, m) for h in (0, 1) for m in (0, 15, 30, 45)], 'load')
        market_obj = self.csv([datetime(2019, 1, 1, h) for h in (0, 1)])
        load, la = read_native([load_obj], 'load')
        market, ma = read_native([market_obj], 'market')
        result, prices, audit = pilot.build_target(load, market, la, ma, TARGET_START, TARGET_START + HOUR)
        self.assertEqual(len(result), 1)
        self.assertEqual(audit['native_energy_mwh'], Decimal('4936.500'))
        self.assertEqual(audit['negative_market_values'], 1)
        self.assertEqual(prices[0].timestamp_utc, TARGET_START)
        with self.assertRaisesRegex(ValueError, 'lengths'):
            pilot.build_target(load, market, la[:-1], ma, TARGET_START, TARGET_START + HOUR)

    def test_cli_raw_audit_cannot_be_mistaken_for_canonical_success(self):
        load = self.csv([datetime(2019, 1, 1, 0, m) for m in (0, 15, 30, 45)], 'load')
        market = self.csv([datetime(2019, 1, 1)])
        fuel = self.root / 'fuel.xlsx'
        workbook(fuel)
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'objects': [load, market, self.obj(fuel, pilot.FUEL_SOURCE)]}))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        summary, code = pilot.run(manifest, 'raw-audit')
        self.assertEqual(code, 0)
        self.assertEqual(summary['git_head'], pilot.source_commit())
        self.assertEqual(summary['canonical']['status'], 'NOT_CHECKED')
        self.assertEqual(summary['raw_coverage']['market']['first_utc'], TARGET_START - HOUR)
        result = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/harmonise/run_de_pilot.py'),
                                 '--manifest', str(manifest), '--mode', 'canonical-check'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'CANONICAL_COVERAGE_FAILED')
        self.assertFalse(json.loads(result.stdout)['scientific_outputs_written'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})


if __name__ == '__main__':
    unittest.main()
