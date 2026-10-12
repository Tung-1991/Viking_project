# QOL giao diện để làm sau

## RULE: nạp sẵn popup trước lần mở đầu tiên

Ghi nhận ngày 12/10/2026. Trạng thái: để làm sau, khi chuẩn bị bán sản phẩm.

Mục tiêu: bấm RULE thấy các trường đã dựng, giảm hiện tượng khung xuất hiện rồi nội dung các tab tải lần lượt.

Hướng triển khai: tận dụng luồng dựng từng phần hiện có để chuẩn bị một popup ẩn khi app nhàn; giữ popup trong RAM và mở lại khi người dùng bấm RULE. Đây là nạp sẵn UI, không phải cache shader hay cache dữ liệu giao dịch.

Phạm vi dự kiến nhỏ đến vừa, chủ yếu tại `viking_v2/rules/window.py`, `viking_v2/dashboard/actions.py` và phần quản lý vòng đời cửa sổ chính nếu cần.

Các điểm cần xử lý khi triển khai:

- Nạp sẵn theo từng đợt trên luồng Tk, giữ app phản hồi; popup ẩn không lấy focus hoặc đổi trạng thái nút RULE.
- Nếu người dùng bấm khi chưa nạp xong, dùng lại popup đó và tiếp tục luồng tải hiện có.
- Hiển thị cấu hình hiện tại khi mở, tránh lưu đè bằng dữ liệu cũ; nạp sẵn không tự lưu settings hoặc tác động BOT/queue.
- Hủy callback khi đóng app; xử lý mở lại sau khi người dùng đóng hẳn popup.
- Đo lần vẽ đầu tiên trên VM/RDP: dựng sẵn widget không bảo đảm toàn bộ chi phí vẽ khi hiện cửa sổ biến mất.

Đánh giá: đáng làm để hoàn thiện trải nghiệm trước khi bán; ưu tiên thấp hiện tại vì tốc độ trên VM đã cải thiện rõ theo phản hồi người dùng. Chỉ tối ưu RULE trước, không mở rộng sang mọi popup trong cùng thay đổi.
