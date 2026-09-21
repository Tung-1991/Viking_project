# Viking — hướng dẫn vận hành PAPER

Tài liệu này mô tả code hiện hành. Báo cáo `backtest_4_modes_20260918.md` là tài liệu lưu trữ của một cấu hình nghiên cứu cũ.

## Luồng quyết định

1. **P1 · VNINDEX:** tự phân loại `UPTREND / ACCUMULATION / DISTRIBUTION / DOWNTREND` và lấy tỷ trọng cổ phiếu tương ứng.
2. **P2 · ENTRY BUY:** EMA BUY và RSI tạo điểm mua. `VOLUME ENTRY` mặc định OFF; khi ON, volume tích lũy của nến ngày hiện tại phải đạt tỷ lệ tối thiểu so với trung bình các phiên ngày đã đóng trước đó.
3. **P3 · Vốn:** giới hạn số vị thế, vốn cho mỗi mã, chống whipsaw và khóa mua sau chuỗi lỗ.
4. **E/M · Thoát:** SL, TP, PROTECT và E là các nhánh độc lập. E chính chỉ còn EMA SELL + RSI; nhánh E sớm thử nghiệm đã bị xoá hoàn toàn.

## Hai tùy chọn vận hành mới

- `OVERRIDE P1` mặc định OFF. Khi ON, trạng thái và `CP %` cố định chỉ điều khiển BUY mới; P1 tự động vẫn chạy để quan sát và vị thế đang giữ không bị sửa.
- `SL` trong `THỰC THI → E/M MẶC ĐỊNH` mặc định ON. Giá trị ON/OFF được chụp vào từng trade BOT mới. Đổi setting không hồi tố vị thế cũ; lệnh manual vẫn quản lý riêng. Khi SL OFF, PROTECT và E vẫn có thể thoát lệnh nếu được bật.

## Dynamic dưới ARM

Dashboard chỉ hiện một dòng ngắn: `ATR14 · START · LÙI`. Di chuột vào dòng này để xem giải thích.

- `ATR14` dùng dữ liệu các phiên ngày đã đóng, không dùng nến tương lai.
- `START ×`: ngưỡng MFE bắt đầu Dynamic = ATR% × hệ số START.
- `TRAIL ×`: khoảng giá được lùi từ đỉnh = ATR% × hệ số TRAIL.
- `GIỮ LÃI %`: mức bảo vệ = giá mua + phần trăm đã chọn của đoạn từ giá mua đến đỉnh cao nhất.
- `TỚI MFE %`: chỉ giới hạn việc tiếp tục nâng theo GIỮ LÃI; mức đã khóa không hạ và TRAIL ATR vẫn có thể nâng.
- Khi MFE đạt ARM, PROTECT chuyển sang trail sau ARM theo cấu hình chung.

## Preview và Health

- P1 ghi rõ trạng thái, tỷ lệ cổ phiếu/tiền, `AUTO` hay `OVERRIDE`, tiến độ xác nhận và thời điểm cập nhật.
- P2 tách rõ BUY và E; `WAIT` nghĩa là chưa có tín hiệu BUY mới, không phải SELL đang chờ.
- P3 ghi rõ số vị thế đang mở, vốn kế hoạch, whipsaw và chuỗi lỗ.
- `HEALTH OK` màu xanh là trạng thái hiện tại bình thường. Lỗi lịch sử đã phục hồi không giữ tiêu đề ở màu đỏ. Thị trường đóng được coi là trạng thái trung tính.

## Telegram

Màn hình chính chỉ giữ các control vận hành. Cơ chế gom BUY, chống trùng CLOSED và chốt quyền được giải thích trong nút `?`; không hiển thị các câu mô tả dài trực tiếp trên UI.
