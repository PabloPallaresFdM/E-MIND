"""Deterministic civil-time to UTC reconstruction.

E-MIND canonical data use UTC interval starts. Provider exports may instead use
naive local civil timestamps that repeat during the autumn DST transition and
skip during the spring transition. This module resolves a complete ordered
sequence by requiring physical UTC continuity; it never drops duplicate labels
or selects an ambiguous fold arbitrarily.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc


class AmbiguousSequenceError(ValueError):
    """Raised when a local timestamp sequence cannot be reconstructed uniquely."""


def _utc_candidates(local_naive: datetime, zone: ZoneInfo) -> List[datetime]:
    if local_naive.tzinfo is not None:
        raise ValueError("local_naive must not already contain tzinfo")

    candidates: List[datetime] = []
    for fold in (0, 1):
        aware = local_naive.replace(tzinfo=zone, fold=fold)
        utc_value = aware.astimezone(UTC)
        # Round-trip validation rejects nonexistent spring-forward civil times.
        back = utc_value.astimezone(zone).replace(tzinfo=None)
        if back == local_naive and utc_value not in candidates:
            candidates.append(utc_value)
    return sorted(candidates)


def resolve_local_interval_starts(
    local_starts: Sequence[datetime] | Iterable[datetime],
    *,
    timezone_name: str,
    interval: timedelta,
) -> List[datetime]:
    """Map an ordered naive-local sequence to unique UTC interval starts.

    For every row after the first, the chosen UTC timestamp must be exactly one
    physical ``interval`` after the preceding row. This rule distinguishes the
    repeated autumn civil labels while naturally bridging the missing spring
    hour. The function fails rather than imputing, deleting, or guessing.

    The first timestamp must itself be unambiguous. E-MIND processing starts at
    ordinary year boundaries, so that requirement is deliberate and auditable.
    """

    values = list(local_starts)
    if not values:
        return []
    if interval <= timedelta(0):
        raise ValueError("interval must be positive")

    zone = ZoneInfo(timezone_name)
    first_candidates = _utc_candidates(values[0], zone)
    if len(first_candidates) != 1:
        raise AmbiguousSequenceError(
            f"first timestamp {values[0]!r} has {len(first_candidates)} valid UTC candidates"
        )

    resolved = [first_candidates[0]]
    for row_index, local_value in enumerate(values[1:], start=1):
        candidates = _utc_candidates(local_value, zone)
        expected = resolved[-1] + interval
        matches = [candidate for candidate in candidates if candidate == expected]
        if len(matches) != 1:
            raise AmbiguousSequenceError(
                "cannot reconstruct unique continuous UTC sequence at row "
                f"{row_index}: local={local_value!r}, expected_utc={expected.isoformat()}, "
                f"candidates={[c.isoformat() for c in candidates]}"
            )
        resolved.append(matches[0])

    return resolved


def validate_local_interval_ends(
    local_starts, local_ends, utc_starts, *, timezone_name, interval,
    allow_nominal_dst_ends=False,
):
    """Audit end labels without letting them select a start fold.

    Physical civil ends are required by default. A provider's nominal civil
    end may be admitted explicitly at an actual offset transition only; callers
    must retain the returned rule and original labels as evidence. This option
    needs raw-backed verification before use on scientific inputs.
    """
    if interval <= timedelta(0):
        raise ValueError('interval must be positive')
    if not (len(local_starts) == len(local_ends) == len(utc_starts)):
        raise ValueError('Start/end sequence lengths differ')
    zone = ZoneInfo(timezone_name)
    rules = []
    for start, end, instant in zip(local_starts, local_ends, utc_starts):
        if start.tzinfo is not None or end.tzinfo is not None:
            raise ValueError('Expected naive civil labels')
        if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
            raise ValueError('Expected aware UTC interval start')
        local = instant.astimezone(zone)
        if local.replace(tzinfo=None) != start:
            raise ValueError('UTC start does not round-trip to source label')
        physical_end = (instant + interval).astimezone(zone)
        if end == physical_end.replace(tzinfo=None):
            rules.append('PHYSICAL_END')
        elif (allow_nominal_dst_ends
              and local.utcoffset() != physical_end.utcoffset()
              and end == start + interval):
            rules.append('NOMINAL_CIVIL_END_AT_DST')
        else:
            raise ValueError('Unsupported provider end label: ' + str(end))
    return rules
