# Giữ giao cắt EMA trong phiên — kiểm chứng ngày 10/10/2026

## Hành vi

- Thêm `buy_signal_session_cross_enabled`, mặc định OFF, trong RULE → Phase 2 → BUY.
  Khi ON với REALTIME và hai lựa chọn EMA, lần cắt lên trong ngày còn hiệu lực có thể dùng sau restart.
- Khi đang chạy vẫn nhận cắt mới theo nhịp chỉ báo; lịch sử giá phút dùng khôi phục phần không quan sát được.
  EMA được dựng từ các phiên ngày đã đóng và một giá đóng ngày tạm tính, không đổi sang EMA trên nến phút.
- Cache `session_prices.json` theo mã/ngày dùng chung hai sổ, ghi nguyên tử, chỉ tải khoảng thiếu.
  Không tải lại dữ liệu đã có khi bật/tắt lựa chọn hoặc mở preview. Lỗi tải thì chờ; retry sau 60 giây.
- EMA xuống/bằng hoặc hết phiên làm mất hiệu lực. Một ID giao cắt chỉ tạo một lệnh mỗi sổ.
  ID theo phút giữ nguyên khi cắt realtime được đối chiếu với phút hoàn tất; dao động cùng phút không tạo nhiều lệnh.
- Claim giao cắt và ghi queue chung transaction SQLite; lỗi ghi queue rollback claim.
  BUY OFF, khóa, thiếu vốn/slot chưa tạo queue thì không consume. Lệnh đã xếp nhưng hủy/hết hạn vẫn consume.
- Xác nhận X phút bắt đầu từ quan sát hiện tại, không backdate theo giờ cắt lịch sử.
  Khi đã xác nhận mà đang chờ vốn, tiếp tục kiểm tra điều kiện và giữ timer đến khi được dùng hoặc bị hủy.
- Trước hand-off kiểm tra ID còn hiệu lực và giá mới nhất vẫn nằm trên ngưỡng giao cắt.
  Các kiểm tra hiện hữu về quote, vốn, Priority, slot, BUY thêm và UNKNOWN giữ nguyên.
- Preview thêm trạng thái CÒN HIỆU LỰC / ĐÃ DÙNG + giờ / HẾT PHIÊN trong vùng P2 hiện có.
  TRACE vẫn cấu hình duy nhất tại Lịch sử; khóa lỗ vẫn THEO GIỜ / KHÓA HẲN.

## Kiểm thử mới

`support/tests/test_session_ema_cross.py`: 40 trường hợp đạt trong 16,24 giây, chạy bằng
`support/tools/run_offline.py`, không đọc dotenv và không có kết nối mạng.

Các đối chứng đã chạy:

- Cắt 14:10, mở lại 14:20 vẫn BUY nếu EMA/RSI hiện tại đạt; chế độ cũ không tự BUY khi EMA đã ở trên.
- EMA bằng kết quả tính độc lập trên chuỗi nến ngày; giá phút chỉ cập nhật phiên ngày hiện tại.
- Restart cùng cache không gọi lại lịch sử; khoảng trống 14:20–14:30 chỉ tải khoảng đó; đổi ngày tải phiên mới.
- Tải lỗi/NaN/Infinity/giá không dương không tạo tín hiệu; `no_data` không tự tạo giá hoặc giao cắt.
- EMA xuống hủy tín hiệu, restart không hồi sinh; cắt mới được nhận; cắt realtime giữ ID khi phút đóng.
  Lịch sử phút lỗi tạm thời sau gián đoạn không xóa ID cắt realtime; khi tải lại thành công vẫn giữ đúng sự kiện.
- RSI không tăng, chỉ báo chưa sẵn sàng, khóa lỗ, hết vốn/slot hoặc lệnh BUY đang chờ đều chặn BUY.
- REAL/PAPER độc lập; claim không mất sau restart/hủy/toggle; lỗi ghi queue không mất tín hiệu.
- Broker giả thực sự nhận một lệnh MARKET hợp lệ. Giá xuống ở quote cuối/hết phiên không gửi.
  Broker giả timeout sau hand-off tạo UNKNOWN; chạy worker lần nữa không gọi gửi lại.
- Giờ mua và xác nhận vẫn áp dụng; cửa sổ cũ không hồi sinh tín hiệu đã dùng; timer đủ không khởi động lại khi chờ vốn.
- Lưu công tắc trong RULE và đồng bộ Backtest; replay chỉ nhận RSI đạt muộn khi giữ cắt được bật.

Kết quả suite cuối và XML nằm trong `.artifacts/session_cross_20261010/verified_full_output.txt`
và `verified_full.xml` của lượt kiểm thử này; kiểm thử riêng chức năng nằm trong `final_feature.xml`.

Suite toàn bộ trên bản code cuối: **2.001 passed trong 278,28 giây**, JUnit **0 failure, 0 error, 0 skipped**.
Lệnh: `.\ckvnvenv\Scripts\python.exe support/tools/run_offline.py support/tests -q --disable-warnings --tb=short`.

## Phụ thuộc dữ liệu broker

Khôi phục yêu cầu DNSE trả OHLC phút đúng mã, mốc thời gian và cùng thang giá với dữ liệu ngày.
Giá phút không tái dựng được mọi dao động giữa hai mẫu; sự kiện đã quan sát realtime được lưu để giữ qua restart.
Kiểm thử dùng broker giả và runtime tạm; không gửi lệnh/Telegram thật hoặc chạy daemon với tài khoản thật.
