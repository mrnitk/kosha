"""Categorization engine: rules, manual overrides, and review queries.

Resolution itself lives in the ``v_transactions_resolved`` SQL view, so a rule
change applies retroactively to all history with no data rewrite. This module
manages the inputs to that view and the queries the review UI needs.

Two ways a transaction gets a category, in precedence order:

    1. **Manual override** on the transaction (``category_override``) — always wins.
    2. **Rule match** on ``merchant_keyword`` — highest ``priority`` wins ties.

Keywords are normalized (see ``features.normalize_keyword``) on the way in so a
rule matches exactly the transactions the user assigned it from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import features
from .db import Database

UNCATEGORIZED = "Uncategorized"


@dataclass(frozen=True)
class Rule:
    id: int
    keyword: str
    category: str
    sub_category: Optional[str]
    priority: int


@dataclass(frozen=True)
class KeywordSpend:
    """A merchant keyword awaiting categorization, with its footprint."""

    keyword: str
    txn_count: int
    total_amount: float


# --- rule CRUD ---------------------------------------------------------------

def add_rule(
    db: Database,
    keyword: str,
    category: str,
    sub_category: Optional[str] = None,
    priority: int = 0,
) -> int:
    """Create (or update in place) the rule for ``keyword``.

    A keyword maps to one category, so re-assigning an existing keyword updates
    the existing rule rather than piling up duplicates. Returns the rule id.
    """
    norm = features.normalize_keyword(keyword)
    if not norm:
        raise ValueError("keyword is empty after normalization")
    if not category or not category.strip():
        raise ValueError("category is required")
    category = category.strip()
    sub_category = sub_category.strip() if sub_category and sub_category.strip() else None

    con = db.connection
    existing = con.execute(
        "SELECT id FROM category_rules WHERE keyword = ?", (norm,)
    ).fetchone()
    if existing:
        con.execute(
            "UPDATE category_rules SET category=?, sub_category=?, priority=? WHERE id=?",
            (category, sub_category, priority, existing[0]),
        )
        con.commit()
        return existing[0]
    cur = con.execute(
        "INSERT INTO category_rules(keyword, category, sub_category, priority) VALUES (?,?,?,?)",
        (norm, category, sub_category, priority),
    )
    con.commit()
    return cur.lastrowid


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
        sets.append("category=?"); params.append(category.strip())
    if sub_category is not None:
        sets.append("sub_category=?")
        params.append(sub_category.strip() or None)
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
        "SELECT id, keyword, category, sub_category, priority "
        "FROM category_rules ORDER BY category, keyword"
    ).fetchall()
    return [Rule(*row) for row in rows]


# --- manual per-transaction overrides ----------------------------------------

def set_override(
    db: Database,
    txn_id: int,
    category: str,
    sub_category: Optional[str] = None,
) -> None:
    """Force a single transaction's category, overriding any rule."""
    if not category or not category.strip():
        raise ValueError("category is required")
    con = db.connection
    con.execute(
        "UPDATE transactions SET category_override=?, sub_category_override=? WHERE id=?",
        (category.strip(), (sub_category.strip() if sub_category and sub_category.strip() else None), txn_id),
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


# --- review queries ----------------------------------------------------------

def uncategorized_keywords(db: Database, direction: str = "debit") -> list[KeywordSpend]:
    """Merchant keywords with no matching rule, ranked by total spend then count.

    Transactions with a manual override are excluded — they're already handled.
    This is the feed for the review screen: biggest unbucketed spend first.
    """
    rows = db.connection.execute(
        """
        SELECT t.merchant_keyword, COUNT(*) AS n, SUM(t.amount) AS total
        FROM transactions t
        WHERE t.direction = ?
          AND t.merchant_keyword IS NOT NULL AND t.merchant_keyword <> ''
          AND t.category_override IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM category_rules r WHERE r.keyword = t.merchant_keyword
          )
        GROUP BY t.merchant_keyword
        ORDER BY total DESC, n DESC
        """,
        (direction,),
    ).fetchall()
    return [KeywordSpend(keyword=k, txn_count=n, total_amount=total) for k, n, total in rows]


def category_totals(db: Database, direction: str = "debit") -> list[tuple[str, float, int]]:
    """(effective_category, total_amount, txn_count) using live resolution."""
    rows = db.connection.execute(
        """
        SELECT effective_category, SUM(amount), COUNT(*)
        FROM v_transactions_resolved
        WHERE direction = ?
        GROUP BY effective_category
        ORDER BY SUM(amount) DESC
        """,
        (direction,),
    ).fetchall()
    return [(c, total, n) for c, total, n in rows]


def distinct_categories(db: Database) -> list[str]:
    """Categories already in use (rules + overrides), for UI dropdowns."""
    rows = db.connection.execute(
        """
        SELECT category FROM category_rules
        UNION
        SELECT category_override FROM transactions WHERE category_override IS NOT NULL
        ORDER BY 1
        """
    ).fetchall()
    return [r[0] for r in rows]
