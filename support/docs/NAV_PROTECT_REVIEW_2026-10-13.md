# NAV, preview EMA và PROTECT — 13/10/2026

Ảnh phản ánh NAV 49.952.140đ trong app và 50.282.140đ trên DNSE, chênh 330.000đ.
600 HDB × (23.150 − 22.600) = 330.000đ: đây là tình huống tái hiện giá định giá cổ trong snapshot
chưa cập nhật, không phải bằng chứng đã truy xuất payload thật của VM.

- NAV trực tiếp do broker trả vẫn ưu tiên. Khi phải ước tính, dùng tổng tiền + cổ − nợ;
  cổ chờ về vẫn được tính. Giá bảng giá mới của đúng mã thay giá snapshot; giá giữ ở nghỉ trưa/đóng phiên
  chỉ được dùng trong cùng ngày. Giá lỗi, sai mã, cũ hoặc thiếu timestamp không thay giá broker.
  Tiền khả dụng là số riêng. Công thức tài sản tham chiếu [hướng dẫn DNSE](https://hdsd.dnse.com.vn/giao-dien-entrade-x/giao-dien/man-hinh-tai-san).
- Preview P2 giữ mode ∞/X phiên, số lần ↑↓ và trạng thái. EMA đặt bên phải BUY/E/RSI,
  không thêm dòng bên dưới P2. Khung hẹp dùng số lần cắt + ký hiệu + ngày cắt;
  số so sánh đầy đủ và giải thích nằm trong tooltip. Giữ font và khung preview 300px logic.
- PROTECT/DYNAMIC gặp mức khi broker báo chưa đủ một lô bán được: WAIT/CHỜ CỔ VỀ,
  không phát PROTECT HIT, không tạo yêu cầu SELL, không tiêu thụ lượt AUTO/ALERT.
  MFE và mức bảo vệ vẫn theo dõi. SL/TP/E giữ cơ chế SELL chờ hiện có; tin BUY kỹ thuật
  độc lập với điều kiện bán của vị thế và không xác nhận đặt/khớp lệnh.
- RECHECK huỷ yêu cầu EM đang chờ khi quyết định mới xác nhận điều kiện SELL đã mất,
  kể cả trước khi cổ về. Điều kiện còn đúng hoặc dữ liệu chưa có: giữ chờ; KEEP vẫn giữ yêu cầu.
  Không gửi thao tác huỷ broker; yêu cầu đã được broker nhận tiếp tục theo luồng quản lý lệnh.
- Ngài xác nhận **cho lãi giảm 28% từ đỉnh = giữ 72% lãi đỉnh**. Default và preset VA là 72.
  Khi operator đổi tỷ lệ giữ lãi, DYNAMIC tính lại sàn đã lưu; cùng tỷ lệ thì sàn vẫn không hạ
  vì ATR thay đổi/restart. Sau ARM giữ cơ chế trail hiện có. ATR có thể tạo sàn cao hơn mức giữ lãi.
- ARM, DYNAMIC trước ARM và E có MODE AUTO/ALERT riêng. Preset đặt ARM AUTO / DYNAMIC ALERT / E AUTO.
  ALERT chỉ báo; Telegram phải bật nhóm PROTECT. Đổi ALERT huỷ yêu cầu chưa gửi của đúng nhóm,
  kể cả KEEP/T+2 cũ; kiểm tra lại trước hand-off. Lệnh broker đã nhận không tự hủy.
  ATR hiện giá trị tiền và %, cùng tính từ nến ngày đã đóng; % chia cho giá đóng cuối.
- Bảng lệnh: chữ nội dung 14 → 16, tiêu đề 15 bold, dòng 48 → 58px. Nới các cột,
  đặc biệt PNL/MAE/MFE và trạng thái; đo mẫu nội dung bằng đúng font hiển thị để ba cặp
  số tiền/% không bị cắt sát cột bên cạnh. Giữ cuộn ngang và menu chuột phải/huỷ lệnh.

## Áp dụng trên VM

Thay đổi source không ghi đè settings tài khoản đã lưu. Sau khi bản sửa được phát hành tới nguồn
cập nhật của VM, đóng app và cập nhật source theo BAT 2.
Đổi riêng **RULE → E/M → DYNAMIC → GIỮ LÃI (%) = 72 → LƯU**; chọn ALERT nếu muốn chỉ báo.
Để nạp toàn bộ preset VA đã chốt dùng BAT **2 → 4 → kiểm tra tài khoản/xác nhận → 3**;
BAT 4 còn đổi hạn mức/P1/E/Telegram/TRACE. Preset không tự bật PROTECT cho vị thế đang có.

Kiểm thử qua `support/tools/run_offline.py` với broker giả, runtime tạm và mạng bị chặn.
Ca NAV dùng đúng các số trong ảnh, kiểm tra không sửa snapshot/vốn; PROTECT có ARM/DYNAMIC,
AUTO/ALERT, trước/sau cổ về; RECHECK/KEEP, thiếu quyết định, giá hồi; rebase sàn qua restart;
UI EMA scale 100/150/200% và lưu mode ALERT. Báo cáo lượt đầu: `.artifacts/nav_protect_review_final.xml`.
Lượt cuối: **2.322 passed**, 433,80 giây. `compileall`, `pip check` và `git diff --check` đều đạt.
Sau khi nới bảng lệnh và tăng font: **151 passed**, gồm UI, DPI, nhóm lệnh, huỷ lệnh và
hồi quy NAV/PROTECT; `.artifacts/order_table_release.xml`. `compileall` và kiểm tra diff đạt.
Không dùng các fixture để khẳng định đã kiểm chứng tài khoản thật hoặc đã cập nhật VM.

## Preview hai mode và Telegram theo vị thế

- X phiên quét lại lịch sử trước thời điểm thêm mã. Lần cắt có nguồn HISTORY chỉ dùng trong
  X phiên, không nhập vào VA khi chuyển mode. Lần cắt LIVE đã quan sát trong thời gian theo dõi
  vẫn có trong VA, kể cả sau khi hết hạn X; lần đã dùng vẫn là đã dùng, đổi mode không cấp lại.
- Nút ⇄ cạnh tên mode EMA chỉ đổi góc preview VA/X, không lưu settings hoặc tạo lệnh.
  Khi điều kiện cắt EMA OFF vẫn hiển thị memory đã lưu. Nếu chưa có cache lịch sử X và chưa có
  lần cắt LIVE, preview X hiển thị thiếu dữ liệu (`?`), không tự tải dữ liệu để tạo bằng chứng.
- Popup quản lý vị thế giảm từ 1040×610 xuống 680×410: giá/PNL và khả năng bán hiển thị hai dòng;
  giữ TP/SL, ba công tắc và nút lưu/tắt toàn bộ. Mô tả cơ chế nằm trong tooltip của từng công tắc.
  Không giảm font. Kiểm tra nút/input trong khung ở scale 100/125/150%.
- BUY kỹ thuật khi còn cổ có tiêu đề `EMA/RSI BUY · ĐANG GIỮ`, kèm lượng app còn giữ và đã bán
  khớp. T+ và SELL còn chờ được ghi riêng; tỷ lệ yêu cầu (ví dụ 33%) khác tỷ lệ thực đã bán.
  Cổ 600 CP, yêu cầu 33% có thể ra 100 CP do làm tròn lô: chỉ khi khớp 100 CP mới ghi đã bán
  16,67% và còn 500 CP. Tin kỹ thuật không sửa ledger, không xác nhận mua lại/mua thêm.
- Context đọc lúc gửi, kể cả tin gom/chờ retry; tách REAL/PAPER và loan package cho số bán được.
  Ledger đã đóng không bị một snapshot cũ làm báo còn cổ. Thiếu snapshot/field bán được không suy đoán T+.
  BOT đã chặn mua thêm khi có SELL chờ hoặc vị thế đã bán một phần; thông báo không nới chặn này.

Kiểm tra cuối gồm preview memory khi OFF/đổi cặp EMA/đóng phiên, swap hai mode không ghi settings,
popup thu gọn và T+/khớp từng phần: `.artifacts/ema_position_review_release.xml`.
Lượt cuối: **443 passed**, 39,16 giây. `compileall` và `git diff --check` đạt.
