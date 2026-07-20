"""Categorization engine: rules, manual overrides, and review queries.

Resolution lives in the ``v_transactions_resolved`` SQL view, so a rule change
applies retroactively to all history with no data rewrite. This module manages
the inputs to that view and the queries the review UI needs.

Model (see the view in schema.sql):

    * **category**     one of {Income, Expense, Savings}. Defaults by direction
      (credit -> Income, debit -> Expense); a rule/override reclassifies (e.g.
      an investment debit as Savings).
    * **sub_category** free-text detail (Food, Rent, Investments, ...).

Precedence: manual per-transaction override > rule match on merchant_keyword
(highest priority wins ties) > direction default. A keyword counts as
"reviewed" once a rule gives it a sub_category.

Keywords are normalized (``features.normalize_keyword``) on the way in so a rule
matches exactly the transactions the user assigned it from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import features
from .db import Database

INCOME = "Income"
EXPENSE = "Expense"
SAVINGS = "Savings"
TRANSFER = "Transfer"
CATEGORIES = (INCOME, EXPENSE, SAVINGS, TRANSFER)

# Canonical value -> label shown in the UI. Stored values stay stable keys; only
# the presentation changes. 'Transfer' covers credit-card bill payments and
# self-transfers — kept out of the income/expense/savings math to avoid
# double-counting spend that's already in the card statement.
DISPLAY_LABELS = {
    INCOME: "Income",
    EXPENSE: "Expense",
    SAVINGS: "Savings / Investments",
    TRANSFER: "Credit Card Payment / Transfer",
}


def display_label(category: str) -> str:
    """Human-facing label for a canonical category value."""
    return DISPLAY_LABELS.get(category, category)


def default_category(direction: str) -> str:
    """The category a transaction gets before any rule/override."""
    return INCOME if direction == "credit" else EXPENSE


def normalize_category(text: Optional[str]) -> str:
    """Coerce free text to one of the canonical categories.

    Tolerates the values an earlier build let users type (e.g. 'Saving') and the
    display labels ('Savings / Investments', 'Credit Card Payment / Transfer').
    Unrecognized input falls back to Expense.
    """
    key = (text or "").strip().lower()
    if key in ("income", "credit"):
        return INCOME
    if key in ("savings", "saving", "invest", "investment",
               "savings / investments", "savings/investments"):
        return SAVINGS
    if key in ("transfer", "credit card payment", "card payment", "cc payment",
               "credit card payment / transfer"):
        return TRANSFER
    return EXPENSE


@dataclass(frozen=True)
class Rule:
    id: int
    keyword: str
    category: str
    sub_category: Optional[str]
    priority: int
    excluded: bool = False


@dataclass(frozen=True)
class KeywordSpend:
    """A merchant keyword awaiting review, with its footprint."""

    keyword: str
    txn_count: int
    total_amount: float
    dominant_direction: str      # 'debit' or 'credit' — drives the default category

    @property
    def suggested_category(self) -> str:
        return default_category(self.dominant_direction)


# --- rule CRUD ---------------------------------------------------------------

def add_rule(
    db: Database,
    keyword: str,
    category: str,
    sub_category: Optional[str] = None,
    priority: int = 0,
    excluded: bool = False,
) -> int:
    """Create (or update in place) the rule for ``keyword``. Returns the rule id.

    A keyword maps to one rule, so re-assigning updates it rather than piling up
    duplicates. ``category`` is coerced to a canonical value.
    """
    norm = features.normalize_keyword(keyword)
    if not norm:
        raise ValueError("keyword is empty after normalization")
    category = normalize_category(category)
    sub_category = sub_category.strip() if sub_category and sub_category.strip() else None
    exc = 1 if excluded else 0

    con = db.connection
    existing = con.execute("SELECT id FROM category_rules WHERE keyword = ?", (norm,)).fetchone()
    if existing:
        con.execute(
            "UPDATE category_rules SET category=?, sub_category=?, priority=?, excluded=? WHERE id=?",
            (category, sub_category, priority, exc, existing[0]),
        )
        con.commit()
        return existing[0]
    cur = con.execute(
        "INSERT INTO category_rules(keyword, category, sub_category, priority, excluded) VALUES (?,?,?,?,?)",
        (norm, category, sub_category, priority, exc),
    )
    con.commit()
    return cur.lastrowid


def assign_many(
    db: Database,
    keywords: list[str],
    category: str,
    sub_category: Optional[str] = None,
    priority: int = 0,
    excluded: bool = False,
) -> int:
    """Bulk-assign the same category/sub-category to several keywords.

    Returns the number of keywords assigned. Runs as one transaction.
    """
    category = normalize_category(category)
    sub = sub_category.strip() if sub_category and sub_category.strip() else None
    exc = 1 if excluded else 0
    con = db.connection
    count = 0
    try:
        for kw in keywords:
            norm = features.normalize_keyword(kw)
            if not norm:
                continue
            existing = con.execute("SELECT id FROM category_rules WHERE keyword=?", (norm,)).fetchone()
            if existing:
                con.execute(
                    "UPDATE category_rules SET category=?, sub_category=?, priority=?, excluded=? WHERE id=?",
                    (category, sub, priority, exc, existing[0]),
                )
            else:
                con.execute(
                    "INSERT INTO category_rules(keyword, category, sub_category, priority, excluded) VALUES (?,?,?,?,?)",
                    (norm, category, sub, priority, exc),
                )
            count += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    return count


def set_excluded_keywords(db: Database, keywords: list[str], excluded: bool) -> int:
    """Flip the exclude flag on one or many keywords (creating a rule if needed).

    Excluding a keyword hides all its transactions from every visual and the
    drill-down table. Returns the number of keywords touched. A keyword with no
    rule yet gets a minimal rule carrying its direction-default category.
    """
    exc = 1 if excluded else 0
    con = db.connection
    count = 0
    try:
        for kw in keywords:
            norm = features.normalize_keyword(kw)
            if not norm:
                continue
            existing = con.execute("SELECT id FROM category_rules WHERE keyword=?", (norm,)).fetchone()
            if existing:
                con.execute("UPDATE category_rules SET excluded=? WHERE id=?", (exc, existing[0]))
            else:
                dom = con.execute(
                    "SELECT CASE WHEN SUM(CASE WHEN direction='credit' THEN 1 ELSE 0 END) "
                    "> SUM(CASE WHEN direction='debit' THEN 1 ELSE 0 END) THEN 'credit' ELSE 'debit' END "
                    "FROM transactions WHERE merchant_keyword=?",
                    (norm,),
                ).fetchone()
                direction = (dom[0] if dom and dom[0] else "debit")
                con.execute(
                    "INSERT INTO category_rules(keyword, category, sub_category, priority, excluded) "
                    "VALUES (?,?,?,?,?)",
                    (norm, default_category(direction), None, 0, exc),
                )
            count += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    return count


def update_rule(
    db: Database,
    rule_id: int,
    *,
    category: Optional[str] = None,
    sub_category: Optional[str] = None,
    priority: Optional[int] = None,
) -> None:
    """Patch fields on an existing rule. Only provided fields change."""
    sets, params = [], []
    if category is not None:
        sets.append("category=?"); params.append(normalize_category(category))
    if sub_category is not None:
        sets.append("sub_category=?"); params.append(sub_category.strip() or None)
    if priority is not None:
        sets.append("priority=?"); params.append(priority)
    if not sets:
        return
    params.append(rule_id)
    con = db.connection
    con.execute(f"UPDATE category_rules SET {', '.join(sets)} WHERE id=?", params)
    con.commit()


def delete_rule(db: Database, rule_id: int) -> None:
    con = db.connection
    con.execute("DELETE FROM category_rules WHERE id=?", (rule_id,))
    con.commit()


def list_rules(db: Database) -> list[Rule]:
    rows = db.connection.execute(
        "SELECT id, keyword, category, sub_category, priority, COALESCE(excluded, 0) "
        "FROM category_rules ORDER BY category, keyword"
    ).fetchall()
    return [Rule(id_, kw, cat_, sub, pri, bool(exc)) for id_, kw, cat_, sub, pri, exc in rows]


# --- manual per-transaction overrides ----------------------------------------

def set_override(db: Database, txn_id: int, category: str, sub_category: Optional[str] = None) -> None:
    """Force a single transaction's category, overriding any rule."""
    con = db.connection
    con.execute(
        "UPDATE transactions SET category_override=?, sub_category_override=? WHERE id=?",
        (normalize_category(category),
         (sub_category.strip() if sub_category and sub_category.strip() else None), txn_id),
    )
    con.commit()


