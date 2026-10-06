"""Strict footer and semantic safeguards; synthetic workbook bytes only."""
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts/harmonise'))
from emind.harmonise.fuel import extract_germany,NS
from audit_fuel import summarise,candidate_rows
import test_phase03b_recovery as fixtures


class FuelAuditTests(unittest.TestCase):
    def test_gaps_and_variants_without_invented_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'synthetic.xlsx';fixtures.workbook(path,missing=True)
            rows,_=extract_germany(path);summary,same=summarise(rows)
            self.assertTrue(same)
            self.assertEqual(summary['WITH_TAX']['gaps_gt_7_days'][0]['nominal_weekly_observations_apparently_absent'],1)
            output=candidate_rows(rows,{'snapshot_id':'synthetic','sha256':'synthetic'})
            self.assertEqual(len(output),4)
            self.assertEqual({r['tax_variant'] for r in output},{'WITH_TAX','WITHOUT_TAX'})
            self.assertTrue(all(r['currency']=='UNKNOWN' and r['available_at'] is None for r in output))
            self.assertTrue(all(r['source_unit']=='UNKNOWN_CURRENCY_PER_1000_L' for r in output))
            self.assertTrue(all(r['native_frequency']=='weekly' for r in output))
            self.assertEqual(summary['WITH_TAX']['nulls'],1)

    def test_duplicate_dates_are_audited_without_silent_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'synthetic.xlsx';fixtures.workbook(path,duplicate=True)
            rows,_=extract_germany(path);summary,_=summarise(rows)
            self.assertEqual(summary['WITH_TAX']['duplicate_dates'],{'2019-01-07':2})
            self.assertEqual(len(rows),4)

    def test_footer_with_value_and_observation_after_footer_fail(self):
        for mode in ('value_at_footer','observation_after_footer'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'synthetic.xlsx';fixtures.workbook(path)
                with zipfile.ZipFile(path) as book:members={n:book.read(n) for n in book.namelist()}
                root=ET.fromstring(members['xl/worksheets/sheet1.xml']);data=root.find('s:sheetData',NS)
                ns='{'+NS['s']+'}'
                row=ET.SubElement(data,ns+'row',r='1088')
                cell=ET.SubElement(row,ns+'c',r='A1088',t='inlineStr')
                ET.SubElement(ET.SubElement(cell,ns+'is'),ns+'t').text='Notes:'
                if mode=='value_at_footer':
                    ET.SubElement(ET.SubElement(row,ns+'c',r='BD1088'),ns+'v').text='1'
                else:
                    row=ET.SubElement(data,ns+'row',r='1089')
                    ET.SubElement(ET.SubElement(row,ns+'c',r='A1089'),ns+'v').text='43493'
                members['xl/worksheets/sheet1.xml']=ET.tostring(root)
                with zipfile.ZipFile(path,'w') as book:
                    for name,payload in members.items():book.writestr(name,payload)
                with self.assertRaises(ValueError):extract_germany(path)
