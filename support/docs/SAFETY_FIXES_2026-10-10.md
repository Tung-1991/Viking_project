# Money Hunter — sửa lỗi tín hiệu và luồng giao dịch, 10/10/2026

Bản sửa trên nền `90dac55`, gồm các sửa đổi tín hiệu đã có trong working tree
và bảy lỗi nghiệp vụ được tái hiện trong đợt audit tiếp theo. Kiểm thử đọc code
và chạy assertion trên dữ liệu giả; không dùng kết luận chat làm bằng chứng.

## Bảy lỗi nghiệp vụ đã sửa

| ID | Tình huống trước sửa và ảnh hưởng | Hành vi sau sửa | Code |
|---|---|---|---|
| BF-01 | Broker báo khớp nhưng thiếu/giá khớp bằng 0. Queue tiến lên còn ledger bỏ fill; bản ghi đầy đủ sau restart không bù được vị thế. | Giữ UNKNOWN, broker ID và reservation; chưa tiến quantity/cost. Đủ giá thì đối soát đúng một lần, không POST lại. Nhật ký phí chỉ ghi phần đã hạch toán. Phản hồi Filled thiếu quantity vẫn có baseline notional/phí nhất quán. Queue cũ đã tiến fill nhưng notional 0 chuyển UNKNOWN và chặn BUY chờ đối soát, không đoán hoặc replay có nguy cơ đếm trùng. | `trading/execution.py`, `trading/orders.py` |
| BF-02 | NAV 100 triệu, cap 50 triệu, giữ 40 triệu; hai BUY cùng snapshot mỗi lệnh 10 triệu có thể nâng danh mục lên 60 triệu. | Mỗi BUY tính lại vốn với các reservation đã lưu; tính vốn và lưu intent cùng transaction. Guard kiểm tra lại trước gửi ngay cả khi Priority tắt; giá trần giữ biên cho MARKET REAL. | `dashboard/actions.py`, `rules/planner.py`, `trading/portfolio.py` |
| BF-03 | Fill đã xác nhận 10 triệu nhưng broker positions chưa cập nhật; cap 10 triệu vẫn cấp thêm vốn. | Ghép quantity broker và durable cycle, chỉ cộng phần còn thiếu. So khớp theo mã/gói, không cộng trùng phần đã có; BOT và MANUAL cùng giữ vốn Priority đã dùng, kể cả cùng mã khác gói. Có quantity nhưng thiếu giá thì vẫn giữ giá trị durable fill; giá thị trường 0 dùng giá vốn hợp lệ. | `trading/portfolio.py` |
| BF-04 | Claim E SELL thành công nhưng ghi intent lỗi; restart vẫn coi tín hiệu đã xử lý, không có SELL. | Claim, hủy exit cũ đủ điều kiện và ghi intent cùng commit/rollback. Quyết định bị từ chối hoặc trùng tín hiệu giữ exit cũ. | `rules/planner.py` |
| BF-05 | Exception trong consumer thoát callback trước đăng ký poll tiếp; daemon RUNNING nhưng quyết định SL/TP/E mới không tới queue. | Poll đăng ký lại trong finally, báo lỗi và thử lại. Tạm chặn BUY ở guard/handoff đến khi một vòng xử lý thành công; tiếp tục đối soát/exit. | `dashboard/actions.py`, `dashboard/window.py` |
| BF-06 | Sửa quantity bị broker từ chối chắc chắn nhưng REPLACE_PENDING/requested_replace không được gỡ; UI mất quyền sửa/hủy lệnh gốc. | Gỡ yêu cầu sửa bị từ chối, khôi phục trạng thái lệnh gốc; giữ UNKNOWN để đối soát. Không hồi sinh lệnh đã khớp trong lúc yêu cầu sửa đang chạy. | `dashboard/actions.py`, `trading/orders.py` |
| BF-07 | External fill tăng quantity nhưng thiếu phí làm mất baseline; phí về sau cộng trùng, hoàn phí bị kẹp về 0. | Giữ riêng baseline fee/tax; áp dụng delta âm/dương cùng quantity. Báo cáo phí giữ dấu hoàn tiền và nhận cả các lần hiệu chỉnh lặp. Dữ liệu cũ chỉ có tổng phí chờ đủ hai thành phần trước khi tách. | `trading/execution.py`, `storage.py` |

Các mã chặn vốn/lỗi poll có nhãn tiếng Việt trong UI. Các lỗi và biến thể trên
được giữ thành **69 ca regression/đối chứng** trong
[test_business_flow_safety_regressions.py](../tests/test_business_flow_safety_regressions.py).
Trong đó có toàn bộ 27 probe audit ban đầu; 11 assertion từng thất bại đều qua
sau sửa. Ca batch yêu cầu thực sự gửi một BUY hợp lệ, tránh kết quả xanh do chặn
tất cả lệnh. Ca snapshot kiểm tra cả thiếu, cập nhật một phần, đầy đủ và khác gói.

