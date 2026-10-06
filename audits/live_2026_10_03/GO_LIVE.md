# Bản vá giao dịch Viking V2 — 05/10/2026

## Kết quả hiện tại

Đã hoàn tất sửa các đường xử lý được xác nhận trong audit và nghiệp vụ đã
thống nhất. **495/495 test passed**, gồm 75 trường hợp trong bộ money-path mới
và 15 regression audit đã chuyển sang expectation an toàn. Biên dịch Python,
kiểm tra dependency và `git diff --check` thành công.

Kiểm chứng bổ sung launcher Windows ngày **06/10/2026**: **518/518 test passed**,
gồm 23 test menu/helper PowerShell, backup, trạng thái Git và fetch/fast-forward
với remote Git local giả (cả đường dẫn có dấu cách). Không chạy app giao dịch,
không lấy code từ remote thật hoặc cài package thật trong đợt kiểm thử launcher.

Đã gọi **7 GET DNSE production, không có mutation**: balance, positions, orders,
lịch giao dịch, gói cash, định nghĩa mã FPT và sức mua PPSE đều hợp lệ. Bốn
file tài chính legacy hiện tại qua kiểm tra cấu trúc; diễn tập migration trên
bản sao tạm của dữ liệu thực thành công, không sửa runtime thật.

Tại thời điểm kiểm tra, trading token chưa sẵn sàng: nhập OTP trong app trước
khi gửi REAL. Orders trả danh sách rỗng nên chưa xác nhận DNSE trả lại remark
trên lệnh thực; không coi remark là khóa idempotency của broker.

Source đã cập nhật trong workspace; **chưa triển khai lên VPS, chưa đặt lệnh
REAL/sandbox, chưa kiểm thử end-to-end toàn bộ GUI**. Đây là kết quả kiểm chứng
code và GET thực, không phải chứng nhận không mất tiền hoặc chiến lược có lãi.
Kết luận NO-GO trong `REPORT.md` là kết luận của source trước bản vá.

## Rà soát trước khi phát hành — 06/10/2026

- Chạy lại toàn bộ bộ kiểm thử offline: **518 passed trong 36,61 giây**.
  `pip check`, `compileall` và kiểm tra whitespace Git đều thành công.
- Khởi tạo/đóng GUI thật trong runtime tạm, bỏ credentials và chặn socket:
  thành công. Không chạy mainloop giao dịch, không khởi động daemon giao dịch
  hoặc gửi thông báo/lệnh thật trong lượt kiểm tra này.
- Quét 93 file dự kiến đưa lên Git và 219 blob trong lịch sử chưa push:
  không phát hiện giá trị key/token thật đã biết hoặc các mẫu credential phổ biến;
  không có `.env` thật hay runtime trong phần lịch sử chưa push.
- Đo RAM trên máy phát triển: GUI idle khoảng **70,2 MiB working set**;
  process chỉ import daemon khoảng **44,6 MiB working set**. Đây là phép đo
  offline ở hai process riêng, **không phải mức RAM khi quét/giao dịch**, không
  tính Windows và không chứng minh VPS RAM 1,5 GB chạy ổn định 24/7.
- Không thay đổi thêm chiến lược, ngưỡng hoặc setting trong lượt rà soát này.
  Chưa đo tải trên VPS/Tiny Windows, chưa xác minh tự khởi động sau reboot.

## Những gì được sửa, những gì giữ nguyên

- Không retry POST/PUT/DELETE. Timeout sau gửi giữ UNKNOWN, đối soát bằng ID/tag
  đã lưu; không đoán chưa gửi và không tự POST lần hai.
- Queue, fill, vị thế, phí và rule state cập nhật trong transaction SQLite.
  Crash khôi phục từ inbox; lịch sử xuất lại có chống trùng. Hai process không
  ghi đè tài chính của nhau; một account không chạy hai instance giao dịch.
- Khớp từng phần vào ngay cùng một đơn vị quản lý. Tính giá vốn từ phần khớp
  tăng thêm, bảo toàn số cổ còn lại, dòng tiền và PNL sau mua thêm/bán bớt.
  Phí/thuế broker đến muộn hoặc được điều chỉnh chỉ ghi nhận chênh lệch một lần.
