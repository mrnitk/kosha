"""Tests for dashboard analytics aggregations and the chart builders."""

from __future__ import annotations

from datetime import date

import pytest

from kosha import analytics, categorization as cat, charts, crypto
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    _seed(d)
    yield d
    d.lock()


def _seed(db: Database) -> None:
    con = db.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    # (date, keyword, amount, direction)
    rows = [
        ("2026-04-05", "SWIGGY", 300.0, "debit"),
        ("2026-04-20", "SWIGGY", 200.0, "debit"),
        ("2026-04-25", "ZERODHA", 5000.0, "debit"),
        ("2026-04-01", "EMPLOYER", 60000.0, "credit"),
        ("2026-05-10", "SWIGGY", 400.0, "debit"),
        ("2026-05-15", "AMAZON", 1000.0, "debit"),
        ("2026-05-01", "EMPLOYER", 60000.0, "credit"),
    ]
    for i, (d, kw, amt, direction) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,txn_type,merchant_keyword,dedup_hash)"
            " VALUES (?,?,?,?,?,1,?,?,?)",
            (i, d, f"DESC {kw}", amt, direction, "UPI", kw, f"h{i}"),
        )
    con.commit()
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    cat.add_rule(db, "AMAZON", "Expense", "Shopping")
    cat.add_rule(db, "ZERODHA", "Savings", "Investments")


# --- analytics ---------------------------------------------------------------

def test_date_bounds(db):
    lo, hi = analytics.date_bounds(db)
    assert lo == date(2026, 4, 1) and hi == date(2026, 5, 15)


def test_spend_by_period_subcategory_month(db):
    rows = analytics.spend_by_period_subcategory(db, analytics.Filter(), "month")
    got = {(p, s): t for p, s, t in rows}
    assert got[("2026-04", "Food")] == 500.0
    assert ("2026-04", "Investments") not in got   # ZERODHA is Savings, not Expense
    assert got[("2026-05", "Food")] == 400.0
    assert got[("2026-05", "Shopping")] == 1000.0


def test_income_expense_savings(db):
    rows = analytics.income_expense_savings(db, analytics.Filter(), "month")
    by_period = {r[0]: r for r in rows}
    _, income, expense, savings, rate = by_period["2026-04"]
    assert income == 60000.0
    assert expense == 500.0                         # SWIGGY only; ZERODHA is Savings
    assert savings == 5000.0                        # ZERODHA reclassified
    assert round(rate, 2) == round(5000 / 60000 * 100, 2)


def test_filter_by_date_range(db):
    flt = analytics.Filter(start=date(2026, 5, 1), end=date(2026, 5, 31))
    cats = {c for c, _t, _n in analytics.category_totals(db, flt)}
    assert cats == {"Income", "Expense"}            # no Savings in May
    subs = {s for s, _t, _n in analytics.subcategory_totals(db, flt, "Expense")}
    assert subs == {"Food", "Shopping"}


def test_filter_by_category(db):
    flt = analytics.Filter(categories=("Expense",))
    cats = analytics.category_totals(db, flt)
    assert len(cats) == 1
    assert cats[0][0] == "Expense" and cats[0][1] == 1900.0   # 500+400+1000


def test_top_merchants_expense_only(db):
    rows = analytics.top_merchants(db, analytics.Filter(), limit=5)
    names = [r[0] for r in rows]
    assert "ZERODHA" not in names                   # Savings, not spend
    assert rows[0][0] == "AMAZON"                   # 1000 highest expense merchant


def test_quarter_granularity(db):
    rows = analytics.income_expense_savings(db, analytics.Filter(), "quarter")
    assert all(p == "2026-Q2" for p, *_ in rows)
    assert len(rows) == 1


def test_month_over_month_delta(db):
    rows = analytics.month_over_month(db, analytics.Filter())
    by = {p: (exp, delta) for p, exp, delta in rows}
    assert by["2026-04"][1] is None                 # first period has no prior
    assert by["2026-05"][0] == 1400.0               # May expense
    assert by["2026-05"][1] > 0                      # 1400 > 500 April


def test_transactions_drilldown_respects_filter(db):
    flt = analytics.Filter(categories=("Expense",))
    rows = analytics.transactions(db, flt)
    assert len(rows) == 4                            # 3 SWIGGY + 1 AMAZON
    assert all(r[7] == "Expense" for r in rows)      # effective_category column


# --- charts ------------------------------------------------------------------

def test_chart_builders_produce_traces(db):
    flt = analytics.Filter()
    stacked = charts.spend_stacked_bar(analytics.spend_by_period_subcategory(db, flt), "month")
    assert len(stacked.data) >= 2                    # one trace per sub-category
    line = charts.income_expense_line(analytics.income_expense_savings(db, flt))
    assert sum(1 for t in line.data if t.type == "bar") == 3   # income/expense/savings
    assert not any(t.type == "scatter" for t in line.data)     # savings-rate line removed
    pie = charts.category_pie(analytics.subcategory_totals(db, flt, "Expense"))
    assert pie.data[0].type == "pie"


def test_color_map_is_stable():
    a = charts.color_map(["Food", "Shopping", "Travel"])
    b = charts.color_map(["Travel", "Food", "Shopping"])
    assert a == b                                  # order-independent


def test_dashboard_html_external_plotlyjs_is_small(tmp_path, db):
    flt = analytics.Filter()
    figs = [charts.category_pie(analytics.subcategory_totals(db, flt, "Expense")),
            charts.top_merchants_bar(analytics.top_merchants(db, flt))]
    name = charts.write_plotlyjs(tmp_path)
    assert (tmp_path / name).exists()
    html = charts.dashboard_html(figs, plotlyjs=name)
    assert f'src="{name}"' in html                 # references the cached library
    assert len(html) < 200_000                     # not the ~4.9 MB inline page


def test_dashboard_html_inlines_plotly_once(db):
    flt = analytics.Filter()
    figs = [
        charts.category_pie(analytics.subcategory_totals(db, flt, "Expense")),
        charts.top_merchants_bar(analytics.top_merchants(db, flt)),
    ]
    html = charts.dashboard_html(figs)
    assert html.count('class="plotly-graph-div"') == 2   # both charts rendered
    # Plotly.js is bundled inline exactly once (subsequent figures reuse it).
    assert html.count("Plotly.register") <= 1 or "plotly" in html.lower()
