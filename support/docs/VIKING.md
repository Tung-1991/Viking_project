# Viking V2 — tài liệu hợp nhất

Tài liệu này là nguồn tham chiếu duy nhất cho kiến trúc, cấu hình PAPER/REAL,
vận hành và kết quả backtest đã chốt. Phần **Hiện hành** mô tả code đang chạy;
phần **Lịch sử** chỉ lưu bằng chứng nghiên cứu và không được dùng làm setting.

Hướng dẫn triển khai và bằng chứng kiểm chứng nằm ngay trong tài liệu này.
Không duy trì báo cáo audit theo ngày hoặc một hướng dẫn go-live riêng.

## 1. Phạm vi và nguồn cấu hình

Viking V2 là ứng dụng giao dịch chứng khoán cơ sở Việt Nam kết nối DNSE, gồm
PAPER/REAL, watchlist, rule BUY/EXIT, quản lý lệnh và vị thế, Telegram, backtest
và giao diện CustomTkinter. `START_SYSTEM.bat` cung cấp menu vận hành Windows;
mục khởi động chạy `python -m viking_v2.main`.

### Menu khởi động/cập nhật Windows

Mở `START_SYSTEM.bat` tại root repo:

1. **Rà soát/cài package:** kiểm tra/đặt múi giờ Windows UTC+7
   (`SE Asia Standard Time`), tạo `ckvnvenv` nếu chưa có, cài đúng bản trong
   `requirements.txt`, chạy `pip check`, thử import Tk/thư viện/DLL và kiểm tra
   cú pháp source. Cần Python 3.12 hoặc 3.13 **x64 có Tcl/Tk** đã cài; không tự
   cài Python hệ thống hoặc ghi đè venv hỏng. Nếu Windows từ chối đổi múi giờ,
   chạy BAT bằng **Run as administrator** hoặc tự đặt UTC+7 trong Settings.
2. **Khởi động:** làm sạch màn hình rồi mở app. Đóng app bình thường quay lại
   menu; thoát lỗi thử mở lại sau 10 giây, chọn `M` để về menu thay vì thử lại.
3. **Kiểm tra/cập nhật code Git:** fetch upstream của branch hiện tại. Có bản
   mới thì sao lưu `viking_v2/runtime` và `viking_v2/.env` vào
   `.artifacts/update-backups/<thời điểm_ID>/`, cập nhật fast-forward đúng
   commit đã kiểm tra, rồi cài/kiểm tra package. Chọn `2` để mở lại app.
0. **Thoát.**

Đóng app trước khi cài package/cập nhật; script từ chối nếu app/daemon của
venv này còn chạy, có sửa source chưa commit hoặc commit local riêng. Không
reset/clean Git, không xóa dữ liệu, không tự rollback hoặc bật app sau lỗi.
`cls` chỉ xóa màn hình. `.env`, runtime, venv và backup được `.gitignore` loại
khỏi Git; cập nhật code không đồng bộ settings UI giữa hai máy. Cần Git đã
cài, branch có upstream và máy truy cập được remote. Commit/push code tại máy
phát triển trước để VPS nhận được bản mới. VPS đang dùng launcher cũ cần lấy
bản menu này một lần trước khi dùng mục cập nhật. Script chỉ đổi múi giờ khi
chưa đúng; không sửa giờ UTC, dịch vụ NTP hoặc tự cài VC++ Redistributable.
Thử import giúp phát hiện lỗi nạp DLL mà `pip check` không kiểm tra được;
nếu Windows báo thiếu runtime, cài VC++ v14 x64 từ Microsoft rồi chạy lại mục 1.

Nguồn cấu hình theo thứ tự ưu tiên:

1. `viking_v2/config.py::DEFAULT_RULE_PARAMETERS` là bộ mặc định duy nhất.
2. UI lưu cấu hình tài khoản vào
   `viking_v2/runtime/accounts/<ACCOUNT_ID>/settings.json`; JSON ghi đè default.
3. Backtest lưu riêng tại `viking_v2/runtime/backtest/settings.json`, không tự
   sửa PAPER/REAL.
4. `StaticRuleParameters` cũng lấy trực tiếp từ default trên; parity test sẽ lỗi
   nếu hai đường nạp cấu hình lệch nhau.

### PNL và phí trên panel

