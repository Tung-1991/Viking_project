# Backtest Viking

Module này độc lập với daemon và trạng thái giao dịch thật.

- `data.py`: tải nến ngày cho rule và nến 5 phút cho thực thi, cache riêng theo resolution.
- `engine.py`: replay theo thời gian, dùng trực tiếp `rules.business.StaticRule`.
- `models.py`: cấu hình, kịch bản, giao dịch và kết quả.
- `report.py`: xuất một file Excel `Summary / Trades / NAV History / Config`; CSV là định dạng audit nội bộ.
- `window.py`: popup `LIÊN TỤC / GIAI ĐOẠN VA / KẾT QUẢ`.

Rule quyết định ở cuối nến ngày. Mô phỏng thực thi ưu tiên nến 5 phút, fallback 15 phút rồi nến ngày; lệnh hợp lệ khớp ở giá mở cửa phiên kế tiếp. BUY theo bội 100; có phí, thuế và T+2. Backtest không ghi vào order cache, token, Telegram hoặc state của bot thật.
