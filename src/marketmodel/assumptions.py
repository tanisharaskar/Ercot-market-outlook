"""Market and scenario assumptions for the ERCOT outlook model.

Every number here is an explicit, documented input. Starting-point values are
rounded approximations of publicly reported ERCOT / EIA figures (see the
``SOURCES`` notes) and are meant to be replaced with licensed or official data
in a client engagement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

START_YEAR = 2026
END_YEAR = 2045
HOURS = 8760

# --------------------------------------------------------------------------------------
# Market rules (ERCOT energy-only design)
# --------------------------------------------------------------------------------------
VOLL = 5_000.0  # $/MWh, ERCOT high system-wide offer cap / value of lost load
PRICE_FLOOR = -250.0  # $/MWh, ERCOT offer floor
MIN_CONTINGENCY_RESERVE_MW = 3_000.0  # ORDC X: reserves below this level => price at VOLL
ORDC_SIGMA_MW = 1_800.0  # std. dev. of reserve forecast error used by the ORDC adder
WIND_OFFER = -22.0  # $/MWh, wind offers below zero because of the production tax credit
SOLAR_OFFER = 0.0  # $/MWh, solar (ITC-financed) curtails at zero
NUCLEAR_OFFER = -5.0  # $/MWh, inflexible must-run

SOURCES = {
    "load": "ERCOT 2024 energy ~486 TWh, 2024 peak ~85.6 GW (ERCOT demand records, EIA-930).",
    "fleet": "ERCOT Capacity, Demand and Reserves (CDR) and EIA-860M installed capacity, rounded.",
    "gas": "Henry Hub / Houston Ship Channel strip, rounded; NYMEX-style contango assumed.",
    "rules": "ERCOT Nodal Protocols: $5,000/MWh HCAP, ORDC with 3,000 MW minimum contingency level.",
}


@dataclass(frozen=True)
class ThermalClass:
    """A thermal technology, split into heat-rate blocks to form a smooth supply curve."""

    name: str
    capacity_mw: float
    heat_rate_low: float  # MMBtu/MWh, most efficient unit in the class
    heat_rate_high: float  # MMBtu/MWh, least efficient unit in the class
    fuel: str  # "gas" or "coal"
    vom: float  # $/MWh variable O&M
    blocks: int = 8


@dataclass(frozen=True)
class YearInputs:
    """All exogenous inputs needed to simulate one market year."""

    year: int
    annual_energy_twh: float
    peak_load_gw: float
    wind_gw: float
    solar_gw: float
    storage_gw: float
    storage_hours: float
    nuclear_gw: float
    gas_cc_gw: float
    gas_ct_gw: float
    gas_steam_gw: float
    coal_gw: float
    gas_price: float  # $/MMBtu annual average
    coal_price: float  # $/MMBtu delivered


@dataclass(frozen=True)
class Scenario:
    """A coherent set of trajectories for load, fuel and the resource mix."""

    key: str
    name: str
    description: str
    load_growth_early: float = 0.035  # annual energy growth through 2030
    load_growth_late: float = 0.015  # annual energy growth after 2030
    large_load_gw_by_2030: float = 0.0  # incremental flat data-center / industrial load
    gas_multiplier: float = 1.0
    solar_adds_gw: float = 4.0
    wind_adds_gw: float = 0.8
    storage_adds_gw: float = 2.5
    gas_adds_gw: float = 1.2
    coal_retire_gw: float = 0.9  # per year starting 2028
    extra: dict = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "base": Scenario(
        key="base",
        name="Base Case",
        description="Moderate load growth, forward-curve gas, continued solar and storage build-out.",
    ),
    "high_gas": Scenario(
        key="high_gas",
        name="High Gas",
        description="Gas prices 40% above base (LNG export pull, supply discipline).",
        gas_multiplier=1.40,
    ),
    "low_gas": Scenario(
        key="low_gas",
        name="Low Gas",
        description="Gas prices 30% below base (associated-gas growth in the Permian).",
        gas_multiplier=0.70,
    ),
    "data_center": Scenario(
        key="data_center",
        name="Data Center Boom",
        description="~12 GW of incremental 24x7 large load (AI data centers, electrification) by 2030.",
        large_load_gw_by_2030=12.0,
    ),
    "dc_constrained": Scenario(
        key="dc_constrained",
        name="Data Center, Slow Build",
        description="Same data center load as the boom case, but interconnection and supply-chain limits cap new gas entry at 2 GW/yr.",
        large_load_gw_by_2030=12.0,
        extra={"max_entry_gw_per_yr": 2.0},
    ),
    "accelerated_re": Scenario(
        key="accelerated_re",
        name="Accelerated Renewables",
        description="Faster solar, wind and storage additions; faster coal retirements.",
        solar_adds_gw=7.0,
        wind_adds_gw=1.5,
        storage_adds_gw=4.5,
        coal_retire_gw=1.5,
    ),
}

# 2026 starting point (rounded public figures)
BASE_2026 = dict(
    annual_energy_twh=505.0,
    peak_load_gw=88.0,
    wind_gw=41.0,
    solar_gw=34.0,
    storage_gw=14.0,
    storage_hours=2.0,
    nuclear_gw=4.9,
    gas_cc_gw=31.0,
    gas_ct_gw=9.5,
    gas_steam_gw=11.5,
    coal_gw=13.5,
)

# Henry Hub-style nominal gas price path ($/MMBtu); escalates 2.5%/yr after the strip.
GAS_STRIP = {2026: 3.70, 2027: 3.85, 2028: 3.80, 2029: 3.85, 2030: 3.95}
GAS_ESCALATION = 0.025
COAL_PRICE_2026 = 2.10
COAL_ESCALATION = 0.015

# Monthly gas shape (winter premium), normalized to average 1.0
GAS_MONTHLY_SHAPE = [1.12, 1.08, 0.98, 0.93, 0.92, 0.96, 1.00, 1.01, 0.96, 0.95, 1.01, 1.08]


def gas_price(year: int) -> float:
    if year in GAS_STRIP:
        return GAS_STRIP[year]
    last = max(GAS_STRIP)
    return GAS_STRIP[last] * (1 + GAS_ESCALATION) ** (year - last)


def year_inputs(scenario: Scenario, year: int) -> YearInputs:
    """Build the exogenous inputs for ``year`` under ``scenario``."""
    n = year - START_YEAR
    early = min(n, 2030 - START_YEAR)
    late = max(0, n - (2030 - START_YEAR))
    growth = (1 + scenario.load_growth_early) ** early * (1 + scenario.load_growth_late) ** late

    # Large flat loads ramp linearly to their 2030 level and then hold.
    large_gw = scenario.large_load_gw_by_2030 * min(1.0, n / (2030 - START_YEAR))
    energy = BASE_2026["annual_energy_twh"] * growth + large_gw * HOURS / 1000
    peak = BASE_2026["peak_load_gw"] * growth + large_gw

    coal = max(0.0, BASE_2026["coal_gw"] - scenario.coal_retire_gw * max(0, year - 2027))
    # Storage duration lengthens as 4-hour projects dominate new builds.
    storage_hours = min(3.5, BASE_2026["storage_hours"] + 0.1 * n)
    return YearInputs(
        year=year,
        annual_energy_twh=energy,
        peak_load_gw=peak,
        wind_gw=BASE_2026["wind_gw"] + scenario.wind_adds_gw * n,
        solar_gw=BASE_2026["solar_gw"] + scenario.solar_adds_gw * n,
        storage_gw=BASE_2026["storage_gw"] + scenario.storage_adds_gw * n,
        storage_hours=storage_hours,
        nuclear_gw=BASE_2026["nuclear_gw"],
        gas_cc_gw=BASE_2026["gas_cc_gw"] + scenario.gas_adds_gw * n * 0.6,
        gas_ct_gw=BASE_2026["gas_ct_gw"] + scenario.gas_adds_gw * n * 0.4,
        gas_steam_gw=max(6.0, BASE_2026["gas_steam_gw"] - 0.25 * max(0, year - 2030)),
        coal_gw=coal,
        gas_price=gas_price(year) * scenario.gas_multiplier,
        coal_price=COAL_PRICE_2026 * (1 + COAL_ESCALATION) ** n,
    )


def thermal_classes(y: YearInputs) -> list[ThermalClass]:
    return [
        ThermalClass("Coal", y.coal_gw * 1000, 10.0, 11.2, "coal", 4.5, 6),
        ThermalClass("Gas CC", y.gas_cc_gw * 1000, 6.4, 8.0, "gas", 2.5, 12),
        ThermalClass("Gas Steam", y.gas_steam_gw * 1000, 10.0, 12.5, "gas", 4.0, 8),
        ThermalClass("Gas CT", y.gas_ct_gw * 1000, 9.4, 12.0, "gas", 5.0, 8),
    ]


def with_overrides(scenario: Scenario, **kwargs) -> Scenario:
    return replace(scenario, **kwargs)