Trong `KẾT NỐI → DNSE → THỐNG KÊ PANEL`, chế độ mặc định `THEO NGÀY` chỉ cộng
PNL của các trade đóng và phí phát sinh trong kỳ hiện tại. `GIỜ CHỐT NGÀY`
(mặc định `00:00`, giờ Việt Nam) quyết định lúc tổng hợp kỳ cũ vào
`daily_stats.json` rồi mở kỳ mới; restart app trước giờ chốt không reset số.
Nếu app tắt qua giờ chốt, lần mở sau sẽ thực hiện rollover còn thiếu. Chế độ
`TỪ LẦN RESET` cộng dồn từ mốc operator bấm `↻`. Nút này reset chung PNL/phí,
BUY pause và loss cooldown của mode đang xem nhưng không sửa tiền, vị thế,
trade cycle hoặc lịch sử.

### Bộ lọc volume VN100

Tab `MÃ CK` có tiện ích `LỌC VOLUME VN100` độc lập với rule và daemon.
Người dùng tự nhập số mã cần quét (1–100), số kết quả cần lấy, chu kỳ 5/10
phiên, ngưỡng và hướng tăng/giảm. Tập quét lấy từ đầu rổ VN100 đã sắp theo vốn
hóa giảm dần; ngày cập nhật rổ được hiển thị ngay trên popup.

Scanner dùng nến ngày DNSE đã đóng, so sánh volume trung bình N phiên gần nhất
với N phiên liền trước và không dùng phiên hiện tại khi chưa đóng. Kết quả chỉ
được hiển thị trong popup. Nút `XUẤT EXCEL` ghi các dòng đang hiển thị vào
`viking_v2/runtime/exports/vn100_volume_YYYYMMDD_HHMMSS.xlsx`; nút
`THAY WATCHLIST` tự xuất bản sao danh sách cũ rồi replace watchlist bằng đúng
các dòng đang hiển thị.

Watchlist có thể xuất/nhập sheet `WATCHLIST`; import replace toàn bộ danh sách.
Mã đã có vị thế hoặc lệnh chưa hoàn tất vẫn được daemon quản lý đến khi kết
thúc nhưng không được BUY lại nếu không còn trong watchlist.

## 2. Rule hiện hành

| Nhóm | Cấu hình chốt |
|---|---|
| Phase 1 | MA200; Pivot 3/3; sai số Pivot 1%; vùng MA 1%; xác nhận 3 phiên; Volume phase 1 OFF |
| Tỷ trọng | UPTREND 90%; ACCUMULATION 60%; DISTRIBUTION 50%; DOWNTREND 10% |
| Entry | EMA 3/6 + RSI14; mua từ 14:00; REALTIME/TICK; Volume BUY OFF; xác nhận BUY phút OFF |
| Vốn BOT | Tối đa 5 position BOT; không compound; tối thiểu 100 cổ |
| Khóa BUY | 3 LOSS trong 24 giờ; Whipsaw ON 3 lần/7 phiên; dừng BUY BOT 15 phút sau SELL tay |
| Phí mặc định | Mua 0,045%; bán 0,045%; thuế bán 0,1% |
| SL | ON; lệnh đầu −3,5%; vào lại −2,5%; bán 100% |
| TP | Giá trị 7% nhưng mode TP mặc định OFF |
| PROTECT | AUTO; Dynamic ON; START ATR14(T−1) ×0,55; TRAIL ×0,8; giữ 90% lãi đỉnh tới MFE 5%; ARM 7%; trail sau ARM 2,5%; bán 100%; REPEAT OFF; reset sàn T+2 OFF |
| E | EMA SELL 3/6 + RSI14 giảm; ALERT; không đặt lệnh; Telegram bật/tắt riêng |
| T+2 | RECHECK: cổ về, điều kiện thoát còn đúng mới bán; điều kiện mất thì hủy yêu cầu cũ |

Mode BOT mặc định là `PROTECT + E`; TP không gắn vào trade. Không có E sớm,
E LOSS hay FAILED RECOVERY trong backend hiện hành.

### Dynamic dưới ARM

