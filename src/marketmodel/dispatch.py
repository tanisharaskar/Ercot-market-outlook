"""Hourly economic dispatch and price formation for an energy-only market.

Price formation follows ERCOT's design in simplified form:

1. **Merit order.** Resources clear in offer order: nuclear (must-run), wind
   (negative offers because of the PTC), solar (zero), then thermal blocks
   sorted by marginal cost = heat rate x fuel price + VOM.
2. **System lambda.** The offer of the marginal resource sets the energy price.
3. **Scarcity adder (ORDC).** An Operating Reserve Demand Curve adder is
   applied as (VOLL - lambda) x LOLP(reserves), with the total capped at VOLL.
4. **Storage.** The battery fleet shaves daily net-load peaks and fills valleys
   (one cycle per day), which flattens prices and lowers scarcity.

All hours are solved in vectorized NumPy, so a full 8,760-hour year solves in
milliseconds and a 20-year, 5-scenario forecast in seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt

import numpy as np
import pandas as pd

from .assumptions import (
    GAS_MONTHLY_SHAPE,
    MIN_CONTINGENCY_RESERVE_MW,
    NUCLEAR_OFFER,
    ORDC_SIGMA_MW,
    PRICE_FLOOR,
    SOLAR_OFFER,
    VOLL,
    WIND_OFFER,
    YearInputs,
    thermal_classes,
)
from .profiles import WeatherYear, scale_load

CO2_TONS_PER_MMBTU = {"gas": 0.0531, "coal": 0.0953}
STORAGE_RTE = 0.86  # round-trip efficiency
STORAGE_USABLE = 0.90  # share of nameplate energy cycled per day (SOC limits, reserves)
NUCLEAR_AVAILABILITY = 0.93
MUST_RUN_THERMAL_MW = 9_000.0  # Gulf Coast industrial cogeneration and minimum-generation commitments


@dataclass(frozen=True)
class SupplyBlock:
    tech: str
    capacity_mw: float
    heat_rate: float
    fuel: str
    vom: float

    def marginal_cost(self, gas: float, coal: float) -> float:
        fuel_price = gas if self.fuel == "gas" else coal
        return self.heat_rate * fuel_price + self.vom


def build_blocks(y: YearInputs) -> list[SupplyBlock]:
    """Split each thermal class into equal-size blocks spanning its heat-rate range."""
    blocks: list[SupplyBlock] = []
    for cls in thermal_classes(y):
        if cls.capacity_mw <= 0:
            continue
        for hr in np.linspace(cls.heat_rate_low, cls.heat_rate_high, cls.blocks):
            blocks.append(SupplyBlock(cls.name, cls.capacity_mw / cls.blocks, float(hr), cls.fuel, cls.vom))
    return blocks


def supply_curve(y: YearInputs, month: int = 7) -> pd.DataFrame:
    """Thermal supply curve for a month (for charts and the Excel workbook)."""
    gas = y.gas_price * GAS_MONTHLY_SHAPE[month - 1]
    rows = [
        dict(tech=b.tech, capacity_mw=b.capacity_mw, heat_rate=b.heat_rate, marginal_cost=b.marginal_cost(gas, y.coal_price))
        for b in build_blocks(y)
    ]
    df = pd.DataFrame(rows).sort_values("marginal_cost", kind="stable").reset_index(drop=True)
    df["cumulative_mw"] = df["capacity_mw"].cumsum()
    return df


def thermal_availability(weather: WeatherYear, seed: int = 11) -> np.ndarray:
    """Hourly share of thermal capacity available after planned and forced outages."""
    rng = np.random.default_rng(seed)
    month = weather.timestamps.month.to_numpy()
    planned = np.select(
        [np.isin(month, [3, 4, 10, 11]), np.isin(month, [6, 7, 8, 9]), np.isin(month, [5])],
        [0.80, 0.94, 0.86],
        default=0.89,
    )
    daily_forced = np.repeat(rng.normal(0.0, 0.015, 365), 24)
    avail = planned + daily_forced
    # Extreme cold drives correlated forced outages (freeze-offs, gas supply), as in Winter Storm Uri.
    temp = weather.temperature_f
    if np.isfinite(temp).all():
        avail -= np.clip((25 - temp) * 0.012, 0, 0.25)
    return np.clip(avail, 0.5, 0.97)


def _norm_sf(x: np.ndarray) -> np.ndarray:
    v_erf = np.vectorize(erf)
    return 0.5 * (1 - v_erf(x / sqrt(2)))


def ordc_adder(reserves_mw: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """ORDC-style scarcity adder: (VOLL - lambda) x P(reserve shortfall)."""
    lolp = np.where(
        reserves_mw <= MIN_CONTINGENCY_RESERVE_MW,
        1.0,
        _norm_sf((reserves_mw - MIN_CONTINGENCY_RESERVE_MW) / ORDC_SIGMA_MW),
    )
    return np.clip((VOLL - lam) * lolp, 0, None)


def storage_schedule(net_load: np.ndarray, power_mw: float, energy_mwh: float) -> tuple[np.ndarray, np.ndarray]:
    """Peak-shave / valley-fill one cycle per day. Returns (charge_mw, discharge_mw)."""
    nl = net_load.reshape(-1, 24)
    e_dis = energy_mwh * STORAGE_USABLE * sqrt(STORAGE_RTE)
    e_chg = energy_mwh * STORAGE_USABLE / sqrt(STORAGE_RTE)

    def fill(values: np.ndarray, target: float, direction: int) -> np.ndarray:
        # Bisection on a per-day threshold so that sum(min(|values - thr|+, P)) == target.
        lo = values.min(axis=1, keepdims=True) - power_mw
        hi = values.max(axis=1, keepdims=True) + power_mw
        for _ in range(40):
            mid = (lo + hi) / 2
            gap = (values - mid) if direction > 0 else (mid - values)
            got = np.clip(gap, 0, power_mw).sum(axis=1, keepdims=True)
            if direction > 0:  # discharge: lower threshold -> more energy
                hi = np.where(got > target, hi, mid)
                lo = np.where(got > target, mid, lo)
            else:  # charge: higher threshold -> more energy
                hi = np.where(got > target, mid, hi)
                lo = np.where(got > target, lo, mid)
        thr = (lo + hi) / 2
        gap = (values - thr) if direction > 0 else (thr - values)
        return np.clip(gap, 0, power_mw)

    if power_mw <= 0 or energy_mwh <= 0:
        z = np.zeros_like(net_load)
        return z, z
    discharge = fill(nl, e_dis, +1)
    charge = fill(nl, e_chg, -1)
    # Skip days where peak and valley are too close to be worth cycling.
    flat = (nl.max(axis=1) - nl.min(axis=1)) < 2 * power_mw * 0.25
    discharge[flat] = 0
    charge[flat] = 0
    return charge.ravel(), discharge.ravel()


def simulate_year(y: YearInputs, weather: WeatherYear, avail: np.ndarray | None = None) -> pd.DataFrame:
    """Solve all 8,760 hours of a market year. Returns an hourly results frame."""
    load = scale_load(weather.load_shape, y.annual_energy_twh, y.peak_load_gw)
    wind_av = weather.wind_cf * y.wind_gw * 1000
    solar_av = weather.solar_cf * y.solar_gw * 1000
    nuclear = np.full_like(load, y.nuclear_gw * 1000 * NUCLEAR_AVAILABILITY)
    avail = thermal_availability(weather) if avail is None else avail
    month = weather.timestamps.month.to_numpy()

    # Storage acts on net load (load less wind, solar and nuclear).
    power = y.storage_gw * 1000
    charge, discharge = storage_schedule(load - wind_av - solar_av - nuclear, power, power * y.storage_hours)
    served = load + charge - discharge

    must_run = MUST_RUN_THERMAL_MW * avail
    residual = served - nuclear - must_run  # after inflexible nuclear and must-run thermal
    wind_gen = np.clip(residual, 0, wind_av)
    solar_gen = np.clip(residual - wind_gen, 0, solar_av)
    flexible_need = np.clip(residual - wind_gen - solar_gen, 0, None)
    thermal_need = flexible_need + must_run

    blocks = build_blocks(y)
    cap = np.array([b.capacity_mw for b in blocks])
    techs = np.array([b.tech for b in blocks])
    fuels = np.array([b.fuel for b in blocks])
    hrs = np.array([b.heat_rate for b in blocks])
    vom = np.array([b.vom for b in blocks])

    # Per-month marginal costs (gas has a seasonal shape).
    gas_m = y.gas_price * np.array(GAS_MONTHLY_SHAPE)[month - 1]
    fuel_price = np.where(fuels[None, :] == "gas", gas_m[:, None], y.coal_price)
    mc = hrs[None, :] * fuel_price + vom[None, :]  # (8760, n_blocks)
    order = np.argsort(mc, axis=1, kind="stable")
    mc_sorted = np.take_along_axis(mc, order, axis=1)
    cap_sorted = cap[order] * avail[:, None]
    cum = np.cumsum(cap_sorted, axis=1)
    total_thermal = cum[:, -1]

    # Block dispatch: fill the sorted stack up to the thermal requirement.
    before = cum - cap_sorted
    disp_sorted = np.clip(thermal_need[:, None] - before, 0, cap_sorted)
    marginal_idx = np.minimum((cum < thermal_need[:, None] - 1e-6).sum(axis=1), len(blocks) - 1)
    lam_thermal = mc_sorted[np.arange(len(load)), marginal_idx]

    unserved = np.clip(thermal_need - total_thermal, 0, None)
    lam = np.select(
        [flexible_need > 0, residual > wind_av, residual > 0],
        [lam_thermal, SOLAR_OFFER, WIND_OFFER],
        default=max(PRICE_FLOOR, NUCLEAR_OFFER - 20),
    )
    # Storage headroom is excluded: the fleet is energy-limited and already scheduled against the daily peak.
    reserves = total_thermal - np.minimum(thermal_need, total_thermal)
    adder = np.where(flexible_need > 0, ordc_adder(reserves, lam), 0.0)
    price = np.clip(np.where(unserved > 0, VOLL, lam + adder), PRICE_FLOOR, VOLL)

    # Map block dispatch back to technologies and compute fuel burn / emissions.
    disp = np.empty_like(disp_sorted)
    np.put_along_axis(disp, order, disp_sorted, axis=1)
    out = {
        "timestamp": weather.timestamps,
        "load_mw": load,
        "price": price,
        "system_lambda": lam,
        "ordc_adder": adder,
        "nuclear_mw": nuclear,
        "wind_mw": wind_gen,
        "solar_mw": solar_gen,
        "wind_curtailed_mw": wind_av - wind_gen,
        "solar_curtailed_mw": solar_av - solar_gen,
        "storage_charge_mw": charge,
        "storage_discharge_mw": discharge,
        "reserves_mw": reserves,
        "unserved_mw": unserved,
        "solar_cf": weather.solar_cf,
    }
    for tech in dict.fromkeys(techs):
        out[f"{tech.lower().replace(' ', '_')}_mw"] = disp[:, techs == tech].sum(axis=1)
    out["co2_tons"] = (disp * hrs[None, :] * np.vectorize(CO2_TONS_PER_MMBTU.get)(fuels)[None, :]).sum(axis=1)
    return pd.DataFrame(out)


def summarize_year(h: pd.DataFrame, year: int) -> dict:
    """Annual market statistics from an hourly results frame."""
    hour = h["timestamp"].dt.hour
    dow = h["timestamp"].dt.day_of_week
    on_peak = (hour >= 6) & (hour < 22) & (dow < 5)  # ERCOT 5x16 (HE7-HE22)
    gen_cols = ["nuclear_mw", "wind_mw", "solar_mw", "coal_mw", "gas_cc_mw", "gas_steam_mw", "gas_ct_mw", "storage_discharge_mw"]
    gen = {c.replace("_mw", "_twh"): (h[c].sum() / 1e6 if c in h else 0.0) for c in gen_cols}
    load_wtd = float((h["price"] * h["load_mw"]).sum() / h["load_mw"].sum())
    solar_w = h["solar_mw"]
    return dict(
        year=year,
        avg_price=float(h["price"].mean()),
        load_weighted_price=load_wtd,
        on_peak_price=float(h.loc[on_peak, "price"].mean()),
        off_peak_price=float(h.loc[~on_peak, "price"].mean()),
        solar_capture_price=float((h["price"] * solar_w).sum() / max(solar_w.sum(), 1)),
        ordc_share=float(h["ordc_adder"].sum() / max(h["price"].clip(lower=0).sum(), 1)),
        scarcity_hours=int((h["price"] >= 1000).sum()),
        negative_hours=int((h["price"] < 0).sum()),
        peak_load_gw=float(h["load_mw"].max() / 1000),
        energy_twh=float(h["load_mw"].sum() / 1e6),
        curtailment_twh=float((h["wind_curtailed_mw"] + h["solar_curtailed_mw"]).sum() / 1e6),
        unserved_gwh=float(h["unserved_mw"].sum() / 1e3),
        co2_mmt=float(h["co2_tons"].sum() / 1e6),
        renewable_share=float((h["wind_mw"] + h["solar_mw"]).sum() / h["load_mw"].sum()),
        **gen,
    )
