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
Telegram tổng/token/chat, EMA/RSI, giờ mua, SL/TP/PROTECT của máy đó; bật E mặc định cho BOT và đặt **E AUTO**.
Preset bật cả 9 loại tin Telegram, BUY gửi ngay, BUY kỹ thuật/MẤT BUY giãn 60 phút, hệ thống 30 phút.
Không tự bật công tắc Telegram tổng nếu máy đang OFF; các khoảng giãn khác giữ lựa chọn tại máy.
Backup setting cũ nằm cạnh `settings.json` (`settings.before-va-*.bak`), không lên Git.
RULE → P1 bật OVERRIDE, CP **100%**; tối đa mã BOT **4**.
KẾT NỐI → MÃ CK → VỐN RIÊNG ON, tổng **50 triệu**, ⚙ từng mã:

| Mã | Hạn mức | Sử dụng | Mua tối đa gồm phí | Giữ tiền |
|---|---:|---:|---:|---:|
| MSN | 16 triệu | 100% | 16 triệu | 0 |
| CTS | 12 triệu | 100% | 12 triệu | 0 |
| HDB | 15 triệu | 100% | 15 triệu | 0 |
| IDC | 7 triệu | 100% | 7 triệu | 0 |

**LƯU PRIORITY**. Tổng được mua **50 triệu**, không giữ lại 25 triệu như bản cũ.
MAX LỆNH **1** mỗi mã; cần tín hiệu BUY, không ép mua hết tiền ngay.
MSN/CTS/HDB được dành tổng 43 triệu; IDC dùng **50 − 16 − 12 − 15 = 7 triệu**, không mượn
phần ba mã kia chưa dùng. Đây là hạn mức cấu hình, không tự tăng khi nạp tiền.
PAPER 100 triệu vẫn chịu cap này: MSN tối đa 16 triệu, IDC tối đa 7 triệu gồm phí.
Nếu 100 CP theo giá trần + phí vượt cap, MARKET AUTO không mua; xem hint KL để biết số tiền cần.
Có thêm 10 triệu: đổi tổng thành 60, cap **IDC** thành 17 rồi lưu; không tự mua bù.
CHIA HẠN MỨC thay các cap nháp bằng Tổng / số mã, không mua lệnh. Khối lượng thực tế
làm tròn lô/dự phòng giá trần nên tiền dùng thấp hơn trần. IDC không mượn cap mã khác.
MANUAL có thể nhập số lượng tay nhưng vẫn bị chặn nếu vượt vốn được dùng sau khi giữ Priority/phần để dành.
Không tự co/chia lại hạn mức để cho một lệnh MANUAL vượt qua; MARKET dự trù giá trần + phí, LO theo giá nhập + phí.
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
  Log Bot/Manual ghi đúng sổ, ID, KL đã khớp/còn lại; CHỜ KHỚP không phải đã mua xong.
  Chỉ ghi khi trạng thái/KL đổi, không phát lại log cũ khi restart. Vị thế ngoài app
  chưa nhận quản lý hiển thị SL OFF; PROTECT CHỜ DỮ LIỆU không phải mức lùi bằng 0.
- Mặc định BUY xét EMA nhanh > EMA chậm và RSI hiện tại > RSI phiên trước theo các công tắc RULE.
  RSI hiện tại là nến 1D tạm tính từ giá mới; mốc so sánh là nến 1D phiên trước, không phải tick trước.
  Tùy chọn **PHẢI VỪA CẮT EMA** mặc định OFF; ON đòi lần quan sát trước nhanh ≤ chậm, lần này nhanh > chậm.
  Đủ chỉ báo chưa phải đã gửi lệnh: còn giờ mua, bộ lọc/WHIPSAW/khóa lỗ, vốn/slot, BOT và token REAL.
  Bật lại BOT sau OFF hoặc hết khóa bán tay sẽ đánh giá điều kiện hiện tại ở chế độ mặc định, không phục hồi lệnh cũ.
  Lệnh bị chặn vốn/slot và tín hiệu chờ có quy tắc riêng; xem lý do, không coi Telegram là xác nhận đã mua.
- BLOCK phải **MỞ BLOCK** đúng mã/sổ. ↻ reset thống kê/khóa chờ, không mở BLOCK,
  không xóa vị thế. THEO NGÀY chốt đúng giờ GMT+7; CỘNG DỒN từ lần ↻ gần nhất.
