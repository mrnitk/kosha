"""Dashboard analytics — read-only aggregations over resolved transactions.

Everything queries ``v_transactions_resolved`` so category resolution (override
> rule > Uncategorized) is always live. A single ``Filter`` (date range,
categories, accounts) plus a period granularity drives every aggregation, so the
dashboard's controls map one-to-one onto these functions.

Amounts follow the ``direction`` column: 'debit' is spending, 'credit' is income.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
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
    tags: tuple[str, ...] = field(default_factory=tuple)
    account_ids: tuple[int, ...] = field(default_factory=tuple)
    include_excluded: bool = False
    search: str = ""

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
        if self.tags:
            # Tags are stored comma-separated ("a, b"); match a whole tag on
            # comma boundaries so 'goa' doesn't match 'goat'. A txn matches if it
            # carries ANY selected tag.
            ors = []
            for t in self.tags:
                ors.append(f"(', ' || COALESCE({p}effective_tag, '') || ', ') LIKE ?")
                params.append(f"%, {t}, %")
            clauses.append("(" + " OR ".join(ors) + ")")
        if self.account_ids:
            marks = ",".join("?" * len(self.account_ids))
            clauses.append(f"{p}account_id IN ({marks})")
            params.extend(self.account_ids)
        if self.search and self.search.strip():
            like = f"%{self.search.strip()}%"
            searchable = [
                f"COALESCE({p}raw_description, '')",
                f"COALESCE({p}merchant_keyword, '')",
                f"COALESCE({p}effective_category, '')",
                f"COALESCE({p}effective_sub_category, '')",
                f"COALESCE({p}effective_tag, '')",
                f"COALESCE({p}account_name, '')",
                f"COALESCE({p}note, '')",
                f"CAST({p}amount AS TEXT)",
            ]
            clauses.append("(" + " OR ".join(f"{s} LIKE ?" for s in searchable) + ")")
            params.extend([like] * len(searchable))
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


def spend_by_period_tag(db: Database, flt: Filter, granularity: str = "month"):
    """Rows (period, tag, total) for Expense, exploding comma-separated tags.

    A transaction with several tags contributes its full amount to each tag (tags
    overlap, unlike sub-categories), so per-tag totals can sum to more than total
    expense. Untagged expense is bucketed as 'Untagged'.
    """
    from .categorization import split_tags
    where, params = flt.where()
    period = _period_expr(granularity)
    sql = f"""
        SELECT {period} AS period, effective_tag, amount
        FROM v_transactions_resolved
        WHERE effective_category='Expense' AND {where}
    """
    agg: dict[tuple[str, str], float] = {}
    for prd, tagstr, amount in db.connection.execute(sql, params).fetchall():
        for tag in (split_tags(tagstr) or ["Untagged"]):
            agg[(prd, tag)] = agg.get((prd, tag), 0.0) + amount
    return [(prd, tag, total) for (prd, tag), total in sorted(agg.items())]


def tag_totals(db: Database, flt: Filter):
    """Rows (tag, total, count) for Expense, exploding comma-separated tags."""
    from .categorization import split_tags
    where, params = flt.where()
    sql = f"""
        SELECT effective_tag, amount FROM v_transactions_resolved
        WHERE effective_category='Expense' AND {where}
    """
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for tagstr, amount in db.connection.execute(sql, params).fetchall():
        for tag in (split_tags(tagstr) or ["Untagged"]):
            totals[tag] = totals.get(tag, 0.0) + amount
            counts[tag] = counts.get(tag, 0) + 1
    rows = [(tag, totals[tag], counts[tag]) for tag in totals]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def distinct_tags(db: Database) -> list[str]:
    """Individual tags present in the data, for the filter (multi-tag aware)."""
    from .categorization import split_tags
    rows = db.connection.execute(
        "SELECT DISTINCT effective_tag FROM v_transactions_resolved "
        "WHERE effective_tag IS NOT NULL AND effective_tag<>''"
    ).fetchall()
    tags = set()
    for (value,) in rows:
        tags.update(split_tags(value))
    return sorted(tags, key=str.lower)


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
    (account name), keyword, category, sub_category, tag, note, id.
    (``note`` and ``id`` are appended so earlier column indices stay stable.)
    """
    where, params = flt.where()
    sql = f"""
        SELECT txn_date, raw_description, amount, direction, txn_type,
               account_name, COALESCE(merchant_keyword, '') AS keyword,
               effective_category, COALESCE(effective_sub_category, '') AS sub,
               COALESCE(effective_tag, '') AS tag, COALESCE(note, '') AS note, id
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


@dataclass(frozen=True)
class Recurring:
    """A merchant that recurs on a regular cadence — a likely subscription/EMI/SIP."""

    keyword: str
    cadence: str            # 'weekly' | 'fortnightly' | 'monthly' | 'quarterly' | ...
    count: int
    avg_amount: float
    last_date: date
    next_estimate: date
    monthly_amount: float   # avg amount normalized to a per-month figure
    category: str


_CADENCE_BANDS = [
    ("weekly", 5, 9), ("fortnightly", 12, 18), ("monthly", 26, 33),
    ("bi-monthly", 55, 70), ("quarterly", 82, 100), ("half-yearly", 170, 200),
    ("yearly", 330, 400),
]


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _classify_cadence(median_interval: float) -> Optional[str]:
    for label, lo, hi in _CADENCE_BANDS:
        if lo <= median_interval <= hi:
            return label
    return None


def recurring_merchants(db: Database, flt: Optional[Filter] = None,
                        min_occurrences: int = 3) -> tuple[list[Recurring], float]:
    """Detect recurring debit merchants and the total monthly committed outflow.

    Groups debit transactions by keyword, and flags those that repeat on a
    regular cadence (consistent gaps between dates). Excludes Transfers (e.g.
    credit-card bill payments) so the underlying spend isn't double-counted, and
    excluded transactions. Returns (rows sorted by monthly amount desc, total).
    """
    flt = flt or Filter()
    where, params = flt.where()
    sql = f"""
        SELECT merchant_keyword, txn_date, amount, effective_category
        FROM v_transactions_resolved
        WHERE direction='debit' AND merchant_keyword IS NOT NULL AND merchant_keyword<>''
              AND effective_category<>'Transfer' AND {where}
        ORDER BY merchant_keyword, txn_date
    """
    by_kw: dict[str, list] = {}
    for kw, d, amount, cat in db.connection.execute(sql, params).fetchall():
        by_kw.setdefault(kw, []).append((date.fromisoformat(d), amount, cat))

    out: list[Recurring] = []
    for kw, rows in by_kw.items():
        if len(rows) < min_occurrences:
            continue
        dates = [r[0] for r in rows]
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        intervals = [i for i in intervals if i > 0]
        if not intervals:
            continue
        med = _median(intervals)
        cadence = _classify_cadence(med)
        if cadence is None:
            continue
        # Require regularity: most gaps close to the median.
        consistent = sum(1 for i in intervals if abs(i - med) <= max(3, 0.35 * med))
        if consistent / len(intervals) < 0.5:
            continue
        amounts = [r[1] for r in rows]
        avg_amount = sum(amounts) / len(amounts)
        per_month = 30.44 / med
        last = max(dates)
        out.append(Recurring(
            keyword=kw, cadence=cadence, count=len(rows), avg_amount=avg_amount,
            last_date=last, next_estimate=last + timedelta(days=round(med)),
            monthly_amount=avg_amount * per_month, category=rows[-1][2],
        ))
    out.sort(key=lambda r: r.monthly_amount, reverse=True)
    total_monthly = sum(r.monthly_amount for r in out)
    return out, total_monthly


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