Kiểm tra độc lập thêm biến thể dữ liệu cũ và valuation; **9/9 probe độc lập qua**
trên diff cuối, các ca tái hiện cũng
được đưa vào regression. Bản sửa không tự ghi lại lịch sử tài khoản thật.
Nếu bản cũ đã lưu quantity khớp với notional 0, trạng thái yêu cầu đối soát giữ
nguyên ledger và đầy đủ reservation; BUY của sổ đó bị chặn. Cần đối chiếu
ledger với bằng chứng broker trước khi sửa dữ liệu, vì bản ghi tổng khớp tiếp
theo không chứng minh phần nào trước đó đã được hạch toán. Không reset runtime.
Guard cũng phát hiện quantity queue vượt quantity mua/bán đã ghi trong cycle,
kể cả notional đã được snapshot sau đó cập nhật thành số dương. Merge vốn
Priority giữ tối thiểu giá vốn fill đã xác nhận khi broker trả giá vốn thấp hơn.

## Các sửa đổi tín hiệu đi kèm

- Bộ lọc giờ mua/xác nhận không ghi đè SELL/ARM/ALERT của vị thế.
- Bỏ mẫu phút cũ khi nguồn giá gián đoạn; chờ mẫu mới hoàn tất trước ENTRY/E
  kỹ thuật, vẫn xét SL/TP/PROTECT bằng giá mới.
- Candidate BUY thêm đã xác nhận được xét lại các guard mà không đòi giao cắt
  thứ hai; thời điểm giao cắt giữ nguyên theo mẫu đã hoàn tất.
- Telegram technical BUY/MẤT BUY dùng outbox, chỉ hoàn tất dedup khi gửi thành
  công; retry/restart vẫn giữ thứ tự thông báo và kiểm tra destination/settings.
- CSV/Excel chống lặp theo ID sự kiện sau lỗi ghi/restart; lưu thêm trạng thái,
  giá và thời gian mẫu cho UI/TRACE/chuẩn hóa.

Regression chính:
[test_signal_safety_regressions.py](../tests/test_signal_safety_regressions.py),
`test_indicator_startup.py`, `test_indicator_preview.py`, `test_signal_trace.py`,
`test_telegram_buy_lost.py`.

## Kiểm chứng

Runner [run_offline.py](../tools/run_offline.py) tắt dotenv, bỏ credentials DNSE/
Telegram khỏi tiến trình, chặn socket và dùng runtime tạm. Broker/Telegram giả;
không mở app giao dịch thật, không đọc hoặc sửa runtime tài khoản thật.

Replay liên tục kiểm tra BUY 300 CP → khớp 100 → snapshot thiếu giá → restart
→ khớp thêm → lỗi ghi ledger trước commit → replay durable inbox sau restart
→ chờ T+2 → SELL 100 timeout → đối soát bằng request tag → SELL 200 còn lại
→ restart/đối soát lặp. Kỳ vọng cuối: CLOSED, mua/bán 300 CP, phí **59.155đ**,
PnL **640.845đ**; ledger và báo cáo khớp nhau, chỉ ba lần gửi broker giả.

```powershell
.\ckvnvenv\Scripts\python.exe support\tools\run_offline.py support\tests -q -p no:cacheprovider --disable-warnings --tb=short
.\ckvnvenv\Scripts\python.exe -m compileall -q viking_v2 support\tests support\tools
.\ckvnvenv\Scripts\python.exe -m pip check
git diff --check
```

Kết quả bản chốt: **1.893 passed trong 255,87 giây**, không failure/error/skip;
gồm 69 ca business-flow và các regression tín hiệu đi kèm. Nhóm vốn/regression
cuối: **188 passed**; kiểm tra độc lập: **9/9 passed**. `compileall`, `pip check`
và `git diff --check` qua. Source/test giữ nguyên trong lượt full suite cuối.

XML `fix-frozen-final.xml` và `fix-capital-final.xml` cùng fingerprint source
`fix-final-manifest.json` nằm tại `.artifacts/business-flow-audit/` cục bộ;
không đưa runtime hoặc artifacts lên Git. Kiểm chứng offline bao phủ các
assertion đã nêu; không phải xác nhận execution, độ trễ endpoints hay slippage
broker thật, cũng không khẳng định mọi tổ hợp ngoài các ca đã kiểm tra đều hết lỗi.
