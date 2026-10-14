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
