"""
Density of liquid water at atmospheric pressure, for `conditions.water_density_gcc = "auto"`. Standard library only.

Kell (1975) formula for air-free water (G. S. Kell, J. Chem. Eng. Data 20, 97). It is accurate to a few parts in 1e5 between 0 and 150 degrees
Celsius; we accept 0 to 100 degrees Celsius only, because liquid water at one atmosphere does not exist above that. The TRIGA pool is
open to the atmosphere, so pressure and dissolved gases are not modelled.
"""
from __future__ import annotations

LOWEST_K = 273.15
HIGHEST_K = 373.15


def in_range(temperature_K):
    return LOWEST_K <= temperature_K <= HIGHEST_K


def density_g_cm3(temperature_K):
    """Density of liquid water in g/cm3 at `temperature_K` (273.15 to 373.15 K). Raises ValueError outside that range."""
    if not in_range(temperature_K):
        raise ValueError(f"liquid water at one atmosphere needs {LOWEST_K} K <= T <= {HIGHEST_K} K, got {temperature_K} K")
    t = temperature_K - 273.15
    kg_m3 = ((999.83952 + 16.945176 * t - 7.9870401e-3 * t ** 2 - 46.170461e-6 * t ** 3 + 105.56302e-9 * t ** 4
              - 280.54253e-12 * t ** 5) / (1.0 + 16.879850e-3 * t))
    return kg_m3 / 1000.0