def clear_override(db: Database, txn_id: int) -> None:
    """Drop a manual override so the transaction falls back to rules."""
    con = db.connection
    con.execute(
        "UPDATE transactions SET category_override=NULL, sub_category_override=NULL WHERE id=?",
        (txn_id,),
    )
    con.commit()


def set_excluded(db: Database, txn_id: int, excluded: Optional[bool]) -> None:
    """Force a single transaction's exclude state (``None`` = inherit its rule)."""
    con = db.connection
    val = None if excluded is None else (1 if excluded else 0)
    con.execute("UPDATE transactions SET excluded_override=? WHERE id=?", (val, txn_id))
    con.commit()


# --- review queries ----------------------------------------------------------

def unreviewed_keywords(db: Database) -> list[KeywordSpend]:
    """Keywords not yet handled, ranked by total amount.

    Every transaction already has a category by direction, so review is about
    assigning the descriptive sub-category (and optionally reclassifying to
    Savings). A keyword counts as handled once a rule gives it a sub_category,
    marks it excluded, or reclassifies it to Transfer — any of which means the
    user has made a deliberate decision about it.
    """
    rows = db.connection.execute(
        """
        SELECT
            t.merchant_keyword,
            COUNT(*) AS n,
            SUM(t.amount) AS total,
            CASE WHEN SUM(CASE WHEN t.direction='credit' THEN 1 ELSE 0 END)
                      > SUM(CASE WHEN t.direction='debit' THEN 1 ELSE 0 END)
                 THEN 'credit' ELSE 'debit' END AS dominant
        FROM transactions t
        WHERE t.merchant_keyword IS NOT NULL AND t.merchant_keyword <> ''
          AND NOT EXISTS (
              SELECT 1 FROM category_rules r
              WHERE r.keyword = t.merchant_keyword
                AND (r.sub_category IS NOT NULL
                     OR COALESCE(r.excluded, 0) = 1
                     OR r.category = 'Transfer')
          )
        GROUP BY t.merchant_keyword
        ORDER BY total DESC, n DESC
        """
    ).fetchall()
    return [
        KeywordSpend(keyword=k, txn_count=n, total_amount=total, dominant_direction=dom)
        for k, n, total, dom in rows
    ]


