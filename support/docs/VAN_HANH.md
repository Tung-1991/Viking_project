# Money Hunter — đọc nhanh để vận hành

## Cài / cập nhật / mở

- Mở `START_SYSTEM.bat`: **1 → 1** chỉ rà soát; **1 → 2** cài phần thiếu
  (Python x64/Tk, Git, VC++ x64, venv, package đúng phiên bản). Cài phần mềm hệ thống
  cần **Run as administrator**. Nếu yêu cầu reboot, reboot rồi kiểm tra lại.
- **2** kiểm tra GitHub; có bản mới thì backup và ghi đè source. Đóng app trước.
  Bản ZIP thiếu `.git` được tự nối repo; mất mạng lúc nối thì chọn 2 lại để tiếp tục.
  `.env`, runtime và venv giữ tại máy; backup ở `.artifacts/update-backups/`, không lên Git.
- **3** khởi động, giữ console xem log. Đóng app hoặc Ctrl+C để dừng.
  Giữ PC/VM bật, không sleep/logoff. Ngắt RDP không phải đăng xuất.

## Trước khi bật BUY BOT

1. KẾT NỐI → DNSE: nhập API, **TEST → kiểm tra đúng tài khoản → LƯU API**.
2. EMAIL OTP/SMART OTP đúng phương thức DNSE đang dùng → nhập mã → XÁC THỰC.
   OTP không lưu; Trading Token phải còn hiệu lực để gửi REAL.
   XÓA TOKEN chỉ bỏ token. XÓA API bỏ cả API/token trên máy, giữ settings/vị thế;
   không hủy lệnh DNSE. Muốn giao dịch REAL tiếp phải nhập API và OTP lại.
3. Chọn đúng REAL/PAPER; đối chiếu tiền, danh mục và lệnh chờ với DNSE.
4. Kiểm tra giá/HEALTH/daemon, watchlist và RULE; hover `?` xem điều kiện/ví dụ.
   PREVIEW là ước tính; **CHƯA LƯU** chưa áp dụng, `—` là thiếu dữ liệu/số lượng.
   Để trống KHỐI LƯỢNG = AUTO theo P1/OVERRIDE và hạn mức; không cần tín hiệu BUY.
   Nhập khối lượng = dùng số đặt tay. TP/SL preview tiền là trước phí/thuế.
   P1 CP%/TIỀN% tính trên NAV sổ đang xem; số tiền là giới hạn phân bổ, không phải
   danh mục thực tế. VỐN AUTO là ngân sách gợi ý cho mã đang chọn, không ép lệnh MANUAL.
   Hover P1 xem số tiền; hover P2/ATR xem nguồn nến. Chưa có quyết định thì chỉ số
   vẫn có preview từ nến đã tải, không tạo lệnh. ATR chỉ dùng nến ngày đã đóng.
   XÁC NHẬN GIẢM 1/3 PHIÊN: đang kiểm tra đổi trạng thái; vẫn dùng P1 đã xác nhận,
   không phải chờ giá giảm để mua/bán. Hover dòng/ⓘ xem trạng thái và tỷ trọng trước/sau.
5. Lệnh REAL đầu tiên đối chiếu ID, khớp/còn lại, giá vốn và phí trước khi tăng quy mô.

## Bộ thử MSN / CTS / HDB / IDC

Thiết lập khi đây đúng là vốn dành cho bộ thử: watchlist/Priority gồm 4 mã;
**Nạp nhanh:** đóng app → BAT **4** → kiểm tra tài khoản trên máy đó → **y** → **3**.
VPS chọn **2 → 4 → y → 3** để cập nhật rồi nạp bộ setting này.
Preset đi cùng Git; không tự đồng bộ mọi chỉnh sửa UI trên máy khác. Giữ API/token/
Telegram, EMA/RSI, giờ mua, SL/TP/PROTECT của máy đó; bật E mặc định cho BOT và đặt **E AUTO**.
Backup setting cũ nằm cạnh `settings.json` (`settings.before-va-*.bak`), không lên Git.
RULE → P1 bật OVERRIDE, CP **100%**; tối đa mã BOT **4**.
KẾT NỐI → MÃ CK → VỐN RIÊNG ON, tổng **50 triệu**, ⚙ từng mã:

| Mã | Hạn mức | Sử dụng | Mua tối đa gồm phí | Giữ tiền |
|---|---:|---:|---:|---:|
| MSN | 15 triệu | 100% | 15 triệu | 0 |
| CTS | 15 triệu | 100% | 15 triệu | 0 |
| HDB | 15 triệu | 100% | 15 triệu | 0 |
| IDC | 5 triệu | 100% | 5 triệu | 0 |

