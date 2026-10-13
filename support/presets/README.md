# Preset VA — ghi chú TRACE bốn mã

Cập nhật ngày 13/10/2026. Preset: `VA_4_MA_50M.json`.

| Thiết lập | Giá trị |
| --- | --- |
| Mã theo dõi/Priority | MSN, CTS, HDB, IDC |
| LƯU TRACE | ON |
| Từ giờ / đến giờ | 14:00 / 14:30, giờ Việt Nam |
| Nhịp ghi | 2 phút |
| Mốc ghi | 14:00, 14:02, 14:04, …, 14:28, 14:30 |
| Đủ khung giờ | 16 mẫu/mã/sổ; 64 mẫu cho bốn mã mỗi sổ |
| BUY cần EMA cắt lên | ON, chỉ sau khi theo dõi mã |
| Giữ EMA | ∞ VA; có thể đổi ⏳ X PHIÊN trong RULE (ô X lưu 3) |
| ARM và DYNAMIC | Mỗi nhóm bán 33% GỐC, REPEAT ON riêng |
| E AUTO | Bán 100% phần còn lại |
| Chốt thống kê ngày | 15:00 Việt Nam; giữ PNL/phí đến 09:00 sáng hôm sau |

Ghi cả khi không có BUY hoặc BOT OFF. Thiếu giá/quyết định thì ghi tình trạng thiếu dữ liệu; không giả số EMA/RSI. TRACE không gửi Telegram hoặc tạo lệnh.

App cần chạy suốt khung giờ. Ngày nghỉ không ghi; mở muộn hoặc app tắt không bù những mốc đã bỏ lỡ. Mẫu đã ghi không bị ghi trùng khi restart.

Xem: **LỊCH SỬ → TÍN HIỆU → LÀM MỚI → Ngày → Mã → ĐỊNH KỲ**. Bốn mã được nhóm riêng dưới ngày ghi; cửa sổ đang mở cần bấm LÀM MỚI để đọc mẫu mới. XUẤT EXCEL lấy cả nhóm chưa xổ.

Git mang theo preset, không tự ghi đè cấu hình tài khoản trên VM. Nếu cần nạp toàn bộ preset VA: đóng app, dùng BAT **2 → 4 → kiểm tra tài khoản và xác nhận → 3**. Preset còn chứa hạn mức, P1, E và lựa chọn tin Telegram đã chốt; muốn chỉ chỉnh lịch TRACE thì dùng **⚙ GHI TÍN HIỆU**.

Kiểm chứng offline ngày 12/10/2026 bằng chính preset: REAL/PAPER, BOT ON/OFF, không có BUY vẫn đủ bốn nhóm mã, 16 mốc/mã và 64 mẫu/sổ trong bảng và Excel. 186 kiểm thử liên quan đạt qua `support/tools/run_offline.py`; kết quả tại `.artifacts/va_trace_preset_20261012/verified.xml`.

Cơ chế EMA và bán lặp mới: [ghi chú triển khai](../docs/EMA_PROTECTION_V2_2026-10-13.md).
