"""Strict DE_CENT D-0036–D-0040 contract, using the repository JSON/YAML subset.

Pass a configuration path explicitly. No runtime data paths or provider access
are inferred. Values retain their units, names and timestamp strings; variable
order is normalized to the frozen provider order. Formula strings are metadata,
never executable expressions. A scientific contract change requires review.
"""

from copy import deepcopy
import json
from pathlib import Path


class WeatherConfigError(ValueError):
    """Invalid configuration; messages never echo untrusted values."""


# Validation contract, not a fallback configuration. All fields are required.
_CONTRACT = {'scenario_id': 'DE_CENT',
 'dataset': 'reanalysis-era5-single-levels',
 'product_type': 'reanalysis',
 'provider': 'ECMWF',
 'distribution': 'Copernicus Climate Data Store / C3S',
 'source_doi': '10.24381/cds.adbb2d47',
 'acquisition_format': 'GRIB',
 'anchor': {'latitude': 51.0, 'longitude': 10.25},
 'audit_bbox': {'north': 51.25, 'west': 10.0, 'south': 50.75, 'east': 10.5},
 'grid': {'type': 'provider_regular', 'resolution_degrees': 0.25, 'custom_regridding': False},
 'variables': [{'provider_name': '2m_temperature',
                'short_name': '2t',
                'param_id': 167,
                'canonical_name': 'temperature_2m_k',
                'unit': 'K',
                'provider_validity_offset_hours': 0},
               {'provider_name': 'surface_solar_radiation_downwards',
                'short_name': 'ssrd',
                'param_id': 169,
                'canonical_name': 'surface_solar_radiation_downwards_j_m2',
                'unit': 'J/m2',
                'provider_validity_offset_hours': 1},
               {'provider_name': '10m_u_component_of_wind',
                'short_name': '10u',
                'param_id': 165,
                'canonical_name': 'wind_u_10m_m_s',
                'unit': 'm/s',
                'provider_validity_offset_hours': 0},
               {'provider_name': '10m_v_component_of_wind',
                'short_name': '10v',
                'param_id': 166,
                'canonical_name': 'wind_v_10m_m_s',
                'unit': 'm/s',
                'provider_validity_offset_hours': 0},
               {'provider_name': '100m_u_component_of_wind',
                'short_name': '100u',
                'param_id': 228246,
                'canonical_name': 'wind_u_100m_m_s',
                'unit': 'm/s',
                'provider_validity_offset_hours': 0},
               {'provider_name': '100m_v_component_of_wind',
                'short_name': '100v',
                'param_id': 228247,
                'canonical_name': 'wind_v_100m_m_s',
                'unit': 'm/s',
                'provider_validity_offset_hours': 0}],
 'horizon': {'start': '2019-01-01T00:00:00Z',
             'end_inclusive': '2025-12-31T23:00:00Z',
             'expected_hours': 61368},
 'canonical_time': {'frequency_hours': 1,
                    'timezone': 'UTC',
                    'timestamp_semantics': 'interval_start'},
 'ssrd': {'accumulation_seconds': 3600,
          'final_provider_validity': '2026-01-01T00:00:00Z',
          'boundary_sha256': 'e2269e7d30c2ab42189d9299e3d418ae752475e1602353ab85d929f7a830a8b1'},
 'derivations': {'surface_solar_irradiance_w_m2': 'surface_solar_radiation_downwards_j_m2 / 3600',
                 'wind_speed_10m_m_s': 'sqrt(wind_u_10m_m_s^2 + wind_v_10m_m_s^2)',
                 'wind_speed_100m_m_s': 'sqrt(wind_u_100m_m_s^2 + wind_v_100m_m_s^2)'},
 'processing': {'interpolation': False,
                'clipping': False,
                'imputation': False,
                'spatial_aggregation': False},
 'availability': {'available_at': 'UNKNOWN',
                  'valid_time_is_available_at': False,
                  'causal_availability_claim': False}}


def _check(actual, expected, field="configuration"):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise WeatherConfigError(f"{field}: missing or unexpected fields")
        for key, value in expected.items():
            _check(actual[key], value, f"{field}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise WeatherConfigError(f"{field}: expected six unique variables")
        for index, (value, frozen) in enumerate(zip(actual, expected)):
            _check(value, frozen, f"{field}[{index}]")
    elif isinstance(expected, bool):
        if type(actual) is not bool or actual != expected:
            raise WeatherConfigError(f"{field}: invalid frozen boolean")
    elif isinstance(expected, (int, float)):
        if type(actual) not in (int, float) or actual != expected:
            raise WeatherConfigError(f"{field}: invalid frozen number")
    elif type(actual) is not type(expected) or actual != expected:
        raise WeatherConfigError(f"{field}: invalid frozen value")


def validate_weather_config(config):
    """Return an independent normalized dict, or raise WeatherConfigError."""
    if not isinstance(config, dict):
        raise WeatherConfigError("configuration: expected a mapping")
    normalized = deepcopy(config)
    variables = normalized.get("variables")
    if isinstance(variables, list):
        order = {v["provider_name"]: i for i, v in enumerate(_CONTRACT["variables"])}
        if any(not isinstance(v, dict) or not isinstance(v.get("provider_name"), str)
               or v["provider_name"] not in order for v in variables):
            raise WeatherConfigError("variables: invalid provider identity")
        normalized["variables"] = sorted(variables, key=lambda v: order[v["provider_name"]])
    _check(normalized, _CONTRACT)
    return normalized


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WeatherConfigError("configuration: duplicate mapping key")
        result[key] = value
    return result


def load_weather_config(path):
    """Load JSON-formatted YAML from an explicit path without provider access."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"),
                            object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError):
        raise WeatherConfigError("configuration: unreadable or invalid JSON/YAML subset") from None
    return validate_weather_config(config)
