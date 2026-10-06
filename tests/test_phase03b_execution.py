"""Synthetic checks for real-execution audit and lossless output machinery."""
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts/harmonise'))
from emind.harmonise.information_loss import information_loss, quantile
from emind.providers.integrity import digest
from audit_de_raw import integrity, schema_audit
from execute_de_candidate import save_csv
import test_phase03b_recovery as fixtures


class ExecutionTests(unittest.TestCase):
    def test_energy_power_peak_variance_and_ramp_units(self):
        result=information_loss([Decimal(v) for v in (1,2,3,4,1,2,3,4)], [Decimal(10),Decimal(10)])
        self.assertEqual(result['native_15min']['max_mw'],16)
        self.assertEqual(result['native_15min']['variance_mw2_population'],20)
        self.assertEqual(result['hourly']['variance_mw2_population'],0)
        self.assertEqual(result['peak_reduction_mw'],6)
        self.assertEqual(result['peak_reduction_fraction'],Decimal('.375'))
        self.assertEqual(result['native_ramps']['absolute_delta_mw']['max'],12)
        self.assertEqual(result['native_ramps']['absolute_rate_mw_per_hour']['max'],48)
        self.assertEqual(result['hourly_ramps']['absolute_rate_mw_per_hour']['max'],0)

    def test_quantile_definition_and_bad_lengths(self):
        self.assertEqual(quantile([Decimal(0),Decimal(100)], '.95'),95)
        with self.assertRaises(ValueError):
            information_loss([Decimal(1)],[Decimal(1)])

    def test_lossless_csv_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.csv'
            rows=[{'timestamp':datetime(2019,1,1,tzinfo=timezone.utc),
                   'value':Decimal('1.0000000000000000001'),'missing':None,'selected':True}]
            result=save_csv(path,rows)
            self.assertIn('1.0000000000000000001',path.read_text())
            self.assertEqual(result['sha256'],digest(path))
            with self.assertRaises(FileExistsError):
                save_csv(path,rows)

    def test_integrity_compares_sidecar_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.csv';path.write_text('synthetic bytes')
            obj={'raw_path':str(path),'sha256':digest(path),'size_bytes':path.stat().st_size,
                 'snapshot_id':'synthetic','source_id':'synthetic','immutable':True}
            sidecar=Path(str(path)+'.manifest.json');sidecar.write_text(json.dumps(obj))
            path.chmod(0o400);sidecar.chmod(0o400)
            self.assertEqual(integrity([obj])['status'],'PASS')
            with self.assertRaisesRegex(ValueError,'RAW_INTEGRITY_FAILURE'):
                integrity([dict(obj,sha256='0'*64)])
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'RAW_INTEGRITY_FAILURE'):
                integrity([obj])

    def test_schema_audit_validates_nominal_transition_before_adapter_option(self):
        fixture=fixtures.RecoveryTests();fixture.setUp()
        try:
            obj=fixture.csv([datetime(2023,3,26,h) for h in (1,3,4)])
            result=schema_audit([obj],'market')
            self.assertEqual(result['endpoint_rules']['NOMINAL_CIVIL_END_AT_DST'],1)
            self.assertEqual(result['missing_spring_civil_labels'],1)
            self.assertTrue(result['utc_unique'])
        finally:
            fixture.doCleanups()

    def test_schema_audit_rejects_unexplained_end_label(self):
        fixture=fixtures.RecoveryTests();fixture.setUp()
        try:
            obj=fixture.csv([datetime(2019,1,1)],ends=[datetime(2019,1,1,2)])
            with self.assertRaisesRegex(ValueError,'Unsupported end label'):
                schema_audit([obj],'market')
        finally:
            fixture.doCleanups()


if __name__=='__main__':
    unittest.main()