- PendingCancel/Replace vẫn theo dõi fill muộn; thay lệnh theo dõi cả ID cũ/mới.
  Lệnh broker đã hủy, từ chối hoặc hết hạn không tự phục sinh.
- Không dùng giá sai mã, giá/tín hiệu quá cũ hoặc heartbeat để hợp thức hóa
  quyết định cũ. RECHECK sau chờ T+2/OTP không gửi khi thiếu quyết định mới.
- REAL/PAPER quản lý song song. OFF chỉ chặn BUY BOT mới; SELL quản lý và
  MANUAL vẫn chạy. Kiểm tra OFF lần nữa trước từng BUY trong batch.
- Kiểm tra tiền cash khả dụng, phí, PPSE, lot và giá trước gửi. MANUAL trống
  khối lượng dùng gợi ý phân bổ; nhập khối lượng riêng được ghi đè gợi ý nhưng
  vẫn phải đủ tiền/đúng quy tắc lệnh. REAL không tự dùng gói margin.
- BUY/SELL trên DNSE trong Deal cash đã chọn đồng bộ về cùng sổ quản lý.
  Không tự nhận Deal chưa chọn; không tự gán fill khi snapshot không khớp.

Giữ layout/settings, EMA/RSI/ATR, SL/PROTECT và ngưỡng chiến lược. Không thêm
setting/framework/dịch vụ. Giữ T+2 màu tím, CACHE màu vàng đã chốt; sửa nhãn
sửa/hủy đang chờ và khối lượng **còn lại** trong popup sửa lệnh broker.

## Trạng thái được giữ và được bỏ

**Giữ:** settings; cooldown với giờ hết hạn; lịch sử tín hiệu đã tiêu thụ;
vị thế/giá vốn/phí/SL/PROTECT; fills; lệnh đã gửi và UNKNOWN/cancel/replace
pending; yêu cầu MANUAL ngoài giờ; SELL chờ cổ được phép giao dịch.

**Bỏ:** BUY đã lỡ/bị chặn không trở thành hàng đợi chờ tiền/slot; BUY BOT chưa
gửi bị bỏ khi restart; candidate xác nhận BUY không sống qua restart; quyết
định cũ không được dùng để đặt lệnh. Cache nến/lịch/giá phục vụ tính toán hoặc
hiển thị vẫn tồn tại, nhưng không phải quyền mua lại tín hiệu cũ.

Khung giờ mua và xác nhận X phút theo setting vẫn hoạt động khi được quan sát
liên tục; downtime không được cộng vào thời gian xác nhận. MANUAL ngoài giờ
hết hiệu lực vào cuối phiên đủ điều kiện đầu tiên, không bỏ qua option ATO.
TTL không xóa lệnh đã gửi hoặc SELL còn chờ T+2. Cooldown không chạy lại từ
đầu sau restart; chốt ngày/reset thống kê vẫn giữ hành vi cũ.

## Cập nhật VPS

1. Đóng app và chờ daemon/I/O worker dừng. Launcher menu mới quay về menu khi
   app đóng bình thường, chỉ thử lại sau 10 giây khi app thoát lỗi. Nếu VPS
   vẫn dùng launcher cũ luôn tự restart, dừng cả launcher/supervisor đó trước.
   Không sửa dữ liệu tài chính khi bản cũ còn chạy.
2. Sao lưu toàn bộ `viking_v2/runtime` và `viking_v2/.env` tại chỗ an toàn.
   Không đưa backup chứa khóa API vào Git/chat. Giữ nguyên account đang dùng.
3. Chép source mới, gồm `trading/durable.py` và `trading/validation.py`.
   **Không ghi đè `.env`, `runtime`, settings bằng dữ liệu máy phát triển.**
   Với launcher menu mới, chọn **3** để tự backup, cập nhật Git fast-forward
   và cài/kiểm tra package. Backup ở `.artifacts/update-backups/`, không lên
   Git. Script từ chối ghi đè source chưa commit và không tự mở app sau lỗi.
