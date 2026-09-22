# Viking — hướng dẫn vận hành PAPER

Tài liệu này mô tả code hiện hành. Báo cáo `backtest_4_modes_20260918.md` là tài liệu lưu trữ của một cấu hình nghiên cứu cũ.

## Luồng quyết định

1. **P1 · VNINDEX:** tự phân loại `UPTREND / ACCUMULATION / DISTRIBUTION / DOWNTREND` và lấy tỷ trọng cổ phiếu tương ứng.
2. **P2 · ENTRY BUY:** EMA BUY và RSI tạo điểm mua. `VOLUME ENTRY` mặc định OFF; khi ON, volume tích lũy của nến ngày hiện tại phải đạt tỷ lệ tối thiểu so với trung bình các phiên ngày đã đóng trước đó.
3. **P3 · Vốn:** giới hạn số vị thế, vốn cho mỗi mã, chống whipsaw và khóa mua sau chuỗi lỗ.
4. **E/M · Thoát:** SL, TP, PROTECT và E là các nhánh độc lập. E chính chỉ còn EMA SELL + RSI; nhánh E sớm thử nghiệm đã bị xoá hoàn toàn. E có ba trạng thái vận hành: `OFF` bằng công tắc E/M của trade, `ALERT` chỉ báo và `AUTO` bán 100% phần còn lại.

## Mặc định và cấu hình đã lưu

- `viking_v2/config.py` là nguồn mặc định cho tài khoản mới và các trường còn thiếu.
- Nút **LƯU THAY ĐỔI** ghi cấu hình UI vào `viking_v2/runtime/accounts/<ACCOUNT_ID>/settings.json`.
- Khi nạp lại, JSON được ưu tiên hơn mặc định; thay đổi default trong code không ghi đè lựa chọn đã lưu trên UI.
- Backtest lưu riêng tại `viking_v2/runtime/backtest/settings.json`, không tự thay đổi PAPER/REAL.

## E · EXIT SELL

- Rule E gốc: EMA SELL nhanh cắt xuống EMA SELL chậm và RSI14 giảm. EMA SELL mặc định 3/6, tách khỏi EMA BUY dù hiện cùng giá trị.
- `ALERT` là mặc định mới: vẫn tính E, ghi signal log và hiện trên dashboard; chỉ gửi Telegram khi `E · EXIT ALERT` được bật trong bảng Telegram; không tạo pending sell và không đặt lệnh.
- `AUTO`: cùng tín hiệu E nhưng bán 100% phần còn lại. `OFF`: trade không có `IND_EXIT`, backend bỏ hẳn E cho trade đó.
- Cùng lúc E ALERT và PROTECT AUTO chạm ngưỡng, PROTECT vẫn được quyền bán; cảnh báo E không được chặn SL, TP hoặc PROTECT.
- Telegram chống trùng theo `PAPER/REAL + mã + lần tín hiệu`; cooldown E mặc định 30 phút. Nội dung ghi EMA SELL, RSI trước/sau và câu xác nhận không đặt lệnh.
- Không còn E sớm sau T+2 trong UI hoặc backend.

## Hai tùy chọn vận hành mới

- `OVERRIDE P1` mặc định OFF. Khi ON, trạng thái và `CP %` cố định chỉ điều khiển BUY mới; P1 tự động vẫn chạy để quan sát và vị thế đang giữ không bị sửa.
- `SL` trong `THỰC THI → E/M MẶC ĐỊNH` mặc định ON. Giá trị ON/OFF được chụp vào từng trade BOT mới. Đổi setting không hồi tố vị thế cũ; lệnh manual vẫn quản lý riêng. Khi SL OFF, PROTECT và E vẫn có thể thoát lệnh nếu được bật.

## Dynamic dưới ARM

Dashboard chỉ hiện một dòng ngắn: `ATR14 · START · LÙI`. Di chuột vào dòng này để xem giải thích.

