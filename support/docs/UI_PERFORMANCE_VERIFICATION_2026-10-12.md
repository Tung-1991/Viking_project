# Kiểm chứng giảm công việc vẽ UI — 12/10/2026

Đọc và đo code hiện tại trong tiến trình Tk thực, với mạng và khởi chạy dịch vụ bị chặn,
dotenv bị vô hiệu hóa, account/runtime/cache mới trong thư mục tạm. Không dùng runtime
tài khoản thật hoặc gọi broker/Telegram thật.

## Bằng chứng trước thay đổi

- Popup gọi `parent.update_idletasks()` trước khi dựng cửa sổ. Đo chuỗi mở RULE →
  KẾT NỐI → BACKTEST cho thấy callback mở KẾT NỐI mất 2,04 giây; 1,69 giây là
  xử lý các idle/layout đang chờ của cửa sổ trước. Callback BACKTEST mất 1,07 giây.
- Nhãn PREVIEW dùng CTkLabel nhận lại cùng `text_color` vẫn vẽ lại. Đối chứng
  bằng chính CTkLabel gốc trong cùng ứng dụng: 25 refresh PREVIEW/HEALTH với dữ liệu
  không đổi mất 0,83 giây, gây 950 lần `_draw` ở nhãn.

## Thay đổi

- `StatusLabel` chỉ bỏ qua `text`, `text_color`, `text_color_disabled`, `fg_color`
  đã bằng giá trị hiện có. Font, ảnh, wraplength, textvariable và yêu cầu vẽ cưỡng bức
  vẫn đi qua CTkLabel. Đổi DPI/appearance vẫn dùng cơ chế vẽ gốc.
- Các nhãn trong PREVIEW dùng StatusLabel. Mỗi vòng vẫn tính preview và đọc trạng thái
  mới như trước; không cache chỉ báo, tiền, vị thế, quyền đặt lệnh hoặc quyết định BUY/SELL.
- Bỏ ép idle trong constructor RULE, KẾT NỐI, scanner volume, BACKTEST, lịch sử và bảng
  dữ liệu. Các cửa sổ được vẽ bởi vòng sự kiện Tk bình thường sau khi callback trở về.

## Kết quả đo

- 25 refresh sau thay đổi: 0,49 giây, giảm khoảng 42% trong lượt đo này.
- Callback mở KẾT NỐI: 0,41 giây; BACKTEST: 0,65 giây trong cùng chuỗi mở popup.
  Đây là thời gian callback dựng cửa sổ, không phải thời gian hoàn tất mọi pixel.
  Layout vẫn được xử lý sau đó; không tuyên bố toàn bộ thời gian vẽ đã biến mất.
- Thời gian dựng main trong hai lượt khoảng 0,42–0,46 giây. Thay đổi này không
  chứng minh tăng tốc cold start, phần import Python hoặc truyền hình ảnh qua remote.
- Cả hai lượt có cProfile, cùng máy host và cache tạm. Số đo không thay thế đo
  trên chính VM sau cập nhật.

Chi tiết đo: `.artifacts/ui_performance_20261012/before.txt`, `after.txt`,
`native_labels.txt`. Probe chạy qua `support/tools/run_offline.py` và tái sử dụng
harness khởi động ứng dụng thực.

## Kiểm tra hành vi

`support/tests/test_ui_refresh_performance.py` kiểm tra:

- trạng thái không đổi không phát sinh vẽ/cập nhật Tk thừa;
- đổi trạng thái, màu và wraplength vẫn hiện đúng, vẽ cưỡng bức vẫn hoạt động;
- textvariable vẫn cập nhật bằng Tk;
- mở RULE/KẾT NỐI/BACKTEST/lịch sử/bảng không chạy idle callback đang chờ lồng
  bên trong constructor; callback đó vẫn chạy khi Tk xử lý idle bình thường.

Các bài hiện có kiểm tra mở app ở REAL/PAPER và nhiều DPI, popup/settings, preview
vốn/giá, TRACE cả khi không có tín hiệu, backtest, và đường gửi lệnh broker giả.
517 kiểm thử đạt, không lỗi/bỏ qua, chạy bằng `support/tools/run_offline.py`.
Kết quả cuối lưu tại `.artifacts/ui_performance_20261012/verified.xml`.

Đây là tối ưu UI. Lỗi treo do DPI đã tái hiện và sửa ở commit trước; chưa có stack
của lần treo trên VM để kết luận nó chỉ có một nguyên nhân. Không thay đổi rule,
ledger, queue, xác thực OTP hoặc mạng của host/VM.
