# Kiểm chứng nạp sẵn RULE — 12/10/2026

## Hành vi

- Dashboard hẹn chuẩn bị RULE sau 2 giây. Các nhóm vẫn được dựng trên luồng Tk, theo từng đợt để nhường xử lý thao tác.
- Trên Windows, popup chuẩn bị ở trạng thái trong suốt (`alpha=0`), vô hiệu hóa nhập liệu và dạng tool window để không có nút taskbar. Cách này cho Tk bố trí từng trang trước khi người dùng mở. Popup không lấy focus; click cửa sổ chính không minimize popup chưa từng mở.
- Khi mở, khôi phục viền cửa sổ, nhập liệu và độ trong suốt bình thường; hiện trang Nghiệp vụ/Phase 1. Bấm trong lúc đang chuẩn bị dùng cùng popup và không tự chuyển các trang phía sau người dùng.
- Cấu hình form thay đổi trước lần mở đầu tiên: bỏ popup đã chuẩn bị và dựng từ cấu hình hiện tại. Các thay đổi TRACE/Telegram không buộc dựng lại form; khi lưu dùng đối tượng settings hiện tại.
- Chuẩn bị không đọc khóa lỗ. Chỉ đọc nền khi popup được mở. Save bị chặn khi chưa mở hoặc chưa dựng đủ.
- X hủy callback và bỏ bản nháp; app chuẩn bị bản mới sau 2 giây. Escape/ẩn giữ bản nháp như trước. Đóng app hủy lịch chuẩn bị và đóng cả popup ẩn.
- Tab chính và các phase RULE dùng cùng cơ chế chặn callback cũ như Lịch sử: chuyển nhanh không làm callback của tab cũ ẩn trang mới.

## Phạm vi và kiểm thử

Thay đổi thuộc dashboard, popup RULE và tiện ích tab/minimize dùng chung. Không sửa planner, strategy, execution, broker hoặc ledger.

Kiểm thử chạy bằng `support/tools/run_offline.py`: runtime/tài khoản/config tạm, dotenv bị chặn, socket không được kết nối. Các probe app thật còn chặn khởi động dịch vụ và thay broker I/O bằng stub.

- `support/tests/test_rule_preload.py`: 10 ca về popup ẩn, không đọc tài khoản, không tự lưu, hủy/nạp lại, bấm sớm, đổi cấu hình, bảo toàn TRACE/Telegram và chuyển tab/phase nhanh. Có ca mở đúng lúc đang chuẩn bị trang E/M để xác nhận các bước chuẩn bị còn lại không đổi trang trước mặt người dùng.
- `support/tests/test_rule_preload_startup.py`: 6 ca trên dashboard thật được map, REAL/PAPER ở DPI 100/150/200%; kiểm tra focus, cấu hình, BOT, queue, trạng thái cửa sổ và hủy lịch khi dừng.
- 15 ca đầu đạt tại `.artifacts/rule_preload_20261012/preload.xml`; ca bổ sung và 20 ca Lịch sử đạt tại `dialogs.xml` (21 ca, 3,64 s). Tổng cộng 16 ca mới.
- Bộ hồi quy 495 ca đạt tại `.artifacts/rule_preload_20261012/regressions.xml`, gồm UI, settings, Priority, EMA trong phiên, TRACE, Lịch sử và các luồng giao dịch giả. Đây là bộ liên quan, không phải toàn bộ suite.
- Lượt hồi quy có một ca layout Lịch sử bị hộp thoại native chặn, phải được người dùng đóng. Sau đó test layout đã mock hộp thoại và ghi nhận lời gọi bất ngờ thành lỗi test; chạy lại toàn bộ 20 ca Lịch sử tự hoàn tất. Không sửa chức năng xuất Excel từ hiện tượng này.

Lệnh hồi quy:

```powershell
.\ckvnvenv\Scripts\python.exe support/tools/run_offline.py support/tests/test_rule_button_loading.py support/tests/test_rule_deferred_build.py support/tests/test_rule_popup_responsiveness.py support/tests/test_loss_lock_ui.py support/tests/test_priority_capital_and_block.py support/tests/test_v2_settings.py support/tests/test_buy_ema_cross_option.py support/tests/test_session_ema_cross.py support/tests/test_ui_refresh_performance.py support/tests/test_app_startup.py support/tests/test_ui_dpi_reconnect.py support/tests/test_dashboard_ui_regressions.py support/tests/test_signal_trace.py support/tests/test_signal_history.py support/tests/test_business_flow_safety_regressions.py support/tests/test_live_safety.py -q --disable-warnings --tb=short --junitxml=.artifacts/rule_preload_20261012/regressions.xml
```

## Đo trên host, không đại diện tốc độ VM

Mỗi ca đo từ click RULE đến khi `app.update()` xử lý xong công việc vẽ đang chờ. Đây là proxy Tk cho lần hiện cửa sổ, không đo thời điểm khung hình xuất hiện tại màn hình RDP.

| DPI | REAL | PAPER |
| --- | ---: | ---: |
| 100% | 0,381 s | 0,398 s |
| 150% | 0,364 s | 0,382 s |
| 200% | 0,387 s | 0,407 s |

Callback mở riêng mất khoảng 31–34 ms. Trong thời gian chuẩn bị, khoảng cách heartbeat lớn nhất là 0,168–0,180 s. Widget đã được bố trí trước, thay vì chỉ tạo trong cửa sổ withdrawn rồi dồn chi phí bố trí vào lần hiện đầu tiên (~1,2 s ở probe ban đầu).

Log từng ca: `.artifacts/rule_preload_20261012/app_{real,paper}_{1.0,1.5,2.0}.txt`. Cần quan sát độ mượt thực tế trên VM/RDP của người dùng sau cập nhật; không quy kết sự khác biệt phần cứng từ phép đo host.
