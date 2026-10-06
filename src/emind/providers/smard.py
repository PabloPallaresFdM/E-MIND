"""SMARD English semicolon exports -> pure native records plus raw-row audit.

Input snapshots must be supplied in physical chronological order. Never sort
civil labels: autumn repeated labels are distinct observations. No guessing of
locale, columns, numeric missingness, source identity or DST folds is allowed.
"""
import csv
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from emind.harmonise.smard import NativeLoadInterval, NativeMarketInterval
from emind.providers.integrity import verify_raw
from emind.time.dst import resolve_local_interval_starts, validate_local_interval_ends

HEADERS = {'load': 'grid load [MWh] Original resolutions',
           'market': 'Germany/Luxembourg [€/MWh] Calculated resolutions'}
# Phase04-A4 native M2 export observed and audited on 2026-10-02.
# Caller must still select this explicitly in market interval_minutes=15 mode.
MARKET_QUARTER_HOUR_HEADER = 'Germany/Luxembourg [€/MWh] Original resolutions'
SOURCE_IDS = {'load': 'de_smard_electrical_load', 'market': 'de_smard_day_ahead'}
MONTHS = {name: i + 1 for i, name in enumerate(
    ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'))}


def parse_label(value):
    match = re.fullmatch(r'([A-Z][a-z]{2}) ([0-9]{1,2}), ([0-9]{4}) ([0-9]{1,2}):([0-9]{2}) (AM|PM)', value)
    if not match or match[1] not in MONTHS or not 1 <= int(match[4]) <= 12:
        raise ValueError('Invalid SMARD civil timestamp label')
    hour = int(match[4]) % 12 + (12 if match[6] == 'PM' else 0)
    return datetime(int(match[3]), MONTHS[match[1]], int(match[2]), hour, int(match[5]))


def number(value):
    if not re.fullmatch(r'-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?', value):
        raise ValueError('Unsupported/missing native number; imputation is forbidden')
    return Decimal(value.replace(',', ''))


def read_native(objects, kind, *, allow_nominal_dst_ends=False,
                interval_minutes=None, reviewed_market_header=None):
    """Verify bytes, parse strictly and return (core records, audit rows).

    source_row is the one-based data-row number within the snapshot. Audit rows
    pair it with snapshot_id and SHA256; global_row_index is also one-based.
    Defaults retain the historical load/hourly-market contract. Quarter-hour
    market mode requires an explicit caller-reviewed value-column header; no
    header is selected automatically. Frequency is never inferred.
    """
    if kind not in HEADERS:
        raise ValueError('Unsupported SMARD kind')
    minutes = (15 if kind == 'load' else 60) if interval_minutes is None else interval_minutes
    if type(minutes) is not int or minutes not in (15, 60) or (kind == 'load' and minutes != 15):
        raise ValueError('Unsupported explicit native interval')
    header = HEADERS[kind]
    if kind == 'market' and minutes == 15:
        if (not isinstance(reviewed_market_header, str) or not reviewed_market_header.strip()
                or any(c in reviewed_market_header for c in '\r\n;')):
            raise ValueError('Quarter-hour market requires a reviewed value-column header')
        header = reviewed_market_header
    elif reviewed_market_header is not None:
        raise ValueError('Header override is scoped to quarter-hour market only')
    objects = list(objects)
    if not objects or len({obj['snapshot_id'] for obj in objects}) != len(objects):
        raise ValueError('Empty or duplicate snapshot input')
    verify_raw(objects)
    records, audit, starts, ends = [], [], [], []
    for obj in objects:
        if obj['source_id'] != SOURCE_IDS[kind]:
            raise ValueError('Source identity does not match provider schema')
        with Path(obj['raw_path']).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream, delimiter=';', strict=True)
            if next(reader, None) != ['Start date', 'End date', header]:
                raise ValueError('Unexpected SMARD schema')
            count = 0
            for index, row in enumerate(reader, 1):
                if len(row) != 3:
                    raise ValueError('Inconsistent SMARD row width')
                start, end, value = parse_label(row[0]), parse_label(row[1]), number(row[2])
                if kind == 'load' and value < 0:
                    raise ValueError('Negative load requires documented raw anomaly review')
                count += 1
                starts.append(start)
                ends.append(end)
                record_type = NativeLoadInterval if kind == 'load' else NativeMarketInterval
                records.append(record_type(start, value, index))
                audit.append({'snapshot_id': obj['snapshot_id'], 'source_id': obj['source_id'],
                              'raw_sha256': obj['sha256'], 'source_row_index': index,
                              'global_row_index': len(audit) + 1, 'source_header': header,
                              'start_date_raw': row[0], 'end_date_raw': row[1], 'value_raw': row[2]})
            if not count:
                raise ValueError('Empty SMARD snapshot')
    delta = timedelta(minutes=minutes)
    instants = resolve_local_interval_starts(starts, timezone_name='Europe/Berlin', interval=delta)
    rules = validate_local_interval_ends(
        starts, ends, instants, timezone_name='Europe/Berlin', interval=delta,
        allow_nominal_dst_ends=allow_nominal_dst_ends)
    zone = ZoneInfo('Europe/Berlin')
    for row, instant, rule in zip(audit, instants, rules):
        row.update(timestamp_utc=instant, interval_minutes=minutes,
                   fold=instant.astimezone(zone).fold, endpoint_rule=rule)
        if kind == 'market' and minutes == 15:
            row['quality_flag'] = 'ORIGINAL'
    verify_raw(objects)
    return records, audit
