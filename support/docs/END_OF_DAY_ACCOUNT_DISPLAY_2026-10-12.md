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

## Bổ sung: cỡ chữ NAV và sao chép chẩn đoán HEALTH

Ảnh người dùng cho thấy cả 2.192đ và 49.952.140đ bị co chữ. Tái hiện trên cửa sổ app thật, scale 100%: sau nhãn chờ, chữ tiền kẹt ở cỡ 14. `dashboard/panels.py` đặt nhãn tiền `sticky="w"`; hàm co font đo bề rộng của chính nhãn, rồi nhãn co theo chữ nhỏ nên không lớn trở lại. Đổi sang `sticky="ew"` để đo theo phần chiều ngang cột cấp cho nhãn; tăng tiêu đề NAV từ 11 lên 13. Startup kiểm tra chuyển từ thiếu tài khoản → 2.192đ → 49.952.140đ, chữ tiền từ cỡ 26 trở lên và vẫn nằm trong panel ở scale 100%/150%.

Log cung cấp tiếp theo xác nhận đồng bộ lại tiền/vị thế/lệnh lúc 19:28:58; chỉ có dòng daemon khởi động lúc 19:41:22, chưa có nguyên nhân đèn API đỏ hiện tại. Không dùng các dòng 400 trước khi phục hồi để khẳng định lỗi hiện tại. Hai ảnh dùng hai tài khoản khác nhau; cùng mạng không đồng nghĩa cùng request hoặc phản hồi DNSE.

HEALTH ⓘ vẫn hiện tooltip khi rê chuột, và bấm để sao chép cùng nội dung. Bổ sung lỗi REST gần nhất của UI/daemon với endpoint, mã HTTP và chi tiết đã che thông tin xác thực; lỗi ngoài nhóm snapshot cũng đọc được. Sao chép không gọi thêm API, không đổi trạng thái lỗi hoặc tự gửi Telegram. Test native mô phỏng click trên widget thật nhưng thay clipboard bằng bộ nhớ tạm, không đọc/ghi clipboard thật của người dùng.

Cache tài khoản và lỗi đọc nằm riêng trong RAM của mỗi DNSE client ở từng tiến trình; nhịp đọc thông thường là 5 giây trong phiên, 300 giây khi xác nhận đóng phiên và không có lệnh REAL chưa rõ/đang xử lý. Mở lại app tạo client/cache mới. `START_SYSTEM.bat` mục 2 gọi `support/launcher.ps1:Update-Code` để fetch upstream và cập nhật source, yêu cầu app đã dừng; mục 3 mở app. Mục cập nhật không tự nạp preset hoặc xóa lịch sử/runtime. Không chạy launcher Update/Start trên tài khoản thật trong lượt kiểm tra này.

Hồi quy riêng lượt cỡ chữ/chẩn đoán: **265 passed, 69.66 giây**, không lỗi/skip, tại `.artifacts/nav_font_health_20261012/verified.xml`. Bao gồm HEALTH/cache, cảnh báo cuối ngày, che thông tin xác thực, click sao chép native, số NAV tăng trở lại và UI/preview/refresh. Ảnh cửa sổ dùng dữ liệu giả: `.artifacts/ui-review/nav-49,952,140-1.0.png`, `nav-2,192-1.0.png` và các bản scale 1.5. Không cộng với 482 ca của lượt trước vì có ca trùng. Khi hoàn tất lượt này chưa có nguyên nhân đèn đỏ; chẩn đoán nhận tiếp theo và bản sửa được ghi bên dưới.

## Bổ sung: lệnh đã khớp bị tra cứu qua endpoint chi tiết hiện tại

Chẩn đoán người dùng sao chép: `UI · GET /accounts/*/orders/* · HTTP 400 · OrderRepository: cannot find object with id: 236032`. NAV và HDB 600 CP vẫn hiện trên UI. Đây là phản hồi API cho lần đọc chi tiết ID, không phải bằng chứng mất kết nối DNS hoặc cổ đã bị xóa.

