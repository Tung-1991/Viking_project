# Viking — cấu hình chốt cho PAPER (21/09/2026)

## Cấu hình mặc định trong code

Nguồn mặc định của ứng dụng là `DEFAULT_RULE_PARAMETERS` trong `viking_v2/config.py`.
File settings cũ thiếu trường sẽ được ghép với bộ mặc định này; giá trị người dùng đã lưu vẫn được ưu tiên.

Thứ tự ưu tiên cấu hình:

1. `config.py` chỉ cấp mặc định cho tài khoản mới hoặc trường còn thiếu.
2. Khi bấm **LƯU THAY ĐỔI** trên UI, ứng dụng ghi toàn bộ cấu hình hiện hành vào `viking_v2/runtime/accounts/<ACCOUNT_ID>/settings.json`.
3. Lần mở sau, giá trị trong JSON phủ lên mặc định trong `config.py`; vì vậy sửa default trong code không tự thay đổi lựa chọn đã lưu trên UI.
4. Backtest có file riêng `viking_v2/runtime/backtest/settings.json`; thay đổi trên màn hình backtest không tự sửa PAPER/REAL.

| Nhóm | Cấu hình chốt |
|---|---|
| Phase 1 | MA200; Pivot 3/3; sai số Pivot 1%; vùng MA 1%; xác nhận 3 phiên; Volume phase 1 OFF |
| Tỷ trọng | UPTREND 90%; DOWNTREND 10%; ACCUMULATION 60%; DISTRIBUTION 50% |
| Entry | EMA 3/6 + RSI14; chỉ mua từ 14:00; đọc REALTIME/TICK; Volume BUY OFF; xác nhận BUY phút OFF |
| Vốn | Tối đa 5 position BOT; MANUAL/EXTERNAL không chiếm slot BOT nhưng vẫn dùng tiền và room thực; không compound; tối thiểu 100 cổ; khóa sau 3 LOSS trong 24 giờ; Whipsaw ON 3 lần/7 phiên; dừng BUY BOT 15 phút sau SELL MANUAL/EXTERNAL khớp |
| Phí | Mua 0,045%; bán 0,045%; thuế bán 0,1% |
| SL | ON; lệnh đầu −3,5%; lệnh vào lại −2,5%; bán 100% |
| TP | Giá trị 7% nhưng mode TP mặc định OFF |
| PROTECT | AUTO; Dynamic ON; START ATR14(T−1) ×0,55; TRAIL ATR ×0,8; giữ 90% lãi đỉnh tới MFE 5%; ARM 7%; trail sau ARM 2,5%; bán 100%; REPEAT OFF; reset sàn T+2 OFF |
| E | EMA SELL 3/6 + RSI14 giảm; ALERT; chỉ log/UI, không đặt lệnh; Telegram bật/tắt riêng trong KẾT NỐI |
| T+2 | RECHECK: khi cổ về, điều kiện thoát còn đúng mới bán; điều kiện đã mất thì huỷ yêu cầu bán cũ |

Mode mặc định của BOT là `PROTECT + E`; TP không được gắn vào trade. SL có công tắc riêng và mặc định ON.
Không có E sớm, E LOSS hay FAILED RECOVERY trong backend vận hành.

## An toàn hàng đợi và bán tay

- 40 mã trong watchlist không phải 40 lệnh chờ. Mỗi chu kỳ chỉ xét các BUY đang hợp lệ; ưu tiên tín hiệu xuất hiện sớm hơn, sau đó theo thứ tự watchlist. Đủ 5 position/lệnh BUY đang hoạt động thì các tín hiệu còn lại bị ghi `MAX_POSITIONS`, không nằm lại trong một hàng đợi bí mật để tự nhảy vào sau.
- `BOT OFF` chặn cả tạo BUY BOT mới lẫn gửi BUY BOT cũ còn trong cache. BUY BOT chưa gửi được hủy; lệnh đã có khả năng lên broker chỉ được cảnh báo để operator kiểm tra. SELL quản lý vị thế và lệnh MANUAL vẫn chạy.
- Khi SELL nguồn `MANUAL` đặt trong Viking khớp lần đầu, PAPER hoặc REAL tương ứng khóa BUY BOT mặc định 15 phút. Thời gian chỉnh tại `RULE → Phase 3 → VỐN → Dừng BUY sau bán tay`; `0` là tắt. Hết hạn tự mở, không cần bật lại nút bot.
- Mỗi lệnh cache có `Sửa lệnh & quản lý` và `Hủy`; bên trong cửa sổ sửa có `Tạm dừng/Tiếp tục`. Lệnh `PAUSED` vẫn giữ slot để không có mã khác lấp chỗ ngoài ý muốn.
- Quota hiển thị tách `BOT x/5 · MANUAL y · TỔNG z`. Lệnh MANUAL/EXTERNAL không chiếm quota BOT nhưng luôn được tính vào tiền mặt và room danh mục; vì vậy BOT không thể vượt sức mua thật.
- Mua nên thực hiện qua Viking. Nếu có lượng mua thêm trực tiếp trên DNSE cùng mã, Viking gắn `EXTERNAL_DNSE`; phần này không làm dừng trade Viking và không bị PROTECT/SL của trade Viking bán nhầm.
- SELL khẩn cấp trực tiếp trên DNSE được nhận diện bằng position giảm và lệnh khớp không có remark `V2:`. Viking đồng bộ khối lượng/PnL/phí, đóng một phần hoặc toàn bộ trade, huỷ SELL cache cũ, khóa BUY BOT 15 phút và có thể gửi Telegram. Không tìm thấy lệnh khớp/giá khớp thì chỉ ghi `RECONCILE_REQUIRED`, tuyệt đối không đoán.
- Cửa sổ `Sửa lệnh & quản lý` quản lý chung khối lượng, giá LO, TP, SL và các mode TP/PROTECT/E.

