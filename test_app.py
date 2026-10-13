"""
Kịch bản kiểm thử tự động cho ứng dụng Chi Tiêu Thông Minh (có đăng nhập nhiều người dùng)
1. Khởi tạo DB & danh mục mặc định
2. Thêm, sửa, xóa giao dịch & validation
3. Dashboard
4. Ngân sách & cảnh báo 80% / vượt hạn mức
5. Bộ lọc & xuất CSV (UTF-8 BOM)
6. Dữ liệu SQLite bền vững sau khi khởi động lại
7. Bắt buộc đăng nhập
8. Đăng ký / đăng nhập / đăng xuất / khóa khi sai nhiều lần
9. Cách ly dữ liệu giữa các người dùng
10. Bảo vệ CSRF
11. Nâng cấp từ DB cũ (chưa có user_id)
"""

import re
import sqlite3
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import app

TEST_DB = BASE_DIR / "test_chi_tieu.db"
EMAIL_A, PASS_A = "a@example.com", "matkhau-A-12345"
EMAIL_B, PASS_B = "b@example.com", "matkhau-B-67890"


def register(client, email, password, confirm=None, follow=True):
    return client.post("/register", data={
        "email": email, "password": password,
        "confirm": password if confirm is None else confirm,
    }, follow_redirects=follow)


def login(client, email, password, follow=True):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=follow)


