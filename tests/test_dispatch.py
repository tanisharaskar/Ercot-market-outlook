import numpy as np
import pytest

from marketmodel.assumptions import SCENARIOS, VOLL, year_inputs
from marketmodel.dispatch import (
    MIN_CONTINGENCY_RESERVE_MW,
    build_blocks,
    ordc_adder,
    simulate_year,
    storage_schedule,
    summarize_year,
    supply_curve,
)
from marketmodel.profiles import scale_load, synthetic_weather_year


@pytest.fixture(scope="module")
def weather():
    return synthetic_weather_year()


@pytest.fixture(scope="module")
def base_2026(weather):
    return simulate_year(year_inputs(SCENARIOS["base"], 2026), weather)


def test_weather_year_is_reproducible_and_calibrated(weather):
    again = synthetic_weather_year()
    assert np.allclose(weather.load_shape, again.load_shape)
    assert len(weather.load_shape) == 8760
    assert weather.wind_cf.mean() == pytest.approx(0.36, abs=0.01)
    assert weather.solar_cf.mean() == pytest.approx(0.25, abs=0.01)
    assert weather.solar_cf[weather.timestamps.hour == 2].max() == 0  # no sun at 2 AM
    # ERCOT peaks in summer
    assert weather.timestamps[weather.load_shape.argmax()].month in (6, 7, 8, 9)


def test_scale_load_hits_energy_and_peak_exactly(weather):
    load = scale_load(weather.load_shape, 500.0, 90.0)
    assert load.sum() / 1e6 == pytest.approx(500.0)
    assert load.max() / 1000 == pytest.approx(90.0)


def test_supply_curve_is_monotonic():
    curve = supply_curve(year_inputs(SCENARIOS["base"], 2026))
    assert curve.marginal_cost.is_monotonic_increasing
    total = sum(b.capacity_mw for b in build_blocks(year_inputs(SCENARIOS["base"], 2026)))
    assert curve.cumulative_mw.iloc[-1] == pytest.approx(total)


def test_ordc_adder_behaviour():
    lam = np.array([30.0, 30.0, 30.0, 30.0])
    res = np.array([MIN_CONTINGENCY_RESERVE_MW - 1, 4_000, 8_000, 30_000])
    adder = ordc_adder(res, lam)
    assert adder[0] == pytest.approx(VOLL - 30)  # at or below the minimum level: price goes to the cap
    assert adder[0] > adder[1] > adder[2] > adder[3] >= 0  # monotone in reserves
    assert adder[3] < 0.01


def test_storage_schedule_respects_limits():
    rng = np.random.default_rng(0)
    nl = 40_000 + 10_000 * np.sin(np.linspace(0, 2 * np.pi * 30, 24 * 30)) + rng.normal(0, 500, 24 * 30)
    p, e = 5_000.0, 20_000.0
    ch, dis = storage_schedule(nl, p, e)
    assert ch.max() <= p + 1e-6 and dis.max() <= p + 1e-6
    assert (ch >= 0).all() and (dis >= 0).all()
    daily_dis = dis.reshape(-1, 24).sum(axis=1)
    daily_ch = ch.reshape(-1, 24).sum(axis=1)
    assert (daily_dis <= e + 1e-3).all()
    # discharge happens at higher net load than charging, and energy out < energy in (losses)
    assert nl[dis > 0].mean() > nl[ch > 0].mean()
    assert daily_dis.sum() < daily_ch.sum()


def test_energy_balance(base_2026):
    h = base_2026
    supply = (
        h.nuclear_mw + h.wind_mw + h.solar_mw + h.coal_mw + h.gas_cc_mw + h.gas_steam_mw + h.gas_ct_mw
        + h.storage_discharge_mw + h.unserved_mw
    )
    demand = h.load_mw + h.storage_charge_mw
    assert np.allclose(supply, demand, atol=1e-3)


def test_prices_within_market_bounds(base_2026):
    assert base_2026.price.max() <= VOLL
    assert base_2026.price.min() >= -250
    assert (base_2026.wind_curtailed_mw >= -1e-9).all()


def test_calibration_2026_is_plausible(base_2026):
    s = summarize_year(base_2026, 2026)
    # Recent ERCOT hub averages have ranged roughly $25-$75/MWh depending on gas and weather.
    assert 25 < s["avg_price"] < 75
    assert s["solar_capture_price"] < s["avg_price"]  # solar cannibalization
    assert 0.3 < s["renewable_share"] < 0.5


def test_higher_gas_means_higher_prices(weather):
    base = summarize_year(simulate_year(year_inputs(SCENARIOS["base"], 2030), weather), 2030)
    high = summarize_year(simulate_year(year_inputs(SCENARIOS["high_gas"], 2030), weather), 2030)
    assert high["avg_price"] > base["avg_price"]


def test_more_load_raises_prices(weather):
    y = year_inputs(SCENARIOS["base"], 2026)
    from dataclasses import replace

    lo = summarize_year(simulate_year(y, weather), 2026)
    hi = summarize_year(simulate_year(replace(y, annual_energy_twh=y.annual_energy_twh * 1.05), weather), 2026)
    assert hi["avg_price"] > lo["avg_price"]
