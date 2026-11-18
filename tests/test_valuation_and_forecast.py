from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from marketmodel.assumptions import SCENARIOS
from marketmodel.forecast import run_scenario
from marketmodel.valuation import BESS, SOLAR, bess_energy_revenue, irr, npv, value_asset


def _flat_hourly(price_fn, years=range(2026, 2046)):
    ts = pd.date_range("2026-01-01", periods=8760, freq="h")
    solar = np.clip(np.sin((ts.hour.to_numpy() - 6) / 12 * np.pi), 0, None) * 0.5
    return {y: pd.DataFrame({"timestamp": ts, "price": price_fn(ts), "solar_cf": solar}) for y in years}


def test_npv_and_irr_agree():
    cfs = np.array([-100.0, 30, 30, 30, 30, 30])
    r = irr(cfs)
    assert npv(r, cfs) == pytest.approx(0, abs=1e-6)
    assert r == pytest.approx(0.1524, abs=1e-3)


def test_battery_earns_nothing_with_flat_prices():
    flat = np.full(8760, 40.0)
    assert bess_energy_revenue(flat, BESS) == 0.0


def test_battery_revenue_matches_hand_calculation():
    # $10 for 12 hours, $110 for 12 hours, every day.
    day = np.r_[np.full(12, 10.0), np.full(12, 110.0)]
    prices = np.tile(day, 365)
    expected = (4 * 110 * BESS.rte - 4 * 10) * BESS.capacity_mw * 365 * BESS.availability * BESS.foresight_capture
    assert bess_energy_revenue(prices, BESS) == pytest.approx(expected)


def test_solar_curtails_at_negative_prices():
    neg = _flat_hourly(lambda ts: np.full(len(ts), -10.0))
    v = value_asset(SOLAR, neg)
    assert v["revenue_pv_musd"] == pytest.approx(0.0, abs=1e-9)


def test_cash_flow_waterfall_and_itc():
    hourly = _flat_hourly(lambda ts: np.where(ts.hour >= 18, 120.0, 20.0))
    v = value_asset(BESS, hourly)
    cf = v["cash_flows"]
    capex = BESS.capex_per_kw * BESS.capacity_mw * 1000
    assert cf.cash_flow.iloc[0] == pytest.approx(-capex)
    assert cf.itc.iloc[1] == pytest.approx(BESS.itc * capex)
    assert cf.itc.iloc[2:].sum() == 0
    # Depreciable basis is reduced by half the ITC
    assert -cf.depreciation.sum() == pytest.approx(capex * (1 - BESS.itc / 2))


def test_value_is_monotone_in_capex_and_wacc():
    hourly = _flat_hourly(lambda ts: np.where(ts.hour >= 18, 120.0, 20.0))
    base = value_asset(BESS, hourly)["npv_musd"]
    assert value_asset(replace(BESS, capex_per_kw=1500), hourly)["npv_musd"] < base
    assert value_asset(replace(BESS, wacc=0.10), hourly)["npv_musd"] < base


def test_breakeven_revenue_gives_zero_npv():
    hourly = _flat_hourly(lambda ts: np.where(ts.hour >= 18, 120.0, 20.0))
    v = value_asset(BESS, hourly)
    scaled = replace(BESS, revenue_multiplier=v["breakeven_revenue_multiple"])
    assert value_asset(scaled, hourly)["npv_musd"] == pytest.approx(0, abs=1e-6)


def test_capacity_entry_keeps_system_reliable():
    annual, _ = run_scenario(SCENARIOS["data_center"], years=range(2026, 2031))
    assert (annual.unserved_gwh <= 2.0 + 1e-9).all()
    assert annual.economic_entry_gw.is_monotonic_increasing


def test_constrained_entry_produces_scarcity():
    fast, _ = run_scenario(SCENARIOS["data_center"], years=range(2026, 2031))
    slow, _ = run_scenario(SCENARIOS["dc_constrained"], years=range(2026, 2031))
    assert slow.avg_price.iloc[-1] > fast.avg_price.iloc[-1]
    assert slow.economic_entry_gw.iloc[-1] <= fast.economic_entry_gw.iloc[-1]
