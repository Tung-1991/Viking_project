# Money Hunter — preview START, menu BAT và nguồn cập nhật

## Preview

Giữ ô PROTECT hiện có, ba dòng:

- Header: PROTECT ON/OFF và ARM AUTO/ALERT.
- Mốc ARM hoặc MỐC/BÁN thực tế đang theo dõi.
- DYN AUTO/ALERT · START theo giá · CHƯA TỚI / ĐÃ BẬT / SAU ARM.

Ô hẹp rút `ĐÃ BẬT` thành `ĐẠT`, hint giữ trạng thái đầy đủ. Không tăng số dòng,
chiều cao panel hoặc thêm dropdown. HEALTH giữ ở cuối.

Đang giữ cổ: START dùng giá vốn vị thế của đúng sổ REAL/PAPER, không dùng giá vào lệnh nháp.
Chưa mua hoặc PROTECT của vị thế OFF: ghi PREVIEW. Thiếu ATR/giá vốn/đỉnh thì ghi chờ dữ liệu.
START OFF nghĩa là sau lần có lãi đầu tiên. Khi qua ARM dùng cơ chế sau ARM.
Đỉnh đã đạt START nhưng cổ chưa bán được vẫn có thể hiện ĐÃ BẬT; dòng mốc/reason tiếp tục
ghi CHỜ CỔ VỀ và CHƯA TẠO SELL. Preview không tiêu thụ lượt, không gửi lệnh hoặc Telegram.

Panel cấu hình DYNAMIC: AUTO/ALERT cùng dòng tiêu đề; GỐC/CÒN LẠI và LẶP cùng dòng.
Giảm khoảng cách giữa nhóm ATR/GIỮ LÃI, giữ nguyên các thiết lập và hành vi bán.
Kiểm tra layout cùng tỷ lệ chữ/cửa sổ 100/150/200%: chiều cao nhóm giảm khoảng 32%,
không chồng nút và không đổi kích thước khi chọn AUTO/ALERT.

P2 phân biệt bộ đếm với lần cắt lên đang nhớ: `1 CẮT - 0P` là số lần đổi phía hôm nay.
Dưới bộ đếm chỉ còn một dòng: có giao cắt hiện `LÊN/XUỐNG dd/mm HH:MM`;
VA chưa có cắt hiện `THEO DÕI dd/mm HH:MM`, X chưa có cắt hiện `QUÉT 09–13/10`.
Chờ lên/xuống, đã dùng, hết hạn và ngày bắt đầu khi đã có cắt nằm trong nút ⓘ.
Một trạng thái CẮT ở đầu; không có dòng trạng thái chờ riêng hoặc dấu `—` bên dưới.
VA không quét trước lúc thêm mã vẫn giữ lần LIVE đã ghi nhận tới khi dùng.
Tái hiện 13/10 lên → 14/10 xuống: dòng cuối hiện XUỐNG 14/10, bộ nhớ BUY vẫn giữ
mốc lên 13/10; EMA hiện tại dưới EMA chậm thì vẫn CHƯA ĐẠT.

Góc xem ⇄ 3P nay tải nền ba phiên giao dịch gần nhất trước hôm nay. Ví dụ 14/10: 09, 12, 13/10.
REALTIME tái dựng EMA 1D theo giá đóng từng phút, CLOSED dùng nến ngày đã đóng. Nếu mã mới
được thêm hôm nay, quét thêm đoạn trước mẫu đầu; phần LIVE sau mẫu đầu vẫn đếm riêng.
Dữ liệu giá được cache riêng, không sửa settings, rule_state, lượt đã dùng, queue hoặc Telegram.
Tải/lỗi API hiện `—`, có trạng thái và retry; không coi thiếu dữ liệu là 0 lần.
Worker giá riêng không giữ hàng đợi tiền/vị thế/lệnh. ⇄ vẫn chỉ đổi góc xem; RULE chọn mode giao dịch.

