"""
Kịch bản kiểm thử tự động toàn diện cho ứng dụng Chi Tiêu Thông Minh
Kiểm tra đầy đủ 7 yêu cầu theo đề bài:
1. Khởi tạo cấu trúc Database & danh mục mặc định
2. Thêm, sửa, xóa giao dịch thu/chi & kiểm tra tính hợp lệ dữ liệu (validation)
3. Dashboard: Tổng thu, tổng chi, số dư ròng, chi tiêu theo danh mục
4. Ngân sách: Thiết lập hạn mức, tính toán tỷ lệ %, cảnh báo 80% và cảnh báo vượt hạn mức
5. Bộ lọc giao dịch & Xuất CSV chuẩn tiếng Việt (UTF-8 BOM)
6. Tính bền vững dữ liệu SQLite sau khi khởi động lại ứng dụng
"""

import sys
import unittest
from pathlib import Path

# Đảm bảo đường dẫn import
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import app

# Sử dụng database kiểm thử riêng biệt để tuyệt đối không ảnh hưởng đến database thật
TEST_DB = BASE_DIR / "test_chi_tieu.db"

class TestChiTieuThongMinh(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Trỏ database sang file test riêng biệt
        if TEST_DB.exists():
            TEST_DB.unlink()
        app.DB = TEST_DB
        app.init_db()
        app.app.config["TESTING"] = True
        cls.client = app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        # Dọn dẹp file test sau khi chạy xong
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

if __name__ == "__main__":
    unittest.main()
