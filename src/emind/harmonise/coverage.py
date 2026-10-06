"""Physical UTC coverage checks independent of row-count coincidences."""
from datetime import datetime, timedelta, timezone

HOUR = timedelta(hours=1)
TARGET_START = datetime(2019, 1, 1, tzinfo=timezone.utc)
TARGET_END = datetime(2024, 1, 1, tzinfo=timezone.utc)


class CoverageError(ValueError):
    """A complete exact target window cannot be certified."""


def validate_index(instants, interval=HOUR):
    values = list(instants)
    if not values or interval <= timedelta(0):
        raise CoverageError('Empty index or invalid interval')
    for index, value in enumerate(values):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise CoverageError('Expected timezone-aware UTC timestamps')
        if index and value != values[index - 1] + interval:
            raise CoverageError('Non-contiguous, duplicate or unordered UTC index')
    return values


def validate_target(instants, start=TARGET_START, end=TARGET_END):
    for boundary in (start, end):
        if (boundary.tzinfo is None or boundary.utcoffset() != timedelta(0)
                or boundary.minute or boundary.second or boundary.microsecond):
            raise CoverageError('Target boundaries must be UTC hour starts')
    if end <= start:
        raise CoverageError('Invalid target window')
    values = validate_index(instants)
    if values[0] != start or values[-1] + HOUR != end:
        raise CoverageError('Exact UTC boundaries differ from target; row count is insufficient')
    if len(values) != (end - start) // HOUR:
        raise CoverageError('Unexpected target row count')


def crop_to_target(rows, start=TARGET_START, end=TARGET_END):
    """Validate full raw-derived index, then require a complete cropped target."""
    rows = list(rows)
    validate_index([row.timestamp_utc for row in rows])
    selected = [row for row in rows if start <= row.timestamp_utc < end]
    validate_target([row.timestamp_utc for row in selected], start, end)
    return selected
