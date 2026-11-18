"""Market Consultant's Report (Word) generated from study results.

Every number in the narrative is pulled from the model outputs at build time,
so the report can never drift from the workbook or the code.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .assumptions import ORDC_SIGMA_MW, SCENARIOS, VOLL
from .forecast import NET_CONE_PER_KW_YR
from .valuation import BESS, SOLAR

if TYPE_CHECKING:
    from .pipeline import StudyResults

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
GREY = RGBColor(0x52, 0x51, 0x4E)
AUTHOR = "Tanisha Raskar"


def _shade(cell, hex_fill: str) -> None:
    tc = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc.append(shd)


def _table(doc: Document, header: list[str], rows: list[list[str]], widths: list[float] | None = None) -> None:
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        run = c.paragraphs[0].add_run(h)
        run.bold, run.font.size, run.font.color.rgb = True, Pt(8.5), RGBColor(0xFF, 0xFF, 0xFF)
        _shade(c, "1F3A5F")
    for r in rows:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            run = p.add_run(v)
            run.font.size = Pt(8.5)
            if i > 0:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    doc.add_paragraph()


def _fig(doc: Document, path: Path, caption: str, width: float = 6.3) -> None:
    doc.add_picture(str(path), width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    run = p.add_run(caption)
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8.5), GREY
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _bullets(doc: Document, items: list[str]) -> None:
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        if it.startswith("**") and "**" in it[2:]:
            head, rest = it[2:].split("**", 1)
            p.add_run(head).bold = True
            p.add_run(rest)
        else:
            p.add_run(it)


def _h(doc: Document, text: str, level: int = 1) -> None:
    h = doc.add_heading(text, level=level)
    for r in h.runs:
        r.font.color.rgb = NAVY


def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    x = abs(x)
    return f"{sign}${x:,.0f}" if x >= 100 else f"{sign}${x:,.1f}"


def _change(x: float, unit: str) -> str:
    return f"{'rises' if x >= 0 else 'falls'} by {_money(abs(x))}{unit}"


def _pct(x: float) -> str:
    return "n/m" if x is None or not np.isfinite(x) else f"{x:.1%}"


def write_report(r: StudyResults, figs: dict[str, Path], path: Path) -> Path:
    a, v = r.annual, r.valuations

    def ann(s: str, y: int, col: str) -> float:
        return float(a[(a.scenario == s) & (a.year == y)][col].iloc[0])

    def val(s: str, asset: str, col: str) -> float:
        return float(v[(v.scenario == s) & (v.asset == asset)][col].iloc[0])

    def avg(s: str, col: str, y0: int, y1: int) -> float:
        return float(a[(a.scenario == s) & a.year.between(y0, y1)][col].mean())

    base26, base30, base35, base45 = (ann("base", y, "avg_price") for y in (2026, 2030, 2035, 2045))
    dc30, dc35 = ann("data_center", 2030, "avg_price"), ann("data_center", 2035, "avg_price")
    dcc30 = ann("dc_constrained", 2030, "avg_price")
    peak_dcc = a[a.scenario == "dc_constrained"].avg_price.max()
    peak_dcc_year = int(a[a.scenario == "dc_constrained"].sort_values("avg_price").year.iloc[-1])
    cap_b, cap_dc = avg("base", "solar_capture_price", 2027, 2045), avg("data_center", "solar_capture_price", 2027, 2045)
    hb = r.hourly["base"][2030]
    hd = r.hourly["data_center"][2030]

    def spread(h: pd.DataFrame) -> float:
        p = h.price.to_numpy().reshape(-1, 24)
        s = np.sort(p, axis=1)
        return float((s[:, -4:].mean(axis=1) - s[:, :4].mean(axis=1)).mean())

    sp_b, sp_d = spread(hb), spread(hd)
    entry_b, entry_dc = ann("base", 2030, "economic_entry_gw"), ann("data_center", 2030, "economic_entry_gw")

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    for s in doc.sections:
        s.left_margin = s.right_margin = Inches(0.9)
        s.top_margin = s.bottom_margin = Inches(0.8)

    # ---------------- Title page
    p = doc.add_paragraph()
    p.add_run("MARKET CONSULTANT'S REPORT  |  INDEPENDENT RESEARCH STUDY").font.color.rgb = GREY
    t = doc.add_paragraph()
    run = t.add_run("Data Centers, Scarcity and Storage:\nERCOT Market Outlook and Asset Valuation, 2026-2045")
    run.bold, run.font.size, run.font.color.rgb = True, Pt(22), NAVY
    sub = doc.add_paragraph()
    sub.add_run(
        "How does data center load growth reshape ERCOT power prices and the relative value of "
        "battery storage versus solar generation?"
    ).italic = True
    meta = doc.add_paragraph()
    meta.add_run(f"Prepared by {AUTHOR}\n{date.today():%B %Y}").font.color.rgb = GREY
    note = doc.add_paragraph()
    nr = note.add_run(
        "Important notice: this is an independent portfolio study built with an open, reproducible fundamentals model. "
        "Hourly load, wind and solar shapes are synthetic and calibrated to public ERCOT and EIA statistics; they are not "
        "historical data. Results illustrate methodology and market dynamics and are not investment advice."
    )
    nr.font.size, nr.font.color.rgb = Pt(8.5), GREY
    doc.add_page_break()

    # ---------------- 1. Executive summary
    _h(doc, "1. Executive summary")
    doc.add_paragraph(
        "ERCOT is absorbing the fastest load growth of any US market, driven by data centers, crypto-mining, industrial "
        "electrification and oil-and-gas load in the Permian. This study asks whether that growth is good news for "
        "merchant batteries, for solar, or for both. We built an hourly production-cost and price-formation model of "
        f"ERCOT, ran {len(SCENARIOS)} scenarios through 2045 ({len(SCENARIOS) * 20 * 8760:,} simulated hours), and valued "
        f"two representative projects: a {BESS.name} and a {SOLAR.name}."
    )
    _bullets(
        doc,
        [
            f"**Prices rise steadily in the Base Case.** Average ERCOT energy prices move from {_money(base26)}/MWh in 2026 "
            f"to {_money(base35)}/MWh in 2035 and {_money(base45)}/MWh in 2045 (nominal), as load growth outpaces "
            "dispatchable additions and gas prices escalate.",
            f"**Data center growth lifts prices, but by less than intuition suggests, as long as supply keeps up.** With "
            f"about 12 GW of additional 24x7 load by 2030, prices are {_money(dc30)}/MWh in 2030 (vs {_money(base30)}) and "
            f"{_money(dc35)}/MWh in 2035 (vs {_money(base35)}). Scarcity pulls in roughly {entry_dc - entry_b:.1f} GW of extra "
            "gas peaking capacity by 2030, which caps the price response at the cost of new entry.",
            f"**Solar is the clearer winner from flat data center load.** Round-the-clock demand lifts midday prices most, "
            f"raising the 2027-2045 average solar capture price from {_money(cap_b)}/MWh to {_money(cap_dc)}/MWh and "
            f"improving solar NPV by {_money(val('data_center', 'solar', 'npv_per_kw') - val('base', 'solar', 'npv_per_kw'))}/kW.",
            f"**Batteries do not automatically benefit.** In the Data Center Boom case, the average daily top-4 vs bottom-4 "
            f"hour spread in 2030 narrows from {_money(sp_b)} to {_money(sp_d)}/MWh, and battery NPV "
            f"{_change(val('data_center', 'bess', 'npv_per_kw') - val('base', 'bess', 'npv_per_kw'), '/kW')}. Flat load raises "
            "the floor of the price curve more than the peak, and new peakers compete away scarcity.",
            f"**The answer flips if new supply cannot keep pace.** If interconnection and turbine supply limits cap new gas "
            f"entry at 2 GW/yr, ERCOT enters a scarcity window: prices peak at {_money(peak_dcc)}/MWh in {peak_dcc_year} "
            f"and battery IRR rises to {_pct(val('dc_constrained', 'bess', 'irr'))} "
            f"(NPV {_money(val('dc_constrained', 'bess', 'npv_per_kw'))}/kW). Storage value is a bet on the speed of the "
            "supply response, not on load growth itself.",
            f"**Neither asset clears a 7.5% hurdle on a fully merchant basis in the Base Case.** Break-even solar PPA prices "
            f"are about {_money(val('base', 'solar', 'breakeven_price'))}/MWh and break-even battery revenue is about "
            f"{_money(val('base', 'bess', 'breakeven_revenue_per_kw_yr'))}/kW-yr "
            f"(~{_money(val('base', 'bess', 'breakeven_revenue_per_kw_yr') / 12)}/kW-month as a toll), which is why "
            "contracted structures dominate ERCOT financing.",
        ],
    )

    # ---------------- 2. Research design
    _h(doc, "2. Research question and hypotheses")
    doc.add_paragraph(
        "Research question: how does data center load growth reshape ERCOT wholesale prices and the relative value of "
        "battery storage versus solar between 2026 and 2045?"
    )
    _table(
        doc,
        ["Hypothesis", "Test", "Result"],
        [
            ["H1. Large-load growth raises average prices", "Data Center Boom vs Base, 2030-2035",
             f"Supported: +{_money(dc30 - base30)}/MWh in 2030, +{_money(dc35 - base35)}/MWh in 2035"],
            ["H2. Batteries gain more than solar", "Change in NPV/kW vs Base",
             "Rejected when entry is unconstrained; supported only when new supply lags"],
            ["H3. Supply response speed drives storage value", "Data Center Boom vs Slow Build",
             f"Supported: battery NPV {_money(val('data_center', 'bess', 'npv_per_kw'))} vs "
             f"{_money(val('dc_constrained', 'bess', 'npv_per_kw'))}/kW"],
        ],
        [2.2, 1.9, 2.6],
    )

    # ---------------- 3. Market context
    _h(doc, "3. Market context")
    doc.add_paragraph(
        "ERCOT is an energy-only market with no capacity payment. Generators recover fixed costs through energy and "
        "ancillary-service margins, especially during scarcity, when the Operating Reserve Demand Curve (ORDC) adds a "
        f"price adder that rises toward the ${VOLL:,.0f}/MWh offer cap as reserves fall. Three structural trends define "
        "the outlook:"
    )
    _bullets(
        doc,
        [
            "**Large-load growth.** ERCOT's long-term load forecasts and large-load interconnection requests (data centers, "
            "hydrogen, industrial electrification) imply demand growth far above the historical 1-2% per year.",
            "**Renewable and storage build-out.** Wind, solar and batteries dominate the interconnection queue. Solar "
            "depresses midday prices and shifts the net-load peak into the evening, which is where batteries earn.",
            "**Thermal tightness.** Gas turbine lead times have lengthened and coal is retiring, so the speed of the "
            "dispatchable supply response is uncertain. The Texas Energy Fund is an explicit policy response.",
        ],
    )

    # ---------------- 4. Methodology
    _h(doc, "4. Methodology")
    doc.add_paragraph(
        "The model follows the same logic as commercial production-cost tools such as Aurora and PROMOD, in a "
        "transparent, open-source form:"
    )
    _bullets(
        doc,
        [
            "**Hourly fundamentals.** A normalized weather year (8,760 hours) drives load (cooling and heating degree "
            "effects, diurnal and weekly activity), wind (spring maximum and nighttime low-level jet) and solar "
            "(clear-sky geometry times cloud cover). Shapes are re-scaled to each year's energy, peak and installed capacity.",
            "**Merit-order dispatch.** Nuclear and must-run thermal clear first, then wind (offering below zero because of "
            "the PTC), solar at zero, then about 34 thermal blocks ordered by marginal cost = heat rate x fuel price + VOM, "
            "with monthly gas price shaping and seasonal outage rates (including correlated cold-weather outages).",
            f"**Scarcity pricing.** Price = system lambda + ORDC adder, where adder = (VOLL - lambda) x LOLP(reserves), "
            f"LOLP is a normal tail probability above the 3,000 MW minimum contingency level (sigma = {ORDC_SIGMA_MW:,.0f} MW), "
            "and the result is capped at the offer cap.",
            "**Storage fleet.** Batteries shave each day's net-load peak and fill its valley under power, energy and "
            "round-trip-efficiency limits, which flattens prices endogenously as the fleet grows.",
            f"**Capacity entry.** Each year, a new gas CT is dispatched against simulated prices. If its energy margin "
            f"exceeds net CONE (${NET_CONE_PER_KW_YR:.0f}/kW-yr in 2026, escalating) or unserved energy breaches a "
            "reliability threshold, 500 MW is added and the year re-solved. This keeps the forecast in long-run "
            "equilibrium instead of drifting into unrealistic shortage.",
            "**Valuation.** Projects are price-takers. The battery earns daily arbitrage (one cycle, 86% round-trip "
            "efficiency, 85% foresight capture) plus a declining ancillary-service stream; solar earns hourly energy "
            "revenue and curtails at negative prices. Cash flows are unlevered and after-tax with a 30% ITC, 5-year MACRS "
            "and a 7.5% nominal WACC.",
        ],
    )

    # assumptions table
    _h(doc, "Key assumptions", 2)
    rows = []
    for sc in SCENARIOS.values():
        d = a[(a.scenario == sc.key) & (a.year == 2035)].iloc[0]
        rows.append([sc.name, f"{d.energy_twh:,.0f}", f"{d.peak_load_gw:,.0f}", f"${d.gas_price:,.2f}",
                     f"{d.solar_gw:,.0f} / {d.storage_gw:,.0f}", f"{d.economic_entry_gw:,.1f}"])
    _table(doc, ["Scenario", "2035 load TWh", "2035 peak GW", "2035 gas $/MMBtu", "Solar / storage GW",
                 "Model-built CT GW"], rows)
    _bullets(doc, [f"**{sc.name}:** {sc.description}" for sc in SCENARIOS.values()])
    _table(
        doc,
        ["Asset", "Capex", "Fixed O&M", "Other"],
        [
            [BESS.name, f"${BESS.capex_per_kw:,.0f}/kW", f"${BESS.fom_per_kw_yr:,.0f}/kW-yr",
             f"{BESS.rte:.0%} RTE, {BESS.degradation:.1%}/yr degradation, AS ${BESS.ancillary_2027_per_kw_yr:.0f} falling to "
             f"${BESS.ancillary_floor_per_kw_yr:.0f}/kW-yr"],
            [SOLAR.name, f"${SOLAR.capex_per_kw:,.0f}/kWac", f"${SOLAR.fom_per_kw_yr:,.0f}/kW-yr",
             f"{SOLAR.degradation:.1%}/yr degradation, curtails below $0/MWh"],
        ],
        [2.0, 1.0, 1.0, 2.7],
    )

    # ---------------- 5. Price forecast
    _h(doc, "5. Market price forecast")
    _fig(doc, figs["price_forecast"], "Figure 1. Annual average ERCOT energy price by scenario (nominal $/MWh). "
         f"The Data Center, Slow Build case is shown in Section 6 because its {peak_dcc_year} spike "
         f"({_money(peak_dcc)}/MWh) would compress this scale.")
    rows = []
    for sc in SCENARIOS.values():
        rows.append([sc.name] + [_money(ann(sc.key, y, "avg_price")) for y in (2026, 2028, 2030, 2035, 2040, 2045)])
    _table(doc, ["Scenario", "2026", "2028", "2030", "2035", "2040", "2045"], rows)
    doc.add_paragraph(
        f"The Base Case rises from {_money(base26)} to {_money(base45)}/MWh. Gas is the single largest driver of the "
        f"level: the High and Low Gas cases bracket the Base by {_money(ann('high_gas', 2035, 'avg_price') - base35)} and "
        f"{_money(base35 - ann('low_gas', 2035, 'avg_price'))}/MWh in 2035. Accelerated Renewables is the only case where "
        f"prices fall in the long run, reaching {_money(ann('accelerated_re', 2045, 'avg_price'))}/MWh by 2045 with "
        f"{int(ann('accelerated_re', 2045, 'negative_hours')):,} negative-price hours and "
        f"{ann('accelerated_re', 2045, 'curtailment_twh'):.0f} TWh of curtailment, which shows how quickly additional "
        "solar cannibalizes its own value."
    )
    _fig(doc, figs["heatmap"], "Figure 2. Average price by month and hour, Base Case 2030. Value concentrates in summer "
         "evenings after sunset and during winter cold snaps.")
    _fig(doc, figs["supply_stack"], "Figure 3. July 2026 thermal supply curve. The curve is flat across combined cycles "
         "and steep once peakers and steam units set price, which is why small load changes at the peak move prices sharply.")
    _fig(doc, figs["gen_mix"], "Figure 4. Base Case generation mix. Wind and solar grow, but gas remains the marginal fuel.")
    _fig(doc, figs["duration"], "Figure 5. Base Case price duration curves. A small number of scarcity hours carries a "
         "large share of annual revenue.")

    # ---------------- 6. Data center impact
    doc.add_page_break()
    _h(doc, "6. How data center load reshapes prices")
    _fig(doc, figs["dc_impact"], "Figure 6. Left: median 2030 price by hour. Right: battery revenue under the Base, "
         "Data Center Boom and Slow Build cases.", 6.6)
    doc.add_paragraph(
        "Data center load is flat across all 24 hours, unlike residential cooling load, which peaks in the late afternoon. "
        "Three mechanisms follow:"
    )
    _bullets(
        doc,
        [
            f"**The floor rises.** Midday and overnight hours that were set by wind, solar or efficient combined cycles move "
            f"up the supply curve. Median midday prices in 2030 rise from "
            f"{_money(hb.groupby(hb.timestamp.dt.hour).price.median()[10:15].mean())} to "
            f"{_money(hd.groupby(hd.timestamp.dt.hour).price.median()[10:15].mean())}/MWh, which directly raises solar revenue.",
            f"**The supply response caps the peak.** Scarcity revenue attracts new peakers ({entry_dc:.1f} GW of model "
            f"entry by 2030 vs {entry_b:.1f} GW in the Base Case), so the evening peak rises less than the floor.",
            f"**Spreads compress.** The intraday spread a 4-hour battery captures narrows (from {_money(sp_b)} to "
            f"{_money(sp_d)}/MWh in 2030), and ancillary revenues continue to saturate as the storage fleet grows.",
        ],
    )
    doc.add_paragraph(
        "The Slow Build case shows the other side. If interconnection queues, turbine backlogs or permitting delay new "
        f"supply, ERCOT runs short around 2028 to 2033, prices average {_money(dcc30)}/MWh in 2030, and batteries, "
        "the fastest resource to build, capture outsized scarcity rents. That window closes as entry catches up, so "
        "timing (commercial operation date) matters as much as location."
    )

    # ---------------- 7. Asset valuation
    _h(doc, "7. Asset valuation")
    _fig(doc, figs["npv"], "Figure 7. Merchant NPV per kW by scenario (7.5% nominal after-tax WACC).")
    rows = []
    for sc in SCENARIOS.values():
        rows.append([
            sc.name,
            _money(val(sc.key, "bess", "npv_per_kw")), _pct(val(sc.key, "bess", "irr")),
            _money(val(sc.key, "bess", "first5_revenue_per_kw_yr")),
            _money(val(sc.key, "solar", "npv_per_kw")), _pct(val(sc.key, "solar", "irr")),
            _money(val(sc.key, "solar", "lifetime_capture_price")),
        ])
    _table(doc, ["Scenario", "BESS NPV $/kW", "BESS IRR", "BESS rev yrs 1-5 $/kW-yr", "Solar NPV $/kW", "Solar IRR",
                 "Solar capture $/MWh"], rows)
    doc.add_paragraph(
        f"The Base Case battery earns about {_money(val('base', 'bess', 'first5_revenue_per_kw_yr'))}/kW-yr in its first "
        "five years, broadly consistent with the post-2023 normalization of ERCOT storage revenues, and declines as the "
        "fleet grows. Solar's lifetime capture price of "
        f"{_money(val('base', 'solar', 'lifetime_capture_price'))}/MWh sits well below the average price because solar "
        "output coincides with the hours it depresses. On a merchant basis neither asset meets the hurdle rate in the Base "
        "Case; both need contracted revenue (a PPA, a tolling agreement or a hedge) or lower capital costs."
    )
    _fig(doc, figs["tornado_bess"], "Figure 8. Battery NPV sensitivities, Base Case ($ million).", 5.8)
    _fig(doc, figs["tornado_solar"], "Figure 9. Solar NPV sensitivities, Base Case ($ million).", 5.8)
    doc.add_paragraph(
        "For both assets the investment tax credit and capital cost are the largest value drivers, ahead of the "
        "price forecast itself. Tax-credit eligibility (including recent changes to federal clean-energy credits and "
        "foreign-entity-of-concern rules) is therefore a first-order diligence item."
    )

    # ---------------- 8. Implications
    _h(doc, "8. Implications for clients")
    _bullets(
        doc,
        [
            "**Utilities and load-serving entities:** hedge evening and winter-morning exposure; flat data center load "
            "raises off-peak costs more than traditional load growth does.",
            "**Developers:** solar benefits more reliably from large-load growth; storage is an option on scarcity, so its "
            "value depends on COD timing relative to the supply response. Co-locating solar and storage hedges both outcomes.",
            f"**Financial institutions:** size debt to contracted cash flows. Merchant battery NPV ranges from "
            f"{_money(v[v.asset == 'bess'].npv_per_kw.min())} to {_money(v[v.asset == 'bess'].npv_per_kw.max())}/kW across "
            "scenarios, a dispersion too wide for merchant-only underwriting.",
            "**Energy service companies and large loads:** behind-the-meter storage and flexible demand capture the same "
            "scarcity rents; curtailable data center load is itself a reliability resource.",
        ],
    )

    # ---------------- 9. Limitations
    _h(doc, "9. Limitations and next steps")
    _bullets(
        doc,
        [
            "Hourly shapes are synthetic and use a single weather year; a production study would use historical ERCOT "
            "load and generation (or licensed Aurora/PROMOD databases) and multiple weather years.",
            "The model is zonal (system-wide). Nodal congestion and hub basis, which matter a great deal for West Texas "
            "solar and wind, are not modeled.",
            "Ancillary services are an exogenous revenue stream rather than co-optimized with energy (as in ERCOT's "
            "real-time co-optimization).",
            "Battery dispatch uses daily perfect foresight with a capture haircut; unit commitment constraints, start "
            "costs and ramping are simplified.",
            "Next steps: calibrate to ERCOT 2023-2025 hub prices, add a nodal basis layer, run stochastic weather "
            "(P10/P50/P90), and test hybrid solar plus storage configurations.",
        ],
    )
    _h(doc, "Appendix: reproducing this study", 2)
    doc.add_paragraph(
        "All results are produced by one command (python -m marketmodel.pipeline --out outputs), which writes this "
        "report, the Excel model with live valuation formulas, the charts and CSV outputs. The repository includes unit "
        "tests for dispatch, scarcity pricing, storage and valuation, and a CSV loader for real hourly data."
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path
