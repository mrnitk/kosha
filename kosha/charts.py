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

# Brand-neutral qualitative palette (accessible, distinct in light and dark).
_PALETTE = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#EECA3B", "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC",
]

_LAYOUT = dict(
    template="plotly_white",
    margin=dict(l=60, r=20, t=48, b=48),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    font=dict(size=12),
)


def color_map(categories) -> dict[str, str]:
    """Assign each category a stable colour from the palette."""
    return {c: _PALETTE[i % len(_PALETTE)] for i, c in enumerate(sorted(set(categories)))}


def spend_stacked_bar(rows, granularity: str = "month") -> go.Figure:
    """Stacked bar of spend per period, one trace per category."""
    periods = sorted({r[0] for r in rows})
    categories = sorted({r[1] for r in rows})
    cmap = color_map(categories)
    by_cat = {c: {p: 0.0 for p in periods} for c in categories}
    for period, category, total in rows:
        by_cat[category][period] = total

    fig = go.Figure()
    for cat in categories:
        fig.add_bar(
            name=cat, x=periods, y=[by_cat[cat][p] for p in periods],
            marker_color=cmap[cat],
        )
    fig.update_layout(barmode="stack", title=f"Spending by category ({granularity})", **_LAYOUT)
    fig.update_yaxes(title="Amount")
    return fig


def income_expense_line(rows) -> go.Figure:
    """Income vs expense (bars) with savings-rate on a secondary axis."""
    periods = [r[0] for r in rows]
    income = [r[1] for r in rows]
    expense = [r[2] for r in rows]
    rate = [r[4] for r in rows]

    fig = go.Figure()
    fig.add_bar(name="Income", x=periods, y=income, marker_color="#54A24B")
    fig.add_bar(name="Expense", x=periods, y=expense, marker_color="#E45756")
    fig.add_scatter(
        name="Savings rate %", x=periods, y=rate, yaxis="y2",
        mode="lines+markers", line=dict(color="#4C78A8", width=2),
    )
    fig.update_layout(
        barmode="group", title="Income vs expense & savings rate",
        yaxis=dict(title="Amount"),
        yaxis2=dict(title="Savings %", overlaying="y", side="right", showgrid=False),
        **_LAYOUT,
    )
    return fig


def category_pie(rows) -> go.Figure:
    """Donut of spend share by category."""
    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    cmap = color_map(labels)
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.5,
        marker=dict(colors=[cmap[l] for l in labels]),
    ))
    fig.update_layout(title="Spend share by category", **_LAYOUT)
    return fig


def top_merchants_bar(rows) -> go.Figure:
    """Horizontal bar of top merchants by spend."""
    merchants = [r[0] for r in rows][::-1]   # largest on top
    totals = [r[1] for r in rows][::-1]
    fig = go.Figure(go.Bar(x=totals, y=merchants, orientation="h", marker_color="#4C78A8"))
    fig.update_layout(title="Top merchants by spend", **_LAYOUT)
    fig.update_xaxes(title="Amount")
    return fig


def figure_to_html(fig: go.Figure) -> str:
    """Self-contained HTML for one figure (Plotly.js inlined, responsive)."""
    return pio.to_html(fig, include_plotlyjs="inline", full_html=True, config={"responsive": True})


def dashboard_html(figures) -> str:
    """Combine multiple figures into one scrollable, responsive page.

    Plotly.js is inlined once; subsequent figures reference the loaded library.
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
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
  body {{ margin:0; padding:8px; font-family: system-ui, sans-serif; }}
  .chart {{ margin-bottom: 12px; }}
</style></head><body>{body}</body></html>"""