Cấu hình PAPER mặc định đã chốt: Dynamic ON; START ×0,55; TRAIL ×0,8;
giữ 90% lãi đỉnh tới MFE 5%; ARM 7%; trail sau ARM 2,5%; bán 100%;
REPEAT OFF. SL lệnh đầu −3,5%, lệnh vào lại −2,5%; E 3/6 + RSI14 ở ALERT.

- `ATR14` dùng dữ liệu các phiên ngày đã đóng, không dùng nến tương lai.
- `START ×`: ngưỡng MFE bắt đầu Dynamic = ATR% × hệ số START.
- `TRAIL ×`: khoảng giá được lùi từ đỉnh = ATR% × hệ số TRAIL.
- `GIỮ LÃI %`: mức bảo vệ = giá mua + phần trăm đã chọn của đoạn từ giá mua đến đỉnh cao nhất.
- `TỚI MFE %`: chỉ giới hạn việc tiếp tục nâng theo GIỮ LÃI; mức đã khóa không hạ và TRAIL ATR vẫn có thể nâng.
- Khi MFE đạt ARM, PROTECT chuyển sang trail sau ARM theo cấu hình chung.

`RECHECK` áp dụng khi tín hiệu bán xuất hiện trong T+2 nhưng cổ phiếu chưa được phép bán.
Khi cổ về, bot kiểm tra lại điều kiện thoát theo giá và tín hiệu hiện tại: điều kiện còn đúng thì bán,
điều kiện đã mất thì huỷ yêu cầu bán cũ. Bot không bán chỉ vì đã từng có tín hiệu trong T+2.

## Preview và Health

- P1 ghi rõ trạng thái, tỷ lệ cổ phiếu/tiền, `AUTO` hay `OVERRIDE`, tiến độ xác nhận và thời điểm cập nhật.
- P2 tách rõ BUY và E; `WAIT` nghĩa là chưa có tín hiệu BUY mới, không phải SELL đang chờ.
- P3 ghi rõ số vị thế đang mở, vốn kế hoạch, whipsaw và chuỗi lỗ.
- `HEALTH OK` màu xanh là trạng thái hiện tại bình thường. Lỗi lịch sử đã phục hồi không giữ tiêu đề ở màu đỏ. Thị trường đóng được coi là trạng thái trung tính.

## Telegram

Mọi cấu hình gửi tin nằm tại `KẾT NỐI → TELEGRAM`, dùng chung cho PAPER và REAL:

- `BOT BUY ĐÃ XẾP LỆNH`: gom theo số phút cấu hình; không gửi MANUAL.
- `VỊ THẾ BOT ĐÃ ĐÓNG`: gửi một lần sau khi đã bán hết.
- `PROTECT CHẠM MỨC`: chỉ điều khiển tin. AUTO vẫn bán khi OFF; ON tạo “vừa bán vừa báo”. ALERT không bán.
- `E · EXIT ALERT`, `BOT BUY BỊ CHẶN`, `CẢNH BÁO CHỐT QUYỀN` và `SELL TRÊN DNSE APP`: bật/tắt và đặt cooldown riêng.

Mở lại app không phát lại tín hiệu đã hết hiệu lực. BUY đang còn hợp lệ được quan sát như tín hiệu hiện tại và chống trùng bằng state lưu trên ổ đĩa.

## Slot BOT và lệnh ngoài Viking

- `max_positions=5` chỉ là quota của BOT. MANUAL/EXTERNAL không chiếm slot BOT nhưng vẫn trừ tiền và room danh mục.
- Dashboard hiển thị riêng `BOT x/5`, `MANUAL y` và tổng số mã thực tế.
- Ưu tiên BUY qua Viking. Lượng mua thêm trực tiếp trên DNSE được gắn `EXTERNAL_DNSE`; trade Viking cùng mã vẫn tiếp tục và chỉ quản lý khối lượng của chính nó.
- SELL khẩn cấp qua DNSE app được Viking đối soát từ position và lệnh khớp ngoài remark `V2:`. Khớp rõ ràng thì cập nhật trade, khóa BUY 15 phút và báo Telegram theo setting; dữ liệu mơ hồ chỉ cảnh báo, không tự đoán.
