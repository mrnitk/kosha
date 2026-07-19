"""Headless (offscreen) tests for the Phase-3 UI widgets."""

from __future__ import annotations

import pytest

from kosha import categorization as cat
from kosha import crypto
from kosha.db import Database
from kosha.ui.categorization_view import CategorizationView
from kosha.ui.main_window import MainWindow
from kosha.ui.unlock import UnlockDialog

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"

pytestmark = pytest.mark.usefixtures("qapp")


def _make_db(tmp_path) -> Database:
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    seed = [
        (1, "SWIGGY LIMITED", 300.0, "debit"),
        (2, "SWIGGY LIMITED", 200.0, "debit"),
        (3, "ZERODHA", 5000.0, "debit"),
        (4, "ACME EMPLOYER", 90000.0, "credit"),
    ]
    for tid, kw, amt, direction in seed:
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (tid, "2026-04-01", f"D {kw}", amt, direction, kw, f"h{tid}"),
        )
    con.commit()
    return d


def test_view_lists_uncategorized_ranked(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    # ZERODHA (5000) ranks above SWIGGY (500); credit ACME excluded.
    assert view._table.rowCount() == 2
    assert view._table.item(0, 0).text() == "ZERODHA"
    db.lock()


def test_view_assign_creates_rule_and_drops_row(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    before = view._table.rowCount()

    view.assign("SWIGGY LIMITED", "Food", "Delivery")

    assert view._table.rowCount() == before - 1
    assert "SWIGGY LIMITED" in [r.keyword for r in cat.list_rules(db)]
    # New category shows in the totals panel.
    totals = [view._totals.item(r, 0).text() for r in range(view._totals.rowCount())]
    assert "Food" in totals
    db.lock()


def test_selected_keyword_tracks_selection(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    view._table.selectRow(0)
    assert view.selected_keyword() == "ZERODHA"
    db.lock()


def test_main_window_status_and_import_menu(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    assert "4 transactions" in win.statusBar().currentMessage()
    menus = [m.title() for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))]
    assert any("File" in t for t in menus)
    win.close()  # triggers db.lock via closeEvent
    assert not db.is_unlocked


def test_unlock_dialog_creates_then_unlocks(tmp_path):
    db = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")

    # First run: create flow.
    create = UnlockDialog(db)
    assert create._creating is True
    create._pw.setText("supersecret")
    create._confirm.setText("supersecret")
    create._on_accept()
    assert db.exists and db.is_unlocked
    db.lock()

    # Second run: unlock flow with wrong then right password.
    unlock = UnlockDialog(db)
    assert unlock._creating is False
    unlock._pw.setText("wrongpass")
    unlock._on_accept()
    assert not db.is_unlocked                 # rejected, dialog stays open
    unlock._pw.setText("supersecret")
    unlock._on_accept()
    assert db.is_unlocked
    db.lock()
