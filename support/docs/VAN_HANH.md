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
- **5** mở thư mục log bằng Explorer; nhiều tài khoản thì chọn đúng ID.
  Dùng được khi app đang chạy hoặc đã dừng, không cần OTP/venv, không sửa dữ liệu.

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
  Chờ giờ mua hoặc xác nhận BUY thêm không trì hoãn SELL, ARM hay ALERT của vị thế đang giữ.
  E không chờ ARM. E/M mặc định chỉ áp dụng trade BOT mới; vị thế đang giữ chỉnh riêng.
  Nạp preset không gắn E vào vị thế cũ đang OFF; vị thế đã bật E dùng chính sách AUTO mới.
- CACHE vàng: chưa gửi ở app. PARTIAL: khớp một phần, phần khớp được quản lý.
  T+ tím: đã mua nhưng chưa đủ cổ được phép bán. PendingCancel: chưa hủy xong.
  Log Bot/Manual ghi đúng sổ, ID, KL đã khớp/còn lại; CHỜ KHỚP không phải đã mua xong.
  Chỉ ghi khi trạng thái/KL đổi, không phát lại log cũ khi restart. Vị thế ngoài app
  chưa nhận quản lý hiển thị SL OFF; PROTECT CHỜ DỮ LIỆU không phải mức lùi bằng 0.
- Bảng LỆNH ĐANG CHẠY: bấm mũi tên ở vị thế để bung/gọn các lệnh app đã liên kết
  với vị thế đó. Dòng cha là số cổ đang nắm giữ; dòng con là lượng đặt và tiến độ lệnh,
  ví dụ `Khớp 400/500 · CÒN 100 CHỜ KHỚP`. Lệnh chưa có vị thế tương ứng vẫn hiện riêng.
  `BÁN ĐƯỢC 400 CP · CHỜ VỀ 100 CP` nghĩa là đã nắm giữ 500 CP, khác với 100 chưa khớp.
  Nút ✖ trên lệnh con yêu cầu hủy phần chưa khớp; cần token REAL và xác nhận của broker.
  Làm mới bảng giữ trạng thái bung/gọn và lệnh đang chọn.
