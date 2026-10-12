# Chi Tiêu Thông Minh

Website quản lý thu chi và ngân sách cá nhân theo PRD v1.0.

## Cách chạy

Mở file `index.html` bằng trình duyệt (Chrome, Edge, Firefox...).

Hoặc dùng live server:

```bash
npx serve .
# hoặc
python3 -m http.server 8080
```

Sau đó truy cập `http://localhost:8080`.

## Tính năng đã có

- **Dashboard**: Tổng thu / tổng chi / số dư ròng theo tháng, biểu đồ cơ cấu chi tiêu, cảnh báo ngân sách, giao dịch gần đây
- **Giao dịch**: Thêm / sửa / xóa, lọc theo từ khóa, loại, danh mục, tháng
- **Ngân sách**: Tạo hạn mức theo tháng (tổng hoặc theo danh mục), hiển thị % sử dụng, cảnh báo 80% và vượt ngân sách
- **Danh mục**: Danh mục mặc định + thêm danh mục cá nhân
- **Xuất CSV**: Xuất giao dịch theo bộ lọc

## Dữ liệu

- Lưu trong **localStorage** của trình duyệt (không cần backend)
- Có sẵn dữ liệu mẫu (lương 15tr + các khoản chi) để demo ngay
- Muốn reset dữ liệu: mở DevTools → Application → Local Storage → xóa các key `cttm_*`

## Kiểm thử theo PRD

| Tiêu chí | Kết quả |
|----------|---------|
| Thêm/sửa/xóa giao dịch | ✅ |
| Dữ liệu còn sau reload | ✅ (localStorage) |
| Tổng thu 15tr, chi ~5.65tr → số dư đúng | ✅ |
| Cảnh báo ngân sách 80% / vượt | ✅ |
| Xuất CSV | ✅ |
| Responsive mobile | ✅ |

## Lưu ý

Đây là bản demo frontend-only. Nếu cần backend thật (SQLite/Postgres + auth) có thể mở rộng sau.
