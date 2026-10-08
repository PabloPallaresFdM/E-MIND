"""Pure Spanish provider parsing under the owner-approved Phase07 contracts.

REData hourly busbar energy is MWh; OMIE is Spanish day-ahead EUR/MWh.
OMIE ordinal-to-clock mapping uses chronological Europe/Madrid delivery intervals.
These technical contracts do not grant redistribution or finality.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
from zoneinfo import ZoneInfo

from emind.harmonise.coverage import validate_index

UTC = timezone.utc
MADRID = ZoneInfo('Europe/Madrid')
HOUR = timedelta(hours=1)
TRANSITION_DAY = date(2025, 10, 1)


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Null/boolean quantity')
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError('Non-numeric quantity') from exc
    if not result.is_finite() or len(result.as_tuple().digits) > 40:
        raise ValueError('Non-finite or excessive precision quantity')
    return result


def parse_redata(body):
    data = json.loads(body, parse_float=Decimal)
    if data.get('data', {}).get('id') != 'dem1':
        raise ValueError('Unexpected REData widget')
    included = data.get('included', [])
    if len(included) != 1 or included[0].get('id') != '10297':
        raise ValueError('Unexpected REData series')
    attrs = included[0]['attributes']
    if attrs.get('title') != 'Demanda':
        raise ValueError('Unexpected REData title')
    if attrs.get('magnitude') not in (None, 'MWh'):
        raise ValueError('Unexpected REData magnitude; review metadata')
    rows = []
    for i, item in enumerate(attrs['values']):
        native = datetime.fromisoformat(item['datetime'])
        if native.tzinfo is None:
            raise ValueError('Missing REData offset')
        civil = native.astimezone(MADRID)
        if native.utcoffset() != civil.utcoffset() or native.replace(tzinfo=None) != civil.replace(tzinfo=None):
            raise ValueError('REData offset inconsistent with Madrid')
        if native.minute or native.second or native.microsecond:
            raise ValueError('REData timestamp not an hour boundary')
        rows.append(dict(timestamp_utc=native.astimezone(UTC), value=number(item['value']),
                         native_timestamp=item['datetime'], source_row=i))
    validate_index([r['timestamp_utc'] for r in rows])
    return rows, dict(series_id='10297', title=attrs['title'], magnitude=attrs.get('magnitude'),
                      description=attrs.get('description'), last_update=attrs.get('last-update'))


def day_bounds(day):
    start = datetime.combine(day, datetime.min.time(), MADRID)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def parse_omie(body, delivery_day):
    lines = body.decode('ascii').splitlines()
    if not lines or lines[0] != 'MARGINALPDBC;' or lines[-1] != '*':
        raise ValueError('OMIE header/terminator missing (possibly HTTP error)')
    start, end = day_bounds(delivery_day)
    minutes = 15 if delivery_day >= TRANSITION_DAY else 60
    step = timedelta(minutes=minutes)
    expected = (end-start)//step
    if len(lines)-2 != expected:
        raise ValueError('OMIE period count inconsistent with delivery day/resolution')
    rows = []
    for i, line in enumerate(lines[1:-1], 1):
        fields = line.split(';')
        if len(fields) != 7 or fields[-1] != '':
            raise ValueError('OMIE column schema')
        if tuple(map(int, fields[:3])) != (delivery_day.year, delivery_day.month, delivery_day.day) or int(fields[3]) != i:
            raise ValueError('OMIE date/period labels')
        number(fields[4])  # Portugal validation; never used as Spanish price.
        rows.append(dict(timestamp_utc=start+(i-1)*step, value=number(fields[5]),
                         source_row=i, period=i, interval_minutes=minutes))
    validate_index([r['timestamp_utc'] for r in rows], step)
    return rows


def hourly_market(rows):
    result = []
    offset = 0
    while offset < len(rows):
        first = rows[offset]; n = 4 if first['interval_minutes'] == 15 else 1
        group = rows[offset:offset+n]
        if len(group) != n or first['timestamp_utc'].minute:
            raise ValueError('Incomplete/nonaligned OMIE hourly group')
        if any(r['interval_minutes'] != first['interval_minutes'] for r in group):
            raise ValueError('Mixed native resolution inside hour')
        validate_index([r['timestamp_utc'] for r in group], timedelta(minutes=first['interval_minutes']))
        with localcontext() as ctx:
            ctx.prec = max(50, max(r['value'].adjusted() for r in group)
                           - min(r['value'].as_tuple().exponent for r in group) + 5)
            mean = sum((r['value'] for r in group), Decimal(0))/Decimal(n)
        result.append(dict(timestamp_utc=first['timestamp_utc'], value=mean,
                           native_periods=[r['period'] for r in group]))
        offset += n
    validate_index([r['timestamp_utc'] for r in result])
    return result


def local_day_audit(rows):
    counts = Counter(r['timestamp_utc'].astimezone(MADRID).date().isoformat() for r in rows)
    duplicates = Counter(r['timestamp_utc'].astimezone(MADRID).replace(tzinfo=None).isoformat() for r in rows)
    return dict(local_day_counts=dict(sorted(counts.items())),
                duplicate_local_civil_labels=sum(v-1 for v in duplicates.values()),
                offsets=sorted({str(r['timestamp_utc'].astimezone(MADRID).utcoffset()) for r in rows}))
