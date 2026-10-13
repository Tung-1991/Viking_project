# EMA theo dõi mã và bán bảo vệ độc lập — 13/10/2026

## Hành vi đã triển khai

- `BUY CẦN EMA CẮT LÊN` vẫn là công tắc chính. OFF xét mức EMA hiện tại; ON cần giao cắt sau baseline của mã.
- Thêm mã khi nhanh đã trên chậm: chờ xuống rồi lên. Thêm khi dưới/bằng: chờ lên. Không nhập giao cắt trước mốc theo dõi.
- `∞ VA` giữ ghi nhận qua phiên đến khi tiêu thụ; `⏳ X PHIÊN` giữ X phiên tính cả phiên cắt, bỏ ngày nghỉ. Cắt lên mới bắt đầu thời hạn mới.
- Xuống/bằng không xóa lần cắt cũ hoặc bộ đếm, nhưng không cho BUY. Một giao cắt chỉ tạo một ý định mua; claim và queue dùng chung giao dịch SQLite.
- Restart giữ lịch sử/claim. Xóa rồi thêm lại, đổi cặp EMA hoặc REALTIME/CLOSED lập baseline mới. REALTIME khôi phục phút sau quan sát cuối; CLOSED chỉ nhận các nến ngày đã đóng sau baseline.
- Preview gọn `∞ · ↑1 ↓1 · ✓` hoặc `⏳2/3P · ↑1 ↓1 · ⏸`; hint giải thích mốc và icon. TRACE/lịch sử/Excel lưu mode, số phiên, bộ đếm và mốc bắt đầu.
- WHIPSAW giữ cách đếm trên nến ngày. ↑↓ mới là số lần đã quan sát/khôi phục, không khẳng định đầy đủ mọi dao động giữa hai mẫu phút.
- ARM và DYNAMIC có `% bán`, `GỐC/CÒN LẠI`, `REPEAT` và trạng thái tiêu thụ riêng. DYNAMIC trước ARM; sau ARM dùng trail đã có. AUTO/ALERT là chính sách chung.
- Mặc định và preset: mỗi nhóm 33% GỐC, REPEAT ON. GỐC tính trên tổng cổ đã mua khớp của giao dịch; CÒN LẠI tính trên số còn giữ. Làm tròn lô, chặn theo số cổ quản lý/bán được và reservation.
- REPEAT nhận hồi trên mức rồi chạm lại hoặc giảm tiếp; không đòi đỉnh mới. Giá đứng yên không lặp, lệnh đang chờ/UNKNOWN chặn lượt tiếp theo. Khớp nhiều phần chỉ tính một lượt.
- Mốc REPEAT gắn với hoàn tất lệnh, kể cả khớp một phần rồi hủy phần còn lại. Ghi nhận hoàn tất lặp/điều chỉnh phí không xóa trạng thái hồi giá.
- SL/TP/E AUTO vẫn bán 100% phần còn lại. E AUTO ưu tiên trước PROTECT. Không đổi luật RSI, giờ mua, vốn/Priority/slot, OTP, phí hoặc cơ chế UNKNOWN.
- T+2 kiểm tra lại giữ đúng cơ sở bán của từng nhóm; chuyển AUTO sang ALERT hủy chỉ các SELL bảo vệ chưa gửi. DYNAMIC dùng loại thông báo PROTECT và cơ chế gom hiện có.

## Cập nhật và cấu hình

Git không nạp preset vào tài khoản thật. Đóng app, BAT `2 → 3` để cập nhật/chạy lại; dùng `2 → 4 → xác nhận tài khoản → 3` nếu muốn nạp toàn bộ preset VA.

Preset chọn ∞ VA, lưu ô X=3; có thể đổi trong RULE. Không tự bật PROTECT trên trade đang giữ. Cấu hình cũ dùng 100% mặc định chuyển thành 33%/REPEAT ON; lựa chọn bán từng phần đã lưu được giữ, làm cơ sở khởi tạo DYNAMIC. Cấu hình phiên bản 2 giữ lựa chọn 100% có chủ ý.

Các mã từ phiên bản cũ lập baseline mới từ mẫu hợp lệ sau cập nhật, vì state cũ không có mốc thêm mã để chứng minh giao cắt trước đó. Vị thế, ledger, fill và phí giữ nguyên. Trạng thái bảo vệ cũ có peak kích hoạt rõ ở dưới ARM được chuyển sang nhóm DYNAMIC, không tiêu lượt ARM.

## Kiểm chứng mới

Chỉ dùng `support/tools/run_offline.py`, runtime/account tạm, broker và Telegram giả. Không đặt lệnh hoặc gửi Telegram thật, không đọc secrets hoặc runtime tài khoản thật.

- Toàn bộ suite: **2.267 passed**, `.artifacts/ema_protection_release.xml`.
- Sau khi hoàn thiện các nhánh DYNAMIC/Telegram/T+2: **235 passed**, `.artifacts/ema_protect_consumers_final.xml`.
- Kiểm tra popup/preset cuối: **167 passed**, `.artifacts/ema_protect_ui_final.xml`.

Đối chứng thực thi có lệnh MARKET hợp lệ đến broker giả; giá xuống ở kiểm tra gửi cuối, EMA hết hạn, UNKNOWN không gửi lại. Có các ca ATC → phiên sau/carry, X phiên/ngày nghỉ, restart/xóa-thêm mã, hai cơ sở 33%, ARM/DYNAMIC độc lập, khớp từng phần/hủy/đối soát, SL/TP/E và giới hạn cổ bán.

Các nguồn kiểm thử: `test_ema_memory_v2.py`, `test_session_ema_cross.py`, `test_protection_repeat_v2.py`, `test_memory_protect_backtest_v2.py`, `test_memory_protect_ui_v2.py` cùng bộ hồi quy hiện tại. Kết quả lấy từ lần chạy mới, không dùng báo cáo cũ làm bằng chứng.

Broker thật vẫn quyết định tính đầy đủ của dữ liệu phút/lịch nghỉ, giá khớp, thời điểm cổ bán được và trạng thái lệnh. Thiếu dữ liệu phục hồi thì chặn điều kiện EMA và hiện ?, không dựng giao cắt để mua. DAILY không tái tạo được giao cắt trong phiên; dùng REPLAY với dữ liệu phút để đối chiếu tình huống ATC.
