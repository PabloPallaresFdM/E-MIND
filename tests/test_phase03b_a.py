"""Only Phase03B-A orchestration: exact crop, row lineage and no extra phase calls."""
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts/harmonise'))
import test_phase03b_recovery as fixtures
from execute_de_load_market import construct
from emind.providers.smard import read_native
from emind.harmonise.coverage import TARGET_START
import json


class Phase03BATests(unittest.TestCase):
    def test_exact_crop_preserves_four_quarter_and_market_lineage(self):
        fixture=fixtures.RecoveryTests();fixture.setUp()
        try:
            l=fixture.csv([datetime(2019,1,1,h,m) for h in (0,1) for m in (0,15,30,45)],'load')
            m=fixture.csv([datetime(2019,1,1,h) for h in (0,1)],'market')
            parsed={'load':read_native([l],'load'),'market':read_native([m],'market')}
            load,market,check=construct(parsed,TARGET_START,TARGET_START+timedelta(hours=1))
            self.assertEqual(len(load),1)
            self.assertEqual(load[0]['load_energy_mwh'],Decimal('4936.500'))
            refs=json.loads(load[0]['native_row_refs'])
            self.assertEqual([r['source_row_index'] for r in refs],[5,6,7,8])
            self.assertEqual(market[0]['source_row_index'],2)
            self.assertEqual(market[0]['day_ahead_price_eur_mwh'],Decimal('-17.50'))
            self.assertEqual(load[0]['timestamp_utc'],market[0]['timestamp_utc'])
        finally:
            fixture.doCleanups()
