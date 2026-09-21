# Viking — 4 mode với SL −3% (18/09/2026, lưu trữ)

> Báo cáo này lưu kết quả lịch sử có nhánh E sớm thử nghiệm. Nhánh đó đã bị gỡ hoàn toàn khỏi UI/backend vì chỉ cải thiện trong mẫu, chưa đủ bằng chứng ngoài mẫu và làm tăng độ phức tạp vận hành. Không dùng tài liệu này làm mô tả cấu hình hiện hành.

## Phạm vi

Chạy lại bốn tổ hợp `AUTO/ALERT × Dynamic OFF/ON` trên **cùng bộ tham số nghiên cứu**, không phải settings đang bật ở tài khoản. Bảy mã CTS, FTS, SHS, SSI, VIX, VND, DPM chạy **độc lập** (mỗi lượt vốn giả lập 1 tỷ đồng, exposure 60%, tối đa một vị thế); các tổng bên dưới không phải PnL của một danh mục chung. Engine chạy `REPLAY`, `save=False`, không xuất Excel. Lệnh mua sau 14:00 theo EMA 3/6 + RSI; **SL −3% lệnh đầu/−2,1% vào lại**; E 3/6 + RSI. Điều kiện **E sớm** sau T+2 ở mức lỗ −1% được giữ ON ở **cả bốn mode** để chỉ so tác động của Dynamic và AUTO/ALERT. T+2 `RECHECK`; không TP, không lãi kép.

PROTECT chung: ARM 7%, trail sau ARM 2,5% từ đỉnh, bán 100%, REPEAT OFF. Khi Dynamic ON: START ATR14(T−1) ×0,6; TRAIL ATR ×0,8; giữ 87,5% MFE khi đỉnh lãi dưới 5%, sàn đã khóa không hạ. Khi Dynamic OFF, ba tham số dưới ARM không tác động. `ALERT` chỉ báo, không đặt lệnh PROTECT; E và SL vẫn bán. **E sớm là một phần của E, không phải Dynamic.**

## Kết quả

Đơn vị tiền: **triệu VND**. MFE trong T+2 và MFE bán được sau T+2 là **hai đỉnh của cùng một lệnh** ở hai giai đoạn, đã tính phí/thuế mô phỏng; **không cộng hai cột MFE**. Chỉ MFE sau T+2 là mẫu số để đánh giá thoát lệnh. `PnL tổng` là equity cuối kỳ trừ vốn đầu kỳ, gồm cả đánh dấu theo giá cuối kỳ của vị thế chưa đóng. `Thu/MFE` dùng PnL tổng đó, không được diễn giải là toàn bộ tiền đã chốt.

| Mode | PnL đã đóng | Vị thế còn mở (tạm tính) | PnL tổng | MFE trong T+2 | MFE bán được sau T+2 | Thu/MFE bán được | Mua / đóng / mở | Cảnh báo |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AUTO · Dynamic OFF | 367,46 | +3,31 | 370,78 | 1.104,91 | 1.184,00 | 31,32% | 57 / 55 / 2 | 0 |
| AUTO · Dynamic ON | **562,41** | **0** | **562,41** | 1.114,13 | 851,50 | **66,05%** | 57 / 57 / 0 | 0 |
| ALERT · Dynamic OFF | 86,00 | +27,14 | 113,14 | 1.088,53 | **1.397,24** | 8,10% | 56 / 53 / 3 | 18 |
| ALERT · Dynamic ON | 86,00 | +27,14 | 113,14 | 1.088,53 | **1.397,24** | 8,10% | 56 / 53 / 3 | 42 |

Theo mã, PnL tổng (triệu VND, làm tròn):

| Mã | AUTO OFF | AUTO ON | ALERT OFF/ON |
|---|---:|---:|---:|
| CTS | 215,61 | 249,01 | 131,51 |
| FTS | 46,78 | 43,88 | −4,61 |
| SHS | 72,10 | 90,52 | 20,65 |
| SSI | −44,20 | 2,39 | −49,93 |
| VIX | 90,82 | 73,86 | 59,56 |
| VND | 13,21 | 88,12 | −20,49 |
| DPM | −23,55 | 14,62 | −23,55 |

