"""Frozen weather configuration; no acquisition or processing side effects."""

from .config import WeatherConfigError, load_weather_config, validate_weather_config

__all__ = ["WeatherConfigError", "load_weather_config", "validate_weather_config"]

from .era5 import (build_monthly_request, build_boundary_request, dry_run,
                   validate_grib, build_provenance, execute_request)

__all__ += ["build_monthly_request", "build_boundary_request", "dry_run",
            "validate_grib", "build_provenance", "execute_request"]

from .harmonise import canonical_index, harmonise_weather

__all__ += ["canonical_index", "harmonise_weather"]

from .structural import structural_audit
from .spatial import spatial_audit

__all__ += ["structural_audit", "spatial_audit"]
