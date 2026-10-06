"""Explicit execution horizons and frozen-history append; synthetic inputs only."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts/harmonise'))
import test_phase03b_recovery as fixtures
from execute_de_extension import construct,append_history,validate_rows,read_history
from emind.providers.smard import MARKET_QUARTER_HOUR_HEADER

UTC=timezone.utc
HOUR=timedelta(hours=1)
QUARTER=timedelta(minutes=15)


class Phase04ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.RecoveryTests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.start=datetime(2024,1,1,tzinfo=UTC)
        self.transition=self.start+HOUR;self.end=self.start+2*HOUR

    def inputs(self):
        def local(t):return t.astimezone(ZoneInfo('Europe/Berlin')).replace(tzinfo=None)
        load=self.fixture.csv([local(self.start+i*QUARTER) for i in range(8)],'load',name='load')
        hourly=self.fixture.csv([local(self.start)],name='m1')
        quarters=[local(self.transition+i*QUARTER) for i in range(4)]
        quarter=self.fixture.csv(quarters,ends=[t+QUARTER for t in quarters],
            header=MARKET_QUARTER_HOUR_HEADER,values=['-20','-10','10','20'],name='m2')
        return [load],[dict(interval_minutes=60,objects=[hourly]),dict(interval_minutes=15,objects=[quarter])]

    def build(self,**options):
        load,market=self.inputs()
        return construct(load,market,start=options.get('start',self.start),
            end=options.get('end',self.end),transition=options.get('transition',self.transition))

    def test_explicit_arbitrary_horizon_and_exact_energy(self):
        load,market,audit=self.build()
        self.assertEqual((len(load),len(market)),(2,2))
        self.assertEqual(audit['energy_difference_mwh'],Decimal(0))
        self.assertEqual(audit['native_energy_mwh'],Decimal('9873.000'))
        self.assertNotIn('load_power_mw',load[0])
        self.assertEqual(audit['m2_max_aggregation_error'],Decimal(0))
        validate_rows(load,'load',start=self.start,end=self.end)

    def test_mixed_lineage_quality_and_native_frequencies(self):
        load,market,audit=self.build()
        self.assertEqual([r['contributing_row_count'] for r in market],[1,4])
        self.assertEqual([r['native_interval_minutes'] for r in market],[60,15])
        self.assertEqual([r['quality_flag'] for r in market],['ORIGINAL','AGGREGATED_NATIVE'])
        self.assertEqual([r['source_native_frequency'] for r in market],['60 minutes','15 minutes'])
        refs=json.loads(market[1]['native_row_refs'])
        self.assertEqual([Decimal(a['native_value']) for a in refs],list(map(Decimal,['-20','-10','10','20'])))
        self.assertEqual(len(json.loads(load[0]['native_row_refs'])),4)

    def test_wrong_horizon_or_transition_fails(self):
        for options in [dict(start=self.start-HOUR),dict(end=self.end+HOUR),dict(transition=self.transition+HOUR)]:
            with self.subTest(options=options),self.assertRaises(ValueError):self.build(**options)

    def test_wrong_resolution_segment_order_fails(self):
        load,market=self.inputs()
        with self.assertRaises(ValueError):
            construct(load,list(reversed(market)),start=self.start,end=self.end,transition=self.transition)

    def test_historical_rows_preserved_with_boundary_adjacency(self):
        load,market,_=self.build()
        for kind,extension in [('load',load),('market',market)]:
            historical=dict(extension[0],timestamp_utc=self.start-HOUR)
            for key in ('native_interval_minutes','contributing_row_count','canonical_transformation','provenance_segment'):
                historical.pop(key)
            if kind=='market':
                historical.pop('source_native_frequency');historical.pop('native_row_refs')
            if kind=='load':historical['load_power_mw']=historical['load_energy_mwh']
            before=dict(historical)
            full=append_history([historical],extension,kind,start=self.start-HOUR,end=self.end)
            for key,value in before.items():
                if key != 'load_power_mw':
                    self.assertEqual(full[0][key],value)
            self.assertEqual(historical,before)
            self.assertEqual(full[0]['timestamp_utc']+HOUR,full[1]['timestamp_utc'])

    def test_history_boundary_gap_and_overlap_rejected(self):
        load,_,_=self.build()
        for offset in (-2,0):
            history=[dict(load[0],timestamp_utc=self.start+offset*HOUR)]
            with self.subTest(offset=offset),self.assertRaises(ValueError):
                append_history(history,load,'load',start=self.start+offset*HOUR,end=self.end)

    def test_full_43824_plus_17544_horizon(self):
        load,_,_=self.build();template=load[0]
        start=datetime(2019,1,1,tzinfo=UTC);end=datetime(2026,1,1,tzinfo=UTC)
        history=[dict(template,timestamp_utc=start+i*HOUR) for i in range(43824)]
        for row in history:
            for key in ('native_interval_minutes','contributing_row_count','canonical_transformation','provenance_segment'):
                row.pop(key)
        extension=[dict(template,timestamp_utc=self.start+i*HOUR) for i in range(17544)]
        full=append_history(history,extension,'load',start=start,end=end)
        self.assertEqual(len(full),61368)
        self.assertEqual(full[43823]['timestamp_utc'],datetime(2023,12,31,23,tzinfo=UTC))
        self.assertEqual(full[43824]['timestamp_utc'],self.start)
        self.assertEqual(full[-1]['timestamp_utc'],end-HOUR)

    def test_missing_and_nonfinite_values_rejected(self):
        load,_,_=self.build()
        for value in (Decimal('NaN'),Decimal('-1')):
            changed=[dict(r) for r in load];changed[0]['load_energy_mwh']=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_rows(changed,'load',start=self.start,end=self.end)


if __name__=='__main__':unittest.main()
