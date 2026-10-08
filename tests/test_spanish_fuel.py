"""Synthetic Spain extraction and unchanged Germany semantics."""
from pathlib import Path
import sys,tempfile,unittest,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from emind.harmonise.fuel import extract_country,extract_germany
import test_phase03b_recovery as fixtures

class SpanishFuelTests(unittest.TestCase):
 def test_spain_both_tax_variants_and_native_dates(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'synthetic.xlsx';fixtures.workbook(p)
   with zipfile.ZipFile(p) as z: members={n:z.read(n) for n in z.namelist()}
   for name,b in list(members.items()):
    if name.startswith('xl/worksheets/'):
     members[name]=b.replace(b'BD',b'CA').replace(b'DE_price_',b'ES_price_')
   with zipfile.ZipFile(p,'w') as z:
    for name,b in members.items():z.writestr(name,b)
   rows,evidence=extract_country(p,country_code='ES')
   self.assertEqual({r['tax_basis'] for r in rows},{'WITH_TAX','WITHOUT_TAX'})
   self.assertEqual({r['date'] for r in rows},{'2019-01-07','2019-01-21'})
   self.assertEqual(len(rows),4)
   self.assertTrue(all(r['value_cell'].startswith('CA') and r['source_field'].startswith('ES_') and r['source_native_frequency']=='weekly' for r in rows))
   self.assertTrue(all(r['native_currency']=='UNKNOWN_IN_WORKBOOK' for r in rows))
 def test_germany_wrapper_is_identical(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'synthetic.xlsx';fixtures.workbook(p)
   self.assertEqual(extract_germany(p),extract_country(p,country_code='DE'))
 def test_unreviewed_country_and_wrong_spain_header_fail(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'synthetic.xlsx';fixtures.workbook(p)
   for code in ['ZZ','ES']:
    with self.assertRaises(ValueError):extract_country(p,country_code=code)
