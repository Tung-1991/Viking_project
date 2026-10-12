# RULE chờ DB trên luồng UI — 12/10/2026

## Phát hiện có bằng chứng

Mức độ: lỗi phản hồi UI mức vừa. Nếu DB chậm hoặc đang có writer, mở RULE có
thể chặn vòng sự kiện Tk; sự chậm trễ đủ lâu có thể gây nhãn Not responding.
Chưa có stack của lần xảy ra trên VM để kết luận đây là nguyên nhân duy nhất.

Code ở commit `bad77d5`:

- `viking_v2/rules/window.py:1175`: `_refresh_blocks()` gọi `loss_blocks()` ngay
  trên Tk; gọi khi dựng Phase 3, khi `show()` và khi đổi sổ.
- `viking_v2/trading/state.py:353`: `loss_blocks()` đọc document của TradeStateStore.
- `viking_v2/trading/durable.py:35`: connection có timeout 15 giây;
  dòng 45 mở `BEGIN IMMEDIATE`, có thể phải chờ lượt ghi đang giữ khóa.

Tình huống tái hiện offline: tạo ledger tạm bằng TradeStateStore, mở RULE,
cho connection thứ hai giữ writer lock 2 giây, gọi `show()` trong callback Tk
và lên lịch heartbeat sau 50 ms.

- Mong đợi: giao diện tiếp tục xử lý heartbeat trong khi đọc danh sách khóa ở nền.
- Trước sửa: heartbeat đến sau 2,05 giây, khi writer đã nhả khóa.
- Sau sửa: khi popup đã dựng xong, heartbeat đến sau khoảng 53 ms, writer vẫn
  giữ khóa. Khi nhả writer, danh sách cập nhật và nút chuyển từ trạng thái đang đọc
  sang trạng thái phù hợp với kết quả.

Tần suất chưa đo trên VM. Ca này cần trùng thời điểm đọc RULE với lượt ghi DB
đủ chậm để người dùng nhận thấy; không suy ra xác suất từ một bài tái hiện.

## Sửa có phạm vi

`RuleSettingsPopup._refresh_blocks()` gửi tác vụ đọc hai sổ ra executor. Tk chỉ
kiểm tra future bằng timer 30 ms và hiển thị kết quả. Worker không gọi widget.
App dùng executor I/O sẵn có; popup độc lập có một worker riêng, được đóng khi
popup đóng.

- Nút mở khóa không hoạt động khi đang chờ hoặc đọc lỗi.
- Khi đổi REAL/PAPER trong lúc chờ, kết quả dùng sổ đang chọn lúc hiển thị.
- Lượt refresh phát sinh khi đang chờ được gộp thành một lượt tiếp theo.
- Đóng popup hủy timer và tác vụ chưa chạy; không đóng executor chung của app.
- Không thay đổi thuật toán, giao dịch broker, cấp vốn, khớp, phí/PnL hoặc khóa
  transaction trong ledger. Hành vi mở khóa sau khi người dùng xác nhận giữ nguyên.

Lần mở đầu vẫn tạo toàn bộ widget RULE đồng bộ; thay đổi này xử lý phần chờ DB,
không cam kết loại bỏ mọi chậm trễ do dựng/vẽ UI hoặc RDP.

## Kiểm chứng

`support/tests/test_rule_popup_responsiveness.py`: writer lock thật khi mở mới/mở
lại, đổi sổ khi đang chờ, đọc lỗi/phục hồi, đóng khi đang chờ và executor chung
tiếp tục hoạt động. Các bài loss-lock hiện có vẫn kiểm tra chế độ theo giờ/khóa
hẳn, lưu và mở đúng sổ/mã.

Chạy qua `support/tools/run_offline.py`, socket bị chặn, dotenv bị vô hiệu hóa,
runtime/account/cache ở thư mục tạm. Broker thật và Telegram thật không được gọi.

330 kiểm thử liên quan đạt, không lỗi hoặc bỏ qua, gồm khởi động app/DPI, loss-lock,
Priority, TRACE/lịch sử và các ca nghiệp vụ có lệnh hợp lệ gửi qua broker giả.

Artifact:

- `.artifacts/rule_popup_responsiveness_20261012/before.xml`: ca chờ writer thất bại trước sửa.
- `.artifacts/rule_popup_responsiveness_20261012/after_warm.xml`: heartbeat sau sửa.
- `.artifacts/rule_popup_responsiveness_20261012/verified.xml`: các ca UI/nghiệp vụ liên quan.

Phân biệt VM/RDP bằng việc đối chiếu thao tác trên console VMware và phiên RDP,
cùng bản code. Tốc độ host không loại trừ lỗi chờ DB trong app; cũng không đủ
bằng chứng kết luận thiếu CPU/RAM, lỗi driver hoặc RDP ở VM.