- Mặc định BUY xét EMA nhanh > EMA chậm và RSI hiện tại > RSI phiên trước theo các công tắc RULE.
  RSI hiện tại là nến 1D tạm tính từ giá mới; mốc so sánh là nến 1D phiên trước, không phải tick trước.
  Tùy chọn **PHẢI VỪA CẮT EMA** mặc định ON theo VA: lần quan sát trước nhanh ≤ chậm, lần này nhanh > chậm.
  OFF chỉ đòi EMA nhanh đang > chậm. RSI vẫn phải đạt; không đổi cách tính hai đường EMA.
  Đủ chỉ báo chưa phải đã gửi lệnh: còn giờ mua, bộ lọc/WHIPSAW/khóa lỗ, vốn/slot, BOT và token REAL.
  Bật lại BOT/hết khóa bán tay không phục hồi lệnh cũ. Khi chỉ bật yêu cầu vừa cắt,
  EMA đã nằm trên không tự sinh ENTRY mới; tắt yêu cầu này thì xét mức EMA hiện tại.
  Cập nhật giữ OFF đã lưu; nạp preset VA bật ON.
  **GIỮ LẦN CẮT TRONG PHIÊN** là lựa chọn trong RULE → Phase 2 → BUY, mặc định ON.
  Cấu hình cũ chưa có lựa chọn này nhận ON; giá trị OFF đã lưu được giữ nguyên.
  Chỉ áp dụng với REALTIME + DÙNG EMA + yêu cầu vừa cắt. ON nhận lần cắt lên còn hiệu lực
  trong ngày: cắt lúc 14:10, restart 14:20 vẫn xét BUY nếu khôi phục được dữ liệu và điều kiện hiện tại đạt.
  EMA vẫn tính trên nến ngày; giá phút đã đóng chỉ dùng dựng lại diễn biến nến ngày trong phiên.
  Cache trên đĩa lưu theo mã/ngày, dùng chung REAL/PAPER; chỉ tải phút thiếu sau restart/mất kết nối,
  không tải lại phần đã có khi tắt/bật lựa chọn, bật BUY hay mở preview. Tải lỗi thì chờ dữ liệu, không đoán BUY.
  Một lần cắt chỉ tạo một lệnh mỗi sổ; dấu đã dùng lưu cùng giao dịch queue nên restart không mua trùng.
  BUY OFF/thiếu vốn chưa tạo lệnh thì chưa dùng tín hiệu. Lệnh đã xếp nhưng bị hủy/hết hạn vẫn tính đã dùng.
  EMA xuống/bằng làm mất hiệu lực; lần cắt lên ở phút mới có ID mới. Dao động trong cùng phút không tạo nhiều lệnh.
  Hết phiên hoặc sang ngày khác không dùng lại. Khi đang chạy, app vẫn nhận cắt mới ngay theo nhịp chỉ báo đã chọn.
  RSI, giờ mua, xác nhận, WHIPSAW, khóa lỗ, vốn và slot vẫn kiểm tra. Xác nhận X phút bắt đầu khi app quan sát,
  không cộng ngược thời gian trước restart. Nhịp 1M/2M/5M vẫn cần mẫu hiện tại hoàn tất sau gián đoạn.
  P2 có vùng **CẮT EMA** bên phải: CHỜ XUỐNG / CHỜ LÊN / ĐÃ LÊN + giờ,
  hoặc **CÒN HIỆU LỰC / ĐÃ DÙNG + giờ / HẾT PHIÊN** khi dùng lựa chọn giữ cắt;
  kèm phép so sánh EMA lần quan sát trước. Số lấy từ backend; preview nến đơn lẻ không tự đoán giao cắt.
  Ngoài phiên, **CUỐI + giờ** là mẫu EMA gần nhất đã lưu trước đóng phiên của sổ đang chọn.
  Nếu chưa có mẫu trong phiên, **ĐÓNG + ngày** dùng EMA từ nến ngày đã đóng trong cache.
  Xanh `>`, đỏ `<`, vàng `=`; số xem lại không tạo tín hiệu BUY. Thiếu cả hai nguồn thì ghi chưa có mốc lưu.
  **CHƯA CÓ CỔ** ở ô E nghĩa là sổ đang chọn chưa có cổ của mã đó; REAL là cổ thật, PAPER là cổ mô phỏng.
  Số cổ của hai sổ độc lập; khối lượng ở phiếu BUY là số dự kiến mua.
  Nhịp 1M/2M/5M chờ mẫu đầu tiên hoàn tất sau khởi động hoặc gián đoạn nguồn giá.
  Không dùng số nền phiên trước hay giá chờ cũ để BUY/E; SL/TP/PROTECT vẫn xét giá mới.
  Giờ cắt EMA là mốc hoàn tất mẫu, không đổi theo mỗi lần poll. TRACE lưu riêng giá/mốc mẫu và giá tick mới.
  Khôi phục phút không tái dựng được dao động chỉ xảy ra giữa hai mẫu phút.
  Lệnh bị chặn vốn/slot và tín hiệu chờ có quy tắc riêng; xem lý do, không coi Telegram là xác nhận đã mua.
- Khóa sau lỗ có hai chế độ: **THEO GIỜ** tự hết sau số giờ đã đặt;
  **KHÓA HẲN** giữ đến khi mở tay. Ô giờ chỉ hiện ở THEO GIỜ.
  Bộ chọn REAL/PAPER và mã chỉ hiện khi có khóa hẳn để mở, không phải chế độ khóa thứ ba.
- BLOCK phải **MỞ BLOCK** đúng mã/sổ. ↻ reset thống kê/khóa chờ, không mở BLOCK,
  không xóa vị thế. THEO NGÀY chốt đúng giờ GMT+7; CỘNG DỒN từ lần ↻ gần nhất.
