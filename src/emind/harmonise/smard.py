"""Pure transformations for frozen SMARD records.

This module intentionally works on already-parsed values. Reading provider CSV
bytes, verifying frozen SHA256 values, and writing Scratch outputs remain the
responsibility of the configured execution layer. Keeping the scientific core
pure makes DST and aggregation rules directly testable on GitHub without raw
provider payloads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Iterable, List, Sequence

from emind.time.dst import resolve_local_interval_starts

BERLIN = "Europe/Berlin"
QUARTER_HOUR = timedelta(minutes=15)
HOUR = timedelta(hours=1)


@dataclass(frozen=True)
class NativeLoadInterval:
    local_start: datetime
    energy_mwh: Decimal
    source_row: int


@dataclass(frozen=True)
class NativeMarketInterval:
    local_start: datetime
    price_eur_mwh: Decimal
    source_row: int


@dataclass(frozen=True)
class CanonicalLoadHour:
    timestamp_utc: datetime
    interval_minutes: int
    load_energy_mwh: Decimal
    load_power_mw: Decimal
    source_id: str = "de_smard_electrical_load"
    source_native_frequency: str = "15 minutes"
    quality_flag: str = "AGGREGATED_NATIVE"


@dataclass(frozen=True)
class CanonicalMarketHour:
    timestamp_utc: datetime
    interval_minutes: int
    day_ahead_price_eur_mwh: Decimal
    source_id: str = "de_smard_day_ahead"
    quality_flag: str = "ORIGINAL"


def aggregate_quarter_hour_load(
    records: Sequence[NativeLoadInterval] | Iterable[NativeLoadInterval],
) -> List[CanonicalLoadHour]:
    """Canonicalise SMARD quarter-hour energy into physical UTC hours.

    Exactly four consecutive physical quarter-hour intervals must form each UTC
    hour. Energy is summed, never averaged. Because the canonical interval is
    exactly one hour, the numerical value of MWh over that interval equals the
    corresponding average MW, while the two quantities remain semantically
    distinct.
    """

    rows = list(records)
    if not rows:
        return []

    utc_starts = resolve_local_interval_starts(
        [row.local_start for row in rows],
        timezone_name=BERLIN,
        interval=QUARTER_HOUR,
    )

    result: List[CanonicalLoadHour] = []
    if len(rows) % 4 != 0:
        raise ValueError(f"native load row count {len(rows)} is not divisible by four")

    for offset in range(0, len(rows), 4):
        block_rows = rows[offset : offset + 4]
        block_times = utc_starts[offset : offset + 4]
        hour_start = block_times[0]
        if hour_start.minute != 0 or hour_start.second != 0 or hour_start.microsecond != 0:
            raise ValueError(f"hour group does not begin on UTC hour boundary: {hour_start.isoformat()}")
        expected = [hour_start + i * QUARTER_HOUR for i in range(4)]
        if block_times != expected:
            raise ValueError(
                "hour group does not contain exactly four consecutive physical quarter-hours: "
                f"{[value.isoformat() for value in block_times]}"
            )

        energy = sum((row.energy_mwh for row in block_rows), Decimal("0"))
        result.append(
            CanonicalLoadHour(
                timestamp_utc=hour_start,
                interval_minutes=60,
                load_energy_mwh=energy,
                load_power_mw=energy,  # E / 1 h; same numerical value, different quantity.
            )
        )

    _assert_regular_hourly([row.timestamp_utc for row in result])
    return result


def canonicalise_market_hours(
    records: Sequence[NativeMarketInterval] | Iterable[NativeMarketInterval],
) -> List[CanonicalMarketHour]:
    """Convert provider-local physical hourly market intervals to UTC.

    Prices are retained exactly, including negative values. No summation,
    clipping, currency conversion, retail-tariff modelling, or imputation occurs.
    """

    rows = list(records)
    if not rows:
        return []

    utc_starts = resolve_local_interval_starts(
        [row.local_start for row in rows],
        timezone_name=BERLIN,
        interval=HOUR,
    )
    _assert_regular_hourly(utc_starts)
    return [
        CanonicalMarketHour(
            timestamp_utc=timestamp,
            interval_minutes=60,
            day_ahead_price_eur_mwh=row.price_eur_mwh,
        )
        for timestamp, row in zip(utc_starts, rows)
    ]


def aggregate_quarter_hour_market(
    records: Sequence[NativeMarketInterval] | Iterable[NativeMarketInterval],
) -> List[CanonicalMarketHour]:
    """Mean four complete physical quarters; never model subhourly dispatch.

    This equals the price for constant-power exposure, not exact settlement
    for unequal quarter-hour energies. Native records remain unchanged.
    """
    rows = list(records)
    if not rows:
        return []
    if len(rows) % 4:
        raise ValueError('Exactly four native market quarters per UTC hour required')
    times = resolve_local_interval_starts(
        [r.local_start for r in rows], timezone_name=BERLIN, interval=QUARTER_HOUR)
    result = []
    for offset in range(0, len(rows), 4):
        block = rows[offset:offset + 4]
        start = times[offset]
        if (start.minute or start.second or start.microsecond
                or times[offset:offset + 4] != [start + i * QUARTER_HOUR for i in range(4)]):
            raise ValueError('Market quarters must be 00/15/30/45 within one UTC hour')
        values = [r.price_eur_mwh for r in block]
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise ValueError('Finite Decimal market prices required')
        # Sufficient precision for exact aligned addition and division by four,
        # independent of the caller's Decimal context (including cancellation).
        precision = max(v.adjusted() for v in values) - min(v.as_tuple().exponent for v in values) + 5
        with localcontext() as context:
            context.prec = max(28, precision)
            mean = sum(values, Decimal(0)) / Decimal(4)
        result.append(CanonicalMarketHour(start, 60, mean, quality_flag='AGGREGATED_NATIVE'))
    _assert_regular_hourly([r.timestamp_utc for r in result])
    return result


def canonicalise_market_with_lineage(records, audit, *, interval_minutes):
    """Expose the existing provider audit as one/four-row native lineage.

    Historical APIs and their serialized schemas remain unchanged. The caller
    supplies the explicit homogeneous native resolution, with read_native audit.
    """
    if type(interval_minutes) is not int or interval_minutes not in (15, 60):
        raise ValueError('Market native interval must be explicitly 15 or 60')
    records, audit = list(records), list(audit)
    if len(records) != len(audit):
        raise ValueError('Native records/audit lengths differ')
    hours = (canonicalise_market_hours(records) if interval_minutes == 60
             else aggregate_quarter_hour_market(records))
    times = resolve_local_interval_starts(
        [r.local_start for r in records], timezone_name=BERLIN,
        interval=timedelta(minutes=interval_minutes))
    required = ('source_id', 'snapshot_id', 'raw_sha256', 'source_row_index',
                'start_date_raw', 'end_date_raw', 'value_raw', 'timestamp_utc', 'interval_minutes')
    refs = []
    for record, row, instant in zip(records, audit, times):
        if (any(k not in row for k in required)
                or row['source_id'] != 'de_smard_day_ahead'
                or row['source_row_index'] != record.source_row
                or row['timestamp_utc'] != instant or row['interval_minutes'] != interval_minutes
                or Decimal(row['value_raw'].replace(',', '')) != record.price_eur_mwh):
            raise ValueError('Native market lineage mismatch')
        refs.append(dict(row, local_start=record.local_start,
                         native_value=record.price_eur_mwh, quality_flag='ORIGINAL'))
    width = 60 // interval_minutes
    return [dict(asdict(hour), source_native_frequency=f'{interval_minutes} minutes',
                 native_interval_minutes=interval_minutes, contributing_row_count=width,
                 canonical_transformation='IDENTITY' if width == 1 else 'ARITHMETIC_MEAN_FOUR_QUARTERS',
                 native_row_refs=refs[i * width:(i + 1) * width])
            for i, hour in enumerate(hours)]


def join_market_segments(hourly, aggregated, *, transition_utc):
    """Join canonical regimes at a caller-supplied transition, without guessing."""
    hourly, aggregated = list(hourly), list(aggregated)
    if (transition_utc.tzinfo is None or transition_utc.utcoffset() != timedelta(0)
            or transition_utc.minute or transition_utc.second or transition_utc.microsecond):
        raise ValueError('Transition must be a UTC hour start')
    if (not hourly or not aggregated or hourly[-1].timestamp_utc + HOUR != transition_utc
            or aggregated[0].timestamp_utc != transition_utc
            or any(r.quality_flag != 'ORIGINAL' for r in hourly)
            or any(r.quality_flag != 'AGGREGATED_NATIVE' for r in aggregated)):
        raise ValueError('Market segments do not match the transition contract')
    result = hourly + aggregated
    if any(r.interval_minutes != 60 for r in result):
        raise ValueError('Canonical market segments must be hourly')
    if any(r.timestamp_utc.tzinfo is None or r.timestamp_utc.utcoffset() != timedelta(0)
           or r.timestamp_utc.minute or r.timestamp_utc.second or r.timestamp_utc.microsecond
           for r in result):
        raise ValueError('Canonical market timestamps must be UTC hour starts')
    _assert_regular_hourly([r.timestamp_utc for r in result])
    return result


def _assert_regular_hourly(timestamps: Sequence[datetime]) -> None:
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("duplicate canonical UTC timestamps")
    for index in range(1, len(timestamps)):
        expected = timestamps[index - 1] + HOUR
        if timestamps[index] != expected:
            raise ValueError(
                f"non-contiguous canonical UTC timeline at row {index}: "
                f"expected {expected.isoformat()}, got {timestamps[index].isoformat()}"
            )
