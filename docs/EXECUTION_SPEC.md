# Viking CKCS — Execution Specification

Tài liệu này chỉ mô tả cách phần mềm thực thi `docs/BUSINESS_RULES.md`.
Nó không được tạo thêm tín hiệu, rule quản trị vốn, safeguard hoặc chiến thuật.

## 1. Nguyên tắc

- `BUSINESS_RULES.md` là nguồn nghiệp vụ duy nhất. Trên UI dùng tên `BUY`, `SELL`, `WAIT`; ký hiệu M/B chỉ còn là tên tín hiệu nội bộ để đối chiếu tài liệu.
- REAL và PAPER dùng chung một luồng `OrderIntent`, nhưng tách hoàn toàn state và broker.
- Mỗi mã chỉ có tối đa một position mở và một BUY intent chưa hoàn tất.
- Mọi intent, trade cycle, lần kích hoạt exit và broker request phải lưu bền theo account.
- Restart không tạo lại tín hiệu/lệnh đã xử lý. Bot luôn OFF sau khi khởi động.
- Tất cả khối lượng tự động là lô chẵn 100; lô lẻ chỉ hiển thị và cảnh báo.

## 2. Trạng thái tối thiểu

### OrderIntent

- ID nội bộ, trade ID, symbol, BUY/SELL, quantity.
- Kiểu thực thi: `MARKET` hoặc `LO_LOCAL`.
- Giá giới hạn nếu là `LO_LOCAL`.
- Nguồn: BOT hoặc MANUAL; môi trường REAL hoặc PAPER.
- Manual BUY lưu `em_modes` và SL kiểu `DEFAULT/PERCENT/PRICE`; các field này đi cùng intent qua cache, partial fill và restart.
- BOT BUY mặc định gắn `NORMAL`, `HIGH`, `EXIT B`; ba tactic dùng chung parameter global và chỉ có ON/OFF.
- SELL do rule tạo lưu chính sách chờ T+: `RECHECK` hoặc `KEEP`.
- Phiên được phép: ATO, OPEN, ATC theo option vận hành.
- Trạng thái: PENDING, READY, SENDING, WORKING, PARTIAL, FILLED, CANCELLED, EXPIRED, UNKNOWN, REJECTED.
- Request tag và broker order ID để đối soát/chống gửi trùng.

### TradeCycle

- Một trade ID từ khi BUY đầu tiên khớp tới khi position đóng hoàn toàn.
- Lệnh re-entry tạo trade ID mới nhưng cùng chu kỳ WIN/LOSS của mã.
- Lưu entry, quantity tổng/đã về/đã bán, phí, peak realtime, highest close.
- Lưu danh sách EM của riêng trade, SL override, số loss liên tiếp và các exit event đã xử lý.
- WIN khi PNL ròng sau phí không âm; LOSS khi PNL ròng sau phí âm.

## 3. Luồng BUY

1. Phase 2 phát tín hiệu BUY mới.
2. Bỏ qua nếu mã đã có position hoặc BUY intent đang hoạt động.
3. Kiểm tra khóa 3 LOSS, Whipsaw Guard, tối đa 5 mã, tỷ trọng Phase 1 và vốn khả dụng.
4. Tính khối lượng đúng Business Rule, trừ vốn đã giữ cho pending BUY và làm tròn xuống lô 100.
   Nếu sức mua thay đổi trước lúc gửi, giảm về lô chẵn lớn nhất còn mua được. Khi `MIN 1 LÔ` bật và phần vốn chia theo mã không đủ 100 CP, hệ thống được dùng đúng 100 CP nếu cash/NAV thực tế vẫn đủ; nếu không thì WAIT.