- `ATR14` dùng Wilder ATR từ các nến ngày đã đóng tới phiên giao dịch trước.
- `START ×0,55`: bắt đầu bảo vệ khi MFE đạt 55% ATR14.
- `TRAIL ×0,8`: sàn ATR cách đỉnh một khoảng bằng 80% ATR14.
- `Giữ 90% tới MFE 5%`: dưới mốc 5%, bot cố khóa 90% đoạn lãi từ giá mua
  đến đỉnh. Sàn đã khóa không hạ; ATR vẫn có thể nâng sàn.
- Khi MFE đạt ARM 7%, bot chuyển sang bám đỉnh với khoảng lùi 2,5%.
- AUTO chạm sàn thì bán; ALERT chỉ ghi nhận. Công tắc Telegram không thay đổi
  hành vi giao dịch.

Ví dụ mua 100, ATR14 là 4%: START tại MFE 2,2%. Nếu đỉnh đạt 104 thì sàn ATR
là 100,8, còn sàn giữ 90% là 103,6; backend chọn mức cao hơn là 103,6.

### E và T+2

- E gốc dùng EMA SELL nhanh cắt xuống EMA SELL chậm và RSI14 giảm.
- `ALERT` ghi signal log/UI và tùy chọn Telegram, không tạo SELL.
- `AUTO` dùng cùng tín hiệu nhưng bán 100%; `OFF` bỏ E cho trade đó.
- Nếu tín hiệu bán xuất hiện khi cổ chưa bán được, `RECHECK` kiểm tra lại lúc
  cổ về. Bot không bán chỉ vì trước đó từng có tín hiệu.

## 3. Hàng đợi, slot và lệnh ngoài Viking

- Watchlist không phải hàng đợi bí mật. Mỗi chu kỳ chỉ xét BUY đang hợp lệ; đủ
  quota thì ghi `MAX_POSITIONS`, không giữ tín hiệu cũ để tự nhảy vào sau.
- `BOT OFF` chặn tạo và gửi BUY BOT. SELL quản lý vị thế và MANUAL vẫn chạy.
- Lệnh `PAUSED` vẫn giữ slot, tránh mã khác lấp chỗ ngoài ý muốn.
- Quota tách `BOT x/5 · MANUAL y · TỔNG z`. MANUAL/EXTERNAL không chiếm slot
  BOT nhưng vẫn dùng tiền thật và room danh mục.
- `MÃ PRIORITY` là tập con của watchlist: được xếp BUY trước và chỉ bypass
  `MAX_POSITIONS`; cash, exposure và mọi entry guard khác vẫn áp dụng. Vị thế
  Priority không chiếm quota BOT thường.
- REAL dùng gói tiền mặt đã xác minh (`initialRate=1`), không tự đổi sang margin.
  Đơn vị quản lý là toàn bộ Deal đã chọn của đúng tài khoản/mã/gói; mua thêm
  hoặc bán một phần trong Deal đó cập nhật cùng một trade, giữ chế độ quản lý.
  Deal khác chưa chọn không được tự nhận quản lý.
- Nên BUY/SELL qua Viking. Giao dịch trực tiếp trên DNSE trong Deal đang quản lý
  được đối soát từ số cổ, giá vốn và lịch sử lệnh. Dữ liệu khớp thì cập nhật
  trade, hủy SELL chưa gửi liên quan sau SELL tay, khóa BUY BOT theo setting
  và báo Telegram theo setting; dữ liệu mơ hồ tạo `RECONCILE_REQUIRED`, không
  tự đoán giá khớp. ID lệnh Viking được lưu riêng, không chỉ dựa vào remark.
- Chọn PAPER không dừng quản lý REAL và ngược lại; cả hai sổ vẫn đối soát
  lệnh/vị thế. Chỉ sổ đang chọn được tạo BUY BOT mới khi BOT ON.
- Queue/trade/rule/PAPER dùng chung `trading.sqlite3` của tài khoản. JSON cũ
  được nhập một lần, giữ bản sao trong `migration-backup`, không còn là nguồn
  trạng thái tài chính hiện hành. Settings vẫn JSON; không thay bộ ngưỡng rule.
- Restart bỏ BUY BOT chưa gửi và candidate chờ xác nhận; giữ MANUAL ngoài giờ,
  SELL chờ cổ về, lệnh đã gửi/UNKNOWN, vị thế và cooldown. Cooldown lưu giờ hết
  hạn; chốt ngày/reset thống kê vẫn theo nghiệp vụ cũ, không reset tài khoản.
