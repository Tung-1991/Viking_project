# Viking — đọc nhanh để vận hành

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
5. Lệnh REAL đầu tiên đối chiếu ID, khớp/còn lại, giá vốn và phí trước khi tăng quy mô.

## Bộ thử MSN / CTS / HDB / IDC

Thiết lập khi đây đúng là vốn dành cho bộ thử: watchlist/Priority gồm 4 mã;
RULE → P1 bật OVERRIDE, CP **100%**; tối đa mã BOT **4**.
KẾT NỐI → MÃ CK → VỐN RIÊNG ON, tổng **50 triệu**, ⚙ từng mã:

| Mã | Hạn mức | Sử dụng | Mua tối đa gồm phí | Giữ tiền |
|---|---:|---:|---:|---:|
| MSN | 15 triệu | 50% | 7,5 triệu | 7,5 triệu |
| CTS | 15 triệu | 50% | 7,5 triệu | 7,5 triệu |
| HDB | 5 triệu | 50% | 2,5 triệu | 2,5 triệu |
| IDC | 15 triệu | 50% | 7,5 triệu | 7,5 triệu |

**LƯU PRIORITY**. P1 100% không ép mua hết tiền; % từng mã vẫn giữ tiền riêng.
Có thêm 10 triệu: đổi tổng thành 60, cap HDB thành 15 rồi lưu; không tự mua bù.
CHIA HẠN MỨC thay các cap nháp bằng Tổng / số mã, không mua lệnh. Khối lượng thực tế
làm tròn lô/dự phòng giá trần nên tiền dùng thấp hơn trần. IDC không mượn cap mã khác.
Hạn mức này áp dụng BUY BOT; số lượng MANUAL do người đặt quyết định.
Preview hiện NAV/tiền khả dụng, tổng quỹ, đã/chưa chia và phần giữ tiền.
CÒN HẠN MỨC trừ vốn cổ đang giữ + BUY chờ, không phải tiền khả dụng.
VỐN RIÊNG OFF dùng ngân sách P1 / số mã BOT như cũ, bỏ qua cap/% nháp.

## Khi đang chạy

- BOT OFF / đổi PAPER **không dừng quản lý vị thế REAL đã mở**. SELL đang quản lý và
  MANUAL vẫn chạy. Muốn dừng app thì đóng app.
- SL/TP/E AUTO bán 100%; PROTECT/Dynamic bán theo % đã đặt. ALERT chỉ ghi nhận.
  E không chờ ARM. E/M mặc định chỉ áp dụng trade BOT mới; vị thế đang giữ chỉnh riêng.
- CACHE vàng: chưa gửi ở app. PARTIAL: khớp một phần, phần khớp được quản lý.
  T+ tím: đã mua nhưng chưa đủ cổ được phép bán. PendingCancel: chưa hủy xong.
- BUY bỏ qua không tự mua lại khi có tiền/slot. Lịch sử tín hiệu để đối chiếu;
  mua sau là quyết định MANUAL mới. Ưu tiên thao tác qua Viking.
- BLOCK phải **MỞ BLOCK** đúng mã/sổ. ↻ reset thống kê/khóa chờ, không mở BLOCK,
  không xóa vị thế. THEO NGÀY chốt đúng giờ GMT+7; CỘNG DỒN từ lần ↻ gần nhất.

## Khi có bất thường

Giá cũ / HEALTH lỗi: kiểm tra mạng, daemon, DNSE và đồng hồ; không dùng giá cũ đặt tay.
Token hết hạn: nhập OTP lại. UNKNOWN / RECONCILE_REQUIRED: đối chiếu lệnh DNSE trước,
không đặt lại vì nghĩ chưa gửi. Giữ runtime/logs; không reset/xóa dữ liệu để chữa lỗi.
Log: `viking_v2/runtime/accounts/<id>/logs/`. Giá màn hình không bảo đảm là giá khớp.