5. Gắn SL -3%; nếu là re-entry sau LOSS thì gắn SL -2.1%.
6. Tạo một OrderIntent bền vững. Tín hiệu BUY lặp lại không tạo intent mới.
7. Giải phóng intent theo kiểu lệnh và phiên ở mục 7.
8. Khớp từng phần thì ghi nhận phần đã khớp. Phần BUY chưa khớp tiếp tục làm việc tới cuối phiên rồi hủy; không tự tạo BUY mới cho phần thiếu.

## 4. Luồng SELL

- SL realtime: bán toàn bộ phần còn lại.
- Indicator Exit B: bán toàn bộ phần còn lại.
- Normal Protection: khi đủ điều kiện, tạo một lần bán 1/3 phần vị thế còn lại.
- High-Profit Protection: khi đủ điều kiện theo giá đóng cửa 1D, tạo một lần bán 1/3 phần vị thế còn lại.
- Một lần đánh giá chỉ tạo một lượng SELL hợp lệ. Nếu cùng lúc có yêu cầu bán toàn bộ và bán một phần, gộp thành bán toàn bộ để không gửi trùng/oversell.
- Bán từng phần vẫn thuộc trade ID cũ. Chỉ tính WIN/LOSS khi position đã đóng hoàn toàn.

## 5. Chờ BUY

- Ngoài phiên, ngày nghỉ hoặc chưa tới phiên được phép: lưu local, không gửi DNSE.
- Thiếu trading token: giữ PENDING và yêu cầu OTP; không retry mù.
- Market data/WS không đủ tin cậy: không kích hoạt `LO_LOCAL` bằng giá cũ.
- Timeout không rõ DNSE đã nhận hay chưa: chuyển UNKNOWN và đối soát bằng request tag/order list trước khi gửi lại.
- TTL theo hành vi hiện tại: 24 giờ nhưng không hết hạn trước khi có ít nhất một phiên giao dịch hợp lệ để thử thực thi.

## 6. Chờ SELL và T+2

- Khi rule yêu cầu SELL nhưng `tradeQuantity` chưa đủ, tạo SELL intent local cho phần phải bán.
- Có bao nhiêu khối lượng đã về thì bán bấy nhiêu, làm tròn xuống lô 100.
- Phần chưa về tiếp tục nằm trong cùng SELL intent/trade ID.
- `RECHECK` là mặc định và áp dụng cho SL, NORMAL, HIGH, EXIT B: khi cổ về đủ lô chẵn, điều kiện SELL còn đúng mới gửi; điều kiện đã mất thì hủy phần local đang chờ.
- `KEEP`: điều kiện đã từng kích hoạt thì cổ về bao nhiêu tiếp tục bán bấy nhiêu đến hết lượng rule yêu cầu.
- Nếu lệnh DNSE chỉ khớp một phần, đối soát phần đã khớp; phần còn phải thoát quay lại trạng thái chờ sau khi broker kết thúc/hủy phần dư.
- Khối lượng SELL được tính chính xác bằng giá trị nhỏ nhất giữa lượng rule yêu cầu, position còn lại và `tradeQuantity`, sau đó round down theo 100.

## 7. MARKET, LO local và phiên

### MARKET

- Trong phiên liên tục: gửi lệnh thị trường theo mapping CKCS đang hoạt động tốt.
- Ngoài phiên: cache local.
- Nếu cho phép ATO, intent phù hợp được gửi ATO ở phiên mở cửa kế tiếp; nếu không thì chuyển tiếp tới OPEN.
- Nếu cho phép ATC, intent phù hợp có thể gửi ATC; nếu không thì tiếp tục chờ phiên hợp lệ kế tiếp.

### LO_LOCAL

- Giá giới hạn chỉ nằm local khi đang chờ.
- BUY chỉ READY khi ask/giá thực thi không cao hơn limit; SELL chỉ READY khi bid/giá thực thi không thấp hơn limit.
- Khi READY, gửi một LO DNSE tại limit. Không đặt resting LO trước đó và không có vòng sửa/đuổi giá qua API.
- LO local mặc định thực thi trong OPEN; không biến thành ATO/ATC.

### Lịch

