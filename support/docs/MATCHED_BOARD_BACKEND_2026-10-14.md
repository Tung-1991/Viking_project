Backend nguồn giá khớp lệnh — 14/10/2026

HDB mua 600 cổ ở 22.650. Dump VM ghi WS=24.600 lúc 09:40:53, kích ARM +8,6093%, rồi giá về 23.250 nhưng vẫn giữ đỉnh. Hai SELL 100 cổ khớp lúc 13:00:08 và 13:00:31, cùng giá 23.150. Snapshot của cả hai: ARM AUTO, Dynamic ALERT, LẶP ON, 33% GỐC. Không có một SELL 200 cổ.

[24HMoney](https://24hmoney.vn/stock/hdb/transactions) ghi giao dịch **thỏa thuận** 922.823 HDB ở 24.600 lúc 09:40:55. Mức giá hợp lệ của một loại giao dịch khác đã đi vào VA/MAE/MFE/ARM, do app subscribe nhiều board nhưng dùng một cache theo symbol. Hai đồng hồ có chênh lệch vài giây; dump không lưu gói WS gốc để xác nhận board của từng packet.

Với giá vốn 22.650: 24.600 là +8,6093%, không phải +10,8%. Đỉnh khớp lệnh 23.450 là +3,5320%, chưa đủ ARM 7%. ARM 7% tương ứng 24.235,5. Giá hiện tại không thể tự hạ đỉnh đã ghi: đỉnh thật cần được giữ để tính trailing; phải lọc loại giao dịch trước khi cập nhật bộ nhớ.

**Thay đổi backend**

- WebSocket chỉ subscribe tick/top_price/expected_price của G1; kiểm tra board ở cả payload và channel, loại board khác/không xác định/mâu thuẫn. Board khác không cập nhật giá, high/low, volume, BBO hoặc thời điểm nhận giá.
- REST trade và quote đều request G1, chọn đúng row G1/symbol thay vì row đầu tiên. Chấp nhận một row legacy thiếu board chỉ khi request đã giới hạn G1; không suy đoán với response nhiều row thiếu board.
- Giữ `boardId`, `market_data_policy`, `exchange_time` ở quote để truy vết. Loại số không hữu hạn. Quote-only không làm giá matched cũ trở thành mới.
- Giá dự kiến đấu giá vẫn có thể được nhận trong cache nhưng không được dùng để xác nhận quote cho tín hiệu/thực thi. Quan sát frozen/stale/board khác/expected không cập nhật extrema và không kích lệnh quản lý vị thế.
- Khi bắt đầu dùng chính sách G1 cho state cũ chưa có provenance, giữ nguyên extrema để review, đánh dấu `price_history_verified=false`. PROTECT giữ WAIT, không dùng đỉnh cũ để AUTO bán. Chặn ở rule, planner, queue và ngay trước hand-off. Lệnh broker đã nhận tiếp tục được đối soát. SL/TP/E và MANUAL giữ cơ chế riêng trên giá hợp lệ.
- Lệnh PROTECT mang policy/version sửa lịch sử; decision/intent cũ trước một lần rebuild không được dùng với lịch sử mới.
- Lưu vốn mua tích lũy `entry_notional_vnd`, không giảm khi bán một phần. Backend cung cấp `current_net_pnl_pct`, `mae_net_pnl_pct`, `mfe_net_pnl_pct` trên cùng mẫu số. UI vẫn dùng công thức hiện tại, chờ lượt sửa UI riêng.
- ARM lưu snapshot notice trong state, GUI có thể gửi từ snapshot của các quyết định sau; Telegram dùng outbox bền vững, gửi lại khi lỗi, không gửi trùng sau restart. Tin ARM ghi thời điểm, mức đỉnh/ngưỡng, chế độ và có đang chờ cổ về; không coi ARM là một lệnh SELL.

LẶP không đổi ở bản này: một giá thấp hơn giá kích hoạt lượt trước vẫn có thể tạo lượt kế tiếp khi LẶP ON. Trong sự cố HDB, một đỉnh thuộc giao dịch thỏa thuận đã làm điều kiện ARM ban đầu sai; sửa nguồn giá ngăn tình huống đó. Không tự thay chiến lược bán theo một cấu hình mới.

**Dữ liệu cũ và phạm vi áp dụng**

Không sửa dữ liệu VM hoặc thư mục Downloads. Đỉnh 24.600/MAE 21.100 đã lưu không tự mất khi nguồn giá mới đúng; khi áp dụng bản backend, PROTECT của lịch sử cũ chưa xác minh sẽ bị giữ lại.

`RuleStateStore.replace_verified_price_metrics` là thao tác rebuild **tường minh** từ lịch sử G1 đã được review. API yêu cầu nguồn/bảng xác định, số hữu hạn và extrema bao chứa giá hiện tại; lưu các metric cũ để đối chiếu, xóa ARM/floor dựa trên giá cũ. Số cổ, fill, realized PNL, broker IDs và số lượt đã bán không bị xóa. Không có auto-reset hoặc tự động gọi API này khi khởi động. Rebuild vận hành cần dữ liệu matched từ thời điểm khớp BUY; không dùng toàn bộ low/high của ngày mua để suy ra extrema sau thời điểm mua nếu chưa kiểm chứng.

Nguồn kỹ thuật đối chiếu: [model Trade của SDK DNSE](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/websocket/models.py), [đăng ký và bộ lọc board của SDK](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/websocket/client.py).

Kiểm tra offline nằm tại `support/tests/test_round_lot_market_data.py`: replay G1/thỏa thuận xen kẽ, JSON/MessagePack, response REST nhiều board, indicative/frozen, vốn gốc sau partial SELL, lịch sử cũ, rebuild bảo toàn fill/counter, lệnh cũ và race trước hand-off, đối chứng lệnh hợp lệ vẫn tới broker, ARM outbox gửi lại/không trùng. Runner chặn mạng và dùng runtime tạm.

**Kết quả kiểm tra**

- 58 case mới về lỗi này đạt; nhóm mới cùng daemon theo phiên đạt 74/74 sau khi bổ sung `boardId=G1` cho fixture WS.
- Toàn bộ suite: 2.560 đạt lần đầu, 16 case daemon thiếu board trong fixture được sửa và chạy lại đạt. Tổng đối chiếu 2.590 case: 2.576 đạt, 14 lỗi còn lại thuộc UI/launcher. Chạy riêng hai file tương ứng trên bản trích xuất commit trước `481b58b` cho đúng cùng 14 lỗi; không có lỗi backend mới còn lại. Không tuyên bố toàn bộ suite xanh.
- Replay trên **bản sao riêng** DB dump thật: `WAIT / PROTECT_HISTORY_UNVERIFIED`; ledger không đổi, mua 600 / còn 400 / đã bán 200, counter hai lượt khớp giữ nguyên. Vốn gốc 13.590.000; PNL 320.000 tương ứng 2,3547% trên vốn gốc. Đỉnh 8,6093% cũ được giữ để review và không được phép dùng cho PROTECT.
- SHA-256 đối chiếu lại toàn bộ 61 file trong thư mục Downloads: không file nào thay đổi. Các báo cáo XML/JSON và bản sao DB kiểm thử ở `.artifacts/hdb-backend-validation`, không đưa dữ liệu tài khoản vào Git.