- MANUAL ngoài giờ có hiệu lực tới hết phiên đủ điều kiện đầu tiên. Không mua
  ATO khi option tắt. Lệnh đã gửi không bị TTL nội bộ làm mất theo dõi;
  broker hủy/hết hạn thì không tự đặt lại. RECHECK sau chờ T+2/OTP cần quyết
  định mới; thiếu dữ liệu thì chờ, điều kiện mất thì hủy, KEEP giữ yêu cầu.

## 4. Telegram

PAPER và REAL dùng chung bảng Telegram. Các nhóm tin có công tắc riêng:

- BOT BUY đã xếp lệnh, gom theo cửa sổ phút cấu hình.
- Vị thế BOT đã đóng.
- PROTECT chạm mức.
- E EXIT ALERT.
- TÍN HIỆU: BUY đã được ghi log nhưng không thành lệnh, ví dụ BOT OFF, đủ slot,
  khóa mua, thiếu vốn hoặc broker từ chối.
- Lịch nghỉ và chốt quyền.
- SELL trên DNSE app.
- Lỗi hệ thống: daemon, lịch giao dịch, DNSE API/WS, làm mới tài khoản hoặc xử lý lệnh.

Các nhóm có thể lặp có cooldown riêng. `PROTECT AUTO` vẫn bán khi Telegram OFF;
bật tin PROTECT chỉ thêm thông báo. App không phát lại tín hiệu đã hết hiệu lực;
tín hiệu còn hợp lệ khi app mở lại được coi là quan sát hiện tại và chống trùng
bằng state lưu trên ổ đĩa.

## 5. Checklist chạy PAPER

1. Chọn đúng tài khoản và `PAPER`; kiểm tra `HEALTH`, daemon, DNSE, WS và API.
2. Kiểm tra watchlist, sàn dự phòng và `BOT OFF` trước khi sửa rule.
3. Xác nhận SL −3,5%/−2,5%, TP OFF, PROTECT AUTO Dynamic ON, E ALERT.
4. Kiểm tra quota BOT, MANUAL, tiền mặt và các lệnh đang cache/paused.
5. Cấu hình Telegram chỉ cho các nhóm tin cần nhận, rồi dùng `GỬI THỬ`.
6. Bật BOT khi sẵn sàng. Theo dõi preview P1/P2/P3 và bảng lệnh, không chỉ
   nhìn thông báo Telegram.
7. Khi SELL tay, chờ hết thời gian khóa BUY hoặc chủ động kiểm tra trạng thái
   trước khi bật lại nghiệp vụ mới.

## 6. Kết quả backtest chốt ngày 21/09/2026

Phạm vi: CTS, FTS, SHS, SSI, VIX, VND và DPM chạy độc lập; mỗi cohort vốn
1 tỷ đồng, exposure 60%, tối đa một vị thế. Entry EMA 3/6 + RSI14 sau 14:00;
SL −3,5%/−2,5%; PROTECT theo cấu hình chốt; E ALERT. PnL gồm phí và thuế mô
phỏng. Hai cột MFE là hai phase của cùng lệnh, không cộng với nhau.

### Đối chứng SL vào lại

| SL vào lại | PnL (triệu) | MFE trong T+2 | MFE sau T+2 | PnL/MFE sau T+2 | Lệnh | Lệnh SL | Max DD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| −2,1% | 602,26 | 1.082,03 | 948,28 | **63,51%** | 55 | 9 | 6,52% |
| **−2,5%** | **607,63** | **1.083,98** | **961,14** | 63,22% | **54** | **8** | **5,91%** |
| −3,0% | 599,21 | 1.082,95 | 959,94 | 62,42% | 54 | 8 | 6,45% |

Chọn −2,5% vì PnL và MFE sau T+2 cao nhất, ít hơn một lệnh SL so với −2,1%
và Max DD thấp nhất trong ba mức.

### So sánh bốn mode

| PROTECT | PnL | MFE trong T+2 | MFE sau T+2 | PnL/MFE sau T+2 | Lệnh | Đóng/mở | WIN/LOSS | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AUTO · Dynamic OFF | 191,21 | 896,17 | **1.242,66** | 15,39% | 49 | 46/3 | 18/28 | 9,51% |
| **AUTO · Dynamic ON** | **607,63** | **1.083,98** | 961,14 | **63,22%** | 54 | 53/1 | 36/17 | **5,91%** |
| ALERT · Dynamic OFF | −299,25 | 508,00 | 1.150,36 | −26,01% | 27 | 23/4 | 0/23 | 23,84% |
| ALERT · Dynamic ON | −299,25 | 508,00 | 1.150,36 | −26,01% | 27 | 23/4 | 0/23 | 23,84% |

