# ARM / DYNAMIC / E — chế độ và flow HDB

## Cấu hình

Trong **RULE → E/M**, ba cột có MODE riêng, chọn AUTO hoặc ALERT:

| Nhóm | Preset VA | Khi AUTO |
| --- | --- | --- |
| SAU ARM | AUTO | Đã đạt ARM, lùi từ đỉnh: bán 33% GỐC |
| DYNAMIC trước ARM | ALERT | Chạm mức ATR/giữ lãi: bán theo KL của DYNAMIC |
| E | AUTO | EMA/RSI SELL đạt: bán hết phần còn lại |

ALERT chỉ ghi nhận/cảnh báo; không đặt lệnh. Công tắc PROTECT/E của từng vị thế vẫn quyết định
nhóm nào được chạy. DYNAMIC ON vẫn tính mức trong ALERT. LẶP và KL của ARM/DYNAMIC giữ riêng.
E AUTO ưu tiên trước PROTECT; SL và TP giữ thứ tự ưu tiên hiện có.

Update source giữ lựa chọn tài khoản cũ: thiếu `dynamic_policy` thì kế thừa `normal_policy` trước đây.
Để dùng ba chế độ trên, chọn từng MODE rồi LƯU; hoặc đóng app và nạp preset bằng BAT 2 → 4 → 3.
Preset còn áp dụng hạn mức/P1/Telegram đã chốt, không tự bật PROTECT trên vị thế đang giữ.

## Chờ cổ về

1. PROTECT chạm mức nhưng broker chưa báo đủ một lô bán được: `CHỜ CỔ VỀ · CHƯA TẠO SELL`.
   Không tạo lệnh chờ T+2, không phát PROTECT HIT, không tiêu lượt AUTO/ALERT. Đỉnh và mức vẫn được theo dõi.
2. Có cổ bán được: xét lại giá/điều kiện hiện tại. ALERT chỉ báo. AUTO tạo yêu cầu theo KL của đúng nhóm.
   600 CP × 33% GỐC = 198 CP, làm tròn lô xuống 100 CP; chưa khớp thì chưa ghi đã bán.
3. Đổi một nhóm sang ALERT: hủy riêng yêu cầu của nhóm đó **chưa gửi** trong REAL/PAPER,
   gồm PENDING, WAITING_TOKEN, WAITING_SETTLEMENT, kể cả KEEP. Lệnh cũ ghi PROTECT/PRICE_PROTECTION
   được nhận diện; nếu không còn dữ liệu phân biệt nhóm thì hủy local thay vì cho vượt ALERT.
4. GUI kiểm tra chính sách mới trước khi lập lệnh từ quyết định daemon cũ; executor kiểm tra lại trước hand-off.
   Lệnh broker đã nhận/khớp một phần được giữ để đối soát hoặc operator hủy rõ ràng.
5. SL/TP/E AUTO có thể tạo yêu cầu SELL chờ khi chưa bán được. RECHECK hủy khi quyết định mới chứng minh
   điều kiện đã mất; KEEP giữ yêu cầu. Chính sách DYNAMIC ALERT không tắt SL/TP/E.

## HDB trong ảnh/log Ngài gửi

- Tin 09:30: DYNAMIC ON, MFE +1,55%, đỉnh 23.000đ, giữ 90%, KL 100%.
  Với giá vốn 22.650đ, mức giữ lãi là `22.650 + (23.000 − 22.650) × 90% = 22.965đ`.
  Giá 22.850đ đã chạm mức; đây là DYNAMIC trước ARM 7%, không phải trail sau ARM.
- Log 13:44:19 ghi **STOP_LOSS**, tạo SELL 600 HDB chờ cổ về; 13:44:20 ghi **ĐÃ HỦY**.
  Tin kỹ thuật cùng lúc chứa giá 21.100đ rồi 23.000đ. Log này không phải bằng chứng về một lần PROTECT khác,
  cũng không chứng minh DNSE đã bán. Chưa có payload/trace của VM để xác định nguồn giá 21.100đ.
- Preview tối: mức 23.082đ và `CHỜ CỔ VỀ` là điều kiện theo dõi, không phải trạng thái một lệnh SELL đã gửi.
  Header/detail/reason mới ghi rõ DYNAMIC/ARM, AUTO/ALERT và CHƯA TẠO SELL.

Telegram mới ghi rõ nhóm kích hoạt và tỷ lệ yêu cầu. Chỉ ghi mã, KL và trạng thái lệnh khi planner đã tạo
intent; không tự suy ra “đã tạo” từ AUTO. Khớp thực tế tiếp tục theo thông báo/ledger khớp lệnh riêng.

## ATR và kiểm chứng

ATR14 Wilder hiện cả đơn vị giá và phần trăm, ví dụ `535đ 2,38%`; đây là ví dụ UI tổng hợp.
Hai số cùng tính từ nến ngày đã đóng; % chia cho giá đóng cuối, không nhân với giá tick mới để giả ATR tiền.
Cache không ghi đè ATR của quyết định hiện tại; chỉ bổ sung số tiền nếu dữ liệu tính khớp cùng tỷ lệ.

Kiểm chứng qua broker giả, runtime tạm và mạng bị chặn: ma trận ARM/DYNAMIC AUTO/ALERT, trước/sau cổ về,
KEEP/RECHECK, legacy queue, đổi ALERT trong lúc đọc tài khoản, daemon AUTO cũ, replay qua T+2,
Wilder ATR và nội dung Telegram. Preview thật kiểm tra DPI 100/150%, ba MODE cùng hàng,
HEALTH cố định; sửa mất pixel khi đổi DPI nhiều lần.

Kết quả: 528 ca liên quan đạt trước kiểm tra race cuối; 124 ca rule/execution/race đạt sau đó;
63 ca UI/DPI và 203 ca preview/Telegram/daemon đạt. Các nhóm có ca trùng nhau, không cộng thành tổng.
Kết quả lưu tại `.artifacts/independent_protect_targeted.xml`, `.artifacts/independent_protect_ui.xml`.
Lượt toàn dự án ban đầu phát hiện fixture còn dùng EMA bằng nhau làm giao cắt; đã sửa thành dưới→trên
và kiểm tra lại 35 ca backtest. Không gửi lệnh, Telegram hoặc ghi cấu hình tài khoản thật để kiểm chứng.
