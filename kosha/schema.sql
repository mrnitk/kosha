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

-- Effective category resolution, evaluated live so new/edited rules apply
-- retroactively to all history with no data rewrite.
--
-- Two levels:
--   * category     in {Income, Expense, Savings}. Defaults by direction
--     (credit -> Income, debit -> Expense); a rule/override can reclassify
--     (e.g. mark an investment debit as Savings).
--   * sub_category  free-text detail (Food, Rent, Investments, ...).
-- Precedence: manual override > best matching rule > direction default.
-- "best matching rule" = highest priority for a keyword, newest id breaks ties.
-- Dropped-and-recreated so re-applying the schema (on migration) always picks
-- up a changed definition.
DROP VIEW IF EXISTS v_transactions_resolved;
CREATE VIEW v_transactions_resolved AS
SELECT
    t.id, t.txn_date, t.raw_description, t.amount, t.direction,
    t.account_id, t.txn_type, t.merchant_keyword,
    t.category_override, t.sub_category_override,
    t.import_batch_id, t.dedup_hash,
    COALESCE(
        t.category_override,
        br.category,
        CASE t.direction WHEN 'credit' THEN 'Income' ELSE 'Expense' END
    ) AS effective_category,
    COALESCE(t.sub_category_override, br.sub_category) AS effective_sub_category
FROM transactions t
LEFT JOIN (
    SELECT keyword, category, sub_category FROM (
        SELECT keyword, category, sub_category,
               ROW_NUMBER() OVER (PARTITION BY keyword ORDER BY priority DESC, id DESC) AS rn
        FROM category_rules
    ) WHERE rn = 1
) br ON br.keyword = t.merchant_keyword;
