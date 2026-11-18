"""Multi-year, multi-scenario price forecast with endogenous capacity entry.

ERCOT is an energy-only market: new capacity is built when scarcity revenue
justifies it. The model mimics a long-term capacity-expansion step (as in
Aurora's LT logic) with a transparent economic rule. Each year, a hypothetical
new gas combustion turbine is dispatched against the simulated prices; while
its energy margin exceeds its net Cost of New Entry (CONE), or expected
unserved energy breaches a reliability threshold, 500 MW of new peaking
capacity is added. Additions persist into later years, so prices converge
toward the long-run cost of new entry rather than drifting unrealistically.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .assumptions import END_YEAR, SCENARIOS, START_YEAR, Scenario, year_inputs
from .dispatch import simulate_year, summarize_year, thermal_availability
from .profiles import WeatherYear, synthetic_weather_year

NET_CONE_PER_KW_YR = 105.0  # $/kW-yr, levelized new CT cost less ancillary revenue (nominal 2026$)
CONE_ESCALATION = 0.02
NEW_CT_HEAT_RATE = 9.4
NEW_CT_VOM = 5.0
RELIABILITY_EUE_GWH = 2.0  # max expected unserved energy per year before entry is triggered
ENTRY_STEP_GW = 0.5
MAX_ENTRY_STEPS = 80


def run_scenario(
    scenario: Scenario,
    weather: WeatherYear | None = None,
    years: range | None = None,
    keep_hourly: tuple[int, ...] = (),
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    """Simulate every year of a scenario. Returns (annual summary, selected hourly frames)."""
    weather = weather or synthetic_weather_year()
    years = years or range(START_YEAR, END_YEAR + 1)
    avail = thermal_availability(weather)
    built_gw = 0.0
    rows, hourly = [], {}
    for yr in years:
        base = year_inputs(scenario, yr)
        cap_steps = scenario.extra.get("max_entry_gw_per_yr")
        steps = MAX_ENTRY_STEPS if cap_steps is None else int(cap_steps / ENTRY_STEP_GW)
        for step in range(steps + 1):
            y = replace(base, gas_ct_gw=base.gas_ct_gw + built_gw)
            h = simulate_year(y, weather, avail)
            margin = new_ct_margin(h, y.gas_price)
            cone = NET_CONE_PER_KW_YR * (1 + CONE_ESCALATION) ** (yr - START_YEAR)
            if h["unserved_mw"].sum() / 1e3 <= RELIABILITY_EUE_GWH and margin <= cone:
                break
            if step == steps:
                break  # entry constrained: the market stays short this year
            built_gw += ENTRY_STEP_GW
        s = summarize_year(h, yr)
        s.update(
            scenario=scenario.key,
            gas_price=y.gas_price,
            wind_gw=y.wind_gw,
            solar_gw=y.solar_gw,
            storage_gw=y.storage_gw,
            thermal_gw=y.gas_cc_gw + y.gas_ct_gw + y.gas_steam_gw + y.coal_gw,
            economic_entry_gw=built_gw,
            new_ct_margin=margin,
            net_cone=cone,
            reserve_margin=(
                y.gas_cc_gw + y.gas_ct_gw + y.gas_steam_gw + y.coal_gw + y.nuclear_gw + y.storage_gw
                + 0.20 * y.wind_gw + 0.10 * y.solar_gw
            ) / y.peak_load_gw - 1,
        )
        rows.append(s)
        if yr in keep_hourly:
            hourly[yr] = h
    return pd.DataFrame(rows), hourly


def new_ct_margin(h: pd.DataFrame, gas_price: float) -> float:
    """Energy margin ($/kW-yr) a new gas CT would earn against simulated hourly prices."""
    from .assumptions import GAS_MONTHLY_SHAPE

    month = h["timestamp"].dt.month.to_numpy() - 1
    mc = NEW_CT_HEAT_RATE * gas_price * np.array(GAS_MONTHLY_SHAPE)[month] + NEW_CT_VOM
    return float(np.clip(h["price"].to_numpy() - mc, 0, None).sum() / 1000)


def run_all(
    scenarios: dict[str, Scenario] | None = None,
    weather: WeatherYear | None = None,
    keep_hourly: tuple[int, ...] = (2026, 2030, 2035),
) -> tuple[pd.DataFrame, dict[str, dict[int, pd.DataFrame]]]:
    """Run every scenario. Returns (long annual table, {scenario: {year: hourly}})."""
    scenarios = scenarios or SCENARIOS
    weather = weather or synthetic_weather_year()
    frames, hourly = [], {}
    for key, sc in scenarios.items():
        annual, h = run_scenario(sc, weather, keep_hourly=keep_hourly)
        frames.append(annual)
        hourly[key] = h
    return pd.concat(frames, ignore_index=True), hourly


def hourly_prices(scenario: Scenario, weather: WeatherYear | None = None) -> dict[int, pd.DataFrame]:
    """Hourly price and solar shape for every forecast year (used by asset valuation)."""
    weather = weather or synthetic_weather_year()
    years = tuple(range(START_YEAR, END_YEAR + 1))
    _, h = run_scenario(scenario, weather, keep_hourly=years)
    return {yr: df[["timestamp", "price", "solar_cf"]] for yr, df in h.items()}
