"""Dashboard analytics — read-only aggregations over resolved transactions.

Everything queries ``v_transactions_resolved`` so category resolution (override
> rule > Uncategorized) is always live. A single ``Filter`` (date range,
categories, accounts) plus a period granularity drives every aggregation, so the
dashboard's controls map one-to-one onto these functions.

Amounts follow the ``direction`` column: 'debit' is spending, 'credit' is income.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .db import Database

GRANULARITIES = ("month", "quarter", "year")


@dataclass(frozen=True)
class Filter:
    """Scope for a dashboard query. ``None``/empty means 'no constraint'.

    Excluded transactions (``effective_excluded=1``) are dropped by default;
    set ``include_excluded=True`` to see them (e.g. a "show excluded" toggle).
    """

    start: Optional[date] = None
    end: Optional[date] = None
    categories: tuple[str, ...] = field(default_factory=tuple)
    sub_categories: tuple[str, ...] = field(default_factory=tuple)
    account_ids: tuple[int, ...] = field(default_factory=tuple)
    include_excluded: bool = False

    def where(self, alias: str = "") -> tuple[str, list]:
        """Build a SQL WHERE fragment (without the keyword) and its params."""
        p = f"{alias}." if alias else ""
        clauses, params = [], []
        if self.start:
            clauses.append(f"{p}txn_date >= ?"); params.append(self.start.isoformat())
        if self.end:
            clauses.append(f"{p}txn_date <= ?"); params.append(self.end.isoformat())
        if self.categories:
            marks = ",".join("?" * len(self.categories))
            clauses.append(f"{p}effective_category IN ({marks})")
            params.extend(self.categories)
        if self.sub_categories:
            marks = ",".join("?" * len(self.sub_categories))
            clauses.append(f"COALESCE({p}effective_sub_category, 'Unassigned') IN ({marks})")
            params.extend(self.sub_categories)
        if self.account_ids:
            marks = ",".join("?" * len(self.account_ids))
            clauses.append(f"{p}account_id IN ({marks})")
            params.extend(self.account_ids)
        if not self.include_excluded:
            clauses.append(f"COALESCE({p}effective_excluded, 0) = 0")
        return (" AND ".join(clauses) if clauses else "1=1"), params


def _period_expr(granularity: str, col: str = "txn_date") -> str:
    """SQL expression producing a sortable period label from an ISO date."""
    if granularity == "year":
        return f"substr({col},1,4)"
    if granularity == "quarter":
        # 'YYYY-Qn'
        return f"substr({col},1,4) || '-Q' || ((CAST(substr({col},6,2) AS INTEGER)+2)/3)"
    if granularity == "month":
        return f"substr({col},1,7)"
    raise ValueError(f"unknown granularity {granularity!r}")


# --- aggregations ------------------------------------------------------------

def spend_by_period_category(db: Database, flt: Filter, granularity: str = "month"):
    """Rows (period, category, total) for debit spend — feeds the stacked bar."""
    where, params = flt.where()
    period = _period_expr(granularity)
    sql = f"""
        SELECT {period} AS period, effective_category, SUM(amount) AS total
        FROM v_transactions_resolved
        WHERE effective_category='Expense' AND {where}
        GROUP BY period, effective_category
        ORDER BY period, total DESC
    """
    return db.connection.execute(sql, params).fetchall()


def income_expense_savings(db: Database, flt: Filter, granularity: str = "month"):
    """Rows (period, income, expense, savings, savings_rate) per period.

    Category-based: Income/Expense/Savings come from ``effective_category`` (so a
    debit reclassified as Savings counts as savings, not spend). Savings rate is
    savings as a share of income.
    """
    where, params = flt.where()
    period = _period_expr(granularity)
    sql = f"""
        SELECT {period} AS period,
               SUM(CASE WHEN effective_category='Income'  THEN amount ELSE 0 END) AS income,
               SUM(CASE WHEN effective_category='Expense' THEN amount ELSE 0 END) AS expense,
               SUM(CASE WHEN effective_category='Savings' THEN amount ELSE 0 END) AS savings
        FROM v_transactions_resolved
        WHERE {where}
        GROUP BY period
        ORDER BY period
    """
    out = []
    for period, income, expense, savings in db.connection.execute(sql, params).fetchall():
        income = income or 0.0
        expense = expense or 0.0
        savings = savings or 0.0
        rate = (savings / income * 100.0) if income else 0.0
        out.append((period, income, expense, savings, rate))
    return out


def category_totals(db: Database, flt: Filter):
    """Rows (category, total, count) across Income/Expense/Savings."""
    where, params = flt.where()
    sql = f"""
        SELECT effective_category, SUM(amount) AS total, COUNT(*) AS n
        FROM v_transactions_resolved
        WHERE {where}
        GROUP BY effective_category
        ORDER BY total DESC
    """
    return db.connection.execute(sql, params).fetchall()


def spend_by_period_subcategory(db: Database, flt: Filter, granularity: str = "month"):
    """Rows (period, sub_category, total) for Expense — the where-money-goes bar.

    Unassigned expense (no sub-category yet) is bucketed as 'Unassigned'.
    """
    where, params = flt.where()
    period = _period_expr(granularity)
    sql = f"""
        SELECT {period} AS period,
               COALESCE(effective_sub_category, 'Unassigned') AS sub,
               SUM(amount) AS total
        FROM v_transactions_resolved
        WHERE effective_category='Expense' AND {where}
        GROUP BY period, sub
        ORDER BY period, total DESC
    """
    return db.connection.execute(sql, params).fetchall()


def subcategory_totals(db: Database, flt: Filter, category: str = "Expense"):
    """Rows (sub_category, total, count) within a category — feeds the donut."""
    where, params = flt.where()
    sql = f"""
        SELECT COALESCE(effective_sub_category, 'Unassigned') AS sub,
               SUM(amount) AS total, COUNT(*) AS n
        FROM v_transactions_resolved
        WHERE effective_category=? AND {where}
        GROUP BY sub
        ORDER BY total DESC
    """
    return db.connection.execute(sql, [category, *params]).fetchall()


def top_merchants(db: Database, flt: Filter, limit: int = 10):
    """Rows (merchant_keyword, total, count) for Expense — highest spend first.

    Expense-category-based, so a debit reclassified as Savings (an investment)
    isn't counted as merchant spend.
    """
    where, params = flt.where()
    sql = f"""
        SELECT merchant_keyword, SUM(amount) AS total, COUNT(*) AS n
        FROM v_transactions_resolved
        WHERE effective_category='Expense' AND merchant_keyword IS NOT NULL
              AND merchant_keyword<>'' AND {where}
        GROUP BY merchant_keyword
        ORDER BY total DESC
        LIMIT ?
    """
    return db.connection.execute(sql, [*params, limit]).fetchall()


def month_over_month(db: Database, flt: Filter):
    """Rows (period, expense, delta_pct vs previous period) at month grain."""
    rows = income_expense_savings(db, flt, "month")
    out = []
    prev = None
    for period, _income, expense, _sav, _rate in rows:
        delta = ((expense - prev) / prev * 100.0) if prev else None
        out.append((period, expense, delta))
        prev = expense if expense else prev
    return out


def transactions(db: Database, flt: Filter, limit: int = 500):
    """Detail rows for drill-down, newest first.

    Columns: date, description, amount, direction, txn_type, source
    (account name), keyword, category, sub_category, tag.
    """
    where, params = flt.where()
    sql = f"""
        SELECT txn_date, raw_description, amount, direction, txn_type,
               account_name, COALESCE(merchant_keyword, '') AS keyword,
               effective_category, COALESCE(effective_sub_category, '') AS sub,
               COALESCE(effective_tag, '') AS tag
        FROM v_transactions_resolved
        WHERE {where}
        ORDER BY txn_date DESC, id DESC
        LIMIT ?
    """
    return db.connection.execute(sql, [*params, limit]).fetchall()


def list_accounts(db: Database) -> list[tuple[int, str]]:
    """(id, name) for every account, for the dashboard's Source filter."""
    return db.connection.execute(
        "SELECT id, name FROM accounts ORDER BY name"
    ).fetchall()


