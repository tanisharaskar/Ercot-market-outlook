"""Merchant asset valuation against simulated hourly prices.

Two hypothetical ERCOT projects are valued as price-takers:

* **BESS**: 100 MW / 400 MWh (4-hour) battery, energy arbitrage plus ancillary services.
* **Solar**: 200 MWac single-axis tracking PV, merchant energy sales.

Cash flows are unlevered and after-tax (21% federal, 5-year MACRS with the ITC
basis reduction), discounted at a nominal after-tax WACC. The Excel workbook
reproduces the same cash-flow waterfall with live formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

MACRS_5 = [0.20, 0.32, 0.192, 0.1152, 0.1152, 0.0576]
TAX_RATE = 0.21
INFLATION = 0.025
COD_YEAR = 2027
LIFE_YEARS = 20


@dataclass(frozen=True)
class Asset:
    key: str
    name: str
    capacity_mw: float
    capex_per_kw: float
    fom_per_kw_yr: float
    itc: float = 0.30
    wacc: float = 0.075
    degradation: float = 0.0
    # BESS-specific
    duration_h: float = 0.0
    rte: float = 0.86
    availability: float = 0.97
    foresight_capture: float = 0.85  # share of perfect-foresight arbitrage achieved in practice
    ancillary_2027_per_kw_yr: float = 0.0
    ancillary_floor_per_kw_yr: float = 0.0
    revenue_multiplier: float = 1.0


BESS = Asset(
    key="bess",
    name="100 MW / 400 MWh Battery (4-hour)",
    capacity_mw=100,
    capex_per_kw=1_000,
    fom_per_kw_yr=25,
    degradation=0.015,
    duration_h=4,
    ancillary_2027_per_kw_yr=30,
    ancillary_floor_per_kw_yr=8,
)

SOLAR = Asset(
    key="solar",
    name="200 MWac Solar PV (single-axis)",
    capacity_mw=200,
    capex_per_kw=1_150,
    fom_per_kw_yr=21,
    degradation=0.005,
    availability=0.99,
)

ASSETS = {"bess": BESS, "solar": SOLAR}


def bess_energy_revenue(prices: np.ndarray, a: Asset, energy_factor: float = 1.0) -> float:
    """Daily perfect-foresight arbitrage (one cycle/day), haircut for imperfect foresight.

    Each day the battery charges in its ``duration`` cheapest hours and discharges
    in its ``duration`` most expensive hours, only if the spread clears losses.
    """
    p = prices.reshape(-1, 24)
    n = max(1, int(round(a.duration_h * energy_factor)))
    srt = np.sort(p, axis=1)
    low = srt[:, :n].sum(axis=1)
    high = srt[:, -n:].sum(axis=1)
    daily = np.clip(high * a.rte - low, 0, None) * a.capacity_mw
    return float(daily.sum() * a.availability * a.foresight_capture)


def solar_energy_revenue(prices: np.ndarray, solar_cf: np.ndarray, a: Asset, degr: float) -> tuple[float, float]:
    """Merchant solar revenue; the plant curtails in negative-price hours. Returns (revenue, MWh)."""
    gen = solar_cf * a.capacity_mw * a.availability * degr
    gen = np.where(prices < 0, 0, gen)
    return float((gen * prices).sum()), float(gen.sum())


def ancillary(a: Asset, year: int) -> float:
    """Ancillary revenue ($) declining as storage saturates ERCOT's AS markets."""
    if a.ancillary_2027_per_kw_yr <= 0:
        return 0.0
    decay = np.exp(-(year - COD_YEAR) / 4.0)
    per_kw = a.ancillary_floor_per_kw_yr + (a.ancillary_2027_per_kw_yr - a.ancillary_floor_per_kw_yr) * decay
    return float(per_kw * a.capacity_mw * 1000)