Nguyên nhân trong `trading/execution.py:900`: `reconcile_working` tiếp tục chọn cả lệnh kết thúc nếu còn broker ID. Khi ID không có trong danh sách hiện tại, dòng 925 hỏi lại chi tiết ID. `connections/dnse/client.py:get_order_detail` trả None khi đọc lỗi; `_cached` không lưu None nên worker có thể hỏi lại ở các vòng tiếp theo. Mỗi lỗi 400 ghi vào REST health, khiến API/DNSE đỏ dù tiền/cổ đọc được. Máy/tài khoản không có record lệnh này sẽ không chạy nhánh tra cứu ID đó.

Đối chứng trước sửa tại `support/tests/test_terminal_order_history.py`: broker giả xác nhận 600 HDB đã khớp, sau đó trả danh sách hiện tại rỗng và chi tiết 236032 trả đúng 400. Kiểm thử thất bại vì vẫn gọi `/orders/236032`. Mong đợi: giữ fill đã xác nhận, đối chiếu lịch sử cho phí muộn và không tiếp tục hỏi chi tiết một lệnh đã kết thúc.

Mức độ vừa: gọi API thừa và báo lỗi gây khó theo dõi; trong đối chứng không làm mất 600 cổ. Điều kiện kích hoạt rõ: local đã kết thúc, ID mất khỏi danh sách hiện tại và API chi tiết không còn trả được. Khi đủ điều kiện này, nhánh lỗi tái hiện được; không gán tỷ lệ xảy ra cho mọi tài khoản/phiên. Việc broker ngừng trả chi tiết ID đúng vào thời điểm nào chưa được xác minh bằng API tài khoản thật.

Bản sửa ở `trading/execution.py:859` và `:920`:

- Lệnh đã kết thúc mà không còn trong danh sách hiện tại được tra cứu qua lịch sử; không gọi lại endpoint chi tiết. Nguồn lịch sử theo ngày và endpoint riêng được mô tả trong [SDK DNSE chính thức](https://github.com/dnse-tech/dnse-py/blob/main/src/dnse/resources/orders.py).
- Chia sẻ cache lịch sử cùng ngày trong service, đọc tối đa một lần mỗi 5 phút. Ngày lấy theo thời điểm handoff/claim/create ở giờ VN. ID có tiền tố ngày chỉ được chuẩn hóa trong đúng ngày truy vấn; ghép thêm mã, BUY/SELL và gói giao dịch. ID lịch sử gốc vẫn giữ trong raw audit.
- Tiếp tục dùng transaction/inbox và tiến độ tích lũy có sẵn để đối soát phí/fill. History thiếu hoặc lỗi không đảo fill, không điền phí giả, không gửi lại lệnh. Phí trả muộn hoặc xác nhận 0 được áp dụng một lần.
- UNKNOWN, SENDING, WORKING, PARTIAL, CANCEL_PENDING, REPLACE_PENDING vẫn dùng đối soát hiện tại/chi tiết; lỗi không tìm thấy ở các trạng thái này vẫn hiện là lỗi. Không đổi rule EMA/RSI hoặc điều kiện gửi lệnh.

**465 passed, 37.59 giây**, không lỗi/skip, tại `.artifacts/terminal_order_history_20261012/verified.xml`. Bao gồm 16 ca mới: ID thường/tiền tố ngày, ngày/mã/chiều/gói không khớp, phí muộn/0, lịch sử thiếu/lỗi, crash giữa lúc ghi phí và restart, hủy một phần giữ số đã khớp và không gửi phần còn lại, cùng sáu trạng thái chưa kết thúc. Hồi quy rộng có execution, broker reconciliation, live safety, review regressions, vốn/Priority, lịch sử, account health và preview. Các lượt có ca trùng nhau nên không cộng số passed. Tất cả chạy offline với broker giả và runtime tạm.

Phí thực tế và thời điểm lịch sử DNSE cung cấp đủ dữ liệu vẫn phụ thuộc broker. Nếu lịch sử không trả phí thì UI tiếp tục ghi `CHỜ DNSE`; bản sửa không biến phí ước tính thành phí thật.
