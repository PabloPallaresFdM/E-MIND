"""Synthetic rejection cases and base-time freedom without ecCodes or RAW."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from emind.weather import load_weather_config, validate_grib


class GribContractTests(unittest.TestCase):
    def setUp(self):
        self.config = load_weather_config(ROOT / 'configs/weather/de_cent_era5.yaml')
        self.message = dict(shortName='ssrd', paramId=169, units='J m**-2',
            dataDate=20251231, dataTime=1800, validityDate=20260101, validityTime=0,
            startStep=5, endStep=6, stepUnits=1, stepType='accum', timeRangeIndicator=4,
            typeOfLevel='surface', level=0, gridType='regular_ll', Ni=3, Nj=3,
            iDirectionIncrementInDegrees=.25, jDirectionIncrementInDegrees=.25,
            latitudeOfFirstGridPointInDegrees=51.25, longitudeOfFirstGridPointInDegrees=10.,
            latitudeOfLastGridPointInDegrees=50.75, longitudeOfLastGridPointInDegrees=10.5,
            numberOfDataPoints=9, numberOfMissing=0)
        self.rows = [(a, b, 1.) for a in (51.25, 51., 50.75) for b in (10., 10.25, 10.5)]

    def validate(self, message=None, rows=None):
        message = message if message is not None else self.message
        rows = rows if rows is not None else self.rows
        outputs = [json.dumps({'messages': [message]}),
                   'Latitude Longitude Value\n' + '\n'.join(f'{a} {b} {v}' for a, b, v in rows)]
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            path = Path(root) / 'fixture.grib'
            path.write_bytes(b'synthetic mocked decoder input')
            with patch('emind.weather.era5.subprocess.check_output', side_effect=outputs):
                return validate_grib(path, self.config, boundary=True)

    def test_alternate_base_and_step_units(self):
        self.assertEqual(self.validate()['status'], 'PASS')
        message = deepcopy(self.message)
        message.update(dataTime=2300, startStep=0, endStep=60, stepUnits=0)
        self.assertEqual(self.validate(message)['status'], 'PASS')

    def test_scanning_order(self):
        message = deepcopy(self.message)
        message.update(latitudeOfFirstGridPointInDegrees=50.75,
                       longitudeOfFirstGridPointInDegrees=10.5,
                       latitudeOfLastGridPointInDegrees=51.25,
                       longitudeOfLastGridPointInDegrees=10.)
        self.assertEqual(self.validate(message, list(reversed(self.rows)))['status'], 'PASS')

    def test_bad_parameter_grid_and_temporal_metadata(self):
        for key, value in [('paramId', 167), ('units', 'W m**-2'), ('Ni', 4),
                           ('numberOfMissing', 1), ('gridType', 'reduced_gg'),
                           ('endStep', 7), ('dataTime', 1700), ('stepType', 'instant'),
                           ('validityTime', 100), ('stepUnits', 99),
                           ('iDirectionIncrementInDegrees', .5)]:
            with self.subTest(key=key):
                message = deepcopy(self.message)
                message[key] = value
                with self.assertRaises(ValueError):
                    self.validate(message)

    def test_invalid_scalar_and_coordinates(self):
        for value in (float('nan'), float('inf'), -1.):
            rows = deepcopy(self.rows)
            rows[1] = (*rows[1][:2], value)
            with self.assertRaises(ValueError):
                self.validate(rows=rows)
        rows = deepcopy(self.rows)
        rows[1] = rows[0]
        with self.assertRaises(ValueError):
            self.validate(rows=rows)

    def test_wrong_message_count(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            path = Path(root) / 'fixture.grib'
            path.write_bytes(b'fixture')
            with patch('emind.weather.era5.subprocess.check_output', return_value='{"messages": []}'), self.assertRaises(ValueError):
                validate_grib(path, self.config, boundary=True)


if __name__ == '__main__':
    unittest.main()
