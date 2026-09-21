# Viking — rule EXIT chốt cho PAPER (21/09/2026)

## Rule vận hành đề xuất

| Nhánh | Cấu hình chốt | Hành động |
|---|---|---|
| SL | ON; lệnh đầu −3,5%, vào lại −2,1% | Bán 100% khi chạm SL |
| TP | OFF | Không tham gia |
| PROTECT | AUTO; Dynamic ON; START ATR ×0,45; TRAIL ATR ×0,8; giữ 90% lãi đỉnh tới MFE 5%; ARM 7%; trail sau ARM 2,5%; SELL 100%; REPEAT OFF | Nhánh thoát chính |
| E gốc | ON; EMA SELL 3/6 + RSI14 giảm; MODE ALERT | Chỉ log/UI/Telegram, không đặt lệnh |
| E sớm | Đã xoá | Không còn UI/backend |
| T+2 | RECHECK; reset sàn T+2 OFF | Tín hiệu trong thời gian chưa bán được chỉ được ghi nhận; lúc cổ về phải kiểm tra lại điều kiện bán |

`OFF / ALERT / AUTO` của E được hiểu như sau: OFF là bỏ E khỏi trade; ALERT giữ E làm chỉ báo quan sát; AUTO mới bán 100%. E ALERT không được chặn SL, TP hoặc PROTECT AUTO nếu cùng xuất hiện.

## Backtest bốn mode PROTECT trên backend hiện hành

Phạm vi: bảy cohort CTS, FTS, SHS, SSI, VIX, VND, DPM chạy độc lập, mỗi cohort 1 tỷ đồng, exposure 60%, tối đa một vị thế, Entry EMA 3/6 + RSI sau 14:00, SL −3,5%/−2,1%, E 3/6 + RSI luôn ở ALERT. Chạy REPLAY `save=False`, không tạo Excel và không đổi settings tài khoản. Đây là dữ liệu trong mẫu; tổng không phải PnL của một danh mục chung.

Đơn vị tiền: triệu VND. `MFE trong T+2` và `MFE sau T+2` là hai phase của cùng lệnh, tuyệt đối không cộng hai cột; tỷ lệ thu dùng MFE sau T+2.

| PROTECT | PnL tổng | MFE trong T+2 | MFE sau T+2 | PnL/MFE sau T+2 | Mua / đóng / mở | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| AUTO · Dynamic OFF | 208,23 | 897,63 | 1.230,68 | 16,92% | 50 / 47 / 3 | 9,78% |
| **AUTO · Dynamic ON** | **598,92** | **1.086,09** | **871,67** | **68,71%** | **55 / 54 / 1** | **6,52%** |
| ALERT · Dynamic OFF | −340,12 | 565,60 | 1.134,79 | −29,97% | 31 / 27 / 4 | 23,57% |
| ALERT · Dynamic ON | −340,12 | 565,60 | 1.134,79 | −29,97% | 31 / 27 / 4 | 23,57% |

Hai dòng PROTECT ALERT có PnL/MFE giống nhau vì PROTECT chỉ cảnh báo, E cũng chỉ cảnh báo; vị thế chỉ còn SL để tự thoát. Dynamic ON/OFF chỉ làm số cảnh báo PROTECT khác nhau, không tạo fill.

## Chi tiết mode được chọn: PROTECT AUTO · Dynamic ON

| Mã | PnL | MFE trong T+2 | MFE sau T+2 | Lệnh | E ALERT |
|---|---:|---:|---:|---:|---:|
| CTS | 264,23 | 216,20 | 327,91 | 8 | 0 |
| FTS | 67,85 | 129,48 | 128,71 | 8 | 7 |
| SHS | 76,68 | 156,97 | 132,03 | 8 | 3 |
| SSI | 11,04 | 132,37 | 67,68 | 9 | 4 |
| VIX | 75,59 | 184,26 | 75,59 | 7 | 1 |
| VND | 88,92 | 182,45 | 125,14 | 10 | 3 |
| DPM | 14,62 | 84,37 | 14,62 | 5 | 0 |
| **Tổng** | **598,92** | **1.086,09** | **871,67** | **55** | **18** |