- Telegram: GOM chờ gom tin BUY, không chờ đặt lệnh; GIÃN hạn chế tin mới cùng mã/loại.
  Tin BUY kỹ thuật/MẤT BUY chỉ đánh dấu đã gửi sau khi thành công. Tin lỗi lưu trên đĩa,
  thử lại sau 60 giây với số liệu gốc kể cả sau restart; tắt nhóm/tổng hoặc đổi nơi nhận hủy tin chờ.
  0 phút vẫn chống tin trùng. 1 TIN/VỊ THẾ = tổng kết khi BOT bán hết, không phải mỗi lần khớp.
  Mặc định cả 9 loại tin ON: BUY đã xếp, BUY EMA/RSI, MẤT BUY, PROTECT, E ALERT, BOT đã đóng,
  lịch nghỉ/chốt quyền, SELL trên DNSE app, hệ thống. BUY đã xếp mặc định GỬI NGAY;
  có thể đổi GOM TIN (mặc định nhớ 30 phút, chỉnh 1–120). Cửa sổ gom tính từ lệnh đầu tiên;
  không có lệnh mới thì không có tin gom mới, không trì hoãn đặt lệnh.
  GỬI NGAY lỗi mạng giữ tin BUY đã xếp để thử lại sau 60 giây khi app còn chạy;
  tin thử lại vẫn riêng từng lệnh, chỉ thử lại tin thất bại. Tắt nhóm/Telegram hoặc đổi nơi gửi hủy tin chờ.
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
- Trong **TÍN HIỆU**, sự kiện và mẫu **ĐỊNH KỲ** nằm chung dưới từng mã, không có tab/view TRACE riêng.
  Sự kiện ghi khi ENTRY xuất hiện/mất, EXIT E xuất hiện hoặc xử lý/lý do đổi;
  giá thay đổi, phút xác nhận tăng hay raw signal NONE không tự sinh thêm sự kiện.
  Thiếu dữ liệu không coi là mất ENTRY. E chỉ được ghi lại sau khi điều kiện E đã mất rồi xuất hiện lại.
  Lỗi ghi CSV không đánh dấu đã ghi. App giữ các quan sát chưa ghi trong bộ nhớ, thử lại theo thứ tự,
  giữ giờ/số gốc và không dừng vòng cập nhật hay thực hiện lại lệnh. Không đóng app khi lỗi ghi chưa hết nếu cần giữ phần đang chờ.
  Mỗi dòng sự kiện mới có ID và bản ghi chờ trên đĩa trước khi thêm CSV; restart hoàn tất
  trạng thái chống lặp mà không thêm lại dòng CSV đã ghi thành công.
  Bản cũ lặp cùng trạng thái được gộp khi xem (×N cũ), giữ bản ghi đầu và nhật ký gốc,
  không xóa dữ liệu hoặc gộp các chu kỳ ENTRY mới. PROTECT không trộn vào bảng tín hiệu ENTRY/E.
- **⚙ GHI TÍN HIỆU**: một form nhỏ bật/tắt ghi định kỳ, chỉnh từ/đến giờ và nhịp phút.
  Không đổi BOT, rule giao dịch hay cấu hình Telegram. Mẫu ĐỊNH KỲ không gửi Telegram.
  Có nút **?** cho từng mục (bấm hoặc rê chuột); giờ ghi theo Việt Nam, không phải giờ được phép mua.
- Chuột phải vào dòng/mã/ngày: **Chi tiết / Sao chép / Xuất Excel phần đã chọn / Xóa khỏi lịch sử**.
  Delete cũng xóa phần đã chọn, có xác nhận. Chỉ đưa ID vào `signal_history_trash.json` của tài khoản;
  không xóa CSV, SQLite, Excel đã lưu, nhật ký lệnh hay trạng thái chống lặp.
  Chuột phải **Khôi phục các dòng đã xóa** trả lại danh sách, không gửi Telegram/tạo lệnh lần nữa.
  Lý do trong bảng được rút ngắn; số gốc và chi tiết vẫn xem được bằng chuột phải hoặc nhấp đúp dòng.
- Cột **CẮT EMA** dùng chứng cứ backend lúc ghi; bản cũ thiếu hiện **Bản cũ chưa lưu**.
  Tín hiệu chờ giờ còn hiệu lực giữ giờ cắt ban đầu, không đòi cắt lại đúng 14h.
- EMA/RSI hiện phép so sánh số gốc; bản ghi mới giữ RSI tham chiếu, phiên tham chiếu và chu kỳ chỉ báo.
  Bản cũ không lưu RSI phiên trước vẫn hiện số hiện tại, ví dụ **52.29 · Trước: chưa lưu**;
  phiên tham chiếu thiếu hiện **Bản cũ chưa lưu**. Đây là trường nhật ký chưa lưu, không phải kết luận bot thiếu dữ liệu lúc chạy.
  Bản ghi mới thiếu dữ liệu vẫn báo thiếu, không che lỗi bằng nhãn bản cũ. Không điền số tính lại vào chứng cứ cũ.
