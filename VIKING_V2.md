# Viking V2 CKCS

Viking V2 chỉ hỗ trợ chứng khoán cơ sở và bám theo
[`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md).

Trước khi sửa source, đọc [bản đồ kiến trúc](viking_v2/README.md). Tài liệu đó chỉ rõ mỗi chức năng nằm ở đâu và quy tắc không sinh thêm file vụn.

## Mở ứng dụng

```powershell
.\ckvnvenv\Scripts\python.exe -m viking_v2.main
```

Đây là lệnh khởi động duy nhất. Ứng dụng tự mở daemon; BOT luôn khởi động OFF.

Không có mode UI-only hoặc tùy chọn startup riêng cho người dùng.

## Kiểm tra dành cho phát triển

Chạy test nền V2:

```powershell
.\ckvnvenv\Scripts\python.exe -m pytest -q tests_v2
```

## Thành phần

- UI thực thi lệnh tay và xử lý hàng đợi lệnh.
- Daemon sở hữu market data, VNINDEX/OHLC 1D, rule static và heartbeat.
- Mỗi account có workspace độc lập tại `viking_v2/runtime/accounts/<account>/`.
- `settings.json`, runtime bridge, pending order, trade/rule state, market cache, CSV và log đều nằm trong workspace đó.
- UI thực thi decision thành MARKET/LO local, quản lý OTP và đối soát order DNSE.
- Pending, trade cycle, rule state và market cache đều phục hồi sau restart.
- Bot luôn OFF khi UI/daemon khởi động.
- Credential DNSE nằm trong `viking_v2/.env`. Trading Token mặc định chỉ giữ trong RAM; người dùng có thể chủ động bật lưu `.env` hoặc xóa token đã lưu ngay trong popup KẾT NỐI.
- Popup `KẾT NỐI` quản lý DNSE/account, watchlist CKCS và Telegram read-only; thay đổi watchlist/Telegram áp dụng ngay.
- DNSE chỉ yêu cầu API Key/Secret; popup kết nối và tải tài khoản CKCS. OTP Email/Smart tạo Trading Token; thời hạn thực tế sẽ được khóa sau vòng test API DNSE thật.
- Telegram có khung giờ cảnh báo, cooldown chống lặp và công tắc riêng cho cảnh báo hệ thống/trạng thái lệnh.
- V2 không tự sao chép credential hoặc state từ hệ thống cũ. Legacy được giữ riêng để tham khảo và không được import vào runtime V2.
- REAL/PAPER tách state; Telegram chỉ đọc.
- Tài liệu thực thi nằm ở [`docs/EXECUTION_SPEC.md`](docs/EXECUTION_SPEC.md).
