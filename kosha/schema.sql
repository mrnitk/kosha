-- Kosha schema v1
-- Applied inside the encrypted (SQLCipher) database only.

-- Institutions the user has statements from; maps to a parser class.
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,              -- e.g. 'HDFC Savings', 'Amex Card'
    account_type  TEXT NOT NULL,              -- 'bank' | 'credit_card'
    institution   TEXT NOT NULL               -- maps to parser class
);

-- One row per imported file.
CREATE TABLE IF NOT EXISTS import_batches (
    id           INTEGER PRIMARY KEY,
    source_file  TEXT NOT NULL,
    account_id   INTEGER REFERENCES accounts(id),
    imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count    INTEGER
);

-- All statements land here with a common schema.
CREATE TABLE IF NOT EXISTS transactions (
    id                    INTEGER PRIMARY KEY,
    txn_date              DATE NOT NULL,
    raw_description       TEXT NOT NULL,       -- verbatim from statement, never modified
    amount                REAL NOT NULL,
    direction             TEXT NOT NULL,       -- 'debit' | 'credit'
    account_id            INTEGER NOT NULL REFERENCES accounts(id),
    txn_type              TEXT,                -- 'UPI'|'NEFT'|'IMPS'|'CARD'|'ATM'|'SI'|'OTHER'
    merchant_keyword      TEXT,                -- extracted, normalized (e.g. 'SWIGGY')
    category_override     TEXT,                -- manual per-txn override; NULL = use rules
    sub_category_override TEXT,
    import_batch_id       INTEGER REFERENCES import_batches(id),
    dedup_hash            TEXT UNIQUE          -- hash(date+amount+norm desc+account)
);

-- User-controlled keyword -> category mappings; applied retroactively.
CREATE TABLE IF NOT EXISTS category_rules (
    id            INTEGER PRIMARY KEY,
    keyword       TEXT NOT NULL,              -- matched against merchant_keyword
    category      TEXT NOT NULL,
    sub_category  TEXT,
    priority      INTEGER DEFAULT 0           -- higher priority wins on conflicts
);

CREATE INDEX IF NOT EXISTS idx_txn_date        ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_account     ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_txn_keyword     ON transactions(merchant_keyword);
CREATE INDEX IF NOT EXISTS idx_rules_keyword   ON category_rules(keyword);
