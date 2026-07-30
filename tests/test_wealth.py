"""Tests for the net-worth module: assets, liabilities, snapshots, analytics."""

from __future__ import annotations

from datetime import date

import pytest

from kosha import crypto, wealth
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    yield d
    d.lock()


@pytest.fixture()
def seeded(db):
    """A small portfolio modelled on the user's spreadsheet."""
    cash = wealth.add_asset(db, "HDFC-Cash", "Bank", "Cash", "High", "Me", invested=0)
    fd = wealth.add_asset(db, "SBI-FD/RD", "Bank", "Debt", "High", "Me", invested=500000)
    mf = wealth.add_asset(db, "Mine (Non Tax saving)", "Mutual funds", "Equity", "Medium",
                          "Me", invested=600000)
    mom = wealth.add_asset(db, "Mom-HDFC-Cash", "Bank", "Cash", "High", "Mom", invested=0)
    nps = wealth.add_asset(db, "NPS", "NPS", "Hybrid", "Lowest", "Me", invested=150000)
    wealth.record_snapshot(db, date(2025, 12, 1),
                           {cash: 81000, fd: 500000, mf: 1114000, mom: 250000, nps: 181000})
    wealth.record_snapshot(db, date(2026, 6, 1),
                           {cash: 92000, fd: 500000, mf: 789000, mom: 547000, nps: 180000})
    return db, {"cash": cash, "fd": fd, "mf": mf, "mom": mom, "nps": nps}


# --- assets ------------------------------------------------------------------

def test_add_and_list_assets(db):
    wealth.add_asset(db, "HDFC-Cash", "Bank", "Cash", "High")
    assets = wealth.list_assets(db)
    assert len(assets) == 1
    a = assets[0]
    assert a.name == "HDFC-Cash" and a.owner == "Me" and a.is_active
    assert a.counts_toward_networth is True


def test_asset_name_required(db):
    with pytest.raises(ValueError):
        wealth.add_asset(db, "   ", "Bank", "Cash", "High")


def test_update_and_retire_asset(db):
    aid = wealth.add_asset(db, "Old FD", "Bank", "Debt", "High")
    wealth.update_asset(db, aid, name="New FD", invested=1000, is_active=False)
    a = wealth.list_assets(db)[0]
    assert a.name == "New FD" and a.invested == 1000 and not a.is_active
    assert wealth.list_assets(db, active_only=True) == []      # retired, history kept


def test_delete_asset_removes_history(db):
    aid = wealth.add_asset(db, "Temp", "Bank", "Cash", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {aid: 500})
    wealth.delete_asset(db, aid)
    assert wealth.list_assets(db) == []
    assert wealth.snapshot_dates(db) == []


# --- snapshots ---------------------------------------------------------------

def test_snapshot_upsert_replaces_same_date(db):
    aid = wealth.add_asset(db, "Cash", "Bank", "Cash", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {aid: 100})
    wealth.record_snapshot(db, date(2026, 1, 1), {aid: 250})   # same date -> replace
    assert wealth.snapshot_dates(db) == ["2026-01-01"]
    assert wealth.latest_values(db)[0][aid] == 250


def test_latest_values_carries_forward(seeded):
    db, ids = seeded
    aid = wealth.add_asset(db, "Gold", "Other", "Equity", "Medium")
    wealth.record_snapshot(db, date(2025, 12, 1), {aid: 50000})
    # Not updated in June, so its December value carries forward.
    assets, _ = wealth.latest_values(db, date(2026, 6, 1))
    assert assets[aid] == 50000


def test_latest_values_respects_as_of(seeded):
    db, ids = seeded
    assets, _ = wealth.latest_values(db, date(2025, 12, 31))
    assert assets[ids["cash"]] == 81000        # December value, not June's
    assets_now, _ = wealth.latest_values(db)
    assert assets_now[ids["cash"]] == 92000


def test_delete_snapshot(seeded):
    db, _ = seeded
    wealth.delete_snapshot(db, date(2026, 6, 1))
    assert wealth.snapshot_dates(db) == ["2025-12-01"]


