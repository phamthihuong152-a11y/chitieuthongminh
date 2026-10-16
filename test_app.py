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

    def test_12_dashboard_balance_comparison_and_chart(self):
        """12. Dashboard: số dư hiện tại, so sánh tháng trước, dữ liệu biểu đồ ngày/tháng/năm"""
        import json
        with app.db() as c:
            cat = c.execute("SELECT id FROM categories WHERE name='Lương'").fetchone()["id"]
        self.client.post("/transactions/new", data={"kind": "income", "amount": "4321000", "tx_date": "2026-09-15",
                         "category_id": str(cat), "note": "Thu tháng 9"})

        def chart_of(client, month):
            html = client.get(f"/?month={month}").get_data(as_text=True)
            m = re.search(r'<script type="application/json" id="chart-data">(.*?)</script>', html, re.S)
            return html, json.loads(m.group(1))

        html, data = chart_of(self.client, "2026-10")
        # Số dư hiện tại tính trên mọi tháng: (15.000.000 + 4.321.000) - 9.500.000
        self.assertIn("Số dư hiện tại", html)
        self.assertIn("9.821.000 đ", html)
        # Thu tháng 10 = 15.000.000, tháng 9 = 4.321.000 -> tăng 247%; chi chưa có số liệu tháng trước
        self.assertIn("247% so với tháng trước", html)
        self.assertIn("Chưa có số liệu tháng trước", html)
        # Biểu đồ theo ngày / tháng / năm
        self.assertEqual(len(data["day"]["labels"]), 31)
        self.assertEqual(data["day"]["income"][0], 15000000)          # 01/10/2026
        self.assertEqual(data["day"]["expense"][4], 9500000)          # 05/10/2026
        self.assertEqual(data["month"]["income"][8], 4321000)         # tháng 9
        self.assertEqual(data["month"]["income"][9], 15000000)        # tháng 10
        self.assertEqual(data["month"]["expense"][9], 9500000)
        self.assertEqual(data["year"]["labels"][-1], "2026")
        self.assertEqual(data["year"]["income"][-1], 19321000)
        self.assertEqual(data["year"]["expense"][-1], 9500000)

        # Tháng 9: không có dữ liệu của tháng 8 nên không so sánh được
        html9, data9 = chart_of(self.client, "2026-09")
        self.assertIn("Chưa có số liệu tháng trước", html9)
        self.assertEqual(len(data9["day"]["labels"]), 30)
        self.assertEqual(data9["day"]["income"][14], 4321000)

        # Giảm so với tháng trước: Tháng 11 không có dữ liệu -> thu giảm 100%
        html11, _ = chart_of(self.client, "2026-11")
        self.assertIn("100% so với tháng trước", html11)

        # Người dùng khác không thấy số liệu của A trong biểu đồ
        b = app.app.test_client()
        register(b, "chart-b@example.com", "matkhau-chart-1")
        htmlb, datab = chart_of(b, "2026-10")
        self.assertNotIn("9.821.000", htmlb)
        for view in ("day", "month", "year"):
            self.assertEqual(sum(datab[view]["income"]) + sum(datab[view]["expense"]), 0)

    def test_13_transactions_search_filter_sort_pagination(self):
        """13. Giao dịch: tìm kiếm, lọc ngày/danh mục/loại, sắp xếp, phân trang, CSV theo bộ lọc, quay về đúng trang"""
        c = app.app.test_client()
        register(c, "paging@example.com", "matkhau-paging-1")
        with app.db() as conn:
            cat = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM categories")}

        def add(kind, amount, d, category, note):
            c.post("/transactions/new", data={"kind": kind, "amount": str(amount), "tx_date": d,
                                              "category_id": str(cat[category]), "note": note})
        for i in range(1, 46):   # 45 khoản chi từ 01/2027
            add("expense", i * 10000, f"2027-01-{(i % 28) + 1:02d}", "Ăn uống" if i % 2 else "Đi lại", f"GD {i:02d}")
        add("income", 20000000, "2027-01-05", "Lương", "Lương T1")
        add("expense", 99000, "2027-02-10", "Ăn uống", "ĂN TỐI NHÀ HÀNG")
        add("expense", 55000, "2027-02-11", "Mua sắm", "giảm 50% áo")
        add("expense", 77000, "2027-02-12", "Mua sắm", "khuyến mãi 50 nghìn")
        total = 49   # 45 + 1 khoản thu + 3 khoản chi

        def get(url):
            return c.get(url).get_data(as_text=True)

        def order(html, notes):
            pos = [html.index(n) for n in notes]
            return pos == sorted(pos)

        # Phân trang: 20 khoản mỗi trang, giữ bộ lọc trên liên kết trang
        p1 = get("/transactions")
        self.assertIn(f"{total} giao dịch", p1)
        self.assertEqual(p1.count('class="tx"'), 20)
        self.assertIn("đang xem 1 đến 20", p1)
        self.assertIn('aria-label="Phân trang"', p1)
        self.assertEqual(get("/transactions?page=3").count('class="tx"'), total - 40)
        self.assertEqual(get("/transactions?page=999").count('class="tx"'), total - 40)   # trang quá lớn -> trang cuối
        self.assertEqual(get("/transactions?page=abc").count('class="tx"'), 20)           # sai định dạng -> trang 1
        pk = get("/transactions?kind=expense&sort=amount_asc")
        self.assertIn("kind=expense", re.search(r'href="([^"]*page=2[^"]*)"', pk).group(1))
        self.assertIn("sort=amount_asc", re.search(r'href="([^"]*page=2[^"]*)"', pk).group(1))

        # Tổng thu/chi tính trên TOÀN BỘ kết quả lọc, không chỉ trang hiện tại
        all_expense = sum(i * 10000 for i in range(1, 46)) + 99000 + 55000 + 77000
        self.assertIn("Chi −" + app.money(all_expense), p1)
        self.assertIn("Thu +" + app.money(20000000), p1)

        # Lọc khoảng ngày + tham số cũ ?month=
        jan = get("/transactions?date_from=2027-01-01&date_to=2027-01-31")
        feb = get("/transactions?month=2027-02")
        self.assertIn("46 giao dịch", jan)
        self.assertIn("3 giao dịch", feb)
        self.assertIn("Từ 01/02/2027", feb)
        self.assertIn("Đến 28/02/2027", feb)
        self.assertEqual(get("/transactions?date_from=2027-02-12&date_to=2027-02-10").count('class="tx"'), 3)  # ngày đảo ngược tự đổi chỗ
        self.assertIn(f"{total} giao dịch", get("/transactions?date_from=khong-hop-le&date_to=2027-99-99"))   # ngày sai bị bỏ qua

        # Lọc loại / danh mục
        self.assertIn("1 giao dịch", get("/transactions?kind=income"))
        self.assertIn(f"{total - 1} giao dịch", get("/transactions?kind=expense"))
        self.assertIn("2 giao dịch", get(f"/transactions?category_id={cat['Mua sắm']}"))
        self.assertIn("Danh mục: Mua sắm", get(f"/transactions?category_id={cat['Mua sắm']}"))

        # Tìm kiếm không phân biệt hoa thường tiếng Việt, tìm theo cả tên danh mục
        self.assertIn("ĂN TỐI NHÀ HÀNG", get("/transactions?q=ăn+tối"))
        self.assertIn("ĂN TỐI NHÀ HÀNG", get("/transactions?q=" + "ĂN TỐI"))
        self.assertIn("Lương T1", get("/transactions?q=lương"))          # khớp tên danh mục
        # Ký tự đặc biệt của LIKE được coi là chữ thường: "50%" chỉ khớp ghi chú có dấu %
        pct = get("/transactions?q=50%25")
        self.assertIn("giảm 50% áo", pct)
        self.assertNotIn("khuyến mãi 50 nghìn", pct)
        self.assertIn("1 giao dịch", get("/transactions?q=%25"))
        self.assertIn("0 giao dịch", get("/transactions?q=zzzkhongco"))
        self.assertIn("Không có giao dịch nào khớp bộ lọc", get("/transactions?q=zzzkhongco"))

        # Sắp xếp
        self.assertTrue(order(get("/transactions?kind=expense&sort=amount_desc"), ["GD 45", "GD 44", "GD 43"]))
        self.assertTrue(order(get("/transactions?kind=expense&sort=amount_asc"), ["GD 01", "GD 02", "GD 03"]))
        self.assertTrue(order(get("/transactions?sort=date_asc&date_from=2027-01-01&date_to=2027-01-31&q=GD"), ["GD 28", "GD 01", "GD 29", "GD 02"]))
        self.assertIn(f"{total} giao dịch", get("/transactions?sort=khong-hop-le"))   # sort sai -> mặc định

        # Xuất CSV: theo bộ lọc nhưng lấy toàn bộ kết quả (không bị cắt theo trang)
        csv_text = c.get("/export.csv?kind=expense&q=GD").data.decode("utf-8-sig")
        self.assertEqual(csv_text.count("GD "), 45)
        self.assertNotIn("Lương T1", csv_text)
        self.assertEqual(c.get("/export.csv").data.decode("utf-8-sig").count("\n") - 1, total)
        self.assertIn("giao_dich_2027-02-01_2027-02-28.csv", c.get("/export.csv?month=2027-02").headers["Content-Disposition"])
        # Chống công thức độc hại trong Excel
        add("expense", 1000, "2027-03-01", "Chi phí khác", "=HYPERLINK(\"http://x\")")
        self.assertIn("'=HYPERLINK", c.get("/export.csv?q=hyperlink").data.decode("utf-8-sig"))

        # Sau khi xóa/sửa: quay về đúng trang đang lọc, và chặn chuyển hướng ra ngoài
        with app.db() as conn:
            tid = conn.execute("SELECT id FROM transactions WHERE note='GD 01'").fetchone()["id"]
        back = "/transactions?kind=expense&sort=amount_asc&page=2"
        edit = c.post(f"/transactions/{tid}/edit?next=" + back.replace("&", "%26").replace("?", "%3F").replace("=", "%3D"),
                      data={"kind": "expense", "amount": "10000", "tx_date": "2027-01-02",
                            "category_id": str(cat["Ăn uống"]), "note": "GD 01"})
        self.assertEqual(edit.status_code, 302)
        self.assertEqual(edit.headers["Location"], back)
        evil = c.post(f"/transactions/{tid}/edit?next=//evil.com", data={"kind": "expense", "amount": "10000",
                      "tx_date": "2027-01-02", "category_id": str(cat["Ăn uống"]), "note": "GD 01"})
        self.assertEqual(evil.headers["Location"], "/transactions")
        dele = c.post(f"/transactions/{tid}/delete", data={"next": back})
        self.assertEqual(dele.headers["Location"], back)
        evil2 = c.post("/transactions/999999/delete", data={"next": "https://evil.com"})
        self.assertEqual(evil2.headers["Location"], "/transactions")

    def test_14_budget_summary_alerts_edit_copy(self):
        """14. Ngân sách: tổng quan, danh mục vượt kế hoạch, cảnh báo 80%/100% khi nhập chi, sửa, sao chép tháng trước"""
        c = app.app.test_client()
        register(c, "budget@example.com", "matkhau-budget-1")
        with app.db() as conn:
            cat = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM categories")}
        M = "2028-03"

        def set_budget(name, limit, month=M):
            return c.post("/budgets", data={"month": month, "category_id": str(cat[name]), "limit_amount": str(limit)}, follow_redirects=True)

        def spend(name, amount, d="2028-03-10", kind="expense"):
            return c.post("/transactions/new", data={"kind": kind, "amount": str(amount), "tx_date": d,
                          "category_id": str(cat[name]), "note": "test"}, follow_redirects=True).get_data(as_text=True)

        set_budget("Ăn uống", 1000000); set_budget("Mua sắm", 500000); set_budget("Đi lại", 400000); set_budget("Giải trí", 2000000)

        # Cảnh báo ngay khi lưu khoản chi
        w80 = spend("Ăn uống", 850000)
        self.assertIn("Cảnh báo: Ăn uống đã dùng 85% ngân sách tháng 03/2028", w80)
        wover = spend("Mua sắm", 650000)
        self.assertIn("Cảnh báo: Mua sắm đã vượt ngân sách tháng 03/2028 là 150.000 đ", wover)
        w100 = spend("Đi lại", 400000)
        self.assertIn("Cảnh báo: Đi lại đã dùng hết ngân sách tháng 03/2028", w100)
        self.assertNotIn("Cảnh báo", spend("Giải trí", 100000))                  # 5%: không cảnh báo
        self.assertNotIn("Cảnh báo", spend("Lương", 5000000, kind="income"))      # khoản thu: không cảnh báo
        self.assertNotIn("Cảnh báo", spend("Ăn uống", 100000, d="2028-04-02"))   # tháng khác, chưa đặt ngân sách

        html = c.get(f"/budgets?month={M}").get_data(as_text=True)
        # Tổng quan: hạn mức 3.900.000, đã chi 2.000.000, còn lại 1.900.000, 51%
        self.assertIn("3.900.000 đ", html)
        self.assertIn("2.000.000 đ", html)
        self.assertIn("Còn lại <b class=\"num\">1.900.000 đ", html)
        self.assertIn(">51%<", html)
        # Danh mục vượt kế hoạch và danh mục từ 80%
        self.assertIn("vượt kế hoạch 150.000 đ (130%)", html)
        self.assertIn("đã dùng hết ngân sách (100%)", html)
        self.assertIn("đã dùng 85%, còn 150.000 đ", html)
        self.assertEqual(html.count('class="al over"'), 2)
        self.assertEqual(html.count('class="al warn"'), 1)
        self.assertIn("Đã vượt ngân sách", html)
        self.assertIn("Vượt 150.000 đ", html)
        # Sắp xếp: danh mục dùng nhiều nhất lên đầu
        self.assertLess(html.index("130%"), html.index("85%"))

        # Dashboard: thanh cảnh báo
        dash = c.get(f"/?month={M}").get_data(as_text=True)
        self.assertIn("2 danh mục vượt ngân sách:</b> Mua sắm, Đi lại.", dash)
        self.assertIn("1 danh mục đã dùng từ 80%:</b> Ăn uống.", dash)

        # Sửa ngân sách (chỉ đổi hạn mức, danh mục và tháng được giữ nguyên)
        with app.db() as conn:
            bid = conn.execute("SELECT id FROM budgets WHERE month=? AND category_id=? AND user_id=(SELECT id FROM users WHERE email='budget@example.com')", (M, cat["Mua sắm"])).fetchone()["id"]
        edit_html = c.get(f"/budgets?month={M}&edit={bid}").get_data(as_text=True)
        self.assertIn("Sửa ngân sách", edit_html)
        self.assertIn('value="500000"', edit_html)
        self.assertIn("Cập nhật ngân sách", edit_html)
        c.post(f"/budgets?month={M}&edit={bid}", data={"month": M, "category_id": str(cat["Mua sắm"]), "limit_amount": "1.000.000"})
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT limit_amount FROM budgets WHERE id=?", (bid,)).fetchone()["limit_amount"], 1000000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budgets WHERE month=? AND category_id=?", (M, cat["Mua sắm"])).fetchone()[0], 1)
        self.assertEqual(c.get(f"/budgets?month={M}").get_data(as_text=True).count('class="al over"'), 1)   # Mua sắm hết vượt

        # Người dùng khác không sửa được ngân sách của mình
        other = app.app.test_client()
        register(other, "budget-other@example.com", "matkhau-other-1")
        self.assertNotIn("Sửa ngân sách", other.get(f"/budgets?month={M}&edit={bid}").get_data(as_text=True))

        # Sao chép từ tháng trước
        nxt = "2028-04"
        r0 = c.post("/budgets/copy", data={"month": "2028-02"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Tháng 01/2028 chưa có ngân sách nào để sao chép", r0)
        r1 = c.post("/budgets/copy", data={"month": nxt}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Đã sao chép 4 ngân sách từ tháng 03/2028", r1)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budgets WHERE month=? AND user_id=(SELECT id FROM users WHERE email='budget@example.com')", (nxt,)).fetchone()[0], 4)
        r2 = c.post("/budgets/copy", data={"month": nxt}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("đã có đủ ở tháng này", r2)
        # Ngân sách đã chỉnh tay ở tháng mới không bị ghi đè khi sao chép lại
        set_budget("Ăn uống", 1234000, month=nxt)
        c.post("/budgets/copy", data={"month": nxt})
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT limit_amount FROM budgets WHERE month=? AND category_id=? AND user_id=(SELECT id FROM users WHERE email='budget@example.com')", (nxt, cat["Ăn uống"])).fetchone()["limit_amount"], 1234000)
        # Tháng 04: khoản chi 100.000 Ăn uống (2/4) so với hạn mức 1.234.000 -> chưa tới 80%
        self.assertNotIn("Cảnh báo", spend("Ăn uống", 1000, d="2028-04-05"))
        # Người dùng khác chưa có ngân sách nên không sao chép được từ dữ liệu của mình
        self.assertIn("chưa có ngân sách nào để sao chép", other.post("/budgets/copy", data={"month": nxt}, follow_redirects=True).get_data(as_text=True))

    def test_15_savings_goals(self):
        """15. Mục tiêu tiết kiệm: tạo, nạp/rút, phần trăm, thời hạn, đã đạt, cách ly dữ liệu"""
        from datetime import date, timedelta
        c = app.app.test_client()
        anon = app.app.test_client()
        self.assertEqual(anon.get("/goals").status_code, 302)
        register(c, "goals@example.com", "matkhau-goals-1")
        today = date.today()
        def iso(days): return (today + timedelta(days=days)).isoformat()
        def create(**over):
            data = {"name": "Du lịch Đà Lạt", "kind": "travel", "target_amount": "10.000.000", "deadline": iso(90), "saved_amount": "2500000"}
            data.update(over)
            return c.post("/goals/new", data=data, follow_redirects=True).get_data(as_text=True)

        # Kiểm tra dữ liệu không hợp lệ
        for bad in ({"name": ""}, {"kind": "khong-co"}, {"target_amount": "0"}, {"target_amount": "abc"},
                    {"deadline": "2020-01-01"}, {"deadline": "khong-phai-ngay"}, {"saved_amount": "-5"},
                    {"target_amount": "99999999999999999999"}):
            self.assertIn("Dữ liệu không hợp lệ", create(**bad), bad)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0], 0)

        # Tạo mục tiêu: 25%, còn thiếu 7.500.000, 90 ngày -> 3 tháng -> 2.500.000 mỗi tháng
        html = create()
        self.assertIn("Đã tạo mục tiêu tiết kiệm", html)
        self.assertIn("25% hoàn thành", html)
        self.assertIn("2.500.000 đ", html)
        self.assertIn("Còn thiếu", html)
        with app.db() as conn:
            g = conn.execute("SELECT id FROM goals").fetchone(); gid = g["id"]
            self.assertEqual(conn.execute("SELECT amount FROM goal_deposits WHERE goal_id=?", (gid,)).fetchone()["amount"], 2500000)
        lst = c.get("/goals").get_data(as_text=True)
        self.assertIn("Du lịch Đà Lạt", lst)
        self.assertIn(">25%<", lst)
        self.assertIn("Còn thiếu 7.500.000 đ, cần tiết kiệm khoảng 2.500.000 đ mỗi tháng", lst)
        self.assertIn("còn 3 tháng", lst)
        self.assertIn("Bạn đã tiết kiệm", lst)

        # Nạp / rút
        def deposit(amount, direction="add", d=None, note=""):
            data = {"amount": str(amount), "direction": direction, "note": note}
            if d: data["dep_date"] = d
            return c.post(f"/goals/{gid}/deposit", data=data, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Số tiền rút vượt quá số đã tích lũy", deposit("20000000", "withdraw"))
        self.assertIn("Dữ liệu không hợp lệ", deposit("0"))
        self.assertIn("Dữ liệu không hợp lệ", deposit("1000", d=iso(5)))             # ngày ở tương lai
        self.assertIn("Dữ liệu không hợp lệ", deposit("1000", direction="xoa-het"))
        done = deposit("7.500.000", note="Thưởng quý")
        self.assertIn("Chúc mừng", done)
        self.assertIn("Đăng xuất", done)       # trang chi tiết vẫn có thanh điều hướng của người đã đăng nhập
        self.assertNotIn(">Đăng ký<", done)
        self.assertIn("100% hoàn thành", done)
        self.assertIn("Thưởng quý", done)
        self.assertIn("Đã hoàn thành", done)
        self.assertIn("(1)", c.get("/goals?status=done").get_data(as_text=True))     # tab Đã đạt có 1
        self.assertIn("Du lịch Đà Lạt", c.get("/goals?status=done").get_data(as_text=True))
        self.assertNotIn("Du lịch Đà Lạt", c.get("/goals?status=active").get_data(as_text=True))
        # Rút bớt: không còn đạt mục tiêu
        w = deposit("1000000", "withdraw", note="Đặt cọc")
        self.assertIn("90% hoàn thành", w)
        self.assertNotIn("Chúc mừng", w)
        self.assertIn("Du lịch Đà Lạt", c.get("/goals?status=active").get_data(as_text=True))
        # Nạp thêm vượt mục tiêu vẫn tính là đã đạt, thanh không vượt 100%
        over = deposit("3000000")
        self.assertIn("100% hoàn thành", over)
        self.assertIn("width:100%", over)
        self.assertNotIn("width:130", over)

        # Xóa một khoản nạp
        with app.db() as conn:
            deps = conn.execute("SELECT id,amount FROM goal_deposits WHERE goal_id=? ORDER BY id", (gid,)).fetchall()
        first_dep = deps[0]["id"]                                                      # 2.500.000 ban đầu
        r = c.post(f"/goals/{gid}/deposits/{first_dep}/delete", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Đã xóa khoản này", r)
        with app.db() as conn:
            saved = conn.execute("SELECT SUM(amount) FROM goal_deposits WHERE goal_id=?", (gid,)).fetchone()[0]
        self.assertEqual(saved, 7500000 + 0 + 3000000 - 1000000)
        self.assertEqual(c.post(f"/goals/{gid}/deposits/99999/delete", follow_redirects=True).status_code, 200)

        # Sửa mục tiêu
        edit_page = c.get(f"/goals/{gid}/edit").get_data(as_text=True)
        self.assertIn("SỬA MỤC TIÊU", edit_page)
        self.assertNotIn("Đã tích lũy (VND)", edit_page)
        self.assertIn("Đã cập nhật mục tiêu", c.post(f"/goals/{gid}/edit", data={"name": "Du lịch Nhật", "kind": "travel",
                      "target_amount": "20000000", "deadline": iso(200)}, follow_redirects=True).get_data(as_text=True))
        self.assertIn("Du lịch Nhật", c.get("/goals").get_data(as_text=True))
        self.assertIn("Dữ liệu không hợp lệ", c.post(f"/goals/{gid}/edit", data={"name": "X", "kind": "travel", "target_amount": "-1",
                      "deadline": iso(10)}, follow_redirects=True).get_data(as_text=True))

        # Quá hạn: chèn trực tiếp mục tiêu có hạn đã qua
        with app.db() as conn:
            uid_ = conn.execute("SELECT id FROM users WHERE email='goals@example.com'").fetchone()["id"]
            od = conn.execute("INSERT INTO goals(user_id,name,kind,target_amount,deadline) VALUES(?,?,?,?,?)", (uid_, "Mua laptop", "shopping", 20000000, iso(-3))).lastrowid
            conn.execute("INSERT INTO goal_deposits(goal_id,user_id,amount,dep_date,note) VALUES(?,?,?,?,?)", (od, uid_, 2000000, iso(-30), ""))
        lst = c.get("/goals").get_data(as_text=True)
        self.assertIn("đã quá hạn 3 ngày", lst)
        self.assertIn(">10%<", lst)
        # Dashboard hiện mục tiêu đang thực hiện
        dash = c.get("/").get_data(as_text=True)
        self.assertIn("Mục tiêu tiết kiệm", dash)
        self.assertIn("Mua laptop", dash)

        # Cách ly dữ liệu giữa người dùng
        other = app.app.test_client()
        register(other, "goals-other@example.com", "matkhau-goals-2")
        self.assertNotIn("Du lịch Nhật", other.get("/goals").get_data(as_text=True))
        self.assertIn("Không tìm thấy mục tiêu", other.get(f"/goals/{gid}", follow_redirects=True).get_data(as_text=True))
        self.assertIn("Không tìm thấy mục tiêu", other.get(f"/goals/{gid}/edit", follow_redirects=True).get_data(as_text=True))
        other.post(f"/goals/{gid}/deposit", data={"amount": "1000000", "direction": "add"})
        other.post(f"/goals/{gid}/delete")
        other.post(f"/goals/{gid}/deposits/{deps[1]['id']}/delete")
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goals WHERE id=?", (gid,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goal_deposits WHERE goal_id=?", (gid,)).fetchone()[0], len(deps) - 1)

        # Xóa mục tiêu: xóa luôn lịch sử nạp rút
        self.assertIn("Đã xóa mục tiêu", c.post(f"/goals/{gid}/delete", follow_redirects=True).get_data(as_text=True))
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goals WHERE id=?", (gid,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goal_deposits WHERE goal_id=?", (gid,)).fetchone()[0], 0)
        self.assertEqual(anon.get(f"/goals/{od}").status_code, 302)

    def test_16_recurring_transactions_and_reminders(self):
        """16. Giao dịch định kỳ: ngày đến hạn, nhắc lịch, ghi nhận/bỏ qua, tự động, tạm dừng, lịch tháng, cách ly dữ liệu"""
        from datetime import date, timedelta
        nxt, add_m = app.next_occurrence, app.add_months

        # --- Quy tắc tính ngày (không phụ thuộc ngày chạy test) ---
        self.assertEqual(nxt(date(2028, 1, 31), "monthly", 31), date(2028, 2, 29))      # tháng ngắn: co lại
        self.assertEqual(nxt(date(2028, 2, 29), "monthly", 31), date(2028, 3, 31))      # rồi trở lại ngày 31
        self.assertEqual(nxt(date(2028, 2, 29), "yearly", 29), date(2029, 2, 28))       # 29/2 năm không nhuận
        self.assertEqual(nxt(date(2031, 2, 28), "yearly", 29), date(2032, 2, 29))       # về lại 29/2 khi nhuận
        self.assertEqual(nxt(date(2028, 12, 15), "monthly", 15), date(2029, 1, 15))     # qua năm
        self.assertEqual(nxt(date(2028, 1, 3), "weekly", 3), date(2028, 1, 10))
        mon = {"start_date": "2028-01-31", "frequency": "monthly", "anchor_day": 31, "end_date": None}
        self.assertEqual(app.occurrences(mon, date(2028, 2, 1), date(2028, 4, 30)), [date(2028, 2, 29), date(2028, 3, 31), date(2028, 4, 30)])
        wk = {"start_date": "2028-01-03", "frequency": "weekly", "anchor_day": 3, "end_date": "2028-02-01"}
        self.assertEqual(app.occurrences(wk, date(2028, 1, 10), date(2028, 2, 10)), [date(2028, 1, 10), date(2028, 1, 17), date(2028, 1, 24), date(2028, 1, 31)])

        c = app.app.test_client()
        register(c, "rec@example.com", "matkhau-rec-123")
        today = date.today()
        with app.db() as conn:
            cat = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM categories")}
            me = conn.execute("SELECT id FROM users WHERE email='rec@example.com'").fetchone()["id"]
        def iso(days): return (today + timedelta(days=days)).isoformat()
        def tx_count():
            with app.db() as conn: return conn.execute("SELECT COUNT(*) FROM transactions WHERE user_id=?", (me,)).fetchone()[0]
        def rec_row(name):
            with app.db() as conn: return conn.execute("SELECT * FROM recurring WHERE note=? AND user_id=?", (name, me)).fetchone()
        def create(**over):
            data = {"kind": "expense", "note": "Tiền thuê nhà", "amount": "5.000.000", "category_id": str(cat["Nhà ở"]), "frequency": "monthly",
                    "next_due": iso(2), "end_date": "", "remind_days": "3"}
            data.update(over)
            return c.post("/recurring/new", data=data, follow_redirects=True).get_data(as_text=True)

        self.assertIn("THÊM KHOẢN ĐỊNH KỲ", c.get("/recurring/new").get_data(as_text=True))
        self.assertIn("Chưa có khoản định kỳ nào", c.get("/recurring").get_data(as_text=True))

        # --- Kiểm tra dữ liệu không hợp lệ ---
        for bad in ({"note": ""}, {"kind": "khac"}, {"frequency": "moi-gio"}, {"amount": "0"}, {"amount": "abc"},
                    {"category_id": str(cat["Lương"])},                      # danh mục thu nhưng khoản là chi
                    {"next_due": iso(-1)}, {"next_due": "khong-phai-ngay"}, {"end_date": iso(-5)}, {"remind_days": "99"}, {"remind_days": "x"}):
            self.assertIn("Dữ liệu không hợp lệ", create(**bad), bad)
        with app.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recurring WHERE user_id=?", (me,)).fetchone()[0], 0)

        # --- Khoản chi nhắc (không tự động), đến hạn sau 2 ngày, nhắc trước 3 ngày ---
        html = create()
        self.assertIn("Đã thêm khoản định kỳ", html)
        self.assertIn("Cần chú ý", html)
        self.assertIn("Còn 2 ngày", html)
        self.assertIn(">Ghi nhận<", html)
        self.assertIn("Chi cố định mỗi tháng", html)
        self.assertIn("5.000.000 đ", html)
        r1 = rec_row("Tiền thuê nhà"); rid = r1["id"]
        self.assertEqual((r1["start_date"], r1["next_due"], r1["anchor_day"], r1["auto"]), (iso(2), iso(2), today.__add__(timedelta(days=2)).day, 0))
        dash = c.get("/").get_data(as_text=True)
        self.assertIn("1 khoản định kỳ cần chú ý:", dash)
        self.assertIn("Tiền thuê nhà (còn 2 ngày)", dash)
        self.assertIn("Giao dịch định kỳ sắp đến hạn", dash)
        before = tx_count()
        # Ghi nhận: tạo giao dịch dated hôm nay (không ghi vào tương lai), chuyển kỳ tiếp theo
        d0 = today + timedelta(days=2)
        rec_html = c.post(f"/recurring/{rid}/record", follow_redirects=True).get_data(as_text=True)
        self.assertIn('Đã ghi nhận &#34;Tiền thuê nhà&#34;', rec_html)
        self.assertEqual(tx_count(), before + 1)
        with app.db() as conn:
            t = conn.execute("SELECT * FROM transactions WHERE user_id=? AND note='Tiền thuê nhà'", (me,)).fetchone()
        self.assertEqual((t["kind"], t["amount"], t["tx_date"], t["category_id"]), ("expense", 5000000, today.isoformat(), cat["Nhà ở"]))
        self.assertEqual(rec_row("Tiền thuê nhà")["next_due"], add_m(d0, 1, d0.day).isoformat())
        # Bấm lại / gửi lại form: không tạo giao dịch trùng
        again = c.post(f"/recurring/{rid}/record", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Không thể ghi nhận khoản này", again)
        self.assertIn("Không thể bỏ qua khoản này", c.post(f"/recurring/{rid}/skip", follow_redirects=True).get_data(as_text=True))
        self.assertEqual(tx_count(), before + 1)

        # --- Lịch tháng: kỳ đã xử lý có dấu hoàn thành, kỳ sắp tới hiện mốc sắp tới ---
        cal_done = c.get(f"/recurring?month={d0.strftime('%Y-%m')}").get_data(as_text=True)
        self.assertIn("chip-r expense done", cal_done)
        self.assertIn("Đã xử lý", cal_done)
        self.assertEqual(cal_done.count('class="cal-cell') % 7, 0)
        nd = add_m(d0, 1, d0.day)
        cal_next = c.get(f"/recurring?month={nd.strftime('%Y-%m')}").get_data(as_text=True)
        self.assertIn("chip-r expense upcoming", cal_next)
        self.assertIn("Sắp tới", cal_next)

        # --- Khoản thu tự động đến hạn hôm nay: tự ghi nhận khi mở trang ---
        before = tx_count()
        create(kind="income", note="Lương tháng", amount="15000000", category_id=str(cat["Lương"]), next_due=iso(0), remind_days="0")
        auto_html = c.get("/recurring").get_data(as_text=True)
        self.assertEqual(tx_count(), before)                                     # chưa bật tự động -> chỉ nhắc
        self.assertIn("Đến hạn hôm nay", auto_html)
        with app.db() as conn: conn.execute("UPDATE recurring SET auto=1 WHERE note='Lương tháng' AND user_id=?", (me,))
        auto_html = c.get("/transactions").get_data(as_text=True)               # mở danh sách giao dịch cũng kích hoạt
        self.assertIn("Đã tự động ghi nhận 1 giao dịch định kỳ", auto_html)
        self.assertEqual(tx_count(), before + 1)
        with app.db() as conn:
            t = conn.execute("SELECT * FROM transactions WHERE user_id=? AND note='Lương tháng'", (me,)).fetchone()
        self.assertEqual((t["kind"], t["amount"], t["tx_date"]), ("income", 15000000, today.isoformat()))
        self.assertEqual(rec_row("Lương tháng")["next_due"], add_m(today, 1, today.day).isoformat())
        c.get("/recurring"); self.assertEqual(tx_count(), before + 1)             # tải lại không tạo thêm

        # --- Ghi bù các kỳ bị lỡ khi chưa mở ứng dụng (khoản tự động) ---
        before = tx_count()
        start = add_m(today, -3, today.day)
        with app.db() as conn:
            conn.execute("INSERT INTO recurring(user_id,kind,amount,category_id,note,frequency,start_date,anchor_day,next_due,remind_days,auto,active) VALUES(?,?,?,?,?,?,?,?,?,?,1,1)",
                         (me, "expense", 300000, cat["Hóa đơn"], "Gói internet", "monthly", start.isoformat(), today.day, start.isoformat(), 3))
        self.assertIn("Đã tự động ghi nhận 4 giao dịch định kỳ", c.get("/").get_data(as_text=True))
        with app.db() as conn:
            dates = {r["tx_date"] for r in conn.execute("SELECT tx_date FROM transactions WHERE user_id=? AND note='Gói internet'", (me,))}
        self.assertEqual(dates, {add_m(today, -k, today.day).isoformat() for k in range(4)})
        self.assertEqual(rec_row("Gói internet")["next_due"], add_m(today, 1, today.day).isoformat())

        # --- Bỏ qua một kỳ: không tạo giao dịch ---
        create(note="Phí gửi xe", amount="100000", category_id=str(cat["Đi lại"]), frequency="weekly", next_due=iso(1))
        before = tx_count()
        sk = c.post(f"/recurring/{rec_row('Phí gửi xe')['id']}/skip", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Đã bỏ qua kỳ", sk)
        self.assertEqual(tx_count(), before)
        self.assertEqual(rec_row("Phí gửi xe")["next_due"], iso(8))

        # --- Tạm dừng: không tự ghi nhận; tiếp tục: bỏ qua các kỳ đã lỡ, không ghi bù ---
        old = today - timedelta(days=40)
        with app.db() as conn:
            pid = conn.execute("INSERT INTO recurring(user_id,kind,amount,category_id,note,frequency,start_date,anchor_day,next_due,remind_days,auto,active) VALUES(?,?,?,?,?,?,?,?,?,?,1,0)",
                               (me, "expense", 200000, cat["Giải trí"], "Gói xem phim", "monthly", old.isoformat(), old.day, old.isoformat(), 3)).lastrowid
        before = tx_count()
        c.get("/"); self.assertEqual(tx_count(), before)
        self.assertIn("Tạm dừng", c.get("/recurring").get_data(as_text=True))
        res = c.post(f"/recurring/{pid}/toggle", follow_redirects=True).get_data(as_text=True)
        self.assertIn('Đã tiếp tục &#34;Gói xem phim&#34;', res)
        resumed = date.fromisoformat(rec_row("Gói xem phim")["next_due"])
        self.assertGreaterEqual(resumed, today)
        self.assertLess(resumed, today + timedelta(days=32))
        self.assertEqual(tx_count(), before + (1 if resumed == today else 0))     # không ghi bù các tháng đã lỡ
        c.post(f"/recurring/{pid}/toggle")
        self.assertEqual(rec_row("Gói xem phim")["active"], 0)

        # --- Có ngày kết thúc: ghi nhận kỳ cuối rồi kết thúc ---
        create(note="Học phí khóa học", amount="2000000", category_id=str(cat["Chi phí khác"]), next_due=iso(0), end_date=iso(0), remind_days="0")
        eid = rec_row("Học phí khóa học")["id"]
        self.assertIn('Đã ghi nhận &#34;Học phí khóa học&#34;', c.post(f"/recurring/{eid}/record", follow_redirects=True).get_data(as_text=True))
        page_end = c.get("/recurring").get_data(as_text=True)
        self.assertIn("Đã kết thúc", page_end)
        self.assertIn("Không thể ghi nhận khoản này", c.post(f"/recurring/{eid}/record", follow_redirects=True).get_data(as_text=True))

        # --- Sửa ---
        self.assertIn("SỬA KHOẢN ĐỊNH KỲ", c.get(f"/recurring/{rid}/edit").get_data(as_text=True))
        cur = rec_row("Tiền thuê nhà")
        ok = c.post(f"/recurring/{rid}/edit", data={"kind": "expense", "note": "Tiền thuê nhà", "amount": "5.500.000", "category_id": str(cat["Nhà ở"]),
                    "frequency": "monthly", "next_due": cur["next_due"], "end_date": "", "remind_days": "7", "auto": "1"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Đã cập nhật khoản định kỳ", ok)
        upd = rec_row("Tiền thuê nhà")
        self.assertEqual((upd["amount"], upd["remind_days"], upd["auto"], upd["start_date"]), (5500000, 7, 1, cur["start_date"]))   # giữ nguyên ngày gốc
        new_due = date.fromisoformat(iso(45))
        c.post(f"/recurring/{rid}/edit", data={"kind": "expense", "note": "Tiền thuê nhà", "amount": "5500000", "category_id": str(cat["Nhà ở"]),
               "frequency": "monthly", "next_due": new_due.isoformat(), "end_date": "", "remind_days": "7"})
        upd = rec_row("Tiền thuê nhà")
        self.assertEqual((upd["start_date"], upd["anchor_day"], upd["next_due"], upd["auto"]), (new_due.isoformat(), new_due.day, new_due.isoformat(), 0))
        self.assertIn("Dữ liệu không hợp lệ", c.post(f"/recurring/{rid}/edit", data={"kind": "income", "note": "x", "amount": "1000", "category_id": str(cat["Nhà ở"]),
                      "frequency": "monthly", "next_due": iso(5), "remind_days": "3"}, follow_redirects=True).get_data(as_text=True))

        # --- Cách ly dữ liệu giữa người dùng ---
        other = app.app.test_client()
        register(other, "rec-other@example.com", "matkhau-rec-456")
        self.assertIn("Chưa có khoản định kỳ nào", other.get("/recurring").get_data(as_text=True))
        self.assertNotIn("Tiền thuê nhà", other.get("/").get_data(as_text=True))
        sid = rec_row("Phí gửi xe")["id"]
        with app.db() as conn: conn.execute("UPDATE recurring SET next_due=? WHERE id=?", (iso(0), sid))
        snapshot = dict(rec_row("Phí gửi xe")); before = tx_count()
        for path in (f"/recurring/{sid}/record", f"/recurring/{sid}/skip", f"/recurring/{sid}/toggle", f"/recurring/{sid}/delete"):
            other.post(path)
        self.assertIn("Không tìm thấy khoản định kỳ", other.get(f"/recurring/{sid}/edit", follow_redirects=True).get_data(as_text=True))
        other.post(f"/recurring/{sid}/edit", data={"kind": "expense", "note": "bị sửa", "amount": "1", "category_id": str(cat["Đi lại"]), "frequency": "weekly", "next_due": iso(3), "remind_days": "3"})
        self.assertEqual(dict(rec_row("Phí gửi xe")), snapshot)
        self.assertEqual(tx_count(), before)

        # --- Xóa khoản định kỳ: giữ lại các giao dịch đã ghi nhận ---
        before = tx_count()
        self.assertIn("Đã xóa khoản định kỳ", c.post(f"/recurring/{rid}/delete", follow_redirects=True).get_data(as_text=True))
        self.assertIsNone(rec_row("Tiền thuê nhà"))
        self.assertEqual(tx_count(), before)
        self.assertEqual(app.app.test_client().get("/recurring").status_code, 302)

    def test_17_server_side_storage_and_schema_file(self):
        """17. Dữ liệu lưu ở CSDL máy chủ (không localStorage/fetch) và schema.sql là nguồn cấu trúc duy nhất"""
        schema_path = BASE_DIR / "schema.sql"
        self.assertTrue(schema_path.exists(), "schema.sql phải nằm cạnh app.py")
        self.assertEqual(app.SCHEMA_FILE, schema_path)
        sql = schema_path.read_text(encoding="utf-8")
        declared_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))
        declared_indexes = set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", sql))
        self.assertEqual(declared_tables, {"users", "categories", "transactions", "budgets", "goals", "goal_deposits", "recurring"})

        previous, tmp, extra = app.DB, BASE_DIR / "test_schema_tmp.db", BASE_DIR / "test_schema_extra.sql"
        for f in (tmp, extra):
            if f.exists(): f.unlink()
        try:
            # 1. CSDL mới được tạo đúng từ schema.sql: đủ bảng, đủ chỉ mục, đủ 11 danh mục mặc định
            app.DB = tmp
            app.init_db()
            with app.db() as c:
                tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                indexes = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}
                self.assertEqual(tables, declared_tables)
                self.assertTrue(declared_indexes <= indexes)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM categories").fetchone()[0], 11)
                # 2. Tên trường nhất quán: snake_case, không còn kiểu categoryId / createdAt / limit
                for t in tables:
                    for col in c.execute(f"PRAGMA table_info({t})"):
                        self.assertRegex(col["name"], r"^[a-z][a-z0-9_]*$", f"{t}.{col['name']}")
                budget_cols = {r["name"] for r in c.execute("PRAGMA table_info(budgets)")}
                self.assertTrue({"category_id", "limit_amount", "user_id"} <= budget_cols)
                self.assertIn("created_at", {r["name"] for r in c.execute("PRAGMA table_info(users)")})
            # 3. Chạy lại không mất dữ liệu và không nhồi lại danh mục đã xóa
            with app.db() as c: c.execute("DELETE FROM categories WHERE name='Chi phí khác'")
            app.init_db(); app.init_db()
            with app.db() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM categories").fetchone()[0], 10)

            # 4. Mã thật sự ĐỌC schema.sql: đổi file thì cấu trúc đổi theo
            extra.write_text(sql + "\nCREATE TABLE IF NOT EXISTS kiem_tra_doc_file(id INTEGER PRIMARY KEY);\n", encoding="utf-8")
            app.SCHEMA_FILE = extra
            app.init_db()
            with app.db() as c:
                self.assertIsNotNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='kiem_tra_doc_file'").fetchone())
            # 5. Thiếu schema.sql (ví dụ Dockerfile quên copy): không sập, dùng bản sao dự phòng và cảnh báo ra stderr
            import contextlib, io
            app.SCHEMA_FILE = BASE_DIR / "khong_ton_tai.sql"
            fresh = BASE_DIR / "test_schema_tmp2.db"
            if fresh.exists(): fresh.unlink()
            app.DB = fresh
            err = io.StringIO()
            with contextlib.redirect_stderr(err): app.init_db()
            self.assertIn("schema.sql", err.getvalue())
            with app.db() as c:
                self.assertEqual({r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}, declared_tables)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM categories").fetchone()[0], 11)
            fresh.unlink()
            # 6. Bản sao dự phòng luôn trùng khớp schema.sql (nếu lệch, hãy chép lại nội dung schema.sql vào EMBEDDED_SCHEMA)
            self.assertEqual(app.EMBEDDED_SCHEMA, sql)
        finally:
            app.SCHEMA_FILE = schema_path
            app.DB = previous
            for f in (tmp, extra):
                try: f.unlink()
                except Exception: pass

        # 7. Không có lưu trữ phía trình duyệt hay gọi fetch/XHR trong các trang đã đăng nhập; dữ liệu nằm trong SQLite
        for path in ("/", "/transactions", "/transactions/new", "/budgets", "/goals", "/recurring", "/recurring/new"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            html = res.get_data(as_text=True)
            for banned in ("localStorage", "sessionStorage", "indexedDB", "fetch(", "XMLHttpRequest"):
                self.assertNotIn(banned, html, f"{banned} xuất hiện ở {path}")
        with app.db() as c:
            self.assertGreater(c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
