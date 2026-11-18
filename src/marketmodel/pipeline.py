"""End-to-end study run: forecast -> valuation -> charts -> workbook -> report.

Usage::

    python -m marketmodel.pipeline --out outputs
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import charts
from .assumptions import END_YEAR, SCENARIOS, START_YEAR, year_inputs
from .dispatch import MUST_RUN_THERMAL_MW, NUCLEAR_AVAILABILITY, supply_curve
from .forecast import run_scenario
from .profiles import WeatherYear, load_profiles_csv, synthetic_weather_year
from .valuation import ASSETS, gross_revenues, sensitivities, value_asset


@dataclass
class StudyResults:
    annual: pd.DataFrame
    hourly: dict[str, dict[int, pd.DataFrame]]
    valuations: pd.DataFrame
    cash_flows: dict[tuple[str, str], pd.DataFrame]
    revenues: dict[tuple[str, str], pd.DataFrame]
    sensitivities: dict[str, pd.DataFrame]
    weather: WeatherYear


def run_study(weather: WeatherYear | None = None) -> StudyResults:
    weather = weather or synthetic_weather_year()
    years = tuple(range(START_YEAR, END_YEAR + 1))
    annual_frames, hourly = [], {}
    for key, sc in SCENARIOS.items():
        a, h = run_scenario(sc, weather, keep_hourly=years)
        annual_frames.append(a)
        hourly[key] = h
    annual = pd.concat(annual_frames, ignore_index=True)

    rows, cfs, revs = [], {}, {}
    for key in SCENARIOS:
        for a in ASSETS.values():
            v = value_asset(a, hourly[key])
            cfs[(key, a.key)] = v.pop("cash_flows")
            revs[(key, a.key)] = gross_revenues(a, hourly[key])
            rows.append(dict(scenario=key, **v))
    valuations = pd.DataFrame(rows)
    sens = {a.key: sensitivities(a, hourly["base"]) for a in ASSETS.values()}
    return StudyResults(annual, hourly, valuations, cfs, revs, sens, weather)


def make_charts(r: StudyResults, out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    y26 = year_inputs(SCENARIOS["base"], 2026)
    h26 = r.hourly["base"][2026]
    july = h26[h26.timestamp.dt.month == 7]
    must_run = (
        y26.nuclear_gw * 1000 * NUCLEAR_AVAILABILITY
        + MUST_RUN_THERMAL_MW * 0.94
        + float((july.wind_mw + july.solar_mw)[july.timestamp.dt.hour == 17].mean())
    )
    afternoon = float(july.load_mw[july.timestamp.dt.hour == 17].mean())
    return {
        "price_forecast": charts.price_forecast(r.annual, out),
        "supply_stack": charts.supply_stack(
            supply_curve(y26, 7), must_run, out, {"Avg 5 PM load": afternoon, "Peak load": float(h26.load_mw.max())}
        ),
        "heatmap": charts.price_heatmap(r.hourly["base"][2030], 2030, out),
        "gen_mix": charts.generation_mix(r.annual, out),
        "dc_impact": charts.data_center_impact(
            r.hourly, {k: r.revenues[(k, "bess")] for k in ("base", "data_center", "dc_constrained")}, out
        ),
        "npv": charts.asset_npv(r.valuations, out),
        "tornado_bess": charts.tornado(r.sensitivities["bess"], "Battery NPV sensitivity (Base Case)", "fig8_tornado_bess", out),
        "tornado_solar": charts.tornado(r.sensitivities["solar"], "Solar NPV sensitivity (Base Case)", "fig9_tornado_solar", out),
        "duration": charts.price_duration({y: r.hourly["base"][y] for y in (2026, 2035, 2045)}, out),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Run the ERCOT market outlook and asset valuation study.")
    ap.add_argument("--out", default="outputs", type=Path)
    ap.add_argument("--profiles", type=Path, help="Optional CSV of real hourly load/wind/solar (see profiles.py)")
    ap.add_argument("--no-report", action="store_true", help="Skip the Word/PDF report")
    args = ap.parse_args(argv)

    t0 = time.time()
    weather = load_profiles_csv(args.profiles) if args.profiles else synthetic_weather_year()
    r = run_study(weather)
    out: Path = args.out
    (out / "charts").mkdir(parents=True, exist_ok=True)
    r.annual.to_csv(out / "annual_forecast.csv", index=False)
    r.valuations.to_csv(out / "asset_valuations.csv", index=False)
    figs = make_charts(r, out / "charts")

    from .workbook import write_workbook

    write_workbook(r, out / "ERCOT_Market_Outlook_Model.xlsx")
    if not args.no_report:
        from .report import write_report

        write_report(r, figs, out / "ERCOT_Market_Consultant_Report.docx")
    summary = {
        "runtime_s": round(time.time() - t0, 1),
        "scenarios": list(SCENARIOS),
        "hours_simulated": int(sum(len(h) for hs in r.hourly.values() for h in hs.values())),
    }
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