AUTO ON đóng bằng 42 PROTECT, 8 E (6 E sớm) và 7 SL. AUTO OFF: 18 PROTECT, 28 E, 9 SL, 2 còn mở. ALERT: 44 E, 9 SL, 3 còn mở. Hai ALERT có PnL/MFE giống hệt nhau vì cảnh báo không tạo fill. Các mode có chuỗi mua/thoát khác nhau, nên không quy toàn bộ chênh lệch MFE/PnL cho riêng một lần đổi sàn PROTECT.

Đối chứng tách E khỏi Dynamic: cùng SL −3% và AUTO Dynamic ON nhưng tắt E sớm, PnL còn **536,20 triệu**, MFE bán được **850,94 triệu**. Bật E sớm làm PnL tăng thêm **26,20 triệu** trên mẫu này. Bảng chính giữ E sớm ON cố định; nó **không tự bật/tắt theo công tắc Dynamic**. Bảng trước dùng SL −4%: AUTO Dynamic ON đạt **599,53 triệu**, cao hơn SL −3% khoảng **37,12 triệu**; đồng thời số lệnh SL chỉ 2 thay vì 7. Do đó không gọi SL −3% là cải thiện chỉ vì tỷ lệ thu/MFE nhích lên.

## Kiểm tra độ nhạy: SL −5% lệnh đầu

Giữ nguyên toàn bộ cấu hình và cùng bảy mã ở bảng chính, chỉ đổi SL lệnh đầu từ −3% sang **−5%**; SL lệnh vào lại vẫn −2,1%. Chạy REPLAY với `save=False`, không tạo Excel và không đổi settings tài khoản. Đơn vị tiền: **triệu VND**. Hai cột MFE vẫn là hai giai đoạn của **cùng lệnh**, không cộng lại; tỷ lệ thu dùng MFE bán được sau T+2.

| Mode | PnL đã đóng | Vị thế còn mở (tạm tính) | PnL tổng | MFE trong T+2 | MFE bán được sau T+2 | Thu/MFE bán được | Mua / đóng / mở |
|---|---:|---:|---:|---:|---:|---:|---:|
| AUTO · Dynamic OFF | 390,07 | +3,31 | 393,39 | 1.106,97 | 1.223,48 | 32,15% | 57 / 55 / 2 |
| AUTO · Dynamic ON | **599,27** | **0** | **599,27** | 1.115,77 | 915,51 | **65,46%** | 57 / 57 / 0 |
| ALERT · Dynamic OFF | 107,80 | +27,14 | 134,94 | 1.091,07 | **1.451,71** | 9,30% | 56 / 53 / 3 |
| ALERT · Dynamic ON | 107,80 | +27,14 | 134,94 | 1.091,07 | **1.451,71** | 9,30% | 56 / 53 / 3 |

Đối chiếu **AUTO · Dynamic ON**: SL −3% đạt PnL **562,41 triệu**, MFE bán được **851,50 triệu** (66,05%); SL −4% đạt **599,53 triệu**, **915,77 triệu** (65,47%); SL −5% đạt **599,27 triệu**, **915,51 triệu** (65,46%). Ở −4% và −5% cùng 43 PROTECT, 12 E, 2 SL; chênh lệch PnL **−0,26 triệu** chỉ đến từ lệnh VIX-05, bán ngày 07/07 với giá mô phỏng lần lượt 15,524 và 15,517. Mức drawdown lớn nhất trong bảy lượt riêng lẻ cùng 7,09%. Như vậy trên đúng mẫu này, nới từ −3% lên −4% giúp tăng PnL tuyệt đối, còn nới tiếp tới −5% **không mang thêm lợi ích quan sát được**; đây không phải bằng chứng −4% sẽ tối ưu ngoài mẫu hoặc khi khớp lệnh thật.

Trong cấu hình thử nghiệm, Dynamic **không chỉ** có START ATR ×0,6 và TRAIL ATR ×0,8: còn có sàn giữ **87,5% mức lãi đỉnh khi MFE dưới 5%**, sàn cũ không hạ, rồi ARM 7% với trail 2,5% theo đỉnh. Đây là ba cơ chế dưới/trên ARM phối hợp; tỷ lệ giữ 87,5% là **mức sàn lý thuyết**, không bảo đảm PnL thực nhận bằng 87,5% MFE vì T+2, gap và giá khớp.

## Dynamic qua một lệnh thật: VIX-06