- Telegram: GOM chờ gom tin BUY, không chờ đặt lệnh; GIÃN hạn chế tin mới cùng mã/loại.
  0 phút vẫn chống tin trùng. 1 TIN/VỊ THẾ = tổng kết khi BOT bán hết, không phải mỗi lần khớp.
  Mặc định cả 9 loại tin ON: BUY đã xếp, BUY EMA/RSI, MẤT BUY, PROTECT, E ALERT, BOT đã đóng,
  lịch nghỉ/chốt quyền, SELL trên DNSE app, hệ thống. BUY đã xếp mặc định GỬI NGAY;
  có thể đổi GOM TIN (mặc định nhớ 30 phút, chỉnh 1–120). Cửa sổ gom tính từ lệnh đầu tiên;
  không có lệnh mới thì không có tin gom mới, không trì hoãn đặt lệnh.
  Giãn mặc định: BUY EMA/RSI 60 phút, MẤT BUY 60, E ALERT 30, hệ thống 30, lịch 1440;
  PROTECT/SELL ngoài app 0 phút nhưng vẫn chống trùng sự kiện. CLOSED 1 tin/vị thế đã bán hết.
  BUY EMA/RSI phải đạt các chỉ báo đang bật; thông báo không bỏ qua RSI. Nó độc lập với giờ/khóa giao dịch.
  MẤT BUY chỉ gửi khi đã báo BUY, điều kiện mất và chưa xếp lệnh; thiếu dữ liệu không coi là mất BUY.
  E AUTO không gửi tin E ALERT; khi bán hết có tin BOT đã đóng. Telegram cần bật tổng, token/chat đúng.
  Cập nhật Git giữ các lựa chọn OFF đã lưu; nạp lại preset VA là thao tác chủ động bật cả 9 loại tin.

## Lịch sử tín hiệu / đối chiếu

- TÍN HIỆU xổ **ngày → mã → giờ ghi nhận**; giữ sổ REAL/PAPER, ưu tiên và slot lúc ghi.
  ENTRY, MẤT ENTRY, EXIT · E là sự kiện chỉ báo; ĐÃ XẾP chỉ là tạo yêu cầu, không phải khớp.
  Kết quả gửi/khớp/hủy xem tab lịch sử giao dịch. Không gộp các giờ khác nhau chỉ vì giá giống nhau.
- EMA/RSI hiện phép so sánh số gốc; bản ghi mới giữ RSI tham chiếu, phiên tham chiếu và chu kỳ chỉ báo.
  Bản cũ thiếu mốc RSI hiện **—**, không lấy RSI cuối ngày hôm nay điền ngược.
- Mặc định **DNSE** giữ nguyên số bot đã ghi. **TRADINGVIEW** tính lại để đối chiếu, không đổi bot/lệnh.
  NẠP CSV 1D xuất từ TradingView, đơn vị VND, ít nhất 100 nến (nên toàn bộ lịch sử); chọn đúng mã.
  Giữ số lẻ của CSV, dùng lịch sử đến phiên trước + giá intraday đã ghi, không dùng close cuối ngày để tính ngược.
  Chỉ đối chiếu phiên cuối của CSV vì chưa có bộ hệ số điều chỉnh theo ngày; phiên khác báo thiếu chuẩn giá.
  Không khôi phục TradingView bằng cách nhân/chia RSI hoặc tự đoán hệ số từ dữ liệu DNSE đã làm tròn.
- XUẤT EXCEL gồm DNSE gốc và TradingView đối chiếu của các dòng đang hiển thị; không tự kéo dữ liệu mạng.
  UI giữ 7 ngày ghi nhận gần nhất, tối đa 250 dòng; CSV gần nhất 500 dòng, Excel tự lưu theo tháng.
  TRACE định kỳ 2 phút từ 14h–14h30 vẫn là phương án chưa triển khai, không nhầm với lịch sử sự kiện hiện có.

## Khi có bất thường

Giá cũ / HEALTH lỗi: kiểm tra mạng, daemon, DNSE và đồng hồ; không dùng giá cũ đặt tay.
CHỜ TIỀN TÀI KHOẢN = chưa có số dư, khác TIỀN KHẢ DỤNG = 0. Hint KL ghi tiền/cap/giá tính lô.
Lỗi làm mới REAL không làm mất số dư PAPER; sổ lỗi giữ snapshot trước, không tạo số dư 0 giả.
Ngoài phiên, không có lệnh REAL chưa rõ kết quả: đồng bộ tài khoản mỗi 5 phút.
Còn lệnh chờ/khớp một phần/UNKNOWN, hoặc trong phiên: giữ nhịp 5 giây;
kiểm tra trước gửi lệnh luôn đọc mới, không dùng snapshot cũ để vượt qua lỗi.
Lỗi ngoài phiên ghi log ngay; kéo dài 10 phút mới gửi cảnh báo Telegram theo GIÃN HỆ THỐNG.
Hint HEALTH/API hiện bước/HTTP/nguyên nhân, lần đồng bộ tốt gần nhất và nhịp thử lại.
Log ghi khi lỗi đổi hoặc phục hồi, không ghi lại cùng lỗi ở mỗi vòng kiểm tra.
LỊCH CHỜ lúc mở app = đang tải; LỊCH LỖI = chưa có lịch dùng được, không gửi lệnh mới.
Token hết hạn: nhập OTP lại. UNKNOWN / RECONCILE_REQUIRED: đối chiếu lệnh DNSE trước,
không đặt lại vì nghĩ chưa gửi. Giữ runtime/logs; không reset/xóa dữ liệu để chữa lỗi.
Log: `viking_v2/runtime/accounts/<id>/logs/`. Giá màn hình không bảo đảm là giá khớp.