ALERT không tạo fill nên hai dòng ALERT có kết quả tiền giống nhau. MFE cao ở
mode giữ lệnh lâu không đồng nghĩa chiến lược tốt hơn nếu không hiện thực hóa
được lợi nhuận.

### Tổng thể theo mã — AUTO Dynamic ON

| Mã | PnL | MFE trong T+2 | MFE sau T+2 | Thu/MFE | Lệnh | Đóng/mở | WIN/LOSS | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CTS | 256,79 | 216,02 | 338,47 | 75,87% | 8 | 8/0 | 7/1 | 2,68% |
| FTS | 45,78 | 127,20 | 128,94 | 35,51% | 8 | 8/0 | 3/5 | 5,91% |
| SHS | 106,60 | 156,97 | 180,34 | 59,11% | 8 | 7/1 | 6/1 | 3,24% |
| SSI | 21,19 | 134,59 | 80,92 | 26,18% | 8 | 8/0 | 4/4 | 5,38% |
| VIX | 73,86 | 182,51 | 73,86 | 100,00% | 7 | 7/0 | 6/1 | 3,14% |
| VND | 88,80 | 182,32 | 144,01 | 61,66% | 10 | 10/0 | 7/3 | 3,77% |
| DPM | 14,62 | 84,37 | 14,62 | 100,00% | 5 | 5/0 | 3/2 | 1,57% |
| **Tổng** | **607,63** | **1.083,98** | **961,14** | **63,22%** | **54** | **53/1** | **36/17** | **5,91%** |

### Theo nhánh thoát

| Nhánh | Lệnh | PnL | MFE trong T+2 | MFE sau T+2 | Thu/MFE | WIN/LOSS |
|---|---:|---:|---:|---:|---:|---:|
| PROTECT | 45 | 792,48 | 1.054,01 | 981,96 | **80,70%** | 36/9 |
| SL | 8 | −165,78 | 19,84 | −30,94 | Không có ý nghĩa | 0/8 |
| Còn mở | 1 | −19,07 | 10,13 | 10,13 | Không dùng | 0/0 |
| **Tổng** | **54** | **607,63** | **1.083,98** | **961,14** | **63,22%** | **36/17** |

PROTECT thu 792,48/981,96 = 80,70% MFE bán được của nhóm nó đóng. Tổng hệ
thống thấp hơn vì tám lệnh SL và một vị thế mở kéo giảm khoảng 184,85 triệu.

Quyết định: chốt START 0,55, SL −3,5%/−2,5%, giữ E ở ALERT, không đưa E sớm
hoặc FAILED RECOVERY vào hệ thống. Bước tiếp theo là PAPER và dữ liệu ngoài mẫu,
không tiếp tục tối ưu trên cùng tập dữ liệu.

## 7. Lịch sử nghiên cứu 18/09/2026 — không còn hiệu lực

Bản cũ dùng SL −3%/−2,1%, START 0,6, giữ 87,5% và có E sớm sau T+2 ở mức
lỗ −1%. AUTO Dynamic ON đạt PnL 562,41 triệu trên 57 lệnh: PROTECT 42 lệnh
+790,06 triệu; E 8 lệnh −74,54 triệu; SL 7 lệnh −153,11 triệu. Bật E sớm chỉ
tăng khoảng 26,20 triệu trên chính mẫu cũ. Nhánh này đã bị xóa khỏi UI/backend
do bằng chứng yếu và làm tăng độ phức tạp.

Kết quả cũ chỉ còn ý nghĩa truy vết quyết định. Chi tiết từng lệnh vẫn tồn tại
trong lịch sử Git; không duy trì một tài liệu vận hành riêng để tránh dùng nhầm.

## 8. Kiến trúc source

Cấu trúc repository:

```text
Viking_project/
├── START_SYSTEM.bat
├── requirements.txt
├── .gitignore
├── viking_v2/              # Package ứng dụng; giữ nguyên đường dẫn runtime
└── support/
    ├── launcher.ps1       # Helper được BAT gọi
    ├── docs/VIKING.md     # Tài liệu hợp nhất
    ├── tests/             # Bộ kiểm thử theo miền nghiệp vụ
    └── tools/             # Runner offline, kiểm tra và đối soát
```

Git theo dõi source, bộ kiểm thử, công cụ vận hành, tài liệu và `.env.example`.
Không đưa `.env` thật, token, settings tài khoản, runtime/database, dữ liệu giá,
log, workbook, backup, virtualenv hoặc kết quả kiểm tra cục bộ lên Git.
Kết quả xuất phục vụ kiểm tra đặt trong `support/output/` hoặc `.artifacts/`.
Đổi bố cục repository không đổi settings hay nghiệp vụ giao dịch.

| Thành phần | Trách nhiệm |
|---|---|
| `config.py` | Default, nạp và chuẩn hóa settings |
| `models.py` | Model lệnh, trade, decision và runtime |
| `connections/` | Adapter DNSE, WebSocket, Telegram và UI kết nối |
| `rules/` | Chỉ báo, BUY, SL, TP, PROTECT, E và state rule |
| `services/` | Daemon, runtime bridge, điều phối BUY/slot và scanner volume độc lập |
| `trading/` | Vốn, settlement, queue, execution và trade state |
| `dashboard/` | UI, preview, bảng, popup và thao tác operator |
| `backtest/` | Dữ liệu lịch sử, replay, mô phỏng, báo cáo và UI |
| `storage.py` | JSON nguyên tử, journal, CSV và thống kê phí |
| `runtime/` | Dữ liệu sinh khi chạy; không phải source, không commit |

Nguyên tắc:

1. PAPER và REAL dùng chung business rule, chỉ adapter thực thi khác nhau.
2. Daemon sinh decision; UI/execution quản lý queue và broker. Telegram lỗi
   không được chặn giao dịch.
3. Công thức chung nằm trong `rules/`, `trading/` hoặc service; UI không tạo
   một bản rule riêng.
4. Backtest tái sử dụng rule/sizing chính; chỉ mô phỏng đường giá, settlement
   và fill lịch sử.
5. SL, TP, PROTECT và E đi qua cùng lifecycle, không bán chồng số lượng.
6. Secret, runtime, log, cache, workbook và virtualenv không commit.

## 9. Test và bảo trì

- `support/tests/` chia theo miền nghiệp vụ, không tạo một file mới cho mỗi bug.
- Test migration cũ được giữ khi nó bảo vệ khả năng đọc settings/state cũ.
- Chỉ xóa test khi code tương ứng đã bị xóa hoặc yêu cầu nghiệp vụ bị hủy.
- Trước commit: chạy toàn bộ suite qua runner offline, `compileall` và
  `git diff --check`. Không chạy pytest money-path trực tiếp với credentials thật.
- Không refactor lớn `dashboard/actions.py` ngay trước phiên PAPER; về sau nên
  tách dần quản lý lệnh, thông báo và polling/runtime.
- Scanner VN100 là tiện ích đọc DNSE và xuất Excel; chỉ replace watchlist khi
  operator xác nhận, không tham gia rule BUY và không chạy trong daemon.

Thư mục ngoài source: `ckvnvenv/` là môi trường Python;
`viking_v2/dnse_api/` là tài liệu DNSE cục bộ; `.pytest_cache/`, `__pycache__/`
và workbook sinh ra có thể xóa, không được coi là source.

Chạy từ root repo:

```powershell
.\ckvnvenv\Scripts\python.exe support\tools\run_offline.py
.\ckvnvenv\Scripts\python.exe -m compileall -q viking_v2 support
.\ckvnvenv\Scripts\python.exe -m pip check
git diff --check
```

Runner không load `.env`, bỏ DNSE/Telegram environment, chặn kết nối socket và
đặt runtime trong thư mục tạm. Mặc định chạy toàn bộ `support/tests/`, gồm cả
các regression trước đây nằm trong thư mục audit. Không gửi lệnh hoặc Telegram.

## 10. Triển khai và cập nhật VPS Windows

Máy phát triển và VPS dùng tài khoản DNSE riêng. Git chỉ cập nhật source;
settings thực tế, `.env` và runtime giữ tại đúng máy, không chép giữa tài khoản.

