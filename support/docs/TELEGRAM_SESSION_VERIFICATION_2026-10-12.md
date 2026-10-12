# Sửa BUY/MẤT BUY Telegram sau phiên — 12/10/2026

Lỗi: watcher Telegram tiếp tục xét bộ chỉ báo tải lại sau khi sàn đóng, tạo MẤT BUY mới; mode không bắt giao cắt cũng có thể báo BUY mới sau phiên.

Đã sửa:

- Chỉ nhận BUY kỹ thuật/MẤT BUY ở ATO, khớp liên tục, ATC theo sàn của từng mã và lịch giao dịch; tính lại giờ hiện tại, không dùng phase OPEN cũ để vượt giờ đóng.
- Nghỉ trưa tạm ngừng nhận thay đổi, giữ watcher. Đóng phiên xóa watcher Telegram, giữ riêng tin đã ghi nhận hợp lệ đang chờ gửi.
- Lưu giờ ghi nhận và sàn cùng tin. Gửi chậm giữ giờ gốc; sau phiên có nhãn GỬI CHẬM / PHIÊN ĐÃ ĐÓNG. Tin kỹ thuật ngày cũ hoặc có giờ gốc ngoài phiên bị bỏ.
- Tin riêng từ bản cũ dùng giờ lưu tin khi chưa có observed_at. Tin gom cũ thiếu observed_at bị bỏ: created_at là giờ bắt đầu gom, không chứng minh giờ của số liệu cuối cùng.
- Kiểm tra lại lúc worker gửi, bao gồm trường hợp đóng phiên giữa lúc xếp tin và worker chạy. Tin gom nhiều sàn đánh dấu riêng mã đã đóng phiên.

Phạm vi: thông báo kỹ thuật và watcher/outbox Telegram. Không đổi công thức EMA/RSI, WHIPSAW, thuật toán BUY/SELL, giờ mua, vốn/Priority, slot, preset hoặc cấu hình UI.
Tin lệnh đã xếp/CLOSED/PROTECT/E/hệ thống giữ luồng riêng.

## Kiểm chứng offline

- 25 ca giờ phiên/tin gửi chậm và 16 ca chạy daemon thật với broker/Telegram giả; REAL/PAPER, bật/tắt yêu cầu giao cắt.
- Đối chứng nến giữ nguyên, REST thiếu nến hôm nay, sửa close hoặc trùng nến: không thêm BUY/MẤT BUY sau 14:45, dù EMA/RSI thay đổi.
- Kiểm tra ATO/OPEN/ATC, 14:44:59 → 14:45, 14:49, 15:28, 16:09, nghỉ trưa, UPCOM đến 15:00, lịch/sàn chưa biết, restart/tin tồn từ bản cũ và đổi ngày.
- Kiểm tra tin trong phiên gửi muộn có giờ gốc, không tạo MẤT BUY tiếp từ watcher đã đóng; tin dài/chia phần, lease và chống gửi trùng vẫn đạt.
- Lượt hồi quy rộng: 682 ca đạt, gồm vốn/Priority, giao cắt, slot, execution/fill và startup UI. Sau bổ sung xử lý tin gom cũ thiếu giờ gốc: 82 ca liên quan đạt.
- Có đối chứng lệnh hợp lệ gửi đúng một lần qua broker giả trong test_session_ema_cross; không gửi lại UNKNOWN.

Tất cả chạy qua support/tools/run_offline.py; mạng bị chặn, runtime trong thư mục tạm. Không đọc secrets, không đặt lệnh/gửi Telegram thật hoặc sửa runtime tài khoản thật.

Bằng chứng: .artifacts/telegram_session_fix_20261012/{verified.xml,legacy.xml,daemon_*.json}.
Các kiểm thử duy trì trong support/tests/test_telegram_session.py và test_telegram_session_daemon.py.
