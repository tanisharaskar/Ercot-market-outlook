"""Excel deliverable: assumptions, forecast, hourly detail and live-formula valuations.

The valuation sheets are not pasted values: FOM, EBITDA, MACRS depreciation,
taxes, ITC, cash flow, NPV and IRR are Excel formulas driven by the input cells
on each sheet, so a reviewer can change capex, WACC or the ITC and watch the
result move. Gross revenues are the only values carried over from the Python
dispatch model (they need the 8,760-hour simulation).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .assumptions import SCENARIOS, SOURCES, year_inputs
from .dispatch import supply_curve
from .valuation import ASSETS, COD_YEAR, INFLATION, LIFE_YEARS, MACRS_5, TAX_RATE

if TYPE_CHECKING:
    from .pipeline import StudyResults

NAVY = "1F3A5F"
INPUT_FILL = PatternFill("solid", fgColor="FFF4CC")  # yellow = editable input (banking convention)
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
SUB_FILL = PatternFill("solid", fgColor="E8EEF5")
HFONT = Font(bold=True, color="FFFFFF")
BLUE_FONT = Font(color="0000CC")  # blue = hard-coded input
THIN = Border(bottom=Side(style="thin", color="C3C2B7"))
USD = '#,##0;[Red](#,##0)'
USD_M = '$#,##0.0,,"M";[Red]($#,##0.0,,"M")'
PCT = "0.0%"
PRICE = "$#,##0.00"


def _header(ws, row: int, values: list[str], col: int = 1) -> None:
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=col + i, value=v)
        c.fill, c.font = HEADER_FILL, HFONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _title(ws, text: str, sub: str = "") -> None:
    ws["A1"] = text
    ws["A1"].font = Font(bold=True, size=14, color=NAVY)
    if sub:
        ws["A2"] = sub
        ws["A2"].font = Font(italic=True, color="52514E")


def _widths(ws, widths: dict[str, float]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _df_to_sheet(ws, df: pd.DataFrame, start_row: int, formats: dict[str, str] | None = None) -> None:
    formats = formats or {}
    _header(ws, start_row, list(df.columns))
    for r, row in enumerate(df.itertuples(index=False), start=start_row + 1):
        for c, v in enumerate(row, start=1):
            if hasattr(v, "to_pydatetime"):
                v = v.to_pydatetime()
            cell = ws.cell(row=r, column=c, value=v)
            fmt = formats.get(df.columns[c - 1])
            if fmt:
                cell.number_format = fmt
    ws.freeze_panes = ws.cell(row=start_row + 1, column=2)


def _cover(wb: Workbook) -> None:
    ws = wb.active
    ws.title = "Cover"
    _title(ws, "ERCOT Market Outlook & Asset Valuation Model, 2026-2045")
    rows = [
        ("Research question", "How does data center load growth reshape ERCOT power prices and the relative value of battery storage vs. solar?"),
        ("Model", "Hourly (8,760) merit-order dispatch with ORDC scarcity pricing, storage fleet, and CONE-based capacity entry."),
        ("Scenarios", ", ".join(s.name for s in SCENARIOS.values())),
        ("Assets", "; ".join(a.name for a in ASSETS.values())),
        ("Units", "Nominal US$; prices in $/MWh; capacity in MW unless noted."),
        ("Colour code", "Yellow cells with blue text are inputs. Black text is formula. Grey is model output pasted from Python."),
        ("Data caveat", "Hourly load/wind/solar shapes are synthetic, calibrated to public ERCOT figures. Replace with ERCOT/EIA data for client use."),
        ("", ""),
        ("Sheet", "Contents"),
        ("Assumptions", "Market inputs by scenario and year"),
        ("Annual_Forecast", "Prices, scarcity, generation mix and capacity entry by scenario-year (with chart)"),
        ("Supply_Stack", "July 2026 thermal supply curve"),
        ("Hourly_Base_2030", "8,760-hour dispatch and prices, Base Case 2030"),
        ("BESS_Valuation / Solar_Valuation", "Live-formula cash-flow models (Base Case); edit the yellow inputs"),
        ("Scenario_Valuation", "NPV, IRR and break-even revenue for both assets in every scenario"),
        ("Sensitivity", "One-at-a-time NPV sensitivities (tornado inputs)"),
        ("Sources", "Public sources used to calibrate starting values"),
    ]
    for i, (k, v) in enumerate(rows, start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v).alignment = Alignment(wrap_text=True, vertical="top")
    for r in (11,):
        for c in (1, 2):
            ws.cell(row=r, column=c).fill, ws.cell(row=r, column=c).font = HEADER_FILL, HFONT
    _widths(ws, {"A": 30, "B": 110})


def _assumptions(wb: Workbook) -> None:
    ws = wb.create_sheet("Assumptions")
    _title(ws, "Scenario input trajectories", "Exogenous inputs before endogenous capacity entry")
    rows = []
    for sc in SCENARIOS.values():
        for yr in (2026, 2028, 2030, 2035, 2040, 2045):
            y = year_inputs(sc, yr)
            rows.append(
                dict(
                    Scenario=sc.name, Year=yr, **{
                        "Energy (TWh)": y.annual_energy_twh, "Peak (GW)": y.peak_load_gw, "Wind (GW)": y.wind_gw,
                        "Solar (GW)": y.solar_gw, "Storage (GW)": y.storage_gw, "Storage (h)": y.storage_hours,
                        "Gas CC (GW)": y.gas_cc_gw, "Gas CT (GW)": y.gas_ct_gw, "Gas steam (GW)": y.gas_steam_gw,
                        "Coal (GW)": y.coal_gw, "Gas ($/MMBtu)": y.gas_price,
                    }
                )
            )
    df = pd.DataFrame(rows)
    _df_to_sheet(ws, df, 4, {c: "0.0" for c in df.columns[2:]} | {"Gas ($/MMBtu)": PRICE})
    r = len(df) + 7
    ws.cell(row=r, column=1, value="Scenario definitions").font = Font(bold=True, size=12, color=NAVY)
    for i, sc in enumerate(SCENARIOS.values(), start=r + 1):
        ws.cell(row=i, column=1, value=sc.name).font = Font(bold=True)
        ws.cell(row=i, column=2, value=sc.description)
    _widths(ws, {"A": 24} | {get_column_letter(i): 13 for i in range(2, 14)})


def _annual(wb: Workbook, annual: pd.DataFrame) -> None:
    ws = wb.create_sheet("Annual_Forecast")
    _title(ws, "Annual market forecast by scenario")
    cols = {
        "scenario": "Scenario", "year": "Year", "avg_price": "Avg price", "load_weighted_price": "Load-wtd price",
        "on_peak_price": "On-peak 5x16", "off_peak_price": "Off-peak", "solar_capture_price": "Solar capture",
        "ordc_share": "ORDC share of revenue", "scarcity_hours": "Hours >= $1,000", "negative_hours": "Negative hours",
        "gas_price": "Gas $/MMBtu", "energy_twh": "Energy TWh", "peak_load_gw": "Peak GW", "economic_entry_gw": "New CT entry GW",
        "reserve_margin": "Reserve margin", "renewable_share": "Wind+solar share", "curtailment_twh": "Curtailment TWh",
        "co2_mmt": "CO2 Mt",
    }
    df = annual[list(cols)].rename(columns=cols).copy()
    df["Scenario"] = df["Scenario"].map({k: s.name for k, s in SCENARIOS.items()})
    fmts = {c: PRICE for c in ["Avg price", "Load-wtd price", "On-peak 5x16", "Off-peak", "Solar capture", "Gas $/MMBtu"]}
    fmts |= {c: PCT for c in ["ORDC share of revenue", "Reserve margin", "Wind+solar share"]}
    fmts |= {c: "0.0" for c in ["Energy TWh", "Peak GW", "New CT entry GW", "Curtailment TWh", "CO2 Mt"]}
    _df_to_sheet(ws, df, 3, fmts)

    # Pivot of average price for charting
    piv = annual.pivot(index="year", columns="scenario", values="avg_price")[list(SCENARIOS)]
    c0 = len(cols) + 3
    ws.cell(row=3, column=c0, value="Year").fill = HEADER_FILL
    ws.cell(row=3, column=c0).font = HFONT
    for j, k in enumerate(piv.columns, start=1):
        cell = ws.cell(row=3, column=c0 + j, value=SCENARIOS[k].name)
        cell.fill, cell.font = HEADER_FILL, HFONT
    for i, (yr, row) in enumerate(piv.iterrows(), start=4):
        ws.cell(row=i, column=c0, value=int(yr))
        for j, v in enumerate(row, start=1):
            ws.cell(row=i, column=c0 + j, value=float(v)).number_format = PRICE
    ch = LineChart()
    ch.title = "Average energy price by scenario ($/MWh)"
    ch.height, ch.width = 9, 18
    data = Reference(ws, min_col=c0 + 1, max_col=c0 + len(piv.columns), min_row=3, max_row=3 + len(piv))
    ch.add_data(data, titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=c0, min_row=4, max_row=3 + len(piv)))
    ws.add_chart(ch, f"{get_column_letter(c0)}{len(piv) + 6}")
    _widths(ws, {"A": 24} | {get_column_letter(i): 12 for i in range(2, c0 + 7)})


def _supply(wb: Workbook) -> None:
    ws = wb.create_sheet("Supply_Stack")
    _title(ws, "Thermal supply curve, July 2026 (Base Case)", "Marginal cost = heat rate x fuel price + VOM")
    df = supply_curve(year_inputs(SCENARIOS["base"], 2026), 7)
    df.columns = ["Technology", "Capacity MW", "Heat rate", "Marginal cost $/MWh", "Cumulative MW"]
    _df_to_sheet(ws, df, 4, {"Capacity MW": "#,##0", "Heat rate": "0.00", "Marginal cost $/MWh": PRICE, "Cumulative MW": "#,##0"})
    ch = BarChart()
    ch.title = "Marginal cost by block"
    ch.height, ch.width = 9, 18
    ch.add_data(Reference(ws, min_col=4, min_row=4, max_row=4 + len(df)), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=5, min_row=5, max_row=4 + len(df)))
    ch.gapWidth = 10
    ws.add_chart(ch, "H4")
    _widths(ws, {"A": 14, "B": 13, "C": 11, "D": 20, "E": 15})


def _hourly(wb: Workbook, h: pd.DataFrame) -> None:
    ws = wb.create_sheet("Hourly_Base_2030")
    _title(ws, "Hourly dispatch, Base Case 2030 (8,760 hours)")
    keep = {
        "timestamp": "Hour beginning", "load_mw": "Load MW", "price": "Price $/MWh", "system_lambda": "System lambda",
        "ordc_adder": "ORDC adder", "nuclear_mw": "Nuclear", "coal_mw": "Coal", "gas_cc_mw": "Gas CC",
        "gas_steam_mw": "Gas steam", "gas_ct_mw": "Gas CT", "wind_mw": "Wind", "solar_mw": "Solar",
        "storage_discharge_mw": "Storage discharge", "storage_charge_mw": "Storage charge",
        "wind_curtailed_mw": "Wind curtailed", "solar_curtailed_mw": "Solar curtailed", "reserves_mw": "Reserves MW",
    }
    df = h[[c for c in keep if c in h]].rename(columns=keep)
    fmts = {c: "#,##0" for c in df.columns[1:]} | {"Price $/MWh": PRICE, "System lambda": PRICE, "ORDC adder": PRICE}
    fmts["Hour beginning"] = "yyyy-mm-dd hh:mm"
    _df_to_sheet(ws, df, 3, fmts)
    _widths(ws, {"A": 18} | {get_column_letter(i): 12 for i in range(2, len(df.columns) + 1)})


def _valuation(wb: Workbook, key: str, rev: pd.DataFrame, py_npv: float, py_irr: float) -> None:
    a = ASSETS[key]
    ws = wb.create_sheet(f"{'BESS' if key == 'bess' else 'Solar'}_Valuation")
    _title(ws, f"{a.name}: unlevered after-tax cash flows (Base Case)", "Yellow cells are inputs; everything else recalculates.")
    inputs = [
        ("Capacity (MW)", a.capacity_mw, "#,##0"),
        ("Capex ($/kW)", a.capex_per_kw, USD),
        ("Fixed O&M ($/kW-yr, 2027$)", a.fom_per_kw_yr, USD),
        ("Investment tax credit", a.itc, PCT),
        ("After-tax WACC", a.wacc, PCT),
        ("Federal tax rate", TAX_RATE, PCT),
        ("Inflation (O&M escalator)", INFLATION, PCT),
        ("Revenue multiplier (stress test)", 1.0, "0.00x"),
    ]
    for i, (label, val, fmt) in enumerate(inputs, start=4):
        ws.cell(row=i, column=1, value=label)
        c = ws.cell(row=i, column=2, value=val)
        c.fill, c.font, c.number_format = INPUT_FILL, BLUE_FONT, fmt
    # named input cells
    CAP, CAPEX, FOM, ITC, WACC, TAX, INFL, MULT = (f"$B${r}" for r in range(4, 12))
    ws["D4"], ws["D5"] = "Total capex ($)", f"={CAPEX}*{CAP}*1000"
    ws["D5"].number_format = USD
    ws["D4"].font = Font(bold=True)
    ws["D6"], ws["D7"] = "Depreciable basis ($)", f"=D5*(1-{ITC}/2)"
    ws["D7"].number_format = USD
    ws["D6"].font = Font(bold=True)

    top = 14
    labels = [
        "Year", "Operating year", "Energy revenue", "Ancillary revenue", "Fixed O&M", "EBITDA", "MACRS rate",
        "Depreciation", "Taxes", "ITC", "Unlevered cash flow",
    ]
    for i, lab in enumerate(labels):
        c = ws.cell(row=top + i, column=1, value=lab)
        c.font = Font(bold=True)
        c.fill = SUB_FILL
    r = {lab: top + i for i, lab in enumerate(labels)}
    # Year 0 (construction / capex)
    ws.cell(row=r["Year"], column=2, value=COD_YEAR - 1)
    ws.cell(row=r["Operating year"], column=2, value=0)
    ws.cell(row=r["Unlevered cash flow"], column=2, value="=-D5").number_format = USD
    for t in range(LIFE_YEARS):
        col = 3 + t
        L = get_column_letter(col)
        ws.cell(row=r["Year"], column=col, value=COD_YEAR + t)
        ws.cell(row=r["Operating year"], column=col, value=t + 1)
        e = ws.cell(row=r["Energy revenue"], column=col, value=f"={float(rev.energy_revenue.iloc[t]):.2f}*{MULT}")
        an = ws.cell(row=r["Ancillary revenue"], column=col, value=f"={float(rev.ancillary_revenue.iloc[t]):.2f}*{MULT}")
        e.fill = an.fill = PatternFill("solid", fgColor="EFEFEF")
        ws.cell(row=r["Fixed O&M"], column=col, value=f"=-{FOM}*{CAP}*1000*(1+{INFL})^({L}{r['Operating year']}-1)")
        ws.cell(row=r["EBITDA"], column=col, value=f"=SUM({L}{r['Energy revenue']}:{L}{r['Fixed O&M']})")
        ws.cell(row=r["MACRS rate"], column=col, value=MACRS_5[t] if t < len(MACRS_5) else 0).number_format = "0.00%"
        ws.cell(row=r["Depreciation"], column=col, value=f"=-$D$7*{L}{r['MACRS rate']}")
        ws.cell(row=r["Taxes"], column=col, value=f"=-{TAX}*({L}{r['EBITDA']}+{L}{r['Depreciation']})")
        ws.cell(row=r["ITC"], column=col, value=f"=IF({L}{r['Operating year']}=1,{ITC}*$D$5,0)")
        ws.cell(row=r["Unlevered cash flow"], column=col,
                value=f"={L}{r['EBITDA']}+{L}{r['Taxes']}+{L}{r['ITC']}")
        for lab in labels[2:]:
            if lab != "MACRS rate":
                ws.cell(row=r[lab], column=col).number_format = USD
    last = get_column_letter(2 + LIFE_YEARS)
    cf = r["Unlevered cash flow"]
    for c in range(1, 3 + LIFE_YEARS):
        ws.cell(row=cf, column=c).border = Border(top=Side(style="thin"), bottom=Side(style="double"))
    out = top + len(labels) + 2
    results = [
        ("NPV ($)", f"=B{cf}+NPV({WACC},C{cf}:{last}{cf})", USD_M),
        ("IRR", f"=IFERROR(IRR(B{cf}:{last}{cf}),\"n/m\")", PCT),
        ("NPV ($/kW)", f"=B{out}/({CAP}*1000)", USD),
        ("Python model NPV (check)", py_npv * 1e6, USD_M),
        ("Python model IRR (check)", py_irr, PCT),
        ("Difference (should be ~0)", f"=B{out}-B{out + 3}", USD),
    ]
    for i, (label, f, fmt) in enumerate(results):
        ws.cell(row=out + i, column=1, value=label).font = Font(bold=True)
        c = ws.cell(row=out + i, column=2, value=f)
        c.number_format = fmt
    ws.cell(row=out, column=2).font = Font(bold=True, size=12, color=NAVY)
    _widths(ws, {"A": 32, "B": 14, "D": 20} | {get_column_letter(i): 12.5 for i in range(3, 3 + LIFE_YEARS)})
    ws.column_dimensions["D"].width = 20
    ws.freeze_panes = "B15"


def _scenario_vals(wb: Workbook, vals: pd.DataFrame) -> None:
    ws = wb.create_sheet("Scenario_Valuation")
    _title(ws, "Merchant valuation by scenario", "Break-even = annual revenue ($/kW-yr) needed for NPV = 0 at the WACC")
    df = vals.copy()
    df["scenario"] = df["scenario"].map({k: s.name for k, s in SCENARIOS.items()})
    df["asset"] = df["asset"].map({k: a.name for k, a in ASSETS.items()})
    keep = {
        "scenario": "Scenario", "asset": "Asset", "npv_musd": "NPV ($M)", "npv_per_kw": "NPV ($/kW)", "irr": "IRR",
        "first5_revenue_per_kw_yr": "Revenue yrs 1-5 ($/kW-yr)", "avg_revenue_per_kw_yr": "Lifetime avg revenue ($/kW-yr)",
        "breakeven_revenue_per_kw_yr": "Break-even revenue ($/kW-yr)", "lifetime_capture_price": "Solar capture ($/MWh)",
        "breakeven_price": "Solar break-even PPA ($/MWh)",
    }
    df = df[[c for c in keep if c in df]].rename(columns=keep)
    _df_to_sheet(ws, df, 4, {"NPV ($M)": "0.0", "NPV ($/kW)": USD, "IRR": PCT} | {c: PRICE for c in list(keep.values())[5:]})
    ws.conditional_formatting.add(
        f"D5:D{4 + len(df)}", ColorScaleRule(start_type="min", start_color="F8D7D3", end_type="max", end_color="D5EBDD")
    )
    _widths(ws, {"A": 24, "B": 34} | {get_column_letter(i): 16 for i in range(3, 11)})


def _sensitivity(wb: Workbook, sens: dict[str, pd.DataFrame]) -> None:
    ws = wb.create_sheet("Sensitivity")
    _title(ws, "NPV sensitivities, Base Case ($ million)")
    row = 4
    for key, df in sens.items():
        ws.cell(row=row, column=1, value=ASSETS[key].name).font = Font(bold=True, color=NAVY)
        d = df.rename(columns={"driver": "Driver", "low_npv": "Downside NPV", "high_npv": "Upside NPV",
                               "base_npv": "Base NPV", "swing": "Swing"})
        _header(ws, row + 1, list(d.columns))
        for i, rr in enumerate(d.itertuples(index=False), start=row + 2):
            for j, v in enumerate(rr, start=1):
                ws.cell(row=i, column=j, value=v).number_format = "0.0"
        row += len(d) + 4
    ws.freeze_panes = None
    _widths(ws, {"A": 28, "B": 15, "C": 15, "D": 15, "E": 12})


def _sources(wb: Workbook) -> None:
    ws = wb.create_sheet("Sources")
    _title(ws, "Calibration sources and limitations")
    for i, (k, v) in enumerate(SOURCES.items(), start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    _widths(ws, {"A": 16, "B": 120})


def write_workbook(r: StudyResults, path: Path) -> Path:
    wb = Workbook()
    _cover(wb)
    _assumptions(wb)
    _annual(wb, r.annual)
    _supply(wb)
    _hourly(wb, r.hourly["base"][2030])
    base_vals = r.valuations[r.valuations.scenario == "base"].set_index("asset")
    for key in ASSETS:
        _valuation(wb, key, r.revenues[("base", key)], base_vals.loc[key, "npv_musd"], base_vals.loc[key, "irr"])
    _scenario_vals(wb, r.valuations)
    _sensitivity(wb, r.sensitivities)
    _sources(wb)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