def monthly_stats(db: Database, flt: Filter) -> dict[str, dict[str, float]]:
    """Per-month average/min/max/total for Income, Expense and Savings.

    Aggregates the monthly income/expense/savings series, then reduces across the
    months that actually have data. Empty series yield zeros. Keys: 'Income',
    'Expense', 'Savings'; each maps to {'avg','min','max','total','months'}.
    """
    rows = income_expense_savings(db, flt, "month")
    series = {
        "Income": [r[1] for r in rows],
        "Expense": [r[2] for r in rows],
        "Savings": [r[3] for r in rows],
    }
    out: dict[str, dict[str, float]] = {}
    for label, values in series.items():
        present = [v for v in values]
        if present:
            total = sum(present)
            out[label] = {
                "avg": total / len(present),
                "min": min(present),
                "max": max(present),
                "total": total,
                "months": len(present),
            }
        else:
            out[label] = {"avg": 0.0, "min": 0.0, "max": 0.0, "total": 0.0, "months": 0}
    return out


def distinct_sub_categories(db: Database, category: Optional[str] = None) -> list[str]:
    """Sub-category labels present in the data, for the filter dropdown.

    Unassigned expense surfaces as 'Unassigned'. Pass ``category`` to limit to
    the sub-categories used within one category (so the dropdown can cascade).
    """
    clause, params = "", []
    if category:
        clause = "WHERE effective_category = ?"
        params = [category]
    sql = f"""
        SELECT DISTINCT COALESCE(effective_sub_category, 'Unassigned') AS sub
        FROM v_transactions_resolved
        {clause}
        ORDER BY sub
    """
    return [r[0] for r in db.connection.execute(sql, params).fetchall()]


def date_bounds(db: Database) -> tuple[Optional[date], Optional[date]]:
    """Min/max transaction date in the vault, for initializing filters."""
    row = db.connection.execute("SELECT MIN(txn_date), MAX(txn_date) FROM transactions").fetchone()
    lo, hi = row if row else (None, None)
    return (
        date.fromisoformat(lo) if lo else None,
        date.fromisoformat(hi) if hi else None,
    )