# --- net worth ---------------------------------------------------------------

def test_networth_series_and_growth(seeded):
    db, _ = seeded
    series = wealth.networth_series(db)
    assert [p.as_of for p in series] == ["2025-12-01", "2026-06-01"]
    dec, jun = series
    assert dec.assets == 2126000 and dec.liabilities == 0
    assert dec.net_worth == 2126000 and dec.growth_pct is None      # first point
    assert jun.assets == 2108000
    expected = (jun.net_worth - dec.net_worth) / dec.net_worth * 100
    assert round(jun.growth_pct, 6) == round(expected, 6)


def test_networth_subtracts_liabilities(seeded):
    db, _ = seeded
    lid = wealth.add_liability(db, "Car loan - HDFC", "Car loan", principal=800000,
                               interest_rate=9.5, emi_amount=16000)
    wealth.record_snapshot(db, date(2026, 6, 1), liability_values={lid: 700000})
    jun = wealth.networth_series(db)[-1]
    assert jun.liabilities == 700000
    assert jun.net_worth == jun.assets - 700000


def test_excluded_asset_not_counted(seeded):
    db, _ = seeded
    before = wealth.current_networth(db).assets
    info = wealth.add_asset(db, "Reference only", "Other", "Cash", "High",
                            counts_toward_networth=False)
    wealth.record_snapshot(db, date(2026, 6, 1), {info: 999999})
    assert wealth.current_networth(db).assets == before


def test_current_networth_empty_db(db):
    point = wealth.current_networth(db)
    assert point.net_worth == 0 and point.growth_pct is None


def test_total_growth_pct(seeded):
    db, _ = seeded
    series = wealth.networth_series(db)
    expected = (series[-1].net_worth - series[0].net_worth) / series[0].net_worth * 100
    assert round(wealth.total_growth_pct(db), 6) == round(expected, 6)


# --- allocations -------------------------------------------------------------

def test_allocation_by_liquidity_sums_to_100(seeded):
    db, _ = seeded
    rows = wealth.allocation(db, "liquidity")
    assert round(sum(pct for _b, _a, pct in rows), 6) == 100.0
    buckets = {b: amt for b, amt, _p in rows}
    assert buckets["High"] == 92000 + 500000 + 547000
    assert buckets["Lowest"] == 180000


def test_allocation_by_owner(seeded):
    db, _ = seeded
    rows = {b: (amt, pct) for b, amt, pct in wealth.allocation(db, "owner")}
    assert rows["Mom"][0] == 547000
    assert round(rows["Me"][1] + rows["Mom"][1], 6) == 100.0


def test_allocation_by_type_and_category(seeded):
    db, _ = seeded
    by_type = {b: amt for b, amt, _p in wealth.allocation(db, "asset_type")}
    assert by_type["Equity"] == 789000 and by_type["Hybrid"] == 180000
    by_cat = {b: amt for b, amt, _p in wealth.allocation(db, "category")}
    assert by_cat["Mutual funds"] == 789000


def test_allocation_rejects_unknown_dimension(db):
    with pytest.raises(ValueError):
        wealth.allocation(db, "nonsense")


# --- liabilities -------------------------------------------------------------

def test_liability_crud_and_emi(db):
    lid = wealth.add_liability(db, "Car loan", "Car loan", principal=800000,
                               emi_amount=16000, interest_rate=9.5,
                               start_date=date(2026, 7, 1))
    liab = wealth.list_liabilities(db)[0]
    assert liab.name == "Car loan" and liab.emi_amount == 16000
    assert liab.start_date == "2026-07-01"
    assert wealth.monthly_obligations(db) == 16000
    wealth.update_liability(db, lid, is_active=False)
    assert wealth.monthly_obligations(db) == 0       # inactive loans don't count
    wealth.delete_liability(db, lid)
    assert wealth.list_liabilities(db) == []


