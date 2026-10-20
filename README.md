# Chi Tiêu Thông Minh

Website quản lý thu chi cá nhân, dùng Flask + SQLite. Có **đăng ký / đăng nhập**, mỗi tài khoản có dữ liệu riêng.

## Tính năng
- Đăng ký, đăng nhập, đăng xuất; mật khẩu được băm (không lưu dạng thô).
- CRUD giao dịch thu/chi, danh mục mặc định.
- Dashboard: số dư hiện tại (tính trên mọi giao dịch), tổng thu, tổng chi và số còn lại trong tháng, kèm so sánh với tháng trước.
- Biểu đồ cột thu/chi theo ngày (trong tháng), theo tháng (trong năm) và theo năm; rê chuột hoặc chạm vào cột để xem chi tiết.
- Biểu đồ chi tiêu theo danh mục.
- Ngân sách theo tháng và danh mục: thẻ tổng quan (tổng hạn mức, đã chi, còn lại), thanh tiến độ có vạch mốc 80%, danh sách danh mục vượt kế hoạch kèm số tiền vượt, sửa hạn mức, sao chép ngân sách từ tháng trước.
- Mục tiêu tiết kiệm: tạo mục tiêu mua sắm, du lịch, quỹ dự phòng hoặc khác; đặt số tiền, hạn hoàn thành và số đã tích lũy; nạp thêm hoặc rút bớt kèm lịch sử; theo dõi phần trăm hoàn thành, thời gian còn lại và số tiền cần tiết kiệm mỗi tháng; lọc Tất cả, Đang thực hiện, Đã đạt.
- Giao dịch định kỳ và nhắc lịch: khoản lặp lại theo tuần, tháng, năm (tiền thuê nhà, hóa đơn, lương...); lịch tháng hiển thị các kỳ đến hạn; nhắc trước N ngày trên Dashboard; bấm Ghi nhận hoặc Bỏ qua từng kỳ, hoặc bật tự động ghi nhận khi đến hạn (kể cả các kỳ bị lỡ khi chưa mở ứng dụng); tạm dừng, tiếp tục, ngày kết thúc. Ngày cuối tháng được xử lý đúng (31 chuyển thành 28, 29 hoặc 30 khi tháng ngắn).
- Cảnh báo ngân sách: khi lưu một khoản chi mà danh mục đã dùng từ 80%, đạt hoặc vượt 100% thì hiện cảnh báo ngay; Dashboard có thanh nhắc các danh mục cần chú ý.
- Quản lý giao dịch: thêm, xem, sửa, xóa; lọc theo khoảng ngày (có nút chọn nhanh: tháng này, tháng trước, 30 ngày qua, năm nay), loại thu/chi và danh mục; tìm theo ghi chú hoặc tên danh mục (không phân biệt hoa thường tiếng Việt); sắp xếp theo ngày hoặc số tiền; phân trang 20 giao dịch mỗi trang; sau khi sửa hoặc xóa sẽ quay về đúng trang đang lọc.
- Xuất CSV theo đúng bộ lọc đang chọn (lấy toàn bộ kết quả, không chỉ trang hiện tại; UTF-8 BOM, mở được bằng Excel).
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
Bộ test gồm 19 nhóm: danh mục, CRUD, dashboard, ngân sách, CSV, lưu bền vững, bắt buộc đăng nhập, đăng ký/đăng nhập, cách ly dữ liệu giữa người dùng, CSRF, nâng cấp từ DB cũ, số dư/so sánh kỳ trước/biểu đồ, tìm kiếm/lọc/sắp xếp/phân trang giao dịch, ngân sách và cảnh báo, mục tiêu tiết kiệm, giao dịch định kỳ, lưu trữ phía máy chủ và schema.sql, API JSON (đăng nhập, CSRF, giao dịch, ngân sách, tổng quan, biểu đồ), tên trường khớp schema. Test dùng file DB riêng và không ảnh hưởng dữ liệu thật.

## Bảo mật
- Mọi truy vấn dữ liệu đều lọc theo `user_id`; người dùng không xem hoặc sửa được dữ liệu của người khác.
- Form POST có token CSRF; cookie phiên `HttpOnly`, `SameSite=Lax`.
- Đăng nhập sai 5 lần sẽ bị khóa tạm thời 10 phút (theo email + IP).
- Chưa có chức năng quên mật khẩu hoặc xác thực email. Nếu mở rộng cho nhiều người dùng, nên bổ sung.
- Sao lưu: dừng app rồi sao chép file `chi_tieu.db` (hoặc thư mục `/data` trong Docker).

## Kiến trúc và lưu trữ dữ liệu

```
Trình duyệt  --fetch /api/...-->  Flask (app.py)  -->  SQLite (chi_tieu.db)
   (HTML + JavaScript)             API JSON             cấu trúc bảng: schema.sql
```