## Telegram

- PAPER và REAL dùng chung một bảng cấu hình; backend không tạo hai bộ quy tắc thông báo riêng.
- Mỗi loại tin có công tắc riêng: BOT BUY đã xếp lệnh, CLOSED, PROTECT chạm mức, E ALERT, BUY bị chặn, chốt quyền và SELL trên DNSE app. Các loại có thể lặp có cooldown riêng; BUY dùng cửa sổ gom riêng.
- RULE và Telegram độc lập: `PROTECT AUTO` luôn bán khi chạm mức; công tắc Telegram OFF chỉ tắt tin. Bật `PROTECT CHẠM MỨC` tạo hành vi “AUTO vừa bán vừa báo”. `PROTECT ALERT` chỉ ghi nhận, không đặt lệnh, bất kể Telegram ON/OFF.
- Tín hiệu đã hết hiệu lực trong lúc app tắt không được gửi lại. Nếu mở app khi tín hiệu vẫn còn hợp lệ, nó được coi là lần quan sát hiện tại và chỉ gửi một lần.

## Đối chứng SL vào lại

Chỉ thay `SL vào lại`, mọi dữ liệu và rule khác giữ nguyên. Đơn vị tiền: triệu VND.

| SL vào lại | PnL | MFE trong T+2 | MFE sau T+2 | PnL/MFE sau T+2 | Lệnh | Lệnh SL | Max DD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| −2,1% | 602,26 | 1.082,03 | 948,28 | **63,51%** | 55 | 9 | 6,52% |
| **−2,5%** | **607,63** | **1.083,98** | **961,14** | 63,22% | **54** | **8** | **5,91%** |
| −3,0% | 599,21 | 1.082,95 | 959,94 | 62,42% | 54 | 8 | 6,45% |

Mức −2,5% được chọn vì đồng thời cho PnL và MFE sau T+2 cao nhất, chỉ còn tám lệnh SL
và có Max DD thấp nhất trong ba mức. Tỷ lệ thu thấp hơn −2,1% khoảng 0,29 điểm phần trăm
vì mẫu số MFE tăng nhanh hơn PnL, không phải vì PnL giảm.

## Phạm vi backtest dùng cho hai bảng

- Bảy cohort CTS, FTS, SHS, SSI, VIX, VND và DPM chạy độc lập; mỗi cohort vốn 1 tỷ đồng, tỷ trọng 60%, tối đa một position.
- Entry EMA 3/6 + RSI14 sau 14:00; SL −3,5%/−2,5%; PROTECT đúng cấu hình chốt; E ở ALERT.
- Whipsaw và khóa LOSS tắt trong phép so sánh để giữ cùng phương pháp với các lượt nghiên cứu trước.
- REPLAY `save=False`; không ghi run JSON và không đổi settings trong lúc chạy. Kết quả được xuất riêng thành một workbook bốn sheet.
- PnL đã gồm phí và thuế mô phỏng. Tổng bảy mã là tổng các lượt độc lập, không phải một danh mục chung.
- `MFE trong T+2` và `MFE sau T+2` là hai phase của cùng lệnh, không cộng hai cột. Tỷ lệ thu chỉ dùng MFE sau T+2.

## Bảng 0 — so sánh bốn mode

Đơn vị tiền: triệu VND. E giữ ở `ALERT` trong cả bốn mode.

| PROTECT | PnL | MFE trong T+2 | MFE sau T+2 | PnL/MFE sau T+2 | Lệnh | Đóng / mở | WIN / LOSS | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AUTO · Dynamic OFF | 191,21 | 896,17 | **1.242,66** | 15,39% | 49 | 46 / 3 | 18 / 28 | 9,51% |
| **AUTO · Dynamic ON** | **607,63** | **1.083,98** | 961,14 | **63,22%** | 54 | 53 / 1 | 36 / 17 | **5,91%** |
| ALERT · Dynamic OFF | −299,25 | 508,00 | 1.150,36 | −26,01% | 27 | 23 / 4 | 0 / 23 | 23,84% |
| ALERT · Dynamic ON | −299,25 | 508,00 | 1.150,36 | −26,01% | 27 | 23 / 4 | 0 / 23 | 23,84% |