- Chỉ có **một nguồn DNSE**, không còn hai mode hay nút nạp CSV. **CHUẨN HOÁ · OFF** mặc định giữ số đã ghi.
  Bấm **ON** tự đọc cache `market_bars.json` của đúng tài khoản mà daemon đã tải từ DNSE.
  Tính lại chạy nền, không chặn Tk; không gọi thêm nguồn/API, không ghi vào nhật ký, rule hay lệnh.
  Chuẩn hoá đơn vị giá, sắp nến theo phiên Việt Nam và dùng đúng một nến 1D/phiên;
  lịch sử chỉ đến trước ngày sự kiện + **giá tick tại giờ ghi**, không dùng close cuối ngày hoặc các tick trước đó làm nến mới.
  Với nhịp phút, dùng giá đóng mẫu chỉ báo đã lưu; giữ giá tick mới riêng trong chứng cứ gốc.
  Mẫu chưa sẵn sàng giữ số nền, chưa đánh dấu ENTRY hoặc chuẩn hoá thành tín hiệu mới.
  EMA chuẩn, RSI Wilder; giữ độ chính xác giá đầu vào. Đủ lịch sử RSI trước mới tính, không đòi CSV 100 nến.
  Dùng chu kỳ đã ghi; bản cũ thiếu thì dùng 3/6/14, ghi rõ giả định trong Chi tiết và Excel.
  **Bảng luôn giữ số gốc, sự kiện, xử lý, cắt EMA và tiêu đề cột**, cả khi ON/OFF; mẫu không tự thành ENTRY/lệnh mới.
  ON mở **khung đối chiếu dưới bảng**: chọn một dòng giờ để xem **Đã ghi / Tính lại DNSE** cạnh nhau.
  Thiếu cache/mã/giá/chu kỳ hợp lệ: chỉ báo chưa tính được trong khung đối chiếu và Chi tiết; không làm trống hoặc thay số bảng.
  Nút có hint; LÀM MỚI cập nhật đối chiếu nếu cache đã đổi, OFF đóng khung đối chiếu.
  Chi tiết tách số đã ghi và tính lại, có phạm vi lịch sử và mốc cache; lịch sử có thể đã được cập nhật/điều chỉnh sau phiên.
  Đây là **tính lại trên DNSE**, không khẳng định khôi phục quyết định cũ hoặc khớp từng số TradingView;
  không tự đoán hệ số cổ tức, sửa giá hoặc nhân/chia RSI để ép khớp chart. File CSV đối chiếu cũ không bị xóa.
- XUẤT EXCEL luôn có **DNSE GỐC**; bật chuẩn hoá thêm **CHUẨN HOÁ**, không còn sheet TradingView rỗng.
  Sheet CHUẨN HOÁ đặt **EMA/RSI đã ghi** và **EMA/RSI tính lại** ở các cột riêng, không thay số gốc bằng số tính lại.
  Sự kiện/xử lý/cắt EMA có nhãn **ĐÃ GHI**; tính lại thất bại không giả là số tính thành công.
  Bao gồm các dòng trong danh sách (kể cả nhóm chưa xổ), không xuất dòng đã xóa/ẩn và không tự kéo dữ liệu mạng.
  Có mẫu định kỳ thì thêm TRACE/SETTING đầy đủ. Chờ chuẩn hoá xong trước khi xuất số tính lại.
  UI giữ 7 ngày ghi nhận gần nhất; nguồn sự kiện gần nhất tối đa 250 dòng, mẫu định kỳ được gộp thêm.
  CSV gần nhất 500 dòng, Excel sự kiện tự lưu theo tháng.

## TRACE / kiểm tra ENTRY trong phiên

- **LỊCH SỬ → TÍN HIỆU → ⚙ GHI TÍN HIỆU**: mặc định ON, 2 phút/lần, 14:00–14:30 giờ Việt Nam.
  Có thể tắt, chỉnh nhịp 1–30 phút và giờ bắt đầu/kết thúc. Độc lập nhịp EMA/RSI, không tạo lệnh hay tin Telegram.
  Đây là nơi duy nhất chỉnh lịch TRACE. Lưu RULE giữ lịch đã chỉnh tại Lịch sử,
  kể cả khi cửa sổ RULE được mở trước đó.
- App phải đang mở. Ghi tất cả mã theo dõi, kể cả không có ENTRY, giá lỗi hay daemon chưa sẵn sàng.
  Mỗi mốc chỉ ghi một lần/mã/sổ; restart không ghi trùng, không bù các mẫu quá khứ đã bỏ lỡ.
  Giờ ghi thực tế và mốc lấy mẫu được lưu riêng. Bao gồm mẫu 14:30; ngày nghỉ không ghi.