**LƯU PRIORITY**. Tổng được mua **50 triệu**, không giữ lại 25 triệu như bản cũ.
MAX LỆNH **1** mỗi mã; cần tín hiệu BUY, không ép mua hết tiền ngay.
MSN/CTS/HDB được dành đủ 15 triệu/mã; IDC dùng **50 − 45 = 5 triệu**, không mượn
phần ba mã kia chưa dùng. Đây là hạn mức cấu hình, không tự tăng khi nạp tiền.
PAPER 100 triệu vẫn chịu cap này: MSN tối đa 15 triệu, IDC tối đa 5 triệu gồm phí.
Nếu 100 CP theo giá trần + phí vượt cap, MARKET AUTO không mua; xem hint KL để biết số tiền cần.
Có thêm 10 triệu: đổi tổng thành 60, cap **IDC** thành 15 rồi lưu; không tự mua bù.
CHIA HẠN MỨC thay các cap nháp bằng Tổng / số mã, không mua lệnh. Khối lượng thực tế
làm tròn lô/dự phòng giá trần nên tiền dùng thấp hơn trần. IDC không mượn cap mã khác.
Hạn mức này áp dụng BUY BOT; số lượng MANUAL do người đặt quyết định.
Preview hiện NAV/tiền khả dụng, tổng quỹ, đã/chưa chia và phần giữ tiền.
CÒN HẠN MỨC trừ vốn cổ đang giữ + BUY chờ, không phải tiền khả dụng.
LỆNH **1/2** = đã dùng/đang chờ 1 lượt BUY BOT trên tối đa 2; khớp từng phần tính 1, bán hết đếm lại. REAL/PAPER đếm riêng.
VỐN RIÊNG OFF dùng ngân sách P1 / số mã BOT như cũ, bỏ qua cap/% nháp.

## Khi đang chạy

- BOT OFF / đổi PAPER **không dừng quản lý vị thế REAL đã mở**. SELL đang quản lý và
  MANUAL vẫn chạy. Muốn dừng app thì đóng app.
- BOT ON cho phép BUY tự động ở sổ đang chọn khi đủ tín hiệu, vốn và điều kiện an toàn.
  OFF vẫn tính chỉ báo/lưu lịch sử; tin BUY không thực hiện chỉ gửi nếu bật TÍN HIỆU.
- SL/TP/E AUTO bán 100%; PROTECT/Dynamic bán theo % đã đặt. ALERT chỉ ghi nhận.
  E không chờ ARM. E/M mặc định chỉ áp dụng trade BOT mới; vị thế đang giữ chỉnh riêng.
  Nạp preset không gắn E vào vị thế cũ đang OFF; vị thế đã bật E dùng chính sách AUTO mới.
- CACHE vàng: chưa gửi ở app. PARTIAL: khớp một phần, phần khớp được quản lý.
  T+ tím: đã mua nhưng chưa đủ cổ được phép bán. PendingCancel: chưa hủy xong.
- BUY bỏ qua không tự mua lại khi có tiền/slot. Lịch sử tín hiệu để đối chiếu;
  mua sau là quyết định MANUAL mới. Ưu tiên thao tác qua Money Hunter.
- BLOCK phải **MỞ BLOCK** đúng mã/sổ. ↻ reset thống kê/khóa chờ, không mở BLOCK,
  không xóa vị thế. THEO NGÀY chốt đúng giờ GMT+7; CỘNG DỒN từ lần ↻ gần nhất.
- Telegram: GOM chờ gom tin BUY, không chờ đặt lệnh; GIÃN hạn chế tin mới cùng mã/loại.
  0 phút vẫn chống tin trùng. 1 TIN/VỊ THẾ = tổng kết khi BOT bán hết, không phải mỗi lần khớp.
  Mặc định loại tin ON: BUY đã xếp (gom 30 phút), BOT đã đóng, E ALERT, lịch nghỉ/chốt
  quyền, bán trên DNSE app, hệ thống. OFF: PROTECT chạm mức và TÍN HIỆU BUY chưa thực hiện.
  E AUTO không gửi tin E ALERT; khi bán hết có tin BOT đã đóng. Telegram cần bật tổng,
  token/chat đúng; nạp preset giữ lựa chọn Telegram của VPS, không reset về mặc định.

## Khi có bất thường

Giá cũ / HEALTH lỗi: kiểm tra mạng, daemon, DNSE và đồng hồ; không dùng giá cũ đặt tay.
CHỜ TIỀN TÀI KHOẢN = chưa có số dư, khác TIỀN KHẢ DỤNG = 0. Hint KL ghi tiền/cap/giá tính lô.
Lỗi làm mới REAL không làm mất số dư PAPER; sổ lỗi giữ snapshot trước, không tạo số dư 0 giả.
LỊCH CHỜ lúc mở app = đang tải; LỊCH LỖI = chưa có lịch dùng được, không gửi lệnh mới.
Token hết hạn: nhập OTP lại. UNKNOWN / RECONCILE_REQUIRED: đối chiếu lệnh DNSE trước,
không đặt lại vì nghĩ chưa gửi. Giữ runtime/logs; không reset/xóa dữ liệu để chữa lỗi.
Log: `viking_v2/runtime/accounts/<id>/logs/`. Giá màn hình không bảo đảm là giá khớp.
