"""Offline scientific invariants for the DE_CENT canonicalisation core."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from emind.harmonise.smard import (  # noqa: E402
    NativeLoadInterval,
    NativeMarketInterval,
    aggregate_quarter_hour_load,
    canonicalise_market_hours,
)
from emind.time.dst import AmbiguousSequenceError, resolve_local_interval_starts  # noqa: E402

UTC = timezone.utc


class DSTTests(unittest.TestCase):
    def test_spring_forward_is_continuous_in_utc(self):
        local = [
            datetime(2023, 3, 26, 1, 0),
            datetime(2023, 3, 26, 3, 0),
            datetime(2023, 3, 26, 4, 0),
        ]
        result = resolve_local_interval_starts(
            local, timezone_name="Europe/Berlin", interval=timedelta(hours=1)
        )
        self.assertEqual(
            result,
            [
                datetime(2023, 3, 26, 0, 0, tzinfo=UTC),
                datetime(2023, 3, 26, 1, 0, tzinfo=UTC),
                datetime(2023, 3, 26, 2, 0, tzinfo=UTC),
            ],
        )

    def test_autumn_repeated_local_hour_maps_to_distinct_utc_hours(self):
        local = [
            datetime(2023, 10, 29, 1, 0),
            datetime(2023, 10, 29, 2, 0),
            datetime(2023, 10, 29, 2, 0),
            datetime(2023, 10, 29, 3, 0),
        ]
        result = resolve_local_interval_starts(
            local, timezone_name="Europe/Berlin", interval=timedelta(hours=1)
        )
        self.assertEqual(len(set(result)), 4)
        self.assertEqual(
            result,
            [
                datetime(2023, 10, 28, 23, 0, tzinfo=UTC),
                datetime(2023, 10, 29, 0, 0, tzinfo=UTC),
                datetime(2023, 10, 29, 1, 0, tzinfo=UTC),
                datetime(2023, 10, 29, 2, 0, tzinfo=UTC),
            ],
        )

    def test_sequence_gap_fails_instead_of_imputing(self):
        local = [datetime(2023, 1, 1, 0), datetime(2023, 1, 1, 2)]
        with self.assertRaises(AmbiguousSequenceError):
            resolve_local_interval_starts(
                local, timezone_name="Europe/Berlin", interval=timedelta(hours=1)
            )


class SMARDTests(unittest.TestCase):
    def test_quarter_hour_energy_is_summed_not_averaged(self):
        records = [
            NativeLoadInterval(datetime(2023, 1, 1, 1, minute), Decimal(value), index)
            for index, (minute, value) in enumerate(
                [(0, "10"), (15, "11"), (30, "12"), (45, "13")]
            )
        ]
        result = aggregate_quarter_hour_load(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].timestamp_utc, datetime(2023, 1, 1, 0, tzinfo=UTC))
        self.assertEqual(result[0].load_energy_mwh, Decimal("46"))
        self.assertEqual(result[0].load_power_mw, Decimal("46"))
        self.assertEqual(result[0].quality_flag, "AGGREGATED_NATIVE")

    def test_negative_market_price_is_preserved(self):
        records = [
            NativeMarketInterval(datetime(2023, 1, 1, 1), Decimal("-17.50"), 0),
            NativeMarketInterval(datetime(2023, 1, 1, 2), Decimal("4.25"), 1),
        ]
        result = canonicalise_market_hours(records)
        self.assertEqual(result[0].day_ahead_price_eur_mwh, Decimal("-17.50"))
        self.assertEqual(result[0].quality_flag, "ORIGINAL")

    def test_incomplete_hour_fails_without_imputation(self):
        records = [
            NativeLoadInterval(datetime(2023, 1, 1, 1, minute), Decimal("1"), index)
            for index, minute in enumerate((0, 15, 30))
        ]
        with self.assertRaisesRegex(ValueError, "not divisible by four"):
            aggregate_quarter_hour_load(records)


if __name__ == "__main__":
    unittest.main()