- Mẫu **ĐỊNH KỲ** nằm ngay trong cây ngày → mã → giờ cùng ENTRY/MẤT ENTRY/EXIT E.
  Chế độ REAL/PAPER ghi ở từng dòng. XUẤT EXCEL có sheet TRACE (số gốc)
  và SETTING (setting lúc ghi, không token/chat). Trace lưu SQLite riêng 30 ngày;
  chỉ dọn các mẫu TRACE cũ, không xóa tín hiệu, nhật ký lệnh hay danh mục.
- Mẫu có giá/nguồn/tuổi dữ liệu, EMA/RSI hiện tại và phiên trước, chu kỳ, ENTRY/EXIT E,
  WHIPSAW, khóa lỗ, giờ mua, vốn/slot/hạn mức, BOT/OTP và lệnh app đã ghi cùng mã/sổ/ngày.
  ENTRY là phần EMA/RSI; XỬ LÝ APP/lý do còn phản ánh giao cắt/giờ/khóa/vốn. Lệnh đã xếp không phải đã khớp.
  Giá/quyết định không hợp lệ hiện **—**, không báo ENTRY từ dữ liệu cũ. Chưa có mẫu ngày cũ thì không dựng giả.
- Phát lại chỉ báo một ngày bằng nến 1 phút DNSE:
  `python support/tools/audit_entry_day.py --symbol IDC --date 2026-10-09 --account <account> --exchange HNX --allow-api --output <report.xlsx>`.
  Tool chỉ GET OHLC; dùng nền 1D đến phiên trước + một nến ngày tạm tính theo close mỗi phút,
  không tính EMA/RSI khung 1 phút, không dùng close cuối ngày hôm đó để tính ngược.
  Close được gắn giờ cuối phút. Báo cáo đối chiếu nền cache và API hiện tại, tách đạt EMA/RSI với cần vừa cắt.
  Đây không phải nhật ký tick/vốn/slot/OTP/lệnh thật VPS; không kết luận đã gửi/khớp từ replay.

## Khi có bất thường

Nếu hiện `DECISION: LỖI · ĐANG THỬ LẠI`, app đăng ký lại vòng nhận quyết định
mỗi giây và tạm chặn BUY đến khi xử lý thành công; đối soát và exit vẫn tiếp tục.
Broker báo khớp nhưng thiếu giá: giữ UNKNOWN và hạn mức chờ đối soát, không gửi lại.
Nếu dữ liệu bản cũ đã ghi khớp nhưng notional 0, app giữ UNKNOWN và chặn BUY
với `LEGACY_FILL_ACCOUNTING_UNVERIFIED`; cần đối soát sổ vị thế, không reset runtime.
Cap danh mục của BOT xét cả fill đã xác nhận chưa có trong snapshot broker và
các BUY đang chờ; MARKET REAL tính biên vốn bằng giá trần trước khi gửi.
Sửa lệnh bị từ chối chắc chắn trả quyền sửa/hủy lệnh gốc; UNKNOWN tiếp tục chờ đối soát.
Chi tiết sửa và regression: [SAFETY_FIXES_2026-10-10.md](SAFETY_FIXES_2026-10-10.md).

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
Log: `<thư mục cài app>/viking_v2/runtime/accounts/<id>/logs/`; BAT **5** mở đúng tài khoản.
Windows ẩn đuôi `.log` có thể hiện tên `daemon`, `ui`, `daemon-process` (Text Document).

| File | Nội dung / khi cần gửi |
|---|---|
| `daemon.log` | Backend giá/chỉ báo/tài khoản; ưu tiên gửi khi lỗi DNSE hoặc bot không ra quyết định |
| `ui.log` | Giao diện, đồng bộ tài khoản và xử lý lệnh phía app; gửi thêm khi lỗi thao tác/gửi lệnh |
| `daemon-process.log` | stdout/stderr tiến trình daemon; cần khi daemon không khởi động hoặc bị dừng/crash |
| `daemon.jsonl`, `ui.jsonl` | Cùng log ở dạng JSON từng dòng để công cụ đọc; thường chỉ cần gửi bản `.log` |

File có đuôi ngày (ví dụ `.2026-10-08`) là log ngày cũ; chọn đúng ngày xảy ra lỗi.
Gửi đoạn có giờ và chi tiết ngay sau lỗi, không chỉ dòng console lặp; không gửi `.env`/API Secret/token/OTP.
Giá màn hình không bảo đảm là giá khớp.
