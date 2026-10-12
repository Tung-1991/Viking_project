# RULE: giảm thời gian chặn UI khi vẽ lần đầu — 12/10/2026

## Triệu chứng và bằng chứng

Ảnh từ VM cho thấy cửa sổ RULE có tab/footer/nút Lưu nhưng nội dung còn vẽ dở,
Windows gắn Not Responding, rồi cửa sổ hoàn tất và dùng được. Người dùng xác nhận
Windows Scale 100%, host mượt và lần mở tiếp theo trên VM nhanh hơn. Chưa thu được
stack của chính lần treo trên VM, nên không quy kết driver, 3D hay RDP.

Đọc code và đo bằng tiến trình Tk thực, main window đã hiện. Đối chứng trên cùng
host, tắt dịch vụ/broker I/O và dùng cache/runtime tạm; không lấy báo cáo cũ làm
bằng chứng. cProfile cho thấy phần layout đầu tạo nhiều Configure/draw sau khi
constructor xong. Với cách dựng đồng bộ, callback 50 ms bị trì hoãn đáng kể.

Ở Scale 100%, đối chứng cùng probe/cProfile:

| Mốc | Dựng đồng bộ | Dựng từng nhóm |
| --- | ---: | ---: |
| Callback mở/tạo khung | 0,562 giây | 0,029 giây |
| Lượt input đầu trong probe | 2,257 giây | 0,197 giây |
| Khoảng cách lớn nhất giữa các pulse UI | 1,694 giây | 0,231 giây |
| Đủ trường cấu hình | 2,257 giây | 0,971 giây |

Đây là số đo trên host với instrumentation. Trong bài app thật không cProfile,
REAL/PAPER ở 100/150/200% hoàn tất khoảng 0,79–0,83 giây, input gap lớn nhất
khoảng 0,19–0,20 giây. Chưa đo lại trên VM để khẳng định cùng tốc độ hoặc xác nhận
triệu chứng Not Responding đã hết.

Đối chứng riêng bỏ guard draw về cách flush native: host không nhanh hơn.
Không rollback bản sửa DPI chỉ dựa vào hiện tượng VM; cũng không đổi 3D/driver.

## Phạm vi thay đổi

- `viking_v2/dashboard/actions.py:1138`: đường mở RULE của dashboard dùng
  `defer_build=True`, chặn mở lồng nhau trong lúc constructor đang chạy.
- `viking_v2/rules/window.py:122`: dựng P1/P2/P3/E-M/thực thi và nối preview qua
  các callback Tk riêng; tạo widget vẫn ở luồng Tk. Không dùng thread dựng UI.
- Khung/status hiện trước. Tab và Lưu tạm vô hiệu hóa để tránh sửa E/M trước khi
  chụp baseline preview hoặc lưu thiếu trường chưa được tạo.
- `save()` cũng từ chối lời gọi trực tiếp trong lúc tải, không chỉ dựa vào trạng
  thái nút. Đủ trường thì bật lại tab và Lưu; cách đọc/lưu rule giữ nguyên.
- Đóng giữa các nhóm hủy callback còn chờ. Ẩn vẫn tải tiếp và mở lại cùng popup.
- Lỗi dựng nhóm giữ Lưu vô hiệu hóa, hiện lỗi tải và hướng dẫn đóng/mở lại.
- Constructor đồng bộ vẫn hỗ trợ caller trực tiếp. Dashboard thực dùng luồng
  từng nhóm và được kiểm thử riêng bằng app đã hiện cửa sổ.

Không thay đổi thuật toán mua/bán, ledger, broker requests hoặc cấu hình VM.
Mức độ: cải thiện phản hồi UI có bằng chứng; chưa đủ dữ liệu để quy toàn bộ
triệu chứng VM cho riêng một đoạn code.

## Kiểm chứng

`support/tests/test_rule_deferred_build.py`: không lưu khi chưa đủ trường, lưu
đầy đủ như constructor đồng bộ dù chưa thăm E/M/thực thi, RSI shared/preview,
đóng/ẩn giữa chừng và lỗi dựng nhóm.

`support/tests/test_rule_button_loading.py`: nút RULE trên app thật đã hiện,
REAL/PAPER, Scale 100/150/200%, callback UI được xử lý lúc tải, bấm thêm không
tạo popup thứ hai, mở lại cùng popup, không đổi setting/BOT hoặc tạo lệnh.

Chạy bằng `support/tools/run_offline.py`; socket bị chặn, dotenv vô hiệu hóa,
runtime/account/cache tạm, không đọc secrets hoặc gọi broker/Telegram thật.

495 kiểm thử liên quan đạt, không lỗi/bỏ qua, gồm các ca mới, settings, DPI,
loss-lock, session-cross, TRACE/lịch sử và luồng lệnh broker giả.

Artifact: `.artifacts/rule_first_paint_20261012/sync.xml`, `staged.xml`,
`current_sync_dpi_1.0.txt`, `current_dpi_1.0.txt`, `app_*.txt`, `verified.xml`.