| Trạng thái cuối | Lệnh | PnL | MFE trong T+2 | MFE sau T+2 |
|---|---:|---:|---:|---:|
| PROTECT | 46 | 784,71 | 1.060,66 | 912,65 |
| SL | 8 | −166,71 | 15,30 | −51,11 |
| Còn mở | 1 | −19,07 | 10,13 | 10,13 |
| **Tổng** | **55** | **598,92** | **1.086,09** | **871,67** |

PROTECT thu 784,71/912,65 = **85,99% MFE bán được** trong riêng nhóm nó đóng. Tổng hệ thống chỉ còn 68,71% vì 8 lệnh SL và một vị thế mở kéo kết quả xuống; không nên sửa PROTECT để chữa phần thua vốn thuộc SL.

## Vì sao chốt E ALERT

| E với cùng PROTECT Dynamic | PnL | MFE sau T+2 | PnL/MFE | Lệnh | Max DD |
|---|---:|---:|---:|---:|---:|
| OFF | 598,92 | 871,67 | 68,71% | 55 | 6,52% |
| **ALERT · EMA3/6 + RSI14** | **598,92** | **871,67** | **68,71%** | **55** | **6,52%** |
| AUTO · EMA3/6 + RSI14 | 542,47 | 819,37 | 66,21% | 57 | 7,43% |
| AUTO · EMA4/6 + RSI14 | 590,67 | 842,85 | 70,08% | 57 | 7,61% |

ALERT giữ nguyên đường tiền như E OFF nhưng vẫn thu được 18 quan sát để đánh giá PAPER. AUTO 3/6 làm mất 56,45 triệu PnL so với ALERT; EMA4/6 dù có tỷ lệ thu 70,08% vẫn thấp hơn ALERT 8,25 triệu PnL, thấp hơn 28,81 triệu MFE bán được và drawdown xấu hơn. Vì vậy chưa có lý do dữ liệu đủ mạnh để cho E tự bán.

## Nội dung ngắn gửi nhóm

> Entry giữ nguyên EMA 3/6 + RSI sau 14h. Exit chốt theo ba lớp: SL bắt buộc −3,5%; PROTECT AUTO Dynamic là nhánh chốt lời chính; E gốc EMA3/6 + RSI14 chuyển sang ALERT để theo dõi nhưng không tự bán. Trên 7 cohort chạy độc lập, mode được chọn đạt PnL 598,92 triệu trên MFE bán được sau T+2 là 871,67 triệu, tương đương 68,71%; riêng 46 lệnh do PROTECT đóng thu được khoảng 85,99% MFE của nhóm. E ALERT phát hiện 18 tín hiệu nhưng không đổi PnL, còn cho E AUTO bán làm PnL và MFE thấp hơn. Đây là kết quả trong mẫu; bước tiếp theo là PAPER và đánh giá log E ALERT trước khi cân nhắc AUTO.

## Điểm đưa sang VA để phản biện

1. Không đề nghị VA tối ưu lại Entry; chỉ phản biện EXIT trên cùng 55 lệnh và cùng quy tắc T+2.
2. Yêu cầu VA tách ba nhóm PROTECT, SL và còn mở; không lấy lỗi của SL để làm chặt thêm PROTECT vốn đang thu gần 86% MFE của nhóm.
3. Đề nghị VA đánh giá 18 E ALERT theo câu hỏi phản thực tế: nếu AUTO tại tín hiệu E thì tránh được bao nhiêu lỗ, đồng thời mất bao nhiêu nhịp hồi và MFE về sau.
4. Không nhận đề xuất chỉ làm đẹp tỷ lệ PnL/MFE bằng cách bán sớm khiến cả PnL lẫn MFE co xuống.
5. Mọi phương án mới phải báo cáo PnL, MFE sau T+2, max drawdown, số lệnh và độ trùng Entry; sau đó mới PAPER, chưa đổi REAL.