def gross_revenues(a: Asset, hourly: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """Annual energy + ancillary revenue over the asset life (nominal $)."""
    rows = []
    last = max(hourly)
    for t in range(LIFE_YEARS):
        yr = COD_YEAR + t
        src = hourly[min(yr, last)]
        esc = (1 + INFLATION) ** max(0, yr - last)  # beyond the forecast horizon, escalate the last year
        prices = src["price"].to_numpy() * esc
        degr = (1 - a.degradation) ** t
        if a.key == "bess":
            energy = bess_energy_revenue(prices, a) * degr
            mwh = a.capacity_mw * a.duration_h * 365 * a.availability * degr
        else:
            energy, mwh = solar_energy_revenue(prices, src["solar_cf"].to_numpy(), a, degr)
        rows.append(
            dict(
                year=yr,
                energy_revenue=energy * a.revenue_multiplier,
                ancillary_revenue=ancillary(a, yr) * a.revenue_multiplier,
                mwh=mwh,
                avg_price=float(prices.mean()),
            )
        )
    return pd.DataFrame(rows)


def cash_flows(a: Asset, rev: pd.DataFrame) -> pd.DataFrame:
    """Unlevered after-tax cash-flow waterfall (year 0 = capex at COD - 1)."""
    capex = a.capex_per_kw * a.capacity_mw * 1000
    basis = capex * (1 - a.itc / 2)
    df = rev.copy()
    t = np.arange(len(df))
    df["fom"] = -a.fom_per_kw_yr * a.capacity_mw * 1000 * (1 + INFLATION) ** t
    df["ebitda"] = df["energy_revenue"] + df["ancillary_revenue"] + df["fom"]
    dep = np.zeros(len(df))
    dep[: len(MACRS_5)] = np.array(MACRS_5) * basis
    df["depreciation"] = -dep
    df["tax"] = -TAX_RATE * (df["ebitda"] + df["depreciation"])  # tax benefit assumed usable (tax equity)
    df["itc"] = np.where(t == 0, a.itc * capex, 0.0)
    df["cash_flow"] = df["ebitda"] + df["tax"] + df["itc"]
    year0 = pd.DataFrame([dict(year=COD_YEAR - 1, cash_flow=-capex)])
    return pd.concat([year0, df], ignore_index=True).fillna(0.0)


def npv(rate: float, cfs: np.ndarray) -> float:
    return float(sum(cf / (1 + rate) ** i for i, cf in enumerate(cfs)))


def irr(cfs: np.ndarray) -> float:
    """IRR by bisection (robust for conventional cash flows). Returns NaN if no sign change."""
    lo, hi = -0.3, 1.0
    f_lo, f_hi = npv(lo, cfs), npv(hi, cfs)
    if np.sign(f_lo) == np.sign(f_hi):
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid, cfs)
        if np.sign(f_mid) == np.sign(f_lo):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def value_asset(a: Asset, hourly: dict[int, pd.DataFrame]) -> dict:
    rev = gross_revenues(a, hourly)
    cf = cash_flows(a, rev)
    cfs = cf["cash_flow"].to_numpy()
    capex = a.capex_per_kw * a.capacity_mw * 1000
    disc = np.array([(1 + a.wacc) ** -i for i in range(1, len(rev) + 1)])
    rev_pv = float(((rev["energy_revenue"] + rev["ancillary_revenue"]) * disc).sum())
    out = dict(
        asset=a.key,
        npv_musd=npv(a.wacc, cfs) / 1e6,
        irr=irr(cfs),
        capex_musd=capex / 1e6,
        npv_per_kw=npv(a.wacc, cfs) / (a.capacity_mw * 1000),
        revenue_pv_musd=rev_pv / 1e6,
        avg_revenue_per_kw_yr=float((rev["energy_revenue"] + rev["ancillary_revenue"]).mean() / (a.capacity_mw * 1000)),
        first5_revenue_per_kw_yr=float(
            (rev["energy_revenue"] + rev["ancillary_revenue"]).head(5).mean() / (a.capacity_mw * 1000)
        ),
    )
    # NPV is linear in the revenue stream, so the break-even revenue scale has a closed form.
    npv0 = npv(a.wacc, cash_flows(replace(a, revenue_multiplier=0.0), gross_revenues(replace(a, revenue_multiplier=0.0), hourly))["cash_flow"].to_numpy())
    npv1 = npv(a.wacc, cfs)
    m_star = -npv0 / (npv1 - npv0) if npv1 != npv0 else float("nan")
    out["breakeven_revenue_multiple"] = m_star
    out["breakeven_revenue_per_kw_yr"] = m_star * out["avg_revenue_per_kw_yr"]
    if a.key == "solar":
        out["breakeven_price"] = m_star * float(rev["energy_revenue"].sum() / max(rev["mwh"].sum(), 1))
    if a.key == "solar":
        out["lifetime_capture_price"] = float(rev["energy_revenue"].sum() / max(rev["mwh"].sum(), 1))
    return out | {"cash_flows": cf}


def sensitivities(a: Asset, hourly: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """One-at-a-time NPV sensitivities for a tornado chart."""
    base = value_asset(a, hourly)["npv_musd"]
    cases = [
        ("Capex ±20%", replace(a, capex_per_kw=a.capex_per_kw * 1.2), replace(a, capex_per_kw=a.capex_per_kw * 0.8)),
        ("Revenue ±15%", replace(a, revenue_multiplier=0.85), replace(a, revenue_multiplier=1.15)),
        ("WACC ±1 pt", replace(a, wacc=a.wacc + 0.01), replace(a, wacc=a.wacc - 0.01)),
        ("Fixed O&M ±25%", replace(a, fom_per_kw_yr=a.fom_per_kw_yr * 1.25), replace(a, fom_per_kw_yr=a.fom_per_kw_yr * 0.75)),
        ("ITC 30% vs 0%", replace(a, itc=0.0), a),
    ]
    if a.key == "bess":
        cases.append(
            ("Foresight capture 75-95%", replace(a, foresight_capture=0.75), replace(a, foresight_capture=0.95))
        )
    rows = []
    for label, low, high in cases:
        rows.append(
            dict(
                driver=label,
                low_npv=value_asset(low, hourly)["npv_musd"],
                high_npv=value_asset(high, hourly)["npv_musd"],
                base_npv=base,
            )
        )
    df = pd.DataFrame(rows)
    df["swing"] = (df["high_npv"] - df["low_npv"]).abs()
    return df.sort_values("swing", ascending=False).reset_index(drop=True)
