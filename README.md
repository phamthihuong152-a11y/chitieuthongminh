# Chi Tiêu Thông Minh — Ứng dụng Quản lý Tài chính (Flask + SQLite)

Ứng dụng quản lý thu chi cá nhân xây dựng bằng Python (Flask Framework) và cơ sở dữ liệu SQLite, chạy trên máy chủ cục bộ hoặc môi trường cloud.

## Tính năng chính
- Quản lý giao dịch thu/chi (Thêm, Sửa, Xóa) với kiểm tra dữ liệu hợp lệ (validation).
- Hệ thống 11 danh mục mặc định phân loại rõ ràng theo Thu nhập và Chi tiêu.
- Dashboard tổng quan: Hiển thị tổng thu, tổng chi, số dư ròng và phân bổ chi tiêu theo danh mục trong tháng.
- Quản lý ngân sách: Thiết lập hạn mức chi theo tháng/danh mục, tự động tính tỷ lệ % thực chi, cảnh báo khi đạt từ 80% và thông báo khi vượt hạn mức.
- Bộ lọc giao dịch linh hoạt theo tháng, loại, danh mục và từ khóa ghi chú.
- Xuất dữ liệu ra file CSV chuẩn mã hóa tiếng Việt (UTF-8 BOM) tương thích tuyệt đối với Microsoft Excel.
- Dữ liệu lưu trữ bền vững trên tệp cơ sở dữ liệu SQLite (`chi_tieu.db`).

## Cài đặt và Chạy trên Windows / Linux / macOS

1. Cài đặt Python 3.10+ từ [python.org](https://www.python.org/) (đảm bảo tích chọn *Add Python to PATH*).
2. Tải hoặc giải nén mã nguồn vào thư mục dự án.
3. Mở Terminal / Command Prompt tại thư mục dự án và chạy các lệnh sau:

```bash
# Tạo môi trường ảo (tùy chọn nhưng khuyến nghị)
python -m venv .venv
# Kích hoạt môi trường ảo:
# Trên Windows:
.venv\Scripts\activate
# Trên macOS/Linux:
source .venv/bin/activate

# Nâng cấp pip và cài đặt thư viện phụ thuộc
python -m pip install --upgrade pip
pip install -r requirements.txt

# Khởi chạy ứng dụng
python app.py