4. Từ root repo, dùng Python của venv trên VPS chạy:

   ```powershell
   .\ckvnvenv\Scripts\python.exe audits\live_2026_10_03\preflight.py --state-only --migration-preview
   .\ckvnvenv\Scripts\python.exe audits\live_2026_10_03\preflight.py
   ```

   Lệnh đầu chỉ kiểm tra state và import bản sao tạm; lệnh sau chỉ GET DNSE.
   Token false nghĩa là cần OTP, không phải một lệnh đã được gửi thất bại.
5. Mở bằng `START_SYSTEM.bat`/launcher hiện có, đúng account. Import tài chính
   tự chạy lần đầu: `trading.sqlite3` trở thành nguồn chính, JSON cũ được sao
   lưu trong `migration-backup`. JSON legacy không cập nhật sau migration.
6. App khởi động với BUY BOT OFF như trước. Kiểm tra kết nối/giá mới, tiền khả
   dụng, vị thế và pending khớp DNSE; nhập OTP. OFF không dừng SELL đang quản lý
   hoặc MANUAL đã được yêu cầu, nên kiểm tra danh sách đó trước khởi động.
7. Lệnh REAL đầu tiên do operator chủ động đặt với khối lượng/vốn chấp nhận
   rủi ro. Đối chiếu ID, số khớp, số còn lại, giá vốn và phí trên DNSE trước
   bật BUY BOT. Không mặc định MARKET chắc chắn khớp hay SL chắc chắn bán được:
   còn phụ thuộc thanh khoản, giá sàn và cổ được phép giao dịch.

SQLite nằm trên ổ đĩa local VPS, không chạy trên thư mục mạng. Backup tài chính
khi app/daemon đã dừng rồi sao lưu cả thư mục account; nếu backup khi chạy,
phải dùng SQLite backup API, không copy riêng `.sqlite3` và bỏ WAL.

Không xóa lock file để vượt instance đang chạy: khóa do OS giữ, tự nhả khi
process chết; file còn trên đĩa không có nghĩa là còn khóa. Sau khi có fills
mới, **không rollback về code JSON cũ và runtime backup cũ** rồi tiếp tục giao
dịch: sẽ mất trạng thái mới. Giữ DB hiện hành, đối soát DNSE trước phục hồi.

## Xử lý UNKNOWN khi broker không trả remark

Không tự đặt lại. Lấy **ID lệnh đã xác minh trên DNSE** cùng UUID local trong
log/queue. Công cụ kiểm tra account/mã/chiều/gói/số lượng/thời điểm trước bind:

```powershell
.\ckvnvenv\Scripts\python.exe audits\live_2026_10_03\recover_order.py --intent <UUID> --broker-order <DNSE_ID> --account <ACCOUNT_ID>
```

Mặc định chỉ preview. Khi đã xác minh đúng và đóng app, chạy lại với `--apply`
để gắn ID và đối soát fill vào DB local. API broker chỉ GET, không POST lệnh
mới; OS lease ngăn apply khi app account đó còn chạy. Nếu dữ liệu không khớp,
công cụ từ chối; không đoán hoặc đánh dấu UNKNOWN là chưa gửi.

## Chạy lại bằng chứng offline

```powershell
.\ckvnvenv\Scripts\python.exe audits\live_2026_10_03\run_offline.py tests_v2 audits/live_2026_10_03/test_observed_risks.py -q --disable-warnings --tb=short
.\ckvnvenv\Scripts\python.exe -m compileall -q viking_v2 audits/live_2026_10_03 tests_v2
.\ckvnvenv\Scripts\python.exe -m pip check
```

Runner không load `.env`, bỏ DNSE/Telegram environment, chặn kết nối socket và
đặt runtime trong thư mục tạm. Không chạy pytest money-path trực tiếp ngoài
runner khi máy có credentials thật.

Nghiệp vụ REAL theo [đặc tả DNSE](https://cdn.entrade.com.vn/dnse-openapi/doc/dnse-openapi-2026-05-07.yaml)
và [quản lý theo Deal](https://hdsd.dnse.com.vn/san-pham-dich-vu/sp-giao-dich-ky-quy-theo-deal/cau-hoi-thuong-gap-faq).
Khả năng bán REAL lấy từ broker, không suy ra chỉ vì đủ hai ngày lịch; PAPER
mô phỏng T+2 lúc 13:00 theo lịch giao dịch, xem [VSDC](https://vsdc.vn/vi/ad/152750).
