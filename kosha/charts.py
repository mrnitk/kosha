"""Plotly figure builders for the dashboard.

Each builder turns analytics rows into a ``plotly.graph_objects.Figure``. The
dashboard renders them to self-contained HTML for a QWebEngineView; tests can
inspect the figures directly without a browser. Charts share one dark-friendly
template and a stable category colour map so a category keeps its colour across
every chart.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

from .format import format_inr

# Brand-neutral qualitative palette (accessible, distinct in light and dark).
_PALETTE = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
]

def _layout(title: str = "", template: str = "plotly_white", **extra) -> dict:
    # Title is pinned to the very top and the legend sits *below* the plot, so
    # neither overlaps the plotting area (the header used to sit on the chart).
    base = dict(
        template=template,
        title=dict(text=title, x=0.02, xanchor="left", y=0.97, yanchor="top"),
        margin=dict(l=64, r=24, t=64, b=72),
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0),
        font=dict(size=12),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    base.update(extra)
    return base


def _inr_hover(y_values, name: str = "") -> dict:
    """Trace kwargs giving an Indian-formatted amount in the hover tooltip."""
    tail = f"<extra>{name}</extra>" if name else "<extra></extra>"
    return dict(
        customdata=[format_inr(v) for v in y_values],
        hovertemplate="%{x}<br>₹%{customdata}" + tail,
    )


def color_map(categories) -> dict[str, str]:
    """Assign each category a stable colour from the palette."""
    return {c: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(sorted(set(categories)))}


def spend_stacked_bar(rows, granularity: str = "month", template: str = "plotly_white") -> go.Figure:
    """Stacked bar of expense per period, one trace per sub-category."""
    periods = sorted({r[0] for r in rows})
    subs = sorted({r[1] for r in rows})
    cmap = color_map(subs)
    by_sub = {s: {p: 0.0 for p in periods} for s in subs}
    for period, sub, total in rows:
        by_sub[sub][period] = total

    fig = go.Figure()
    for sub in subs:
        y = [by_sub[sub][p] for p in periods]
        fig.add_bar(name=sub, x=periods, y=y, marker_color=cmap[sub], **_inr_hover(y, sub))
    fig.update_layout(**_layout(f"Expense by sub-category ({granularity})", template, barmode="stack"))
    fig.update_yaxes(title="Amount (₹)")
    return fig


def income_expense_line(rows, template: str = "plotly_white") -> go.Figure:
    """Income / Expense / Savings bars with savings-rate on a secondary axis."""
    periods = [r[0] for r in rows]
    income = [r[1] for r in rows]
    expense = [r[2] for r in rows]
    savings = [r[3] for r in rows]
    rate = [r[4] for r in rows]

    fig = go.Figure()
    fig.add_bar(name="Income", x=periods, y=income, marker_color="#54A24B", **_inr_hover(income, "Income"))
    fig.add_bar(name="Expense", x=periods, y=expense, marker_color="#E45756", **_inr_hover(expense, "Expense"))
    fig.add_bar(name="Savings / Investments", x=periods, y=savings, marker_color="#4C78A8",
                **_inr_hover(savings, "Savings / Investments"))
    fig.add_scatter(
        name="Savings rate %", x=periods, y=rate, yaxis="y2",
        mode="lines+markers", line=dict(color="#EECA3B", width=2),
        hovertemplate="%{x}<br>%{y:.1f}%<extra>Savings rate</extra>",
    )
    fig.update_layout(**_layout(
        "Income vs expense vs savings", template,
        barmode="group",
        yaxis=dict(title="Amount (₹)"),
        yaxis2=dict(title="Savings %", overlaying="y", side="right", showgrid=False),
    ))
    return fig


def category_pie(rows, title: str = "Expense share by sub-category", template: str = "plotly_white") -> go.Figure:
    """Donut of share by label (sub-category or category)."""
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    cmap = color_map(labels)
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.5,
        marker=dict(colors=[cmap[l] for l in labels]),
        customdata=[format_inr(v) for v in values],
        hovertemplate="%{label}<br>₹%{customdata} (%{percent})<extra></extra>",
    ))
    fig.update_layout(**_layout(title, template))
    return fig


def top_merchants_bar(rows, template: str = "plotly_white") -> go.Figure:
    """Horizontal bar of top merchants by spend."""
    merchants = [r[0] for r in rows][::-1]   # largest on top
    totals = [r[1] for r in rows][::-1]
    fig = go.Figure(go.Bar(
        x=totals, y=merchants, orientation="h", marker_color="#4C78A8",
        customdata=[format_inr(v) for v in totals],
        hovertemplate="%{y}<br>₹%{customdata}<extra></extra>",
    ))
    fig.update_layout(**_layout("Top merchants by spend", template))
    fig.update_xaxes(title="Amount (₹)")
    return fig


def figure_to_html(fig: go.Figure) -> str:
    """Self-contained HTML for one figure (Plotly.js inlined, responsive)."""
    return pio.to_html(fig, include_plotlyjs="inline", full_html=True, config={"responsive": True})


def dashboard_html(figures, dark: bool = False) -> str:
    """Combine multiple figures into one scrollable, responsive page.

    Plotly.js is inlined once; subsequent figures reference the loaded library.
    ``dark`` matches the page chrome to the app's theme.
    """
    blocks = []
    for i, fig in enumerate(figures):
        blocks.append(pio.to_html(
            fig,
            include_plotlyjs=("inline" if i == 0 else False),
            full_html=False,
            config={"responsive": True},
            default_height="360px",
        ))
    body = "\n".join(f'<div class="chart">{b}</div>' for b in blocks)
    bg = "#1e1e1e" if dark else "#ffffff"
    fg = "#e0e0e0" if dark else "#202020"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
  body {{ margin:0; padding:8px; font-family: system-ui, sans-serif;
          background:{bg}; color:{fg}; }}
  .chart {{ margin-bottom: 12px; }}
</style></head><body>{body}</body></html>"""
