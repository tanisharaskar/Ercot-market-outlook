"""Publication charts for the report (static PNG, print-oriented light theme)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

# Validated categorical palette, assigned in fixed order (never cycled).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

SCENARIO_COLORS = {
    "base": SERIES[0],
    "high_gas": SERIES[1],
    "low_gas": SERIES[2],
    "data_center": SERIES[6],
    "dc_constrained": SERIES[7],
    "accelerated_re": SERIES[3],
}
SCENARIO_NAMES = {
    "base": "Base Case",
    "high_gas": "High Gas",
    "low_gas": "Low Gas",
    "data_center": "Data Center Boom",
    "dc_constrained": "Data Center, Slow Build",
    "accelerated_re": "Accelerated Renewables",
}
TECH_COLORS = {
    "Nuclear": SERIES[6],
    "Coal": INK2,
    "Gas CC": SERIES[0],
    "Gas Steam": SERIES[4],
    "Gas CT": SERIES[1],
    "Wind": SERIES[2],
    "Solar": SERIES[3],
    "Storage": SERIES[7],
}


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "font.family": ["Inter", "DejaVu Sans"],
            "font.size": 10,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": INK2,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlecolor": INK,
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "lines.linewidth": 2,
        }
    )


def _save(fig, out: Path, name: str) -> Path:
    path = out / f"{name}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def price_forecast(annual: pd.DataFrame, out: Path) -> Path:
    _style()
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for key, color in SCENARIO_COLORS.items():
        if key == "dc_constrained":
            continue  # shown separately; its scarcity spike would flatten every other line
        d = annual[annual.scenario == key]
        ax.plot(d.year, d.avg_price, color=color, label=SCENARIO_NAMES[key], lw=2.4 if key == "base" else 2)
        ax.annotate(
            f"${d.avg_price.iloc[-1]:.0f}", (d.year.iloc[-1], d.avg_price.iloc[-1]), xytext=(6, 0),
            textcoords="offset points", va="center", fontsize=8.5, color=INK2,
        )
    ax.set_title("ERCOT annual average energy price by scenario")
    ax.set_ylabel("$/MWh (nominal)")
    ax.set_xlim(annual.year.min(), annual.year.max() + 1.5)
    ax.set_ylim(0, None)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(ncol=3, loc="upper left", bbox_to_anchor=(0, -0.1))
    return _save(fig, out, "fig1_price_forecast")


def supply_stack(curve: pd.DataFrame, must_run_mw: float, out: Path, loads: dict[str, float]) -> Path:
    _style()
    fig, ax = plt.subplots(figsize=(8, 4.2))
    left = must_run_mw
    ax.bar(0, 0, width=0)
    for _, r in curve.iterrows():
        ax.bar(left / 1000, r.marginal_cost, width=r.capacity_mw / 1000, align="edge",
               color=TECH_COLORS[r.tech], edgecolor=SURFACE, linewidth=0.6)
        left += r.capacity_mw
    for tech in dict.fromkeys(curve.tech):
        ax.bar(0, 0, color=TECH_COLORS[tech], label=tech)
    ax.axvspan(0, must_run_mw / 1000, color=GRID, alpha=0.6, lw=0)
    ax.text(must_run_mw / 2000, 4, "Nuclear, wind,\nsolar, must-run", ha="center", fontsize=8, color=INK2)
    for i, (label, mw) in enumerate(loads.items()):
        ax.axvline(mw / 1000, color=INK, lw=1, ls="--")
        ax.text(mw / 1000, ax.get_ylim()[1] * (0.97 - 0.07 * i), f" {label}", fontsize=8, color=INK2, va="top")
    ax.set_title("Thermal supply curve, July 2026 (typical summer afternoon)")
    ax.set_xlabel("Cumulative capacity (GW)")
    ax.set_ylabel("Marginal cost ($/MWh)")
    ax.legend(loc="upper left")
    return _save(fig, out, "fig2_supply_stack")


def price_heatmap(h: pd.DataFrame, year: int, out: Path) -> Path:
    _style()
    piv = h.assign(month=h.timestamp.dt.month, hour=h.timestamp.dt.hour).pivot_table(
        index="month", columns="hour", values="price", aggfunc="mean"
    )
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    fig, ax = plt.subplots(figsize=(8, 3.8))
    vmax = np.percentile(piv.values, 97)
    im = ax.imshow(piv.values, aspect="auto", cmap=cmap, vmin=0, vmax=vmax)
    ax.grid(False)
    ax.set_yticks(range(12), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    cb = fig.colorbar(im, ax=ax, pad=0.01)
    cb.set_label("Avg $/MWh", color=INK2)
    cb.outline.set_visible(False)
    ax.set_title(f"Average price by month and hour of day, Base Case {year}")
    return _save(fig, out, f"fig3_price_heatmap_{year}")


def generation_mix(annual: pd.DataFrame, out: Path, scenario: str = "base") -> Path:
    _style()
    d = annual[(annual.scenario == scenario) & annual.year.isin([2026, 2030, 2035, 2040, 2045])]
    cols = {
        "Nuclear": "nuclear_twh", "Coal": "coal_twh", "Gas CC": "gas_cc_twh", "Gas Steam": "gas_steam_twh",
        "Gas CT": "gas_ct_twh", "Wind": "wind_twh", "Solar": "solar_twh", "Storage": "storage_discharge_twh",
    }
    fig, ax = plt.subplots(figsize=(8, 4.2))
    bottom = np.zeros(len(d))
    x = np.arange(len(d))
    for tech, col in cols.items():
        vals = d[col].fillna(0).to_numpy() if col in d else np.zeros(len(d))
        ax.bar(x, vals, bottom=bottom, color=TECH_COLORS[tech], label=tech, width=0.6, edgecolor=SURFACE, linewidth=1.5)
        bottom += vals
    ax.set_xticks(x, d.year.astype(str))
    ax.set_ylabel("TWh")
    ax.set_title("Generation mix, Base Case")
    ax.legend(ncol=4, loc="upper left", bbox_to_anchor=(0, -0.08))
    return _save(fig, out, "fig4_generation_mix")


def data_center_impact(hourly: dict[str, dict[int, pd.DataFrame]], bess_rev: dict[str, pd.DataFrame], out: Path) -> Path:
    _style()
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    ax = axes[0]
    for key in ("base", "data_center"):
        h = hourly[key][2030]
        prof = h.groupby(h.timestamp.dt.hour)["price"].median()
        ax.plot(prof.index, prof.values, color=SCENARIO_COLORS[key], label=SCENARIO_NAMES[key], marker="o", ms=3)
    ax.set_title("Median hourly price shape, 2030")
    ax.set_xlabel("Hour of day")
    ax.set_ylabel("$/MWh")
    ax.set_xticks(range(0, 24, 4))
    ax.legend(loc="lower right")
    ax = axes[1]
    for key in [k for k in ("base", "data_center", "dc_constrained") if k in bess_rev]:
        r = bess_rev[key]
        ax.plot(r.year, (r.energy_revenue + r.ancillary_revenue) / 100_000, color=SCENARIO_COLORS[key], label=SCENARIO_NAMES[key])
    ax.set_title("Battery revenue, 100 MW / 400 MWh")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("$/kW-yr")
    ax.set_ylim(0, None)
    ax.legend(loc="upper right")
    fig.tight_layout()
    return _save(fig, out, "fig5_data_center_impact")


def asset_npv(vals: pd.DataFrame, out: Path) -> Path:
    _style()
    fig, ax = plt.subplots(figsize=(8, 4))
    scen = list(SCENARIO_COLORS)
    x = np.arange(len(scen))
    w = 0.36
    for i, (asset, label) in enumerate([("bess", "Battery (4-hr)"), ("solar", "Solar PV")]):
        d = vals[vals.asset == asset].set_index("scenario").loc[scen]
        y = d.npv_per_kw.to_numpy()
        bars = ax.bar(x + (i - 0.5) * w, y, width=w - 0.02, color=SERIES[i * 2], label=label)
        for b, v in zip(bars, y):
            ax.text(b.get_x() + b.get_width() / 2, v - 12 if v < 0 else v + 4, f"{v:,.0f}", ha="center",
                    va="top" if v < 0 else "bottom", fontsize=8, color=INK2)
    ax.axhline(0, color=INK, lw=0.8)
    ax.set_xticks(x, [SCENARIO_NAMES[s].replace(", ", "\n").replace(" ", "\n", 1) for s in scen], fontsize=8.5)
    ax.set_ylabel("Merchant NPV ($/kW)")
    ax.set_title("Merchant project NPV by scenario (7.5% after-tax WACC)")
    ax.legend(loc="upper left")
    return _save(fig, out, "fig6_asset_npv")


def tornado(sens: pd.DataFrame, title: str, name: str, out: Path) -> Path:
    _style()
    d = sens.iloc[::-1]
    base = d.base_npv.iloc[0]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    y = np.arange(len(d))
    lo = d[["low_npv", "high_npv"]].min(axis=1)
    hi = d[["low_npv", "high_npv"]].max(axis=1)
    ax.barh(y, lo - base, left=base, color=SERIES[1], height=0.55, label="Downside")
    ax.barh(y, hi - base, left=base, color=SERIES[0], height=0.55, label="Upside")
    ax.axvline(base, color=INK, lw=1)
    ax.set_yticks(y, d.driver)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("NPV ($ million)")
    ax.set_title(title)
    ax.text(base, len(d) - 0.35, f" Base ${base:,.1f}M", fontsize=8, color=INK2)
    ax.legend(loc="lower right")
    return _save(fig, out, name)


def price_duration(hourly: dict[int, pd.DataFrame], out: Path) -> Path:
    _style()
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for i, (yr, h) in enumerate(sorted(hourly.items())):
        p = np.sort(h.price.to_numpy())[::-1]
        ax.plot(np.arange(len(p)) / len(p) * 100, p, color=SERIES[i], label=str(yr))
    ax.set_yscale("symlog", linthresh=100)
    ax.set_xlabel("% of hours")
    ax.set_ylabel("$/MWh (symlog)")
    ax.set_title("Price duration curve, Base Case")
    ax.legend(loc="upper right")
    return _save(fig, out, "fig7_price_duration")