- ATO/ATC là option cho phép phiên, không phải lệnh cấm BUY tuyệt đối.
- Nghỉ trưa, ngoài giờ, cuối tuần, lễ/Tết: chỉ cache và theo dõi; không gửi DNSE.
- Lịch working dates của DNSE là nguồn chính; cache DNSE gần nhất là fallback. Nếu cả hai đều chưa có thì fail-closed và không gửi lệnh.
- Qua kỳ nghỉ dài, intent còn hạn được chuyển tới phiên hợp lệ kế tiếp theo quy tắc TTL ở mục 5.

## 8. Khớp thiếu, lô chẵn và lô lẻ

- BUY 10.000, khớp 8.000: ghi nhận 8.000; 2.000 tiếp tục chờ trong phiên và bị hủy nếu hết phiên không khớp.
- SELL xử lý tuần tự theo khối lượng đã về và đã khớp.
- Khối lượng đặt tự động luôn round down theo 100.
- Phần dưới 100 không ghép, không bán vượt và không dùng để mở lệnh tự động; hiển thị ở cột lô lẻ để operator xử lý.

## 9. Re-entry, WIN/LOSS và Whipsaw

- Trade đầu dùng SL -3%; trade re-entry sau LOSS dùng SL -2.1%.
- WIN kết thúc chu kỳ re-entry và reset loss liên tiếp; trade sau quay về SL -3%.
- Ba LOSS liên tiếp khóa BUY mới và cảnh báo operator.
- Whipsaw mặc định: ON, N=3, X=7 phiên. Khi đạt N crossover trong cửa sổ X, khóa BUY/re-entry; position đang giữ không bị khóa quản lý.
- Position đang mở luôn tiếp tục được quản lý khi BUY bị khóa.

## 10. Corporate Action

- Operator đánh dấu mã và ngày chốt quyền tại tab `THỰC THI`.
- Mã đang được đánh dấu bị chặn BOT BUY dù có tín hiệu BUY.
- Nếu mã đang có position, Viking ghi log và gửi một cảnh báo Telegram cho operator xử lý thủ công.
- Không tự động SELL vì chốt quyền.
- Dữ liệu cũ vẫn được đọc an toàn nhưng `sell_enabled` cũ không còn kích hoạt lệnh ẩn.

## 11. Hủy, lỗi và phục hồi

- Intent local có thể hủy trực tiếp; chưa gửi DNSE thì không gọi cancel API.
- Order đã WORKING tại DNSE phải hủy/đối soát qua DNSE rồi mới kết thúc state local.
- Manual order và bot order dùng cùng lifecycle nhưng giữ source riêng.
- Position MANUAL/external vẫn chặn BOT mua trùng symbol và không tự bị bán.
- Operator phải chọn `BẮT ĐẦU QUẢN LÝ`; lúc đó hệ thống tạo TradeCycle và chỉ chạy các tactic/SL được operator chọn.
- Mọi mutation ghi journal: quyết định rule, intent, gửi, ack, partial fill, fill, cancel, reject, timeout và recovery.

## 12. NAV, phí và kết quả trade

- REAL ưu tiên số dư, position, execution và phí/thuế thực tế trả về từ DNSE.
- PAPER dùng cùng công thức phí/thuế CKCS đã cấu hình kỹ thuật; không có option mô phỏng chiến thuật.
- Vốn đã giữ cho pending BUY được trừ khi xét exposure/sức mua để nhiều intent không tiêu cùng một khoản tiền.
- Khi `KHÔNG COMPOUND` ON, vốn theo từng mã và từng REAL/PAPER chỉ phục hồi tối đa tới vốn gốc sau WIN; LOSS làm giảm vốn lần sau. OFF thì chia lại theo NAV hiện tại.
- PNL dùng để xác định WIN/LOSS là PNL ròng sau phí và thuế của toàn bộ trade ID sau khi position đóng hoàn toàn.