def transactions_for_keyword(db: Database, keyword: str, limit: int = 500):
    """All transactions under a merchant keyword, newest first (for drill-in)."""
    norm = features.normalize_keyword(keyword)
    return db.connection.execute(
        """
        SELECT txn_date, raw_description, amount, direction,
               effective_category, effective_sub_category
        FROM v_transactions_resolved
        WHERE merchant_keyword = ?
        ORDER BY txn_date DESC, id DESC
        LIMIT ?
        """,
        (norm, limit),
    ).fetchall()


def category_totals(db: Database, direction: Optional[str] = None) -> list[tuple[str, float, int]]:
    """(effective_category, total_amount, txn_count) using live resolution.

    With no ``direction`` filter this spans Income, Expense and Savings.
    """
    if direction:
        sql = ("SELECT effective_category, SUM(amount), COUNT(*) FROM v_transactions_resolved "
               "WHERE direction=? GROUP BY effective_category ORDER BY SUM(amount) DESC")
        rows = db.connection.execute(sql, (direction,)).fetchall()
    else:
        sql = ("SELECT effective_category, SUM(amount), COUNT(*) FROM v_transactions_resolved "
               "GROUP BY effective_category ORDER BY SUM(amount) DESC")
        rows = db.connection.execute(sql).fetchall()
    return [(c, total, n) for c, total, n in rows]


def subcategory_totals(db: Database) -> list[tuple[str, str, float, int]]:
    """(category, sub_category, total_amount, txn_count) using live resolution.

    Spans every category; unassigned sub-categories surface as 'Unassigned'.
    Excluded transactions are left out (they're hidden everywhere).
    """
    rows = db.connection.execute(
        """
        SELECT effective_category,
               COALESCE(effective_sub_category, 'Unassigned') AS sub,
               SUM(amount), COUNT(*)
        FROM v_transactions_resolved
        WHERE COALESCE(effective_excluded, 0) = 0
        GROUP BY effective_category, sub
        ORDER BY SUM(amount) DESC
        """
    ).fetchall()
    return [(c, s, total, n) for c, s, total, n in rows]


def data_summary(db: Database) -> tuple[Optional[str], Optional[str], int]:
    """(min_date, max_date, txn_count) for the 'what data do I have' readout."""
    row = db.connection.execute(
        "SELECT MIN(txn_date), MAX(txn_date), COUNT(*) FROM transactions"
    ).fetchone()
    lo, hi, n = row if row else (None, None, 0)
    return lo, hi, (n or 0)


def distinct_sub_categories(db: Database) -> list[str]:
    """Sub-categories already in use, for autocomplete."""
    rows = db.connection.execute(
        """
        SELECT sub_category FROM category_rules WHERE sub_category IS NOT NULL
        UNION
        SELECT sub_category_override FROM transactions WHERE sub_category_override IS NOT NULL
        ORDER BY 1
        """
    ).fetchall()
    return [r[0] for r in rows]
