# Money Hunter — nghiệm thu bản sửa 10/10/2026

Nghiệm thu nghiệp vụ offline: **đạt**, 1959 tests, 0 failures, 0 errors,
0 skipped. Trong đó có 53 trường hợp regression độc lập mới. Không còn lỗi
chặn đã tái hiện trong đợt review này sau sửa.

## Các lỗi đã sửa và đối chứng

| Luồng | Kết quả sau sửa |
|---|---|
| Sửa BUY/SELL đang chạy | Kiểm tra order detail mới, tiền gồm phí, Priority, phần đã khớp và cổ bán được trước PUT. BUY 300 cổ giá 50.000 với hạn mức 10 triệu bị chặn; sửa hợp lệ vẫn gửi PUT tới broker giả. |
| SL khi PROTECT đang bán một phần | Giữ lệnh đã gửi và chỉ đặt phần chưa được phủ. Có 300 cổ, PROTECT đang bán 100: SL gửi thêm 200. Đánh giá lại không gửi trùng. UNKNOWN vẫn giữ reservation. |
| Broker điều chỉnh phí nhiều lần | Phí 1.500 → 3.000 → 1.500, kể cả poll lặp và restart: phí cuối 1.500; không cộng lại lượng khớp. |
| Phí SELL đợt trước đến muộn | Giữ broker ID và progress của đợt đã khớp; nhận phí muộn cả trước và sau đợt tiếp theo, không đổi lifecycle của đợt hiện tại. |
| Telegram CLOSED gửi thất bại | Lưu notice bền vững cùng transaction; retry theo khoảng 60 giây hiện có, kể cả sau restart. |

Regression mới nằm trong
[`test_review_regressions.py`](../tests/test_review_regressions.py).
Ngoài các lỗi trên, các đối chứng mới kiểm tra giá cũ/sai mã/mất kết nối,
EMA/RSI bằng phép tính tham chiếu riêng, giờ mua, BUY thêm, biên tiền và phí,
cổ bán được, timeout/UNKNOWN, fill từng phần, poll lặp, crash và restart.
Lệnh hợp lệ có POST/PUT thật qua adapter DNSE tới `_request` giả;
trường hợp bị chặn được kiểm tra không có broker write.

## Lệnh nghiệm thu

```powershell
& '.\ckvnvenv\Scripts\python.exe' support/tools/run_offline.py support/tests -q --tb=short --disable-warnings -p no:cacheprovider --junitxml=.artifacts/independent_review_20261010/full_fix_results.xml
```

Kết quả chạy: `1959 passed in 281.78s`.
Runner chặn kết nối mạng, không nạp dotenv và dùng runtime tạm.
Không đặt lệnh, gửi Telegram hoặc sửa runtime tài khoản thật.
Log và JUnit tại `.artifacts/independent_review_20261010/full_fix_*`
được giữ ở máy review, không đưa vào Git.

## Đối chiếu VPS

Kết quả áp dụng cho code trong commit chứa tài liệu này. VPS chạy bản cũ
chưa có các sửa lỗi này. Đối chiếu `git rev-parse HEAD` sau cập nhật và mở lại
app theo hướng dẫn [vận hành](VAN_HANH.md).

Phần tích hợp chưa được chạy trong phiên này: ACK/timeout, semantics sửa/hủy,
fill/fee/settlement và stream giá của broker thật. Kết quả nghiệm thu code
không phải bằng chứng VPS đã chạy cùng phiên bản hoặc chiến lược sẽ có lãi.
