"""Phase04-A3 contracts: synthetic CSVs/timestamps only; no provider access."""
import csv
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from emind.harmonise.coverage import validate_target
from emind.harmonise.smard import (
    NativeMarketInterval, aggregate_quarter_hour_market, canonicalise_market_hours,
    canonicalise_market_with_lineage, join_market_segments,
)
from emind.providers.integrity import digest
from emind.providers.smard import HEADERS, SOURCE_IDS, MARKET_QUARTER_HOUR_HEADER, read_native
from test_phase03b_recovery import label

UTC = timezone.utc
BERLIN = ZoneInfo('Europe/Berlin')
HOUR = timedelta(hours=1)
QUARTER = timedelta(minutes=15)
# Deliberately synthetic; this is not an accepted actual provider header.
SYNTHETIC_HEADER = 'Synthetic reviewed market price [EUR/MWh]'
TRANSITION = datetime(2025, 10, 1, tzinfo=BERLIN).astimezone(UTC)


def native(start, count, step, values=None):
    return [NativeMarketInterval(
        (start + i * step).astimezone(BERLIN).replace(tzinfo=None),
        Decimal(values[i] if values is not None else '-1.25'), i + 1,
    ) for i in range(count)]


class MixedResolutionMarketTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='emind-market-synthetic-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def parse(self, start, count, minutes=15, values=None, header=SYNTHETIC_HEADER,
              **options):
        step = timedelta(minutes=minutes)
        rows = native(start, count, step, values)
        path = self.directory / 'synthetic.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['Start date', 'End date', header])
            for i, row in enumerate(rows):
                end = (start + (i + 1) * step).astimezone(BERLIN).replace(tzinfo=None)
                writer.writerow([label(row.local_start), label(end), str(row.price_eur_mwh)])
        obj = dict(raw_path=str(path), sha256=digest(path), size_bytes=path.stat().st_size,
                   snapshot_id='synthetic', source_id=SOURCE_IDS['market'])
        return read_native([obj], 'market', **options)

    def test_explicit_quarter_hour_schema_and_native_audit(self):
        records, audit = self.parse(TRANSITION, 4, interval_minutes=15,
                                    reviewed_market_header=SYNTHETIC_HEADER)
        self.assertEqual(len(records), 4)
        self.assertEqual([a['timestamp_utc'] for a in audit],
                         [TRANSITION + i * QUARTER for i in range(4)])
        self.assertTrue(all(a['interval_minutes'] == 15 for a in audit))
        self.assertTrue(all(a['quality_flag'] == 'ORIGINAL' for a in audit))
        self.assertEqual(audit[0]['source_header'], SYNTHETIC_HEADER)

    def test_a4_actual_native_quarter_hour_header_contract(self):
        # Exact observed header, with synthetic values only; no RAW fixture.
        self.assertEqual(MARKET_QUARTER_HOUR_HEADER,
                         'Germany/Luxembourg [€/MWh] Original resolutions')
        records, audit = self.parse(TRANSITION, 4, header=MARKET_QUARTER_HOUR_HEADER,
            values=['-10.25', '0.00', '10.25', '20.50'], interval_minutes=15,
            reviewed_market_header=MARKET_QUARTER_HOUR_HEADER)
        self.assertEqual([r.price_eur_mwh for r in records],
                         [Decimal('-10.25'), Decimal('0.00'), Decimal('10.25'), Decimal('20.50')])
        self.assertTrue(all(a['source_header'] == MARKET_QUARTER_HOUR_HEADER
                            and a['interval_minutes'] == 15 for a in audit))

    def test_a4_actual_header_does_not_relax_historical_market_schema(self):
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.parse(TRANSITION, 1, minutes=60, header=MARKET_QUARTER_HOUR_HEADER)

    def test_quarter_header_is_required_and_validation_is_scoped(self):
        with self.assertRaisesRegex(ValueError, 'reviewed'):
            self.parse(TRANSITION, 4, interval_minutes=15)
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.parse(TRANSITION, 4, interval_minutes=15, reviewed_market_header='Different reviewed header')
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.parse(TRANSITION, 1, minutes=60)
        with self.assertRaisesRegex(ValueError, 'scoped'):
            self.parse(TRANSITION, 1, minutes=60, interval_minutes=60,
                       reviewed_market_header=SYNTHETIC_HEADER)
        for minutes in (30, 0, True, 15.0):
            with self.subTest(minutes=minutes), self.assertRaisesRegex(ValueError, 'interval'):
                self.parse(TRANSITION, 4, interval_minutes=minutes,
                           reviewed_market_header=SYNTHETIC_HEADER)

    def test_frequency_is_not_inferred_from_rows(self):
        with self.assertRaises(ValueError):
            self.parse(TRANSITION, 4, header=HEADERS['market'])

    def test_historical_default_and_explicit_60_are_identical(self):
        start = datetime(2019, 1, 1, tzinfo=UTC)
        old = self.parse(start, 2, minutes=60, header=HEADERS['market'])
        explicit = self.parse(start, 2, minutes=60, header=HEADERS['market'], interval_minutes=60)
        self.assertEqual(old, explicit)
        self.assertEqual([asdict(r) for r in canonicalise_market_hours(old[0])],
                         [asdict(r) for r in canonicalise_market_hours(explicit[0])])
        self.assertEqual(set(asdict(canonicalise_market_hours(old[0])[0])),
                         {'timestamp_utc', 'interval_minutes', 'day_ahead_price_eur_mwh',
                          'source_id', 'quality_flag'})

    def test_positive_mean(self):
        self.assert_mean(['10', '20', '30', '40'], '25')

    def test_negative_mean(self):
        self.assert_mean(['-10', '-20', '-30', '-40'], '-25')

    def test_mixed_sign_mean(self):
        self.assert_mean(['-20', '-10', '10', '20'], '0')

    def test_exact_decimal_mean(self):
        self.assert_mean(['0.1', '0.2', '0.3', '0.4'], '0.25')

    def assert_mean(self, values, expected):
        records = native(TRANSITION, 4, QUARTER, values)
        before = list(records)
        result = aggregate_quarter_hour_market(records)
        self.assertEqual(result[0].day_ahead_price_eur_mwh, Decimal(expected))
        self.assertEqual(records, before)
        self.assertEqual(result[0].quality_flag, 'AGGREGATED_NATIVE')
        self.assertEqual(result[0].interval_minutes, 60)

    def test_decimal_mean_does_not_depend_on_ambient_precision(self):
        values = ['123456789012345678901234567890.01',
                  '-123456789012345678901234567890.00', '0.01', '0.02']
        with localcontext() as context:
            context.prec = 3
            self.assert_mean(values, '0.01')

    def test_malformed_groups_fail(self):
        valid = native(TRANSITION, 8, QUARTER)
        bad = {
            'missing': valid[:2] + valid[3:4],
            'duplicate': [valid[0], valid[1], valid[1], valid[3]],
            'off_grid': native(TRANSITION + timedelta(minutes=1), 4, QUARTER),
            'unordered': [valid[0], valid[2], valid[1], valid[3]],
            'five': valid[:5],
            'three': valid[:3],
            'discontinuous': valid[:3] + valid[4:5],
            'mixed_hours': valid[1:5],
            'gap_between_complete_hours': valid[:4] + native(TRANSITION + 2 * HOUR, 4, QUARTER),
        }
        for reason, records in bad.items():
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                aggregate_quarter_hour_market(records)

    def test_nonfinite_or_float_prices_fail(self):
        for value in (Decimal('NaN'), Decimal('Infinity'), 1.0):
            records = native(TRANSITION, 4, QUARTER)
            records[0] = NativeMarketInterval(records[0].local_start, value, 1)
            with self.subTest(value=value), self.assertRaises(ValueError):
                aggregate_quarter_hour_market(records)

    def test_autumn_2025_parser_and_aggregation_preserve_both_folds(self):
        start = datetime(2025, 10, 26, tzinfo=BERLIN).astimezone(UTC)
        records, audit = self.parse(start, 100, interval_minutes=15,
                                    reviewed_market_header=SYNTHETIC_HEADER)
        self.assertEqual(len({a['timestamp_utc'] for a in audit}), 100)
        for minute in (0, 15, 30, 45):
            repeated = [a for r, a in zip(records, audit)
                        if r.local_start.hour == 2 and r.local_start.minute == minute]
            self.assertEqual([a['fold'] for a in repeated], [0, 1])
            self.assertEqual(repeated[1]['timestamp_utc'] - repeated[0]['timestamp_utc'], HOUR)
        hours = aggregate_quarter_hour_market(records)
        self.assertEqual(len(hours), 25)
        validate_target([r.timestamp_utc for r in hours], start, start + 25 * HOUR)

    def test_spring_2025_quarter_market_bridges_missing_civil_hour(self):
        start = datetime(2025, 3, 30, tzinfo=BERLIN).astimezone(UTC)
        records, _ = self.parse(start, 92, interval_minutes=15,
                                reviewed_market_header=SYNTHETIC_HEADER)
        self.assertFalse(any(r.local_start.hour == 2 for r in records))
        hours = aggregate_quarter_hour_market(records)
        self.assertEqual(len(hours), 23)
        validate_target([r.timestamp_utc for r in hours], start, start + 23 * HOUR)

    def test_lineage_exposes_one_or_four_original_values(self):
        for minutes in (60, 15):
            width = 60 // minutes
            records, audit = self.parse(TRANSITION, width, minutes=minutes,
                header=HEADERS['market'] if minutes == 60 else SYNTHETIC_HEADER,
                interval_minutes=minutes,
                **({'reviewed_market_header': SYNTHETIC_HEADER} if minutes == 15 else {}))
            before = [dict(a) for a in audit]
            output = canonicalise_market_with_lineage(records, audit, interval_minutes=minutes)[0]
            self.assertEqual(output['contributing_row_count'], width)
            self.assertEqual(output['native_interval_minutes'], minutes)
            self.assertEqual(output['source_native_frequency'], f'{minutes} minutes')
            self.assertEqual(output['quality_flag'], 'ORIGINAL' if minutes == 60 else 'AGGREGATED_NATIVE')
            self.assertEqual(output['canonical_transformation'],
                             'IDENTITY' if minutes == 60 else 'ARITHMETIC_MEAN_FOUR_QUARTERS')
            refs = output['native_row_refs']
            self.assertEqual([a['native_value'] for a in refs], [r.price_eur_mwh for r in records])
            self.assertEqual([a['timestamp_utc'] for a in refs], [a['timestamp_utc'] for a in audit])
            self.assertEqual([a['source_row_index'] for a in refs], list(range(1, width + 1)))
            self.assertTrue(all(a['snapshot_id'] == 'synthetic' and a['quality_flag'] == 'ORIGINAL'
                                and a['start_date_raw'] and a['raw_sha256'] for a in refs))
            self.assertEqual(audit, before)

    def test_lineage_mismatch_fails(self):
        records, audit = self.parse(TRANSITION, 4, interval_minutes=15,
                                    reviewed_market_header=SYNTHETIC_HEADER)
        for key, value in [('timestamp_utc', TRANSITION + HOUR), ('interval_minutes', 60),
                           ('source_id', 'wrong'), ('source_row_index', 99), ('value_raw', '99')]:
            changed = [dict(a) for a in audit]; changed[0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'lineage'):
                canonicalise_market_with_lineage(records, changed, interval_minutes=15)
        with self.assertRaisesRegex(ValueError, 'lengths'):
            canonicalise_market_with_lineage(records, audit[:-1], interval_minutes=15)

    def test_transition_mapping_and_join_contract(self):
        self.assertEqual(TRANSITION, datetime(2025, 9, 30, 22, tzinfo=UTC))
        before = canonicalise_market_hours(native(TRANSITION - HOUR, 1, HOUR))
        after = aggregate_quarter_hour_market(native(TRANSITION, 8, QUARTER))
        joined = join_market_segments(before, after, transition_utc=TRANSITION)
        validate_target([r.timestamp_utc for r in joined], TRANSITION - HOUR, TRANSITION + 2 * HOUR)
        with self.assertRaisesRegex(ValueError, 'transition'):
            join_market_segments(before, after, transition_utc=datetime(2025, 10, 1, tzinfo=UTC))
        for offset in (-1, 1):
            bad = aggregate_quarter_hour_market(native(TRANSITION + offset * HOUR, 4, QUARTER))
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                join_market_segments(before, bad, transition_utc=TRANSITION)

    def test_full_synthetic_extension_cardinality_and_exact_boundaries(self):
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 1, tzinfo=UTC)
        m1 = native(start, 15334, HOUR)
        m2 = native(TRANSITION, 8840, QUARTER)
        first = canonicalise_market_hours(m1)
        second = aggregate_quarter_hour_market(m2)
        self.assertEqual((len(m1), len(first)), (15334, 15334))
        self.assertEqual((len(m2), len(second)), (8840, 2210))
        joined = join_market_segments(first, second, transition_utc=TRANSITION)
        self.assertEqual(len(joined), 17544)
        validate_target([r.timestamp_utc for r in first], start, TRANSITION)
        validate_target([r.timestamp_utc for r in second], TRANSITION, end)
        validate_target([r.timestamp_utc for r in joined], start, end)
        self.assertEqual(joined[-1].timestamp_utc, datetime(2025, 12, 31, 23, tzinfo=UTC))


if __name__ == '__main__':
    unittest.main()
