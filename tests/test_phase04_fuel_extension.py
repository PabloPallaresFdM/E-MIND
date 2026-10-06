"""Synthetic offline tests for range extension and fail-closed regression."""
import copy
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts/harmonise'))
from execute_fuel_extension import extract_window, accepted_summary, regression, serialized, SEMANTICS
from audit_fuel import candidate_rows
from emind.harmonise.fuel import NS
import test_phase03b_recovery as fixtures


class FuelExtensionTests(unittest.TestCase):
    def test_extended_range_boundaries_and_historical_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'synthetic.xlsx'
            fixtures.workbook(path)
            old, _ = extract_window(path, date(2019,1,1), date(2024,1,1))
            with zipfile.ZipFile(path) as book:
                members = {n: book.read(n) for n in book.namelist()}
            ns = '{'+NS['s']+'}'
            for name in ('xl/worksheets/sheet1.xml', 'xl/worksheets/sheet2.xml'):
                root = ET.fromstring(members[name]); data = root.find('s:sheetData', NS)
                for index, day in enumerate((date(2024,1,1),date(2025,12,29),date(2026,1,1)), 100):
                    row = ET.SubElement(data, ns+'row', r=str(index))
                    for col, value in (('A',str((day-date(1899,12,30)).days)),('BD','1000')):
                        ET.SubElement(ET.SubElement(row, ns+'c', r=col+str(index)),ns+'v').text=value
                members[name] = ET.tostring(root)
            with zipfile.ZipFile(path,'w') as book:
                for name, payload in members.items(): book.writestr(name,payload)
            full, _ = extract_window(path,date(2019,1,1),date(2026,1,1))
            self.assertEqual(len(full),len(old)+4)
            self.assertEqual({r['tax_basis'] for r in full},{'WITH_TAX','WITHOUT_TAX'})
            self.assertEqual({r['date'] for r in full if r['date']>='2024-01-01'}, {'2024-01-01','2025-12-29'})
            snapshot = dict(snapshot_id='synthetic',sha256='synthetic')
            historical = serialized(candidate_rows(old,snapshot))
            output = candidate_rows(full,snapshot)
            self.assertEqual(regression(output,historical)['maximum_numeric_difference'],'0')
            altered = copy.deepcopy(output); altered[0]['source_value'] += 1
            with self.assertRaises(ValueError): regression(altered,historical)
            altered = copy.deepcopy(output); altered[0]['tax_variant']='CHANGED'
            with self.assertRaises(ValueError): regression(altered,historical)
            self.assertEqual(SEMANTICS['tax_default'],'UNSELECTED')
            self.assertFalse(SEMANTICS['interpolation']); self.assertFalse(SEMANTICS['hourly_resampling'])
            self.assertTrue(all(r['available_at'] is None for r in output))

    def test_gap_preservation_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'synthetic.xlsx'; fixtures.workbook(path)
            rows,_=extract_window(path,date(2019,1,1),date(2026,1,1))
            summary=accepted_summary(rows)
            self.assertEqual(summary['WITH_TAX']['cadence_elapsed_days'],{14:1})
            self.assertEqual(summary['WITH_TAX']['gaps_gt_7_days'][0]['elapsed_days'],14)
            fixtures.workbook(path,duplicate=True)
            rows,_=extract_window(path,date(2019,1,1),date(2026,1,1))
            with self.assertRaises(ValueError): accepted_summary(rows)
            fixtures.workbook(path,missing=True)
            rows,_=extract_window(path,date(2019,1,1),date(2026,1,1))
            with self.assertRaises(ValueError): accepted_summary(rows)

    def test_empty_window_rejected(self):
        with self.assertRaises(ValueError): extract_window('unused',date(2026,1,1),date(2026,1,1))