Cấu hình nghiên cứu của lệnh: mua **13,5** ngày 21/08/2026; SL lệnh đầu **−4%** tương ứng **12,96**; Dynamic ON; START ×0,6; TRAIL ATR ×0,8; giữ 87,5% lãi đỉnh tới khi MFE đạt 5%; ARM 7%; sau ARM trail 2,5%; AUTO bán 100%.

1. Khi bước sang phiên 24/08, backend chỉ dùng các nến **ngày đã đóng tới 21/08**. ATR14 tính được **0,5559 đơn vị giá**; giá đóng 21/08 là **13,5**, nên `ATR% = 0,5559 / 13,5 = 4,118%`. `T−1` ở đây nghĩa là phiên giao dịch đã đóng gần nhất, **không phải ATR trừ 1** và không phải mức lãi của lệnh.
2. START ×0,6 tạo ngưỡng `4,118% × 0,6 = 2,471%`. Từ giá mua 13,5, giá cao nhất phải đạt khoảng **13,834** thì các cách bảo vệ dưới ARM mới bắt đầu. Sáng 24/08 VIX mở 14,0 nên đã vượt ngưỡng.
3. TRAIL ATR ×0,8 cho khoảng lùi `4,118% × 0,8 = 3,294%` tính từ giá cao nhất. Khi VIX từng lên **14,15**, cách ATR cho mức bảo vệ khoảng **13,684**.
4. Cùng tại đỉnh 14,15, lệnh từng lãi 4,815%. Cách giữ 87,5% cho mức bảo vệ `13,5 + (14,15 − 13,5) × 87,5% = 14,06875`. Backend lấy mức cao hơn, tức **14,06875**.
5. Sau đó giá lên 14,20 rồi 14,25, MFE đã vượt 5%. Công thức giữ 87,5% ngừng nâng thêm, mức **14,06875 vẫn được lưu**; mức ATR lúc đó thấp hơn nên không thay thế nó. Lệnh chưa đạt ARM 7%.
6. Giá quay xuống chạm mức bảo vệ trong khi cổ còn bị khóa T+2 nên chưa bán được. Đến lúc bán được, backtest khớp **13,95**, thấp hơn mức bảo vệ. Đây là lý do “giữ 87,5%” không đồng nghĩa thực nhận đúng 87,5% MFE.

Nếu AUTO đổi thành ALERT thì cùng phép tính chỉ ghi log/gửi Telegram, không đặt lệnh. Nếu Dynamic OFF thì bốn cấu hình dưới ARM không tác động; SL, E và PROTECT sau ARM vẫn độc lập.

## Rà soát backend/UI

- Backend live và backtest cùng gọi một công thức `protect_level`. Bốn công tắc START ATR, TRAIL ATR, GIỮ LÃI và MỐC GIỮ ĐẾN đã được truyền qua settings, quyết định, pending T+2, báo cáo, bảng dashboard, Telegram và execution metadata. Cấu hình cũ không có các khóa mới sẽ mặc định ON để không âm thầm đổi hành vi.
- UI hiện hành vẫn tách E khỏi Phase 2 ENTRY BUY; E chỉ còn EMA SELL + RSI. Nhánh E sớm trong lần nghiên cứu này không còn trong code.
- Trong PROTECT, START + TRAIL được gom thành nhóm **ATR14 · 1D · phiên trước**; GIỮ LÃI + GIỮ ĐẾN được gom thành nhóm **GIỮ LÃI THEO ĐỈNH**. Thẻ PROTECT ở màn hình chính hiển thị preview theo đúng mã đang chọn: ATR%, ngưỡng START, khoảng LÙI ATR và tỷ lệ bán. `KL BÁN KHI CHẠM 100%` nghĩa là bán sạch khối lượng đang giữ.
- GIỮ LÃI áp dụng lên **phần lãi từ giá mua**, không nhân trực tiếp giá đỉnh. Ví dụ mua 10, đỉnh 13 thì lãi đỉnh là 3; giữ 87,5% tạo mức bảo vệ `10 + 3 × 87,5% = 12,625`. Khi giá giảm về mức này và cổ đã bán được, AUTO bán theo tỷ lệ KL BÁN KHI CHẠM.
- Nút **SO SÁNH 4 MODE PROTECT** tạo đủ AUTO OFF, AUTO ON, ALERT OFF và ALERT ON độc lập. ALERT chỉ thông báo; không tạo fill PROTECT.
- Không sửa settings tài khoản trong các lượt backtest/read-only. Cấu hình nghiên cứu trong báo cáo không đồng nghĩa đã bật trên PAPER/REAL.
- Kiểm thử cuối cùng phải qua toàn bộ `tests_v2`, `compileall` và `git diff --check` trước commit. REPLAY vẫn có hai giới hạn mô hình: không có order book/trượt giá/thanh khoản thật và không biết thứ tự high/low bên trong cùng một nến.