### Cài lần đầu

1. Cài Git và Python 3.13 x64 có pip, Launcher và Tcl/Tk. Phiên bản đã chọn
   cho VPS ngày 06/10/2026: Python 3.13.16; không nâng cả bộ thư viện lên latest.
2. Clone repo, mở `START_SYSTEM.bat`, chọn **1** rồi **2**.
3. Nhập API KEY/SECRET của tài khoản VPS, chọn đúng account và xác thực OTP.
4. Kiểm tra giá mới, tiền, danh mục và lệnh chờ so với DNSE. App bắt đầu với
   BUY BOT OFF. OFF không dừng SELL đang quản lý hoặc MANUAL đã yêu cầu.
5. Lệnh REAL đầu tiên do operator chủ động đặt với vốn chấp nhận rủi ro;
   đối chiếu ID, số khớp/còn lại, giá vốn và phí trước khi bật BUY BOT.

Launcher không tự khởi động app sau reboot Windows; operator cần mở BAT và
chọn 2. Ngắt kết nối RDP không phải đăng xuất; đăng xuất/reboot đóng ứng dụng.

### Cập nhật những lần sau

Đóng app, chọn **3**, chờ backup/cập nhật/kiểm tra package xong rồi chọn **2**.
Backup nằm trong `.artifacts/update-backups/`, gồm runtime, `.env` và revision.
Không ghi đè dữ liệu VPS bằng runtime hoặc settings của máy phát triển.

SQLite phải nằm trên ổ local. Backup khi app/daemon đã dừng, sao lưu cả thư mục
account; backup khi chạy phải dùng SQLite backup API, không copy riêng DB bỏ WAL.
Sau khi có fills mới, không rollback về code JSON/runtime backup cũ rồi giao
dịch tiếp; giữ DB hiện hành và đối soát DNSE trước phục hồi. Không xóa lock file
để vượt instance đang chạy: khóa do OS giữ, tự nhả khi process chết.

### Công cụ kiểm tra/đối soát khi cần

```powershell
.\ckvnvenv\Scripts\python.exe support\tools\preflight.py --state-only --migration-preview
.\ckvnvenv\Scripts\python.exe support\tools\preflight.py
.\ckvnvenv\Scripts\python.exe support\tools\recover_order.py --intent <UUID> --broker-order <DNSE_ID> --account <ACCOUNT_ID>
```

Preflight kiểm tra cấu trúc local hoặc GET DNSE; migration preview chỉ chạy trên
bản sao tạm. Không POST/PUT/DELETE. Token chưa sẵn sàng nghĩa là cần nhập OTP.
Recover mặc định chỉ preview: dùng ID lệnh đã xác minh trên DNSE để đối chiếu
account/mã/chiều/gói/khối lượng/thời điểm với intent UNKNOWN. Chỉ khi đúng và app
đã đóng mới thêm `--apply` để gắn ID/đối soát fill vào DB local. Không tự đặt lại,
không đoán UNKNOWN là chưa gửi. Công cụ không chạy tự động cùng app.

### Bằng chứng kiểm chứng

Trước khi gom cấu trúc ngày 06/10/2026, suite offline đạt **518/518 passed**,
gồm 23 test launcher, 75 money-path safety và 15 regression audit. `pip check`,
`compileall`, `git diff --check` và khởi tạo/đóng GUI offline thành công.
Đã có 7 GET DNSE production read-only trong đợt kiểm tra trước; không đặt lệnh
REAL/sandbox hoặc kiểm thử end-to-end trên VPS. Các báo cáo rời đã bỏ khỏi cây
hiện tại; bản gốc vẫn có trong lịch sử Git. Đây là bằng chứng kiểm chứng code,
không phải đảm bảo chiến lược có lãi hoặc không có rủi ro vận hành.

Sau khi gom cấu trúc và cập nhật launcher ngày 06/10/2026: **527/527 test
passed trong 37,84 giây** trên máy phát triển Windows/Python 3.13.14. Đã thử
import thư viện thật qua PowerShell 5.1; các test đổi múi giờ dùng mock,
không đổi timezone máy phát triển. Package vẫn giữ phiên bản đã chốt,
code trong `viking_v2/` và runtime tài khoản không thay đổi trong lượt này.
