# Tiền và cổ bị ẩn khi DNSE ngừng API lệnh cuối ngày — 12/10/2026

## Bằng chứng và tái hiện

Log người dùng tại 19:11:22 và 19:18:57: `/accounts/*/orders` trả HTTP 400 với nội dung "Hệ thống đã chuyển trạng thái ngừng giao dịch để xử lý các công việc cuối ngày!". Trước đó BUY HDB 600 CP có log đã gửi và khớp hết.

`trading/execution.py:853` (`ExecutionService.account_snapshot`) đọc balance → positions → orders. Lỗi ở bước orders làm cả lời gọi raise; `dashboard/actions.py:_refresh_snapshots` không đưa hai phần đã đọc được lên dashboard. Khi khởi động lại, snapshot REAL ban đầu rỗng nên số tiền hiện `CHỜ TÀI KHOẢN`, bảng cổ trống. Log lỗi orders không cho biết giá trị tiền/cổ, và lỗi này không phải bằng chứng lệnh HDB bị mất.

Đối chứng offline: DNSE giả trả thành công NAV 49.951.941đ, tiền khả dụng 36.391.941đ, HDB 600 CP (bán được 0), riêng orders trả đúng 400 trên. Kiểm thử trước sửa thất bại tại giá trị số tiền: thực tế `CHỜ TÀI KHOẢN`, mong đợi `49,951,941 ₫`. Các số trong đối chứng là fixture, không phải payload thật của tài khoản người dùng.

Đây là lỗi hiển thị/đồng bộ UI, mức vừa. Điều kiện kích hoạt cụ thể: khởi động khi API orders không đọc được; hoặc bản đã chạy giữ dữ liệu cũ khi API này lỗi. Khi điều kiện này xảy ra thì lỗi tái hiện được; chưa có dữ liệu để gán tỷ lệ xảy ra cho mọi phiên giao dịch.

## Bản sửa

- Khi riêng orders lỗi, UI dùng lại balance/positions đã đọc thành công từ cache client để hiển thị tiền và cổ. Lưu riêng trong `_account_display_snapshots`; không thay `self.snapshots` dùng cho logic giao dịch bằng dữ liệu thiếu lệnh.
- Không chạy đối soát bằng danh sách lệnh thiếu, không đổi fill/ledger/queue. API orders và `ExecutionService.account_snapshot` vẫn raise lỗi cho các bên cần snapshot đầy đủ.
- Khi API phục hồi, refresh đầy đủ thay phần hiển thị riêng và xóa fault. Nếu tiền/cổ lỗi ở lần sau, giữ số hiển thị trước đó với màu cảnh báo và hint dữ liệu cũ; không điền tiền hoặc số cổ giả.
- Chỉ thông báo 400 đúng endpoint orders, đúng nội dung ngừng giao dịch cuối ngày và thị trường `CLOSED` được hiển thị màu cảnh báo `DNSE CUỐI NGÀY` / `API CUỐI NGÀY`. Lỗi trong phiên, HTTP 400 khác, lỗi tiền, lỗi endpoint khác hoặc lỗi rule vẫn báo lỗi.
- Nhãn tiền tự co font để `CHỜ TÀI KHOẢN` không bị cắt trong panel trái.

Vị trí sửa: `dashboard/actions.py:_refresh_snapshots`, `_paint_account`, `_account_money_hint`; `dashboard/account_money.py:displayed_account_snapshot`; `dashboard/tables.py:_render_tables`; `dashboard/panels.py:_refresh_api_health_panel`, `_api_health_hint`; `connections/dnse/snapshot_health.py`.

## Kiểm chứng

Tất cả kiểm thử qua `support/tools/run_offline.py`, runtime tạm, khóa mạng thật và dotenv. Broker giả chỉ nhận GET; không gửi lệnh, Telegram thật, không đọc secrets hoặc sửa runtime tài khoản thật.

Kiểm tra cold restart, hiển thị tiền/HDB, cổ chưa bán được, lỗi balance, UNKNOWN giữ nguyên, không POST, snapshot giao dịch đầy đủ vẫn lỗi, API phục hồi, ledger không mất/đếm lại fill, phân loại cảnh báo và các đối chứng lỗi khác. Native UI kiểm tra bảng cổ và nhãn tiền trên các mức scale trong startup suite. Hồi quy **482 passed, 102.43 giây**, không lỗi/skip, được ghi tại `.artifacts/end_of_day_account_20261012/verified.xml`; bao gồm execution, live safety, broker reconciliation, vốn/Priority, lịch sử, UI/startup. Không cộng với số test của các lượt trước vì có ca trùng.

Việc đọc được số tiền/cổ thật vẫn phụ thuộc hai endpoint đó trên DNSE. Bản sửa xử lý riêng lỗi orders; không làm DNSE mở lại API và không xác nhận số NAV thật từ fixture.
