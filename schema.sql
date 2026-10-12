-- Chi Tiêu Thông Minh - Database Schema (SQLite)
-- Theo PRD v1.0

CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('income', 'expense'))
);

CREATE TABLE transactions (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
    amount REAL NOT NULL CHECK(amount > 0),
    date TEXT NOT NULL,                    -- YYYY-MM-DD
    category_id TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,              -- ISO datetime
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE budgets (
    id TEXT PRIMARY KEY,
    month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
    year INTEGER NOT NULL,
    category_id TEXT,                      -- NULL = ngân sách tổng chi tiêu
    limit_amount REAL NOT NULL CHECK(limit_amount > 0),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE INDEX idx_tx_date ON transactions(date);
CREATE INDEX idx_tx_type ON transactions(type);
CREATE INDEX idx_tx_category ON transactions(category_id);
CREATE INDEX idx_budget_period ON budgets(year, month);

-- Dữ liệu mẫu đã có sẵn trong file chi_tieu.db
-- Tổng thu: 17.000.000 | Tổng chi: 5.650.000 | Số dư: 11.350.000
