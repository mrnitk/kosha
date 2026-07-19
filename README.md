# Kosha — Personal Expense Tracker (v3)

A fully local, encrypted personal expense tracker for Windows. Successor to ExpenseTracker_v2. Single user. No cloud, no network calls anywhere in the app.

## Core Requirements

1. **Statement import** — Import bank and credit card statements (PDF / Excel / CSV) into a local database.
2. **Feature engineering** — On import, derive: transaction type (UPI / NEFT / IMPS / card / ATM / standing instruction), merchant keyword (extracted from the raw description), and sub-category.
3. **User-controlled categorization** — The user buckets keywords into categories/sub-categories via the UI. Rules apply retroactively to all historical transactions. Per-transaction manual overrides always win over rules.
4. **Interactive visualizations** — User-adjustable charts: date range, category filters, granularity (month/quarter/year), drill-down from chart to transaction list.
5. **Trends** — Expense, income, and savings-rate trends over time; category breakdowns; top merchants; month-on-month deltas.
6. **Fully local + encrypted** — Entire database encrypted at rest (SQLCipher, AES-256). Master password on launch; key derived via Argon2/PBKDF2; key never stored on disk. Zero network calls.
7. **Normal Windows app** — Installs via a proper installer, Start Menu shortcut, runs like native software.

## Tech Stack

- **Language:** Python 3.12
- **UI:** PySide6 (Qt Widgets); Plotly charts embedded via QWebEngineView
- **Database:** SQLite encrypted with SQLCipher (`sqlcipher3` package)
- **Parsing:** pandas + openpyxl (Excel/CSV), pdfplumber (PDF statements)
- **Packaging:** PyInstaller (`--onedir` mode) wrapped with Inno Setup installer
- **Data location:** `%APPDATA%\Kosha\` (survives app updates)

## Database Schema (initial)

```sql
-- All statements land here with a common schema
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    txn_date DATE NOT NULL,
    raw_description TEXT NOT NULL,        -- verbatim from statement, never modified
    amount REAL NOT NULL,
    direction TEXT NOT NULL,              -- 'debit' | 'credit'
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    txn_type TEXT,                        -- 'UPI' | 'NEFT' | 'IMPS' | 'CARD' | 'ATM' | 'SI' | 'OTHER'
    merchant_keyword TEXT,                -- extracted, normalized (e.g. 'SWIGGY')
    category_override TEXT,               -- manual per-transaction override; NULL = use rules
    sub_category_override TEXT,
    import_batch_id INTEGER REFERENCES import_batches(id),
    dedup_hash TEXT UNIQUE                -- hash(date + amount + normalized description + account)
);

CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,                   -- e.g. 'HDFC Savings', 'Amex Card'
    account_type TEXT NOT NULL,           -- 'bank' | 'credit_card'
    institution TEXT NOT NULL             -- maps to parser class
);

CREATE TABLE category_rules (
    id INTEGER PRIMARY KEY,
    keyword TEXT NOT NULL,                -- matched against merchant_keyword
    category TEXT NOT NULL,
    sub_category TEXT,
    priority INTEGER DEFAULT 0            -- higher priority wins on conflicts
);

CREATE TABLE import_batches (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER
);
```

Effective category resolution (in queries/views): `COALESCE(category_override, rule_match.category, 'Uncategorized')`.

## Architecture Notes

- **One parser class per institution**, all producing the common transactions schema. Base class defines the interface; institution classes handle layout specifics.
- **Deduplication:** `dedup_hash` unique constraint means re-importing an overlapping statement silently skips duplicates (report skipped count to user).
- **Raw description is immutable.** All enrichment (type, keyword, category) is derived and re-derivable.
- **Categorization UI:** show uncategorized merchant keywords ranked by total spend and frequency; user assigns category via dropdown. Assignments write to `category_rules` and re-resolve historical data instantly.
- **Charts:** Plotly HTML rendered in QWebEngineView. Core dashboards: (a) monthly expense by category (stacked bar), (b) income vs expense vs savings rate line, (c) category drill-down → transaction table, (d) top merchants, (e) MoM deltas.
- **Security:** SQLCipher pragma key set from Argon2-derived key at unlock. Lock the app (drop key from memory) on close. Encrypted backups = file copies of the DB to a user-chosen folder.

## Build Order (work in phases; each phase must run before moving on)

1. **Phase 1 — Core DB:** Project skeleton, venv, dependencies. SQLCipher database module: create/unlock/lock, schema migration, master password setup flow (CLI-level is fine for now).
2. **Phase 2 — First parser end-to-end:** One institution's Excel/CSV parser → feature engineering (txn_type, merchant_keyword) → insert with dedup. Test against sample files in `samples/`.
3. **Phase 3 — Categorization engine + UI:** Rules table CRUD, uncategorized-keyword review screen, retroactive resolution, manual overrides.
4. **Phase 4 — Dashboard:** Main window, Plotly views with filters (date range, categories, granularity), drill-down.
5. **Phase 5 — Remaining parsers:** Add other institutions (including PDF-based via pdfplumber).
6. **Phase 6 — Packaging:** PyInstaller onedir build, Inno Setup installer, app icon, `%APPDATA%` data path handling, first-run experience.

## Conventions

- Type hints everywhere; `pytest` tests for parsers and the rules engine (parsers are the highest-risk code).
- Sample statements in `samples/` are **sanitized** (fake account numbers, real layout). Never commit real financial data — `.gitignore` the data directory and any real statements.
- Git checkpoint at the end of every working phase.