class TestChiTieuThongMinh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_DB.exists():
            TEST_DB.unlink()
        app.DB = TEST_DB
        app.init_db()
        app.LOGIN_FAILS.clear()
        app.app.config["TESTING"] = True
        app.app.config["CSRF_ENABLED"] = False   # test CSRF riêng ở test_10
        cls.client = app.app.test_client()
        res = register(cls.client, EMAIL_A, PASS_A)
        assert res.status_code == 200 and "Đã tạo tài khoản" in res.get_data(as_text=True)

    @classmethod
    def tearDownClass(cls):
        app.app.config["CSRF_ENABLED"] = True
        if TEST_DB.exists():
            try:
                TEST_DB.unlink()
            except Exception:
                pass

    def test_01_init_db_and_categories(self):
        """1. Kiểm tra khởi tạo DB và 11 danh mục mặc định"""
        with app.db() as c:
            cats = c.execute("SELECT id, name, kind FROM categories ORDER BY kind, id").fetchall()
            cat_dict = {r["name"]: r["kind"] for r in cats}
            
            # Kiểm tra số lượng danh mục
            self.assertEqual(len(cats), 11, "Phải có đúng 11 danh mục mặc định")
            
            # Kiểm tra danh mục Thu nhập
            self.assertIn("Lương", cat_dict)
            self.assertEqual(cat_dict["Lương"], "income")
            self.assertIn("Thưởng", cat_dict)
            self.assertEqual(cat_dict["Thưởng"], "income")
            self.assertIn("Thu nhập khác", cat_dict)
            self.assertEqual(cat_dict["Thu nhập khác"], "income")
            
            # Kiểm tra danh mục Chi phí
            for exp in ["Ăn uống", "Đi lại", "Mua sắm", "Hóa đơn", "Giải trí", "Sức khỏe", "Nhà ở", "Chi phí khác"]:
                self.assertIn(exp, cat_dict)
                self.assertEqual(cat_dict[exp], "expense")

    def test_02_crud_transactions_and_validation(self):
        """2. Kiểm tra Thêm, Sửa, Xóa giao dịch và Validation dữ liệu"""
        # Lấy ID danh mục Lương và Ăn uống
        with app.db() as c:
            cat_luong = c.execute("SELECT id FROM categories WHERE name='Lương'").fetchone()["id"]
            cat_an_uong = c.execute("SELECT id FROM categories WHERE name='Ăn uống'").fetchone()["id"]

        # 2.1. Thêm giao dịch Thu (Lương 15.000.000)
        res = self.client.post("/transactions/new", data={
            "kind": "income",
            "amount": "15000000",
            "tx_date": "2026-10-01",
            "category_id": str(cat_luong),
            "note": "Lương tháng 10"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Đã lưu giao dịch", res.get_data(as_text=True))

        # 2.2. Thêm giao dịch Chi (Ăn uống 9.000.000)
        res = self.client.post("/transactions/new", data={
            "kind": "expense",
            "amount": "9000000",
            "tx_date": "2026-10-05",
            "category_id": str(cat_an_uong),
            "note": "Tiền ăn hàng quán"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Đã lưu giao dịch", res.get_data(as_text=True))

        # Kiểm tra dữ liệu trong DB
        with app.db() as c:
            txs = c.execute("SELECT * FROM transactions ORDER BY id").fetchall()
            self.assertEqual(len(txs), 2)
            tx_exp_id = txs[1]["id"]
            self.assertEqual(txs[1]["amount"], 9000000)

        # 2.3. Sửa giao dịch Chi từ 9.000.000 -> 9.500.000
        res = self.client.post(f"/transactions/{tx_exp_id}/edit", data={
            "kind": "expense",
            "amount": "9500000",
            "tx_date": "2026-10-05",
            "category_id": str(cat_an_uong),
            "note": "Tiền ăn sau điều chỉnh"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        with app.db() as c:
            tx_updated = c.execute("SELECT * FROM transactions WHERE id=?", (tx_exp_id,)).fetchone()
            self.assertEqual(tx_updated["amount"], 9500000)
            self.assertEqual(tx_updated["note"], "Tiền ăn sau điều chỉnh")

        # 2.4. Thêm một giao dịch tạm và Xóa
        res = self.client.post("/transactions/new", data={
            "kind": "expense",
            "amount": "500000",
            "tx_date": "2026-10-10",
            "category_id": str(cat_an_uong),
            "note": "Chi phí tạm để test xóa"
        }, follow_redirects=True)
        with app.db() as c:
            temp_tx = c.execute("SELECT id FROM transactions WHERE note='Chi phí tạm để test xóa'").fetchone()
            temp_id = temp_tx["id"]
        
        # Gọi POST xóa
        res_del = self.client.post(f"/transactions/{temp_id}/delete", follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)
        self.assertIn("Đã xóa giao dịch", res_del.get_data(as_text=True))
        with app.db() as c:
            del_check = c.execute("SELECT * FROM transactions WHERE id=?", (temp_id,)).fetchone()
            self.assertIsNone(del_check, "Giao dịch đã xóa không được tồn tại trong DB")

        # 2.5. Kiểm tra Validation từ chối dữ liệu sai
        # Số tiền <= 0
        res_err1 = self.client.post("/transactions/new", data={
            "kind": "expense",
            "amount": "-50000",
            "tx_date": "2026-10-05",
            "category_id": str(cat_an_uong)
        }, follow_redirects=True)
        self.assertIn("Dữ liệu không hợp lệ", res_err1.get_data(as_text=True))

        # Chọn sai loại (Thu nhưng lại chọn danh mục Chi)
        res_err2 = self.client.post("/transactions/new", data={
            "kind": "income",
            "amount": "500000",
            "tx_date": "2026-10-05",
            "category_id": str(cat_an_uong)
        }, follow_redirects=True)
        self.assertIn("Dữ liệu không hợp lệ", res_err2.get_data(as_text=True))

    def test_03_dashboard(self):
        """3. Kiểm tra Dashboard: Tổng thu, Tổng chi, Số dư ròng và Chi tiêu theo danh mục"""
        res = self.client.get("/?month=2026-10")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Tổng thu: 15.000.000 đ
        self.assertIn("15.000.000 đ", html)
        # Tổng chi: 9.500.000 đ
        self.assertIn("9.500.000 đ", html)
        # Số dư ròng: 15.000.000 - 9.500.000 = 5.500.000 đ
        self.assertIn("5.500.000 đ", html)
        # Danh mục Ăn uống
        self.assertIn("Ăn uống", html)

    def test_04_budget_management_and_alerts(self):
        """4. Kiểm tra Ngân sách: Thiết lập hạn mức, cảnh báo >=80% và cảnh báo vượt hạn mức (>=100%)"""
        with app.db() as c:
            cat_an_uong = c.execute("SELECT id FROM categories WHERE name='Ăn uống'").fetchone()["id"]

        # Hiện tại Ăn uống đã chi 9.500.000 đ.
        # Trường hợp 1: Đặt ngân sách 10.000.000 đ -> 9.500.000 / 10.000.000 = 95% (>= 80% và < 100%)
        res = self.client.post("/budgets", data={
            "month": "2026-10",
            "category_id": str(cat_an_uong),
            "limit_amount": "10000000"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("95%", html)
        self.assertIn("Đã dùng ít nhất 80% ngân sách", html)

        # Trường hợp 2: Cập nhật hạn mức xuống 9.000.000 đ (chi 9.500.000 đ > 9.000.000 đ -> >= 100%)
        res2 = self.client.post("/budgets", data={
            "month": "2026-10",
            "category_id": str(cat_an_uong),
            "limit_amount": "9000000"
        }, follow_redirects=True)
        self.assertEqual(res2.status_code, 200)
        html2 = res2.get_data(as_text=True)
        self.assertIn("Đã vượt ngân sách", html2)

        # Kiểm tra trên Dashboard cũng phải hiển thị cảnh báo
        res_dash = self.client.get("/?month=2026-10")
        self.assertIn("Đã vượt ngân sách", res_dash.get_data(as_text=True))

        # Kiểm tra Xóa ngân sách
        with app.db() as c:
            b_id = c.execute("SELECT id FROM budgets WHERE month='2026-10' AND category_id=?", (cat_an_uong,)).fetchone()["id"]
        res_del = self.client.post(f"/budgets/{b_id}/delete", follow_redirects=True)
        self.assertEqual(res_del.status_code, 200)
        self.assertIn("Đã xóa ngân sách", res_del.get_data(as_text=True))

    def test_05_filters_and_csv_export(self):
        """5. Kiểm tra Lọc giao dịch và Xuất file CSV (UTF-8 BOM tiếng Việt)"""
        # Lọc theo loại: Thu
        res_income = self.client.get("/transactions?kind=income")
        html_income = res_income.get_data(as_text=True)
        self.assertIn("Lương tháng 10", html_income)
        self.assertNotIn("Tiền ăn sau điều chỉnh", html_income)

        # Lọc theo loại: Chi
        res_expense = self.client.get("/transactions?kind=expense")
        html_expense = res_expense.get_data(as_text=True)
        self.assertNotIn("Lương tháng 10", html_expense)
        self.assertIn("Tiền ăn sau điều chỉnh", html_expense)

        # Lọc theo từ khóa ghi chú
        res_search = self.client.get("/transactions?q=điều+chỉnh")
        html_search = res_search.get_data(as_text=True)
        self.assertIn("Tiền ăn sau điều chỉnh", html_search)
        self.assertNotIn("Lương tháng 10", html_search)

        # Xuất CSV toàn bộ
        res_csv = self.client.get("/export.csv")
        self.assertEqual(res_csv.status_code, 200)
        self.assertEqual(res_csv.content_type, "text/csv; charset=utf-8")
        
        csv_bytes = res_csv.data
        # Kiểm tra ký tự UTF-8 BOM
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"), "File CSV phải bắt đầu bằng UTF-8 BOM để Excel hiển thị đúng tiếng Việt")
        
        csv_text = csv_bytes.decode("utf-8-sig")
        self.assertIn("Ngày,Loại,Số tiền (VND),Danh mục,Ghi chú", csv_text)
        self.assertIn("Lương tháng 10", csv_text)
        self.assertIn("15000000", csv_text)
        self.assertIn("Tiền ăn sau điều chỉnh", csv_text)
        self.assertIn("9500000", csv_text)

    def test_06_sqlite_data_persistence_after_restart(self):
        """6. Kiểm tra Dữ liệu SQLite tồn tại bền vững sau khi khởi động lại ứng dụng"""
        # Kiểm tra số lượng giao dịch trước khi giả lập restart
        with app.db() as c:
            before_count = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            self.assertEqual(before_count, 2)

        # Giả lập khởi động lại ứng dụng:
        # 1. Gọi lại init_db() như khi chạy 'python app.py' lần mới
        app.init_db()

        # 2. Đọc trực tiếp từ file SQLite
        with app.db() as c:
            after_txs = c.execute("SELECT * FROM transactions ORDER BY id").fetchall()
            self.assertEqual(len(after_txs), before_count, "Dữ liệu giao dịch không bị mất sau khi khởi động lại")
            self.assertEqual(after_txs[0]["amount"], 15000000)
            self.assertEqual(after_txs[1]["amount"], 9500000)

    def test_07_login_required(self):
        """7. Chưa đăng nhập: trang chủ hiện form đăng nhập, các trang khác chuyển hướng"""
        anon = app.app.test_client()
        res = anon.get("/")
        html = res.get_data(as_text=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Đăng nhập", html)
        self.assertNotIn("Tổng thu", html)
        for path in ("/transactions", "/transactions/new", "/budgets", "/export.csv"):
            r = anon.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.headers["Location"], path)
        with app.db() as c:
            before = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        r = anon.post("/transactions/1/delete")
        self.assertEqual(r.status_code, 302)
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], before)
        self.assertEqual(anon.get("/health").status_code, 200)

    def test_08_register_login_logout(self):
        """8. Đăng ký, đăng nhập, đăng xuất và khóa khi sai nhiều lần"""
        c = app.app.test_client()
        self.assertIn("Email không hợp lệ", register(c, "khong-phai-email", "matkhau12345").get_data(as_text=True))
        self.assertIn("từ 8 đến 128", register(c, "x@example.com", "ngan").get_data(as_text=True))
        self.assertIn("không khớp", register(c, "x@example.com", "matkhau12345", confirm="khac12345").get_data(as_text=True))
        self.assertIn("đã được đăng ký", register(c, EMAIL_A.upper(), "matkhau12345").get_data(as_text=True))

        with app.db() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE email=?", (EMAIL_A,)).fetchone()
        self.assertNotIn(PASS_A, row["password_hash"], "Mật khẩu không được lưu dạng thô")

        self.assertIn("không đúng", login(c, EMAIL_A, "sai-mat-khau").get_data(as_text=True))
        self.assertIn("không đúng", login(c, "khong-ton-tai@example.com", "bat-ky-12345").get_data(as_text=True))
        res = login(c, EMAIL_A, PASS_A)
        self.assertIn(EMAIL_A, res.get_data(as_text=True))
        self.assertIn("Tổng thu", res.get_data(as_text=True))

        out = c.post("/logout", follow_redirects=True)
        self.assertIn("Đã đăng xuất", out.get_data(as_text=True))
        self.assertEqual(c.get("/transactions").status_code, 302)

        # Khóa sau 5 lần sai
        register(app.app.test_client(), "lock@example.com", "matkhau12345")
        lc = app.app.test_client()
        for _ in range(app.MAX_FAILS):
            login(lc, "lock@example.com", "sai-mat-khau")
        locked = login(lc, "lock@example.com", "matkhau12345").get_data(as_text=True)
        self.assertIn("quá nhiều lần", locked)
        app.LOGIN_FAILS.clear()

    def test_09_data_isolation_between_users(self):
        """9. Người dùng B không thấy/sửa/xóa dữ liệu của người dùng A"""
        with app.db() as c:
            a_id = c.execute("SELECT id FROM users WHERE email=?", (EMAIL_A,)).fetchone()["id"]
            tx_id = c.execute("SELECT id FROM transactions WHERE note='Lương tháng 10'").fetchone()["id"]
            cat = c.execute("SELECT id FROM categories WHERE name='Ăn uống'").fetchone()["id"]
            self.assertEqual(c.execute("SELECT user_id FROM transactions WHERE id=?", (tx_id,)).fetchone()["user_id"], a_id)

        # A tạo ngân sách
        self.client.post("/budgets", data={"month": "2026-10", "category_id": str(cat), "limit_amount": "3000000"})
        with app.db() as c:
            a_budget = c.execute("SELECT id FROM budgets WHERE user_id=?", (a_id,)).fetchone()["id"]

        b = app.app.test_client()
        register(b, EMAIL_B, PASS_B)

        self.assertNotIn("Lương tháng 10", b.get("/transactions").get_data(as_text=True))
        self.assertNotIn("Lương tháng 10", b.get("/export.csv").data.decode("utf-8-sig"))
        dash = b.get("/?month=2026-10").get_data(as_text=True)
        self.assertNotIn("15.000.000", dash)
        self.assertNotIn("3.000.000", b.get("/budgets?month=2026-10").get_data(as_text=True))

        # B cố sửa / xóa giao dịch của A
        r = b.post(f"/transactions/{tx_id}/edit", data={"kind": "income", "amount": "1", "tx_date": "2026-10-01",
                   "category_id": "1", "note": "bị hack"}, follow_redirects=True)
        self.assertIn("Không tìm thấy giao dịch", r.get_data(as_text=True))
        b.post(f"/transactions/{tx_id}/delete")
        self.assertEqual(b.get(f"/transactions/{tx_id}/edit").status_code, 302)
        with app.db() as c:
            row = c.execute("SELECT amount,note FROM transactions WHERE id=?", (tx_id,)).fetchone()
            self.assertIsNotNone(row, "Giao dịch của A không được bị B xóa")
            self.assertEqual(row["amount"], 15000000)
            self.assertEqual(row["note"], "Lương tháng 10")

        # B cố xóa ngân sách của A, rồi tự tạo ngân sách cùng tháng/danh mục
        b.post(f"/budgets/{a_budget}/delete")
        b.post("/budgets", data={"month": "2026-10", "category_id": str(cat), "limit_amount": "7777000"})
        with app.db() as c:
            self.assertEqual(c.execute("SELECT limit_amount FROM budgets WHERE id=?", (a_budget,)).fetchone()["limit_amount"], 3000000)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM budgets WHERE month='2026-10' AND category_id=?", (cat,)).fetchone()[0], 2)
        self.assertNotIn("7.777.000", self.client.get("/budgets?month=2026-10").get_data(as_text=True))

    def test_10_csrf_protection(self):
        """10. POST thiếu hoặc sai token CSRF bị từ chối"""
        app.app.config["CSRF_ENABLED"] = True
        try:
            c = app.app.test_client()
            self.assertEqual(c.post("/login", data={"email": EMAIL_A, "password": PASS_A}).status_code, 400)
            html = c.get("/login").get_data(as_text=True)
            token = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
            bad = c.post("/login", data={"email": EMAIL_A, "password": PASS_A, "csrf": "sai-token"})
            self.assertEqual(bad.status_code, 400)
            good = c.post("/login", data={"email": EMAIL_A, "password": PASS_A, "csrf": token})
            self.assertEqual(good.status_code, 302)
            # Sau khi đăng nhập, xóa giao dịch không kèm token vẫn bị chặn
            self.assertEqual(c.post("/transactions/1/delete").status_code, 400)
        finally:
            app.app.config["CSRF_ENABLED"] = False

    def test_11_upgrade_from_legacy_db(self):
        """11. DB cũ (chưa có user_id): dữ liệu được giữ lại và gán cho tài khoản đầu tiên"""
        legacy = BASE_DIR / "test_legacy.db"
        if legacy.exists():
            legacy.unlink()
        conn = sqlite3.connect(legacy)
        conn.executescript("""
        CREATE TABLE categories(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('income','expense')));
        CREATE TABLE transactions(id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('income','expense')), amount INTEGER NOT NULL CHECK(amount>0), tx_date TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), note TEXT DEFAULT '');
        CREATE TABLE budgets(id INTEGER PRIMARY KEY, month TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), limit_amount INTEGER NOT NULL CHECK(limit_amount>0), UNIQUE(month,category_id));
        INSERT INTO categories(name,kind) VALUES('Lương','income'),('Ăn uống','expense');
        INSERT INTO transactions(kind,amount,tx_date,category_id,note) VALUES('income',1000000,'2026-09-01',1,'Dữ liệu cũ');
        INSERT INTO budgets(month,category_id,limit_amount) VALUES('2026-09',2,500000);
        """)
        conn.commit(); conn.close()
        previous = app.DB
        try:
            app.DB = legacy
            app.init_db()
            app.init_db()  # chạy lại phải an toàn
            with app.db() as c:
                self.assertIn("user_id", [r["name"] for r in c.execute("PRAGMA table_info(transactions)")])
                self.assertIn("user_id", [r["name"] for r in c.execute("PRAGMA table_info(budgets)")])
                self.assertEqual(c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM budgets").fetchone()[0], 1)
            first = app.app.test_client()
            register(first, "chu@example.com", "matkhau12345")
            self.assertIn("Dữ liệu cũ", first.get("/transactions").get_data(as_text=True))
            second = app.app.test_client()
            register(second, "khach@example.com", "matkhau12345")
            self.assertNotIn("Dữ liệu cũ", second.get("/transactions").get_data(as_text=True))
            with app.db() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM transactions WHERE user_id IS NULL").fetchone()[0], 0)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM budgets WHERE user_id IS NULL").fetchone()[0], 0)
        finally:
            app.DB = previous
            try:
                legacy.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
