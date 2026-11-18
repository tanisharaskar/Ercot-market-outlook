"""Hourly weather-driven profiles: load, wind and solar.

The model runs on a single *normalized weather year* (8,760 hours) that is
re-scaled to each forecast year's energy, peak and installed capacity. This is
the standard approach in production-cost studies: weather shape stays fixed so
that differences between years and scenarios come from fundamentals, not noise.

The default profiles are synthetic but calibrated to ERCOT's shape (summer
afternoon peak, winter morning secondary peak, nighttime / spring wind, ~25%
solar capacity factor). ``load_profiles_csv`` swaps in real hourly data, for
example an ERCOT hourly load / generation-by-fuel export.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .assumptions import HOURS

LATITUDE_DEG = 31.0  # central Texas
WIND_TARGET_CF = 0.36
SOLAR_TARGET_CF = 0.25


@dataclass(frozen=True)
class WeatherYear:
    """Normalized hourly shapes for one weather year."""

    timestamps: pd.DatetimeIndex
    load_shape: np.ndarray  # dimensionless, mean 1.0
    wind_cf: np.ndarray  # capacity factor 0..1
    solar_cf: np.ndarray  # capacity factor 0..1
    temperature_f: np.ndarray


def _index(year: int = 2026) -> pd.DatetimeIndex:
    idx = pd.date_range(f"{year}-01-01", periods=HOURS, freq="h")
    return idx


def _ar1(rng: np.random.Generator, n: int, phi: float, sigma: float) -> np.ndarray:
    eps = rng.normal(0.0, sigma, n)
    out = np.empty(n)
    out[0] = eps[0]
    for i in range(1, n):
        out[i] = phi * out[i - 1] + eps[i]
    return out


def synthetic_weather_year(seed: int = 7) -> WeatherYear:
    """Generate a reproducible ERCOT-like weather year."""
    rng = np.random.default_rng(seed)
    idx = _index()
    doy = idx.dayofyear.to_numpy()
    hour = idx.hour.to_numpy()
    dow = idx.day_of_week.to_numpy()

    # --- temperature (deg F): seasonal + diurnal + persistent weather anomalies
    daily_anom = _ar1(rng, 365, 0.8, 4.0)
    seasonal = 68 + 17 * np.cos(2 * np.pi * (doy - 205) / 365)
    diurnal = 9 * np.sin(2 * np.pi * (hour - 10) / 24)
    temp = seasonal + diurnal + daily_anom[doy - 1]
    # One winter cold snap and one summer heat dome stress the system (Uri / 2023-style).
    temp[(doy >= 34) & (doy <= 37)] -= 13
    temp[(doy >= 222) & (doy <= 230)] += 6

    # --- load: cooling + heating degree effects, activity shape, weekends
    cdd = np.clip(temp - 65, 0, None)
    hdd = np.clip(55 - temp, 0, None)
    activity = 1.0 + 0.10 * np.sin(2 * np.pi * (hour - 9) / 24) - 0.06 * (hour < 6)
    weekend = np.where(dow >= 5, 0.95, 1.0)
    load = (100 + 1.75 * cdd**1.12 + 1.6 * hdd**1.05) * activity * weekend
    load *= 1 + _ar1(rng, HOURS, 0.95, 0.004)
    load_shape = load / load.mean()

    # --- wind: spring maximum, nighttime low-level jet, multi-day weather regimes
    seasonal_w = 1 + 0.22 * np.cos(2 * np.pi * (doy - 110) / 365) + 0.10 * np.cos(4 * np.pi * (doy - 110) / 365)
    diurnal_w = 1 + 0.25 * np.cos(2 * np.pi * (hour - 2) / 24)
    regime = np.exp(_ar1(rng, HOURS, 0.985, 0.09))
    wind = seasonal_w * diurnal_w * regime
    # wind lulls during heat domes and cold snaps
    wind[(doy >= 222) & (doy <= 230)] *= 0.45
    wind[(doy >= 34) & (doy <= 37)] *= 0.55
    wind_cf = _scale_cf(wind, WIND_TARGET_CF, cap=0.92)

    # --- solar: clear-sky elevation model x daily cloudiness
    decl = 23.44 * np.sin(np.radians(360 * (284 + doy) / 365))
    hour_angle = 15 * (hour + 0.5 - 13.0)  # solar noon ~1 PM CST
    lat = np.radians(LATITUDE_DEG)
    elev = np.arcsin(
        np.sin(lat) * np.sin(np.radians(decl)) + np.cos(lat) * np.cos(np.radians(decl)) * np.cos(np.radians(hour_angle))
    )
    clear = np.clip(np.sin(elev), 0, None) ** 1.15
    cloud_daily = rng.beta(6, 1.6, 365)
    solar = clear * cloud_daily[doy - 1]
    solar_cf = _scale_cf(solar, SOLAR_TARGET_CF, cap=0.88)

    return WeatherYear(idx, load_shape, wind_cf, solar_cf, temp)


def _scale_cf(raw: np.ndarray, target: float, cap: float) -> np.ndarray:
    """Scale a raw shape to a target annual capacity factor while respecting a hard cap."""
    lo, hi = 0.0, 10.0 / max(raw.mean(), 1e-9)
    for _ in range(60):
        mid = (lo + hi) / 2
        if np.clip(raw * mid, 0, cap).mean() < target:
            lo = mid
        else:
            hi = mid
    return np.clip(raw * (lo + hi) / 2, 0, cap)


def scale_load(shape: np.ndarray, energy_twh: float, peak_gw: float) -> np.ndarray:
    """Affine-map a load shape so it hits the target annual energy and peak exactly (MW)."""
    mean_mw = energy_twh * 1e6 / len(shape)
    peak_mw = peak_gw * 1000
    s_mean, s_max = shape.mean(), shape.max()
    a = (peak_mw - mean_mw) / (s_max - s_mean)
    b = mean_mw - a * s_mean
    return a * shape + b


def load_profiles_csv(path: str | Path) -> WeatherYear:
    """Load a real hourly weather year.

    Expected columns: ``timestamp, load_mw, wind_mw, solar_mw, wind_capacity_mw,
    solar_capacity_mw`` (8,760 rows). Capacity columns convert MW into capacity
    factors so the year can be re-scaled to any future build-out.
    """
    df = pd.read_csv(path, parse_dates=["timestamp"]).sort_values("timestamp").head(HOURS)
    if len(df) != HOURS:
        raise ValueError(f"expected {HOURS} hourly rows, got {len(df)}")
    load = df["load_mw"].to_numpy(float)
    return WeatherYear(
        timestamps=pd.DatetimeIndex(df["timestamp"]),
        load_shape=load / load.mean(),
        wind_cf=np.clip(df["wind_mw"] / df["wind_capacity_mw"], 0, 1).to_numpy(float),
        solar_cf=np.clip(df["solar_mw"] / df["solar_capacity_mw"], 0, 1).to_numpy(float),
        temperature_f=np.full(HOURS, np.nan),
    )
