# QOL giao diện

## RULE: nạp sẵn popup trước lần mở đầu tiên

Ghi nhận ngày 12/10/2026. Trạng thái: đã triển khai nạp sẵn riêng popup RULE và kiểm thử offline.

Mục tiêu: bấm RULE thấy các trường đã dựng, giảm hiện tượng khung xuất hiện rồi nội dung các tab tải lần lượt.

Hướng triển khai: tận dụng luồng dựng từng phần hiện có để chuẩn bị một popup ẩn khi app nhàn; giữ popup trong RAM và mở lại khi người dùng bấm RULE. Đây là nạp sẵn UI, không phải cache shader hay cache dữ liệu giao dịch.

Phạm vi dự kiến nhỏ đến vừa, chủ yếu tại `viking_v2/rules/window.py`, `viking_v2/dashboard/actions.py` và phần quản lý vòng đời cửa sổ chính nếu cần.

Các điểm cần xử lý khi triển khai:

- Nạp sẵn theo từng đợt trên luồng Tk, giữ app phản hồi; popup ẩn không lấy focus hoặc đổi trạng thái nút RULE.
- Nếu người dùng bấm khi chưa nạp xong, dùng lại popup đó và tiếp tục luồng tải hiện có.
- Hiển thị cấu hình hiện tại khi mở, tránh lưu đè bằng dữ liệu cũ; nạp sẵn không tự lưu settings hoặc tác động BOT/queue.
- Hủy callback khi đóng app; xử lý mở lại sau khi người dùng đóng hẳn popup.
- Đo lần vẽ đầu tiên trên VM/RDP: dựng sẵn widget không bảo đảm toàn bộ chi phí vẽ khi hiện cửa sổ biến mất.

Đã thực hiện các điểm trên cho RULE; xem [kiểm chứng nạp sẵn RULE](RULE_PRELOAD_VERIFICATION_2026-10-12.md). Các popup khác giữ luồng hiện có.