X lấy từ setting: đổi thành 7 thì góc xem tải 7 phiên trước và hiện `7P`.
Bộ đếm cộng cả lên/xuống: `1 CẮT` có thể chỉ là một lần xuống, không phải đủ điều kiện BUY.
P2 đưa trạng thái CẮT sang cụm phải, hint chỉ mở trên hai nút ⓘ, không trên các dòng chỉ số.
Dòng cuối giữ ngày giờ cả cắt xuống; không lặp `CHƯA CÓ CẮT LÊN` dưới trạng thái CHƯA ĐẠT.
Hint X phiên ghi hạn dùng, tính cả phiên cắt. Ngày 14/10 quét 3 phiên trước là 09, 12, 13/10;
lần cắt lên 09/10 dùng được trong 09, 12, 13 và hết hạn lúc 14:45 ngày 13/10 (HOSE).
Tính lại từ cache giá 09–13/10: HDB 3 lên/3 xuống, đều ngày 09/10, lần lên cuối 09:31;
MSN 0 lên/1 xuống, mốc xuống 13/10 10:43. Đọc cache trong thư mục tạm, không sửa dữ liệu tài khoản.

## BAT mới

1. Mở Money Hunter.
2. Cập nhật.
3. Nạp preset VA.
4. Cài đặt/sửa môi trường: kiểm tra hoặc cài phần thiếu.
5. Chẩn đoán: kiểm tra môi trường, mở log, kiểm tra dữ liệu local chỉ đọc.
0. Thoát.

Chẩn đoán dữ liệu local gọi preflight với `--state-only`, không gọi broker.
Preset vẫn yêu cầu app dừng và xác nhận đúng tài khoản, có backup.

## Repo

Ngài chọn tiếp tục Git cũ trong lượt này. Repo mới `Money_hunter` private sẽ tách khi Ngài chỉ định URL.
Bản tách khi thực hiện sẽ chỉ lấy snapshot source hiện tại, không đưa `.git`/lịch sử Viking,
`.env`, runtime, token, database, venv, log hoặc artifact sang repo. Giữ package `viking_v2`
để tương thích dữ liệu và entry point; tên ứng dụng là Money Hunter.

`support/repository.json` giữ URL nguồn cập nhật. Hiện vẫn là Viking_project theo yêu cầu của Ngài.
ZIP chưa có URL dừng rõ ràng; launcher không đoán repo Viking. Origin khác nguồn đã cấu hình
dừng trước fetch/reset, không tự thay remote. Private repo cần Git trên máy có quyền truy cập.

Khi tách sau này, source sẽ bắt đầu lịch sử riêng. Repo Viking và dữ liệu tài khoản trên máy giữ nguyên.
Khi chuyển VPS sang repo mới: dừng app, backup `.env` và runtime, cài source Money Hunter riêng rồi chuyển dữ liệu
của đúng tài khoản bằng thao tác local; không nạp lại tài khoản trống khi đang còn vị thế.
Không copy venv sang máy khác; dùng BAT 4 để tạo môi trường tại máy đích.

## Kiểm tra 14/10

- Launcher: 79 ca đạt, gồm cập nhật clone/ZIP với Git local và giữ dữ liệu máy.
- Preview/UI/khởi động/PROTECT/repo: 141 ca đạt, dùng runtime tạm và broker giả.
- START/cache/UI refresh: 24 ca đạt trong lượt riêng, có ca trùng nhóm trên.
- `compileall` và kiểm tra diff đạt. Không chạy giao dịch REAL hoặc nạp preset vào tài khoản.
- Sau khi thu gọn DYNAMIC: 129 ca thiết lập/lưu và 139 ca UI/PROTECT đạt ở hai lượt riêng.
- Sau khi làm rõ mốc VA: 87 ca UI/bộ nhớ/replay đạt; bộ đếm qua ngày giữ riêng với bằng chứng BUY.
- Tải lịch sử cho góc xem: 107 ca quét/UI/bộ nhớ đạt ở lượt cuối; 87 ca I/O/NAV/khởi động và
  170 ca UI/replay đạt ở các lượt trước (có ca trùng). Kiểm tra giá chỉ dùng broker giả, không chạy API tài khoản thật.
- Chuyển trạng thái CẮT sang phải và hint trên icon: 142 ca UI/EMA, 11 ca khởi động/refresh,
  3 ca vệ sinh repo đạt; có ca tải đủ 7 phiên, chỉ cắt xuống, không cho BUY.
- Mốc giao cắt gần nhất và hạn 3P: 236 ca UI/bộ nhớ/lịch sử, 3 ca vệ sinh repo đạt.
  Bộ nhớ BUY đã dùng vẫn giữ nguyên; kiểm tra giá HDB/MSN bằng cache, không gọi API tài khoản.
- Gộp dòng P2: 166 ca UI/EMA/tín hiệu, 11 ca khởi động/refresh và 3 ca vệ sinh repo đạt.
  Đã kiểm tra trường hợp X quét đủ nhưng 0 lần cắt và bố cục cố định ở 100/150/200%.