def test_debt_to_asset(seeded):
    db, _ = seeded
    assert wealth.debt_to_asset(db) == 0.0
    lid = wealth.add_liability(db, "Car loan", "Car loan", emi_amount=16000)
    wealth.record_snapshot(db, date(2026, 6, 1), liability_values={lid: 527000})
    ratio = wealth.debt_to_asset(db)
    assert round(ratio, 2) == round(527000 / 2108000 * 100, 2)


def test_liability_outstanding_declines(db):
    lid = wealth.add_liability(db, "Car loan", "Car loan", principal=800000)
    wealth.record_snapshot(db, date(2026, 7, 1), liability_values={lid: 800000})
    wealth.record_snapshot(db, date(2026, 12, 1), liability_values={lid: 720000})
    series = wealth.networth_series(db)
    assert [p.liabilities for p in series] == [800000, 720000]
    assert [p.net_worth for p in series] == [-800000, -720000]


# --- gains, matrix, insurance ------------------------------------------------

def test_invested_vs_current(seeded):
    db, _ = seeded
    rows = {name: (inv, cur, gain) for name, inv, cur, gain in wealth.invested_vs_current(db)}
    inv, cur, gain = rows["Mine (Non Tax saving)"]
    assert inv == 600000 and cur == 789000 and gain == 189000


def test_snapshot_matrix_shape(seeded):
    db, _ = seeded
    lid = wealth.add_liability(db, "Car loan", "Car loan")
    wealth.record_snapshot(db, date(2026, 6, 1), liability_values={lid: 700000})
    dates, rows = wealth.snapshot_matrix(db)
    assert dates == ["2025-12-01", "2026-06-01"]
    by_name = dict(rows)
    assert by_name["HDFC-Cash"] == [81000, 92000]
    # Liabilities are negative and only present where recorded.
    assert by_name["Car loan (liability)"] == [None, -700000]


def test_insurance_is_informational(seeded):
    db, _ = seeded
    before = wealth.current_networth(db).assets
    wealth.add_insurance(db, "Medical - Self - HDFC ergo", "Medical",
                         premium_per_year=16000, coverage=2000000)
    wealth.add_insurance(db, "LIC", "Life", premium_per_year=17850)
    assert wealth.current_networth(db).assets == before      # never counted
    premium, coverage = wealth.insurance_summary(db)
    assert premium == 33850 and coverage == 2000000
    assert len(wealth.list_insurance(db)) == 2


def test_insurance_delete(db):
    iid = wealth.add_insurance(db, "Temp", "Other")
    wealth.delete_insurance(db, iid)
    assert wealth.list_insurance(db) == []


# --- settings ----------------------------------------------------------------

def test_settings_roundtrip(db):
    assert wealth.get_setting(db, "auto_lock_minutes", "5") == "5"
    wealth.set_setting(db, "auto_lock_minutes", "10")
    assert wealth.get_setting(db, "auto_lock_minutes") == "10"
    wealth.set_setting(db, "auto_lock_minutes", "15")            # upsert
    assert wealth.get_setting(db, "auto_lock_minutes") == "15"


# --- migration ---------------------------------------------------------------

def test_v11_tables_added_to_existing_vault(tmp_path):
    """An existing expense vault gains the wealth tables without losing data."""
    db_file, salt_file = tmp_path / "k.db", tmp_path / "k.salt"
    d = Database(db_file=db_file, salt_file=salt_file)
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','x')")
    con.execute("INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,dedup_hash) "
                "VALUES (1,'2026-04-01','D SWIGGY',300,'debit',1,'h1')")
    con.execute("PRAGMA user_version = 10")          # pretend it's a v10 vault
    con.commit()
    d.lock()

    d2 = Database(db_file=db_file, salt_file=salt_file)
    d2.unlock(PW)                                    # triggers migration
    assert d2.connection.execute("PRAGMA user_version").fetchone()[0] == 11
    tables = {r[0] for r in d2.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"assets", "asset_valuations", "liabilities", "liability_valuations",
            "insurance", "app_settings"} <= tables
    # Expense data survived.
    assert d2.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1
    d2.lock()