Hai mode ALERT giống nhau về tiền vì PROTECT chỉ báo, E cũng chỉ báo và Dynamic không được phép đặt lệnh.
MFE cao của AUTO OFF/ALERT không đồng nghĩa chiến lược tốt hơn: vị thế được giữ lâu hơn nhưng phần lớn lợi nhuận không được hiện thực hoá.

Chi tiết được xuất thành **7 workbook riêng theo từng mã** trong
`viking_v2/runtime/backtest/runs/exports/`. Mỗi workbook có đúng bốn sheet:
`AUTO_DYNAMIC_OFF`, `AUTO_DYNAMIC_ON`, `ALERT_DYNAMIC_OFF`, `ALERT_DYNAMIC_ON`.
Mỗi dòng là một lệnh; MFE trong T+2, MFE sau T+2 và MFE toàn lệnh được tách thành
ba cột riêng, tuyệt đối không cộng hai phase MFE với nhau.
Sáu cột kiểm toán Dynamic đi kèm gồm: `ATR14 1D %`, `START MFE %`,
`TRAIL ATR %`, `ĐỈNH LÚC TÍNH`, `SÀN GIỮ 90%` và `SÀN PROTECT`.
Giá trị được lấy từ trạng thái backend tại lúc PROTECT khớp/ra cảnh báo;
`DYNAMIC OFF` và `CHƯA KÍCH HOẠT` được ghi rõ thay vì điền số suy đoán.

## Bảng 1 — tổng thể theo mã

Đơn vị tiền: triệu VND.

| Mã | PnL | MFE trong T+2 | MFE sau T+2 | PnL/MFE sau T+2 | Lệnh | Đóng / mở | WIN / LOSS | Max DD | Phí | Thuế |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CTS | 256,79 | 216,02 | 338,47 | 75,87% | 8 | 8 / 0 | 7 / 1 | 2,68% | 4,42 | 5,05 |
| FTS | 45,78 | 127,20 | 128,94 | 35,51% | 8 | 8 / 0 | 3 / 5 | 5,91% | 4,20 | 4,70 |
| SHS | 106,60 | 156,97 | 180,34 | 59,11% | 8 | 7 / 1 | 6 / 1 | 3,24% | 4,07 | 4,29 |
| SSI | 21,19 | 134,59 | 80,92 | 26,18% | 8 | 8 / 0 | 4 / 4 | 5,38% | 4,18 | 4,66 |
| VIX | 73,86 | 182,51 | 73,86 | 100,00% | 7 | 7 / 0 | 6 / 1 | 3,14% | 3,77 | 4,23 |
| VND | 88,80 | 182,32 | 144,01 | 61,66% | 10 | 10 / 0 | 7 / 3 | 3,77% | 5,40 | 6,05 |
| DPM | 14,62 | 84,37 | 14,62 | 100,00% | 5 | 5 / 0 | 3 / 2 | 1,57% | 2,69 | 2,99 |
| **Tổng** | **607,63** | **1.083,98** | **961,14** | **63,22%** | **54** | **53 / 1** | **36 / 17** | **5,91%** | **28,73** | **31,97** |

## Bảng 2 — chi tiết theo nhánh thoát

Đơn vị tiền: triệu VND.

| Nhánh | Lệnh | PnL | MFE trong T+2 | MFE sau T+2 | Thu/MFE sau T+2 | WIN / LOSS | Phí | Thuế |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PROTECT | 45 | 792,48 | 1.054,01 | 981,96 | **80,70%** | 36 / 9 | 24,31 | 27,43 |
| SL | 8 | −165,78 | 19,84 | −30,94 | Không có ý nghĩa | 0 / 8 | 4,16 | 4,54 |
| Còn mở | 1 | −19,07 | 10,13 | 10,13 | Không dùng | 0 / 0 | 0,27 | 0,00 |
| **Tổng** | **54** | **607,63** | **1.083,98** | **961,14** | **63,22%** | **36 / 17** | **28,73** | **31,97** |

Riêng PROTECT thu được 792,48/981,96 = **80,70% MFE bán được** của nhóm nó đóng.
PnL toàn hệ thống thấp hơn vì tám lệnh SL và một vị thế còn mở kéo giảm khoảng 184,85 triệu.
MFE sau T+2 của nhóm SL có thể âm vì ngay cả mức giá tốt nhất trong phase bán được vẫn không bù đủ phí và thuế.

## Quyết định

- Chốt START 0,55 để ưu tiên không gian chạy và MFE bán được gần 950 triệu.
- Giữ SL lệnh đầu −3,5%; chốt SL vào lại −2,5%.
- So với −2,1%, mức −2,5% tăng PnL 5,37 triệu, tăng MFE sau T+2 12,86 triệu, giảm một lệnh SL và hạ Max DD từ 6,52% xuống 5,91%.
- Giữ E ở ALERT. E AUTO/E LOSS không làm tổng nhóm `E + SL` tốt hơn một cách đáng tin cậy.
- Không đưa FAILED RECOVERY vào hệ thống: backtest cho thấy nó cắt nhầm các lệnh đang âm nhưng sau đó hồi thành lệnh thắng.
- Bước tiếp theo là PAPER và thu dữ liệu ngoài mẫu; không tiếp tục tối ưu trên cùng tập dữ liệu.