- Toàn bộ dữ liệu nằm trong **cơ sở dữ liệu SQLite phía máy chủ** (`chi_tieu.db`, hoặc đường dẫn trong biến `DB_PATH`). Trình duyệt **không lưu dữ liệu nào** (không dùng `localStorage`).
- Giao diện **gọi API của máy chủ bằng `fetch`**: biểu đồ Dashboard (`GET /api/chart`), thêm và sửa giao dịch (`POST`, `PUT /api/transactions`), xóa giao dịch (`DELETE /api/transactions/<id>`), lưu và xóa ngân sách (`POST /api/budgets`, `DELETE /api/budgets/<id>`). Các trang còn lại được máy chủ dựng sẵn và vẫn dùng được khi tắt JavaScript.
- Cấu trúc bảng nằm trong `schema.sql` và là **nguồn duy nhất**: `app.py` đọc và chạy file này mỗi lần khởi động (`init_db`). Muốn thêm bảng hoặc cột thì sửa `schema.sql`. Mọi câu lệnh dùng `IF NOT EXISTS` nên chạy lại không làm mất dữ liệu. File `schema.sql` phải nằm cạnh `app.py` (Dockerfile đã copy cả hai); nếu thiếu, ứng dụng dùng bản sao dự phòng nhúng trong `app.py` và ghi cảnh báo ra log.
- Tên trường thống nhất `snake_case` ở cả cơ sở dữ liệu lẫn JSON của API (`category_id`, `limit_amount`, `tx_date`, `created_at`...). Ngày lưu chuỗi ISO `YYYY-MM-DD`, số tiền là số nguyên VND.

## API REST

Mọi API trả JSON (lỗi có dạng `{"error": "..."}` kèm mã HTTP 400, 401, 403, 404, 405, 413, 429). Cần đăng nhập (cookie phiên). Các thao tác thay đổi dữ liệu cần header `X-CSRF-Token` (mã nhận được khi đăng nhập hoặc từ `GET /api/me`). Mọi dữ liệu chỉ thuộc về người đang đăng nhập.

| Phương thức và đường dẫn | Mô tả |
|---|---|
| `POST /api/login` | Đăng nhập: `{"email", "password"}`, trả `user` và `csrf` (không cần mã CSRF, bắt buộc gửi JSON) |
| `POST /api/logout`, `GET /api/me` | Đăng xuất; thông tin phiên và mã CSRF |
| `GET /api/categories` | Danh sách danh mục thu và chi |
| `GET /api/transactions` | Danh sách giao dịch. Tham số: `date_from`, `date_to`, `kind`, `category_id`, `q`, `sort`, `page`, `per_page` (tối đa 100). Trả `items`, `total`, `pages`, `total_income`, `total_expense` |
| `POST /api/transactions` | Thêm giao dịch: `kind`, `amount`, `tx_date`, `category_id`, `note`. Trả giao dịch và `warning` nếu danh mục đã dùng từ 80% ngân sách |
| `GET`, `PUT`, `DELETE /api/transactions/<id>` | Xem, sửa, xóa một giao dịch |
| `GET /api/budgets?month=YYYY-MM` | Ngân sách của tháng: hạn mức (`limit_amount`), đã chi, còn lại, phần trăm, trạng thái |
| `POST /api/budgets` | Thêm hoặc cập nhật ngân sách: `month`, `category_id`, `limit_amount` |
| `DELETE /api/budgets/<id>` | Xóa ngân sách |
| `GET /api/summary?month=YYYY-MM` | Số dư, tổng thu, tổng chi, so sánh tháng trước, chi theo danh mục, tình trạng ngân sách |
| `GET /api/chart?month=YYYY-MM` | Dữ liệu biểu đồ thu chi theo ngày, tháng, năm |

Ví dụ:

```bash
curl -c cj -X POST localhost:5000/api/login -H "Content-Type: application/json" -d '{"email":"a@example.com","password":"matkhau-cua-ban"}'
curl -b cj localhost:5000/api/transactions?per_page=5
curl -b cj -X POST localhost:5000/api/transactions -H "Content-Type: application/json" -H "X-CSRF-Token: <csrf>" \
     -d '{"kind":"expense","amount":"150000","tx_date":"2026-10-20","category_id":4,"note":"Ăn trưa"}'
```

## Nâng cấp cơ sở dữ liệu
Các tính năng mới chỉ **thêm bảng** (`goals`, `goal_deposits`, `recurring`) bằng `CREATE TABLE IF NOT EXISTS` khi ứng dụng khởi động. Dữ liệu và tài khoản hiện có không bị thay đổi hay xóa. Nên sao lưu file `chi_tieu.db` trước mỗi lần cập nhật.
