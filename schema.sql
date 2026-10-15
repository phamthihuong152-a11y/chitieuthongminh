-- Chi Tiêu Thông Minh: cấu trúc cơ sở dữ liệu SQLite.
--
-- File này là NGUỒN DUY NHẤT của cấu trúc CSDL: app.py đọc và chạy nó mỗi lần khởi động (hàm init_db).
-- Mọi câu lệnh đều dùng IF NOT EXISTS nên chạy lại nhiều lần vẫn an toàn và không xóa dữ liệu.
-- Quy ước: tên cột dạng snake_case, ngày lưu chuỗi ISO 'YYYY-MM-DD', số tiền là số nguyên VND.
-- Mọi bảng dữ liệu cá nhân đều có user_id, ứng dụng luôn lọc theo user_id của người đang đăng nhập.

-- Tài khoản người dùng. Mật khẩu chỉ lưu dạng băm (password_hash), không lưu mật khẩu gốc.
CREATE TABLE IF NOT EXISTS users(
    id            INTEGER PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Danh mục thu/chi dùng chung cho mọi người dùng.
CREATE TABLE IF NOT EXISTS categories(
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('income','expense'))
);

-- Giao dịch thu/chi. kind phải khớp với kind của danh mục (ứng dụng kiểm tra khi lưu).
CREATE TABLE IF NOT EXISTS transactions(
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK(kind IN ('income','expense')),
    amount      INTEGER NOT NULL CHECK(amount>0),
    tx_date     TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    note        TEXT DEFAULT '',
    user_id     INTEGER REFERENCES users(id)
);

-- Ngân sách theo người dùng, tháng ('YYYY-MM') và danh mục chi.
CREATE TABLE IF NOT EXISTS budgets(
    id           INTEGER PRIMARY KEY,
    month        TEXT NOT NULL,
    category_id  INTEGER NOT NULL REFERENCES categories(id),
    limit_amount INTEGER NOT NULL CHECK(limit_amount>0),
    user_id      INTEGER REFERENCES users(id),
    UNIQUE(user_id, month, category_id)
);

-- Mục tiêu tiết kiệm. Số đã tích lũy = tổng amount trong goal_deposits.
CREATE TABLE IF NOT EXISTS goals(
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK(kind IN ('shopping','travel','emergency','other')),
    target_amount INTEGER NOT NULL CHECK(target_amount>0),
    deadline      TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Lịch sử nạp (amount > 0) và rút (amount < 0) của từng mục tiêu. Xóa mục tiêu thì xóa luôn lịch sử.
CREATE TABLE IF NOT EXISTS goal_deposits(
    id       INTEGER PRIMARY KEY,
    goal_id  INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id),
    amount   INTEGER NOT NULL CHECK(amount<>0),
    dep_date TEXT NOT NULL,
    note     TEXT DEFAULT ''
);

-- Khoản thu/chi định kỳ. next_due là ngày đến hạn kế tiếp chưa được xử lý,
-- anchor_day là ngày gốc trong tháng (để tháng ngắn co lại rồi trở về đúng ngày),
-- auto = 1: tự ghi nhận giao dịch khi đến hạn, active = 0: đang tạm dừng.
CREATE TABLE IF NOT EXISTS recurring(
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL CHECK(kind IN ('income','expense')),
    amount      INTEGER NOT NULL CHECK(amount>0),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    note        TEXT NOT NULL DEFAULT '',
    frequency   TEXT NOT NULL CHECK(frequency IN ('weekly','monthly','yearly')),
    start_date  TEXT NOT NULL,
    anchor_day  INTEGER NOT NULL,
    next_due    TEXT NOT NULL,
    end_date    TEXT,
    remind_days INTEGER NOT NULL DEFAULT 3 CHECK(remind_days BETWEEN 0 AND 30),
    auto        INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tx_user_date  ON transactions(user_id, tx_date);
CREATE INDEX IF NOT EXISTS idx_goal_user     ON goals(user_id);
CREATE INDEX IF NOT EXISTS idx_goal_dep_goal ON goal_deposits(goal_id);
CREATE INDEX IF NOT EXISTS idx_rec_user      ON recurring(user_id, next_due);

-- 11 danh mục mặc định, chỉ thêm khi bảng categories còn trống.
INSERT INTO categories(name, kind)
SELECT column1, column2 FROM (VALUES
    ('Lương','income'), ('Thưởng','income'), ('Thu nhập khác','income'),
    ('Ăn uống','expense'), ('Đi lại','expense'), ('Mua sắm','expense'),
    ('Hóa đơn','expense'), ('Giải trí','expense'), ('Sức khỏe','expense'),
    ('Nhà ở','expense'), ('Chi phí khác','expense')
)
WHERE NOT EXISTS (SELECT 1 FROM categories);
