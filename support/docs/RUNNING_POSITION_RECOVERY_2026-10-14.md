Quản lý vị thế, sửa thống kê và thông báo T+2 — 14/10/2026

**Thao tác trên VM cho vị thế HDB đang giữ**

1. Dừng app và daemon, cập nhật code Git. Thực hiện việc cập nhật/quản lý này trước phiên tiếp theo.
2. Mở app, chuột phải dòng HDB → **Mở bảng** → tắt **Tự bán** → **LƯU**. Công tắc lưu theo trade ID và giữ qua restart. SL/TP/PROTECT/E tự động bị chặn; SELL tự động chưa gửi bị hủy, kể cả lệnh chờ cổ về. BUY không chịu công tắc này; lệnh bán tay vẫn dùng được. SELL broker đã nhận phải kiểm tra/hủy riêng; công tắc không hoàn tác fill.
3. Trong bảng vị thế HDB → **Đo lại MAE/MFE**. App đọc giá khớp lệnh G1, hiện giá để review, rồi mới ghi dữ liệu. Quote không hợp lệ/cũ sẽ bị từ chối. Nếu còn SELL đã gửi broker đang đối soát, chỉ giữ khóa tự bán và chưa đo lại. Bao gồm cả SELL MANUAL mà app đang theo dõi.
4. Sau khi đồng ý, bắt đầu một khoảng đo mới tại giá đã đọc; lưu extrema cũ để đối chiếu và xóa ARM/floor phát sinh từ bộ giá cũ. Số cổ, giá vốn, vốn mua gốc, fill, realized PNL và số lượt PROTECT đã khớp được giữ nguyên. **Tự bán tiếp tục tạm dừng**; operator chủ động bật lại khi muốn.

Không xóa dòng vị thế, `trading.sqlite3`, trade ledger hoặc journal. Tác vụ đo lại không giả định đã khôi phục đầy đủ giá từ lúc mua. Đỉnh 23.450 của phiên ở DNSE có thể khác đỉnh được quan sát kể từ mốc đo mới; hint ghi rõ thời điểm bắt đầu lại. Muốn khôi phục toàn bộ extrema sau BUY cần lịch sử khớp lệnh đã được kiểm chứng; API rebuild backend từ bản trước vẫn giữ nguyên.

**Hiển thị và chỉnh sửa**

- PNL gồm phần đã bán cộng phần còn giữ, chia vốn mua gốc; không chia extrema của cả chu kỳ cho vốn 400 cổ còn lại.
- MAE/MFE hiển thị **giá và phần trăm theo giá vốn**. Ví dụ dữ liệu mô phỏng: MAE 22.450 (−0,88%), MFE 23.450 (+3,53%). Chúng là excursion theo giá; không phải phần trăm của tiền lãi toàn chu kỳ sau các partial SELL.
- Lịch sử chưa xác minh hiển thị `MAE — / MFE — / CHỜ XÁC MINH`; preview không coi đỉnh đó là ARM đã xác nhận. PROTECT vẫn bị chặn ở backend.
- Dòng trạng thái ngắn; nội dung đầy đủ nằm trong hint từng ô/cột. Chuột phải chỉ có **Mở bảng / Đóng**. Bảng vị thế chứa TP/SL theo giá hoặc %, ARM/DYNAMIC/E theo **Theo RULE / OFF / ALERT / AUTO**, công tắc Tự bán và nút đo lại MAE/MFE. Ghi đè thuộc riêng trade; chọn Theo RULE bỏ ghi đè và lấy cấu hình chính.
- Ctrl + click hoặc Shift + click chọn nhiều mã, Ctrl + A chọn mọi vị thế. **BÁN ĐÃ CHỌN** mở bảng kiểm tra chung trước khi enqueue SELL MANUAL. Chỉ bán cổ đã về theo lô 100; mã có SELL đang chờ được bỏ qua. Kiểm tra lại toàn bộ các mã trước khi ghi lệnh để ngăn snapshot cũ hoặc click trùng tạo bán vượt số cổ.

**Thông báo trước khi cổ bán được**

ARM vẫn có snapshot riêng. PROTECT HIT (SAU ARM/DYNAMIC), E và SL/TP được quan sát khi đang T+2 sẽ có tin ghi thời điểm, giá, chế độ và số cổ bán được. Tin ghi **chưa gửi SELL**, hướng dẫn chuột phải để tạm dừng tự bán. ARM là bật theo dõi; PROTECT HIT là chạm mốc bán — hai sự kiện được ghi riêng.

Snapshot được lưu qua những poll GUI bỏ lỡ. Telegram đi qua outbox, retry khi lỗi và dedup sau restart; E ALERT đang T+2 không gửi thêm một tin E ALERT thường trùng nội dung. Khi một điều kiện mất rồi xuất hiện lại, có episode mới để được báo tiếp. Tạm dừng tự bán cũng chặn decision/intent AUTO cũ và được kiểm tra lại sau khi đọc tài khoản, ngay trước hand-off. Broker orders/fills đã có vẫn được đối soát.

**Kiểm tra**

Các case offline mới nằm ở `support/tests/test_position_repair_and_tplus_alerts.py`. Có đối chứng MANUAL vẫn được gửi, mọi nhánh AUTO bị chặn ở REAL/PAPER, race sau snapshot, T+2 notice bỏ lỡ/retry/restart, quote sai board/indicative/stale không sửa dữ liệu, partial SELL không giảm mẫu số, broker SELL làm hoãn đo lại và UI lưu đúng công tắc.

88 case mới đạt. Suite đầy đủ và các lần kiểm tra lại phần vừa chỉnh cuối đối chiếu 2.678 case hiện tại: 2.664 đạt, 14 lỗi UI/launcher còn lại đúng cùng các lỗi đã tái hiện trên baseline trước. Kiểm tra trực tiếp popup/hint bằng cửa sổ Tk mô phỏng cũng đạt; không chạy app giao dịch thật để test.

Đợt UI tiếp theo: 34 case mới cho ghi đè ARM/DYNAMIC/E, bán nhiều mã, xác minh gói/book, chống lệnh trùng và sửa khi đang hand-off; các bộ liên quan cùng lần chạy lại cuối có 813 case đạt. Popup vị thế kiểm tra ở DPI 1/1,25/1,5, nhóm EMA ở 1/1,5/2. VNINDEX & phân bổ gộp cùng trang BUY, MA/Pivot/Volume thu trong nút Tham số; bốn panel BUY xếp theo hai cột và các giải thích dài nằm trong hint. Dòng chọn nền tím, chữ trắng, tách khỏi các màu trạng thái. Các kiểm thử chỉ dùng dữ liệu giả, không gửi broker thật.

Trên bản sao dump HDB: mua 600, còn 400, đã bán 200, realized PNL 100.000 và hai lượt PROTECT giữ nguyên; restart mô phỏng ở giá G1 23.100 cho PNL 280.000 / 13.590.000 = +2,0603%, MFE từ mốc mới +1,9868%, MAE 0%. ARM/floor cũ được bỏ, tự bán tạm dừng. Đây là replay, không phải cập nhật VM.

Thư mục Downloads được người dùng đưa lại trong phiên; đối chiếu sau khi khôi phục: cả 61 file SHA-256 khớp bản điều tra ban đầu. Chỉ bản sao trong `.artifacts/hdb-followup-validation` được sửa để kiểm thử. Dữ liệu tài khoản và ảnh kiểm tra không đưa vào Git.
