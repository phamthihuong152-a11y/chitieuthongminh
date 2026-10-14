# Chi Tiêu Thông Minh

Website quản lý thu chi cá nhân, dùng Flask + SQLite. Có **đăng ký / đăng nhập**, mỗi tài khoản có dữ liệu riêng.

## Tính năng
- Đăng ký, đăng nhập, đăng xuất; mật khẩu được băm (không lưu dạng thô).
- CRUD giao dịch thu/chi, danh mục mặc định.
- Dashboard: số dư hiện tại (tính trên mọi giao dịch), tổng thu, tổng chi và số còn lại trong tháng, kèm so sánh với tháng trước.
- Biểu đồ cột thu/chi theo ngày (trong tháng), theo tháng (trong năm) và theo năm; rê chuột hoặc chạm vào cột để xem chi tiết.
- Biểu đồ chi tiêu theo danh mục.
- Ngân sách theo tháng/danh mục, cảnh báo từ 80% và khi vượt hạn mức.
- Lọc theo tháng, loại, danh mục; tìm ghi chú; xuất CSV (UTF-8 BOM, mở được bằng Excel).
- Giao diện responsive cho máy tính/điện thoại.

## Chạy trên máy (Windows)
1. Cài Python 3.10+ (đánh dấu Add Python to PATH).
2. Mở CMD trong thư mục dự án và chạy:

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

3. Mở http://127.0.0.1:5000, bấm **Đăng ký** để tạo tài khoản đầu tiên.

Ứng dụng tự tạo `chi_tieu.db` (dữ liệu) và `.secret_key` (khóa ký phiên đăng nhập) cạnh `app.py`. Không đưa hai file này lên GitHub.

## Nâng cấp từ bản chưa có đăng nhập
Dữ liệu cũ được giữ nguyên. **Tài khoản đầu tiên đăng ký sẽ nhận toàn bộ dữ liệu cũ**, nên hãy tự đăng ký ngay sau khi deploy (trước khi chia sẻ đường dẫn cho người khác).

## Biến môi trường
| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `PORT` | Cổng lắng nghe (khi có biến này, app nghe `0.0.0.0`) | 5000 |
| `HOST` | Địa chỉ lắng nghe | `127.0.0.1` (hoặc `0.0.0.0` nếu có `PORT`) |
| `DB_PATH` | Đường dẫn file SQLite | `chi_tieu.db` cạnh `app.py` (Docker: `/data/chi_tieu.db`) |
| `SECRET_KEY` | Khóa ký cookie đăng nhập | tự sinh và lưu cạnh file DB |
| `INVITE_CODE` | Nếu đặt, người đăng ký phải nhập mã này | không đặt (ai cũng đăng ký được) |
| `COOKIE_SECURE` | Đặt `1` khi chạy sau HTTPS để cookie chỉ gửi qua HTTPS | tắt |
| `FLASK_DEBUG` | Đặt `1` để bật debug (chỉ dùng trên máy cá nhân) | tắt |

## Triển khai bằng Docker
`Dockerfile` chạy gunicorn trên `0.0.0.0:3000` với 1 worker (phù hợp SQLite). Trên nền tảng deploy:
- Gắn **volume vào `/data`** để giữ database và khóa phiên khi deploy lại.
- Đặt `COOKIE_SECURE=1` nếu trang chạy qua HTTPS.
- Nên đặt `INVITE_CODE` nếu không muốn người lạ tự đăng ký tài khoản.
- Có thể đặt `SECRET_KEY` riêng; nếu không, khóa được tự sinh trong `/data/.secret_key`.

## Kiểm thử
```bat
python test_app.py
```
Bộ test gồm 12 nhóm: danh mục, CRUD, dashboard, ngân sách, CSV, lưu bền vững, bắt buộc đăng nhập, đăng ký/đăng nhập, cách ly dữ liệu giữa người dùng, CSRF, nâng cấp từ DB cũ, số dư/so sánh kỳ trước/biểu đồ. Test dùng file DB riêng và không ảnh hưởng dữ liệu thật.

## Bảo mật
- Mọi truy vấn dữ liệu đều lọc theo `user_id`; người dùng không xem hoặc sửa được dữ liệu của người khác.
- Form POST có token CSRF; cookie phiên `HttpOnly`, `SameSite=Lax`.
- Đăng nhập sai 5 lần sẽ bị khóa tạm thời 10 phút (theo email + IP).
- Chưa có chức năng quên mật khẩu hoặc xác thực email. Nếu mở rộng cho nhiều người dùng, nên bổ sung.
- Sao lưu: dừng app rồi sao chép file `chi_tieu.db` (hoặc thư mục `/data` trong Docker).
