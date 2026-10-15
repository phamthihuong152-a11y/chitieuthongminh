-- Lược đồ cơ sở dữ liệu Chi Tiêu Thông Minh (Khớp chuẩn với app.py)

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('income','expense'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('income','expense')),
    amount INTEGER NOT NULL CHECK(amount>0),
    tx_date TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY,
    month TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    limit_amount INTEGER NOT NULL CHECK(limit_amount>0),
    UNIQUE(month, category_id)
);

-- Dữ liệu mẫu khởi tạo tự động qua app.init_db() khi chạy ứng dụng lần đầu.
