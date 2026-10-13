# Quét EMA X phiên và gom UI — 13/10/2026

- RULE: gom công tắc giao cắt, hai mode và ô X trong một nhóm; RSI ở ngoài nhóm.
  Mode ∞ VA làm mờ ô X khi không bật QUÉT TRƯỚC; chuyển lại X PHIÊN giữ số đã nhập. Hint trong RULE, preview,
  lịch sử và backtest giải thích quét các phiên trước.
- ∞ VA giữ luật chỉ ghi nhận từ khi thêm mã. Khi bật VA QUÉT TRƯỚC hoặc X PHIÊN,
  quét thêm X phiên giao dịch trước mốc bắt đầu theo dõi mã, kể cả khi bật tùy chọn
  sau đó; loại cuối tuần/ngày nghỉ theo lịch. Không lấy X phiên trước ngày bật công tắc.
- X vừa là số phiên quét lại, vừa là hạn giữ tính từ phiên cắt (phiên cắt là phiên 1).
  Ví dụ thứ Hai 05/10 chọn X=3: quét 30/09, 01/10, 02/10 và phần 05/10 đã diễn ra.
  Lần cắt 02/10 còn hạn đến đóng phiên 06/10; lần cắt 30/09 đã hết hạn từ 02/10.
- REALTIME tái tính EMA ngày từ giá phút đã hoàn thành và giá hiện tại hợp lệ;
  CLOSED chỉ dùng nến ngày đã đóng. Luôn giữ thời điểm cắt thật, không gia hạn tín hiệu cũ.
- Cache lưu trên đĩa, dùng chung REAL/PAPER; restart/đổi mode không tải lại khoảng đã có.
  Lỗi lịch sử có backoff và không cho dùng lần cắt chưa xác minh; lần cắt mới có bằng chứng
  quan sát trực tiếp vẫn có thể dùng. ID đã tiêu thụ không trở thành mới khi quét lại/đổi X.
- Lịch sử bổ sung tách khỏi quan sát trực tiếp: chuyển về VA không mang theo giao cắt
  trước mốc theo dõi. Preview/TRACE/Excel cộng số lần cắt theo đúng mode.
- Backtest DAILY dùng các phiên đã đóng trước ngày chạy; REPLAY cần nguồn 1 phút FULL
  để xác minh lịch sử, không suy ra giao cắt phút từ nến ngày/2 phút hay dữ liệu tương lai.
- RSI, giờ mua, vốn/Priority/slot, WHIPSAW, OTP và kiểm tra cuối trước gửi vẫn áp dụng.

Kiểm thử bằng `support/tools/run_offline.py`, runtime/account tạm và broker giả.
Có đối chứng lần cắt ở phiên trước còn hạn gửi MARKET một lần qua broker giả;
kiểm tra hết hạn, VA bỏ lịch sử, đổi mode/đổi X/restart giữ claim, mất dữ liệu/retry,
không đếm lại giao cắt, REPLAY mua ở phiên sau và bố cục scale 100%/150%/200%.
Kết quả mới:
- Hồi quy toàn ứng dụng: **2.295 passed**, `.artifacts/ema_lookback_release.xml`.
- Kiểm tra EMA/broker giả/backtest, gồm bổ sung tương thích state v2 cũ: **88 passed**,
  `.artifacts/ema_lookback_final.xml`.
- UI, scale, dựng popup và preload: **93 passed**, `.artifacts/ema_lookback_ui.xml`.

Cập nhật khi đã đóng app bằng BAT `2 → 3`; không cần nạp lại preset để có cơ chế mới.
