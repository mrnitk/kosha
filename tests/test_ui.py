"""Headless (offscreen) tests for the Phase-3 UI widgets."""

from __future__ import annotations

import pytest

from kosha import categorization as cat
from kosha import crypto
from kosha.db import Database
from kosha.ui.categorization_view import CategorizationView
from kosha.ui.main_window import MainWindow
from kosha.ui.rules_view import RulesView
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
    # All unreviewed keywords, ranked by amount: ACME (90000) > ZERODHA > SWIGGY.
    assert view._table.rowCount() == 3
    assert view._table.item(0, 0).text() == "ACME EMPLOYER"
    db.lock()


def test_view_assign_creates_rule_and_drops_row(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    before = view._table.rowCount()

    view.assign("SWIGGY LIMITED", "Expense", "Food")

    assert view._table.rowCount() == before - 1
    rules = {r.keyword: r for r in cat.list_rules(db)}
    assert rules["SWIGGY LIMITED"].sub_category == "Food"
    # Totals panel shows the fixed categories.
    totals = [view._totals.item(r, 0).text() for r in range(view._totals.rowCount())]
    assert "Expense" in totals and "Income" in totals
    db.lock()


def test_bulk_assign_multiple_keywords(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    view.assign(["SWIGGY LIMITED", "ZERODHA"], "Expense", "Misc")
    keywords = {r.keyword for r in cat.list_rules(db) if r.sub_category == "Misc"}
    assert keywords == {"SWIGGY LIMITED", "ZERODHA"}
    db.lock()


def test_selected_keywords_tracks_selection(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    view._table.selectRow(0)
    assert view.selected_keywords() == ["ACME EMPLOYER"]
    db.lock()


def test_keyword_detail_shows_transactions(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    # Select the SWIGGY row (has 2 transactions) and check the drill-in panel.
    for r in range(view._table.rowCount()):
        if view._table.item(r, 0).text() == "SWIGGY LIMITED":
            view._table.selectRow(r)
            break
    assert view._detail.rowCount() == 2
    db.lock()


def test_filter_box_narrows_keywords(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    assert view._table.rowCount() == 3
    view._filter_text.setText("swig")          # case-insensitive substring
    assert view._table.rowCount() == 1
    assert view._table.item(0, 0).text() == "SWIGGY LIMITED"
    view._filter_text.clear()
    assert view._table.rowCount() == 3
    db.lock()


def test_filter_by_category_dropdown(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    idx = view._filter_category.findData("Income")
    view._filter_category.setCurrentIndex(idx)
    # Only ACME EMPLOYER (a credit) defaults to Income.
    assert view._table.rowCount() == 1
    assert view._table.item(0, 0).text() == "ACME EMPLOYER"
    db.lock()


def test_assign_with_exclude_flag(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    view.assign("SWIGGY LIMITED", "Expense", "Food", excluded=True)
    rule = {r.keyword: r for r in cat.list_rules(db)}["SWIGGY LIMITED"]
    assert rule.excluded is True
    db.lock()


def test_category_column_renamed(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    assert view._table.horizontalHeaderItem(4).text() == "Category"
    db.lock()


def test_sub_category_totals_and_data_summary_present(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    view.refresh()
    subs = [
        (view._sub_totals.item(r, 0).text(), view._sub_totals.item(r, 1).text())
        for r in range(view._sub_totals.rowCount())
    ]
    assert ("Expense", "Food") in subs
    assert "2026-04-01" in view._data_summary.text()
    db.lock()


def test_review_row_assign_is_direction_scoped(tmp_path):
    db = _make_db(tmp_path)
    # Make SWIGGY bidirectional: add a refund (credit) alongside the debits.
    db.connection.execute(
        "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
        "VALUES (99,'2026-04-02','REFUND SWIGGY',150,'credit',1,'SWIGGY LIMITED','h99')")
    db.connection.commit()

    view = CategorizationView(db)
    # Two SWIGGY slices now exist (debit + credit); select the credit one.
    target = None
    for r in range(view._table.rowCount()):
        ks = view._keywords[r]
        if ks.keyword == "SWIGGY LIMITED" and ks.direction == "credit":
            target = r
            break
    assert target is not None
    view._table.selectRow(target)
    view._category.setCurrentIndex(view._category.findData("Income"))
    view._sub_category.setText("Refund")
    view._on_assign()

    rules = {(r.keyword, r.direction): r for r in cat.list_rules(db)}
    assert rules[("SWIGGY LIMITED", "credit")].sub_category == "Refund"
    # The debit slice is untouched and still pending review.
    pending = {(k.keyword, k.direction) for k in cat.unreviewed_keywords(db)}
    assert ("SWIGGY LIMITED", "debit") in pending
    assert ("SWIGGY LIMITED", "credit") not in pending
    db.lock()


def test_rules_view_edits_a_mapping(tmp_path):
    db = _make_db(tmp_path)
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    view = RulesView(db)
    # Find and select the SWIGGY rule row.
    target = next(i for i, r in enumerate(view._rules) if r.keyword == "SWIGGY LIMITED")
    view._table.selectRow(target)
    assert view._sub_category.text() == "Food"
    view._sub_category.setText("Dining")
    view._on_save()
    rule = {r.keyword: r for r in cat.list_rules(db)}["SWIGGY LIMITED"]
    assert rule.sub_category == "Dining"
    db.lock()


def test_rules_view_deletes_a_mapping(tmp_path):
    db = _make_db(tmp_path)
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    view = RulesView(db)
    target = next(i for i, r in enumerate(view._rules) if r.keyword == "SWIGGY LIMITED")
    view._table.selectRow(target)
    # _on_delete pops a modal confirm; exercise the engine + refresh path instead.
    cat.delete_rule(db, view._rules[target].id)
    view.refresh()
    assert "SWIGGY LIMITED" not in {r.keyword for r in cat.list_rules(db)}
    assert view._table.rowCount() == 0
    db.lock()


def test_main_window_has_rules_tab(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    titles = [win._tabs.tabText(i) for i in range(win._tabs.count())]
    assert titles == ["Dashboard", "Categorize", "Rules"]
    win.close()


def test_dashboard_has_source_and_subcategory_columns(tmp_path):
    db = _make_db(tmp_path)
    from kosha.ui.dashboard_view import DashboardView
    dash = DashboardView(db)
    headers = [dash._table.horizontalHeaderItem(c).text() for c in range(dash._table.columnCount())]
    assert "Source" in headers and "Sub-category" in headers
    db.lock()


def test_dashboard_defaults_to_last_six_months(tmp_path):
    from datetime import date
    from kosha.ui.dashboard_view import DashboardView, _months_back
    db = _make_db(tmp_path)
    dash = DashboardView(db)
    flt = dash.current_filter()
    assert flt.end == date.today()
    assert flt.start == _months_back(date.today(), 6)
    db.lock()


def test_ignore_button_excludes_keyword(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    target = next(i for i, ks in enumerate(view._keywords) if ks.keyword == "SWIGGY LIMITED")
    view._table.selectRow(target)
    view._on_ignore()
    from kosha import categorization as cat
    assert "SWIGGY LIMITED" not in {k.keyword for k in cat.unreviewed_keywords(db)}
    db.lock()


def test_main_window_has_template_actions(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    file_menu = next(m for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))
                     if "File" in m.title())
    labels = [a.text() for a in file_menu.actions()]
    assert any("Import from" in t and "template" in t.lower() for t in labels)
    assert any("Download" in t and "template" in t.lower() for t in labels)
    win.close()


def test_main_window_has_clear_action(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    file_menu = next(m for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))
                     if "File" in m.title())
    labels = [a.text() for a in file_menu.actions()]
    assert any("Clear all data" in t for t in labels)
    win.close()


def test_main_window_status_and_no_theme_menu(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    assert "4 transactions" in win.statusBar().currentMessage()
    menus = [m.title() for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))]
    assert any("File" in t for t in menus)
    assert not any("View" in t for t in menus)   # theme options removed; light-only
    win.close()                      # triggers db.lock via closeEvent
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
