# Viking CKCS — UI và QOL Scope

## Hướng làm

- Không tiếp tục giao diện V2 hiện tại.
- Dùng panel legacy làm chuẩn về bố cục, tỷ lệ, màu, font và trải nghiệm.
- Dựng lại widget/backend binding gọn trong V2; không copy logic, popup hoặc settings legacy.
- Chỉ CKCS. Một chế độ rule static, không preset và không symbol override.

## Giữ và làm lại

### Panel chính

- Account, NAV/tổng tài sản, tiền, sức mua, PNL và phí.
- Trạng thái phiên, daemon, DNSE REST, WebSocket, trading token và BOT ON/OFF.
- Chọn mã CKCS, REAL/PAPER.
- Giá realtime, khối lượng, BUY tay, MARKET/LO/ATO/ATC.
- Hai kiểu thực thi bot: MARKET và LO local; option cho phép ATO/ATC.
- Rule Preview read-only: market state, M/B/WAIT, lý do, vốn/tỷ trọng và trạng thái khóa.

### Bảng và popup

- Lệnh đang chạy: tách Local Pending và DNSE Working nhưng xem trong cùng khu vực.
- Danh mục CKCS REAL/PAPER: tổng KL, bán được, T+ chờ về, lô lẻ, giá vốn, giá thị trường, PNL và trade ID.
- Lịch sử: order lifecycle, trade đã đóng, WIN/LOSS, phí và lý do exit.
- Log: System, BOT, Manual/API; có timestamp và mức lỗi rõ ràng.
- API Health: REST/WS/account/order/working dates, lần thành công cuối và lỗi cuối.
- Chốt quyền chưa có UI thực thi; dữ liệu cũ được giữ nhưng tự động hóa tạm ngừng.
- Kết nối tối thiểu: DNSE credential/account, OTP, Telegram và watchlist.
- Popup RULE duy nhất có ba tab mẹ: `NGHIỆP VỤ`, `EXIT MANAGER`, `THỰC THI`. Tab thực thi chỉ chứa kiểu lệnh BOT, phiên ATO/ATC, EM mặc định của BOT, chính sách SELL chờ T+ và chốt quyền thủ công.

## Popup/nút cũ được thay nghĩa

- TSL/E-E cũ bỏ logic; Exit Manager mới chỉ gồm `NORMAL`, `HIGH`, `IND EXIT` theo từng trade.
- `BOT` chỉ có rule static, trạng thái, MARKET/LO local, ATO/ATC và các default parameter hợp lệ.
- Preview hiển thị lệnh manual, rule và health trong cùng một vùng; không preset.

## Xóa hoàn toàn

- CKPS và mọi account/tab/config/test riêng CKPS.
- Preset, preset manual, merge config và symbol override.
- Sandbox LEGO, voting, group G0-G3 và indicator builder.
- Safeguard, checklist, bypass và opportunity/gợi ý BOT.
- TSL/Entry-Exit legacy, quick TSL/EE, DEF, DCA/PCA, REV và A.CUT.
- Margin ở giai đoạn hiện tại; Liquidity Cap 5% ở giai đoạn hiện tại.
- AI scan report, scheduler, trigger, payload strategy và history riêng của advisor.
- TP chỉ preview. SL manual mặc định theo Phase 3 và có thể override theo % hoặc giá.
- Các setting kỹ thuật timeout/cache/retry trên UI.

## QOL bắt buộc

- Bot luôn OFF sau startup; REAL luôn yêu cầu xác nhận/OTP phù hợp.
- State REAL/PAPER và theo account tách hoàn toàn.
- UI không đứng khi gọi API; mọi network action chạy nền và trả trạng thái rõ.
- Sau restart, bảng pending/working/position phục hồi và đối soát tự động.
- Dòng trạng thái phải nói rõ vì sao đang WAIT: ngoài phiên, nghỉ lễ, thiếu token, T+2, khóa vốn, loss lock, whipsaw hoặc data lỗi.
- Lệnh local và lệnh đã lên DNSE có màu/nhãn khác nhau; không để operator nhầm nơi cần hủy.
- Lô lẻ và T+ hiển thị riêng, không giấu trong tổng quantity.
- Có nút hủy local, hủy DNSE working và đóng position với xác nhận đúng đối tượng.
