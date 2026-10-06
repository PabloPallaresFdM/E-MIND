"""Focused synthetic invariants for Phase03B-B quantitative descriptors."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from emind.harmonise.information_loss import detailed_information_loss


class InformationLossTests(unittest.TestCase):
    def times(self):
        start=datetime(2019,12,31,22,tzinfo=timezone.utc)
        return [start+i*timedelta(hours=1) for i in range(4)]

    def test_energy_conversion_and_population_moments(self):
        r=detailed_information_loss([Decimal(v) for v in [1,2,3,4]*4],[Decimal(10)]*4,self.times())
        self.assertEqual(r['energy_check']['native_mwh'],40)
        self.assertEqual(r['energy_check']['hourly_mwh'],40)
        self.assertEqual(r['energy_check']['absolute_difference_mwh'],0)
        self.assertEqual(r['native_15min']['mean_mw'],10)
        self.assertEqual(r['native_15min']['variance_mw2_population'],20)
        self.assertEqual(r['variance_mw2_population_relative_change'],-1)
        self.assertEqual(r['hourly']['coefficient_of_variation'],0)

    def test_peak_and_within_hour_variability(self):
        r=detailed_information_loss([Decimal(v) for v in [1,2,3,4]*4],[Decimal(10)]*4,self.times())
        self.assertEqual(r['hidden_peak_mw']['median'],6)
        self.assertEqual(r['within_hour_range_mw']['p99'],12)
        self.assertAlmostEqual(float(r['within_hour_std_mw_population']['max']),20**.5)
        self.assertEqual(r['global_native_peak']['timestamp_utc'],self.times()[0]+timedelta(minutes=45))
        self.assertEqual(r['global_native_peak']['attenuation_fraction'],Decimal('.375'))
        self.assertEqual(r['attenuation_thresholds']['strictly_greater_fraction_counts']['0.10'],4)

    def test_ramp_horizons_and_year_boundary_are_explicit(self):
        r=detailed_information_loss([Decimal(1)]*8+[Decimal(10)]*8,
                                   [Decimal(4)]*2+[Decimal(40)]*2,self.times())
        self.assertEqual(r['native_ramps']['absolute_delta_mw']['max'],36)
        self.assertEqual(r['native_ramps']['absolute_rate_mw_per_hour']['max'],144)
        self.assertEqual(r['hourly_ramps']['absolute_rate_mw_per_hour']['max'],36)
        self.assertEqual(r['native_ramps']['signed_rate_mw_per_hour_max'],144)
        for year in ('2019','2020'):
            self.assertEqual(r['annual_utc'][year]['hourly_intervals'],2)
            self.assertEqual(r['annual_utc'][year]['native_abs_ramp_max_mw_per_h'],0)
            self.assertEqual(r['annual_utc'][year]['hourly_abs_ramp_max_mw_per_h'],0)

    def test_global_energy_equality_cannot_hide_wrong_hour_groups(self):
        with self.assertRaisesRegex(ValueError,'Per-hour'):
            detailed_information_loss([Decimal(1)]*8+[Decimal(10)]*8,
                                      [Decimal(40)]*2+[Decimal(4)]*2,self.times())

    def test_invalid_population_or_time_axis_fails(self):
        with self.assertRaises(ValueError):
            detailed_information_loss([Decimal(1)]*15,[Decimal(4)]*4,self.times())
        times=self.times();times[1]=times[0]
        with self.assertRaises(ValueError):
            detailed_information_loss([Decimal(1)]*16,[Decimal(4)]*4,times)
