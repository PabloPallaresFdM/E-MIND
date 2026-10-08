"""Synthetic-only Spanish interval contracts; no provider payload fixtures."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from emind.providers.spanish import day_bounds, parse_redata, parse_omie, hourly_market
from emind.harmonise.coverage import validate_target, CoverageError


def load_body(day,values=None):
    start,end=day_bounds(day); count=(end-start)//timedelta(hours=1)
    entries=[dict(datetime=(start+i*timedelta(hours=1)).astimezone(ZoneInfo('Europe/Madrid')).isoformat(),value=(values[i] if values else '1234.567890'),percentage=1) for i in range(count)]
    return json.dumps(dict(data=dict(id='dem1'),included=[dict(id='10297',attributes=dict(title='Demanda',magnitude=None,values=entries))])).encode()


def market_body(day,prices=None,count=None):
    start,end=day_bounds(day); minutes=15 if day>=date(2025,10,1) else 60
    n=(end-start)//timedelta(minutes=minutes) if count is None else count
    rows=['MARGINALPDBC;']+[f'{day.year};{day.month};{day.day};{i+1};999;{prices[i] if prices else "-1.25"};' for i in range(n)]+['*']
    return ('\r\n'.join(rows)+'\r\n').encode()


class SpanishSignalsTests(unittest.TestCase):
    def test_load_dst_and_mwh_preservation(self):
        for day,count in [(date(2019,3,31),23),(date(2019,10,27),25),(date(2025,1,1),24)]:
            with self.subTest(day=day):
                rows,_=parse_redata(load_body(day))
                self.assertEqual(len(rows),count)
                self.assertTrue(all(r['value']==Decimal('1234.567890') for r in rows))
                start,end=day_bounds(day); validate_target([r['timestamp_utc'] for r in rows],start,end)

    def test_load_numeric_json_precision_preserved(self):
        body=load_body(date(2025,1,1)).replace(b'"1234.567890"',b'1234.5678901234567890123456789')
        rows,_=parse_redata(body)
        self.assertEqual(rows[0]['value'],Decimal('1234.5678901234567890123456789'))

    def test_load_rejects_missing_duplicate_and_offset(self):
        for mode in ['missing','duplicate','offset','null','bool','nonfinite','series','magnitude']:
            j=json.loads(load_body(date(2019,10,27))); attrs=j['included'][0]['attributes']; entries=attrs['values']
            if mode=='missing': entries.pop(4)
            elif mode=='duplicate': entries[4]=entries[3]
            elif mode=='offset': entries[0]['datetime']='2019-10-27T00:00:00+01:00'
            elif mode=='null': entries[0]['value']=None
            elif mode=='bool': entries[0]['value']=True
            elif mode=='nonfinite': entries[0]['value']='NaN'
            elif mode=='series': j['included'][0]['id']='1293'
            elif mode=='magnitude': attrs['magnitude']='MW'
            with self.subTest(mode=mode),self.assertRaises(ValueError): parse_redata(json.dumps(j).encode())

    def test_market_transition_and_spain_field(self):
        for day,count,minutes in [(date(2025,9,30),24,60),(date(2025,10,1),96,15)]:
            rows=parse_omie(market_body(day),day)
            self.assertEqual(len(rows),count); self.assertEqual(rows[0]['interval_minutes'],minutes)
            self.assertTrue(all(r['value']==Decimal('-1.25') for r in rows))
        first=parse_omie(market_body(date(2025,10,1)),date(2025,10,1))[0]
        self.assertEqual(first['timestamp_utc'],datetime(2025,9,30,22,tzinfo=timezone.utc))

    def test_market_exact_quarter_mean_and_negatives(self):
        day=date(2025,10,1); rows=parse_omie(market_body(day,prices=['-10.25','0','10.25','-20.50']*24),day)
        hours=hourly_market(rows); self.assertEqual(len(hours),24)
        self.assertEqual(hours[0]['value'],Decimal('-5.125'))
        self.assertEqual(hours[0]['native_periods'],[1,2,3,4])

    def test_quarter_mean_keeps_mixed_decimal_scales(self):
        day=date(2025,10,1)
        rows=parse_omie(market_body(day,prices=['1e60','0.1','0','0']*24),day)
        expected=Decimal('250000000000000000000000000000000000000000000000000000000000.025')
        self.assertEqual(hourly_market(rows)[0]['value'],expected)

    def test_market_dst_23_25_92_100(self):
        for day,n,hours in [(date(2019,3,31),23,23),(date(2019,10,27),25,25),(date(2025,10,26),100,25),(date(2026,3,29),92,23)]:
            with self.subTest(day=day):
                rows=parse_omie(market_body(day),day); self.assertEqual(len(rows),n)
                canonical=hourly_market(rows); self.assertEqual(len(canonical),hours)
                start,end=day_bounds(day); validate_target([r['timestamp_utc'] for r in canonical],start,end)

    def test_market_invalid_product_count_period_country(self):
        day=date(2025,10,1); body=market_body(day)
        for bad in [body.replace(b'MARGINALPDBC',b'INTRADAY'),market_body(day,count=95),body.replace(b';1;999;',b';2;999;',1),body.replace(b';999;',b';bad;',1),body.replace(b'*',b'')]:
            with self.assertRaises(ValueError): parse_omie(bad,day)

    def test_market_rejects_partial_hour(self):
        rows=parse_omie(market_body(date(2025,10,1)),date(2025,10,1))
        with self.assertRaises(ValueError): hourly_market(rows[:-1])

    def test_utc_horizon_boundary_and_cross_signal(self):
        day=date(2025,12,31); following=date(2026,1,1)
        load=parse_redata(load_body(day))[0]+parse_redata(load_body(following))[0]
        market=hourly_market(parse_omie(market_body(day),day))+hourly_market(parse_omie(market_body(following),following))
        start=datetime(2025,12,31,tzinfo=timezone.utc); end=datetime(2026,1,1,tzinfo=timezone.utc)
        left=[r['timestamp_utc'] for r in load if start<=r['timestamp_utc']<end]
        right=[r['timestamp_utc'] for r in market if start<=r['timestamp_utc']<end]
        self.assertEqual(left,right); validate_target(left,start,end)
        with self.assertRaises(CoverageError): validate_target(left[:-1],start,end)