## Đoạn gửi sếp

> “Chúng tôi thử bốn chế độ trên 7 mã với cùng cách mua và mức cắt lỗ 3%. Dynamic là phần tự bảo vệ lợi nhuận khi thị trường dao động; không phải cách mua mới. Chế độ tự bán có Dynamic chốt được **562 triệu**, cao hơn chế độ tự bán không Dynamic (**367 triệu**). Phần lãi cao nhất quan sát **sau khi cổ được phép bán** là **852 triệu**; hệ thống thu được khoảng **66%**. Còn mức 1.114 triệu xuất hiện trong thời gian T+2 chưa bán được, nên chỉ để tham khảo, không cộng vào 852 triệu. Chế độ chỉ cảnh báo không tự bán, dù thấy MFE sau T+2 cao hơn, chỉ chốt được **86 triệu** bởi các rule thoát khác. Đây là backtest, chưa bật cấu hình này trên tài khoản; cần thử ngoài mẫu và PAPER trước khi dùng thật.”

18 file Excel backtest cũ trong workspace (6 báo cáo hiện hành, 12 bản `.artifacts`) đã bị xóa trực tiếp trước lượt chạy; các Excel nhật ký `signal_log` không bị xóa. Dữ liệu OHLC/cache không bị xóa. Kết quả tính trong phiên này không tạo Excel mới.

## Cập nhật Dynamic: bốn công tắc riêng

Sau báo cáo bốn mode ở trên, UI vận hành và UI backtest đã bổ sung ON/OFF riêng cho START ATR, TRAIL ATR, GIỮ LÃI ĐỈNH và mốc GIỮ ĐẾN MFE. Mặc định các công tắc ON để cấu hình cũ cho cùng kết quả; không tự sửa settings tài khoản. START OFF bỏ điều kiện phải đạt ATR trước khi bảo vệ, nhưng vẫn đợi lệnh từng có lãi >0. TRAIL ATR OFF bỏ riêng mức bảo vệ theo ATR. GIỮ LÃI OFF bỏ riêng công thức giữ phần lãi cao nhất. Mốc GIỮ ĐẾN OFF cho công thức GIỮ LÃI tiếp tục tới ARM thay vì ngừng nâng ở 5%; mức đã khóa không hạ. Nếu cả TRAIL ATR và GIỮ LÃI OFF thì không có Dynamic dưới ARM; SL/E và trail sau ARM vẫn còn. `ATR14(T−1)` nghĩa là ATR Wilder từ nến **ngày đã đóng tới phiên giao dịch trước**, không phải ATR trừ 1 hay biến động của lệnh đang giữ.

Đối chiếu đọc-only `save=False` trên đúng bảy cohort và cấu hình SL −4%/E sớm −1% của bản 599,53 triệu; chỉ đổi công tắc mốc GIỮ ĐẾN:

| Cách dùng GIỮ LÃI | PnL tổng (triệu VND) | MFE bán được sau T+2 (triệu VND) | Thu/MFE |
|---|---:|---:|---:|
| Mốc 5% ON | 599,53 | 915,77 | 65,47% |
| Mốc 5% OFF → giữ tới ARM 7% | 466,33 | 701,95 | 66,43% |

Trên mẫu này, giữ tới 7% làm PnL giảm **133,19 triệu** và MFE bán được giảm **213,82 triệu** vì đường thoát và các lệnh về sau thay đổi; tỷ lệ thu/MFE tăng nhẹ nhưng không bù được PnL tuyệt đối. Điều đó giải thích vì sao **chưa tự đổi mặc định nghiên cứu từ 5% sang 7%**. Mốc 5% vẫn chỉ là tham số đã chọn trên chính dữ liệu thử, chưa được chứng minh tối ưu ngoài mẫu. Cả hai lượt không tạo Excel hoặc thay đổi settings tài khoản.
