# Money Hunter — ghi chú repo cho AI/dev

Hướng dẫn gửi đối tác: [VAN_HANH.md](VAN_HANH.md). Code là nguồn sự thật;
setting đang chạy nằm riêng theo tài khoản, không suy ra từ ảnh/backtest cũ.
Tên hiển thị chung ở `viking_v2/branding.py`; giữ package/repo/runtime cũ để tương thích.
Tài liệu dài/nghiên cứu trước đây còn trong lịch sử Git trước bản cập nhật này.

## Đường đi chính

DNSE → `services/daemon.py` lấy dữ liệu, tạo decision theo từng sổ →
`dashboard/actions.py` điều phối → `rules/planner.py` tạo intent →
`trading/execution.py` gửi/đối soát → `trading/state.py` quản lý vị thế.
UI/Telegram không thay rule.

| Nơi trong viking_v2 | Trách nhiệm |
|---|---|
| `config.py` | Default duy nhất, chuẩn hóa/nạp/lưu settings |
| `rules/business.py`, `entry_filters.py`, `state.py` | EMA/RSI, P1, BUY/thoát, chờ giờ/xác nhận, chống trùng |
| `trading/portfolio.py`, `services/signal_coordinator.py` | Vốn, sizing, giữ slot Priority, xếp BUY |
| `trading/orders.py`, `execution.py`, `state.py`, `storage.py` | Queue, fill, SQLite, quản lý/đối soát |
| `connections/` | DNSE, giá/WS, Telegram và popup kết nối |
| `dashboard/`, `rules/window.py` | UI/preview/settings; không nhân bản thuật toán |
| `backtest/` | Mô phỏng dùng chung rule/sizing, không chứng minh giá khớp/độ trễ LIVE |

`support/launcher.ps1`/`START_SYSTEM.bat`: môi trường, update, start Windows.
BAT 4: `support/presets/VA_4_MA_50M.json` qua `tools/apply_va_preset.py`; chỉ sửa
danh sách/hạn mức/P1/max mã và E AUTO, bổ sung IND_EXIT cho trade BOT mới (không bỏ
mode cũ), giữ kết nối/rule khác/runtime; yêu cầu app/daemon dừng. BAT 2 chỉ update source;
BAT 4 xác nhận nạp preset trên tài khoản máy đích, không đồng bộ settings.json toàn bộ.
Preset VA 50 triệu: MSN/CTS/HDB 15 triệu, IDC 5 triệu, 100%, MAX=1; IDC không mượn cap khác.
`support/tests/`/`support/tools/`: regression offline, preflight, phục hồi có kiểm tra.

## Bất biến cần giữ

- Giá nội bộ là **nghìn đồng**; adapter DNSE chuyển giá lệnh/tài khoản sang đồng.
  Phí settings là %, phép tính dùng tỷ lệ; BUY lô 100, kiểm tra tiền gồm phí.
- REAL/PAPER có queue, vị thế, vốn, cooldown/BLOCK riêng. Cả hai quản lý vị thế;
  chỉ sổ đang chọn tạo BUY BOT mới. OFF không dừng SELL/MANUAL.
- Fire-and-forget: BUY bị chặn ghi lịch sử/chống trùng rồi bỏ, không mua lại khi có slot.
  Chỉ chờ giờ/X phút theo setting; restart bỏ candidate/BUY BOT chưa gửi, giữ MANUAL
  ngoài giờ, SELL chờ cổ, lệnh đã gửi/UNKNOWN, vị thế và cooldown. Không phát lại log.
- Timeout gửi là chưa rõ kết quả, không POST lại trước đối soát. PendingCancel chưa
  phải hủy xong. Fill tích lũy chỉ áp dụng phần tăng mới; queue/vị thế cùng transaction
  SQLite. Khớp từng phần thuộc cùng lệnh/trade, không tự tách sổ.
- SL/TP/E AUTO bán 100%; PROTECT/Dynamic dùng `normal_sell_pct`; E độc lập với ARM.
  MFE là lãi đỉnh từng đạt, không phải PNL đã chốt. REPEAT giữ rule cũ.
  E/M mặc định chỉ gắn cho trade BOT mới, không xóa chế độ của vị thế đã mở.
- SELL dùng sellable DNSE; RECHECK cần điều kiện còn đúng, KEEP giữ yêu cầu.
  Không tự tăng SELL vượt phần quản lý. BLOCK phải mở tay; restart/WIN/↻ không mở.
- Priority giữ **1 slot/mã bên trong** `max_positions`, kể cả chưa mua. Không bypass,
  không tự bán nhường chỗ; mã thường dùng phần slot còn lại.
- Priority vốn riêng OFF: giữ phần NAV × P1 / max_positions mỗi mã. ON: mỗi BUY ≤ hạn mức
  gồm phí × sử dụng%; tổng ≤ min(hạn mức, mỗi BUY × MAX LỆNH). Mặc định MAX=1 giữ nghiệp vụ cũ.
  `TradeCycle.entry_order_ids` đếm BUY BOT khớp; union pending, khớp từng phần chỉ 1, UNKNOWN giữ lượt.
  Chỉ thêm vào vị thế BOT chưa thoát, snapshot đủ cổ, không BUY/SELL đang chờ; cần tín hiệu mới
  và qua bộ lọc BUY. Cùng cycle/giá vốn bình quân/SL/PROTECT; 1 mã vẫn 1 slot. Bán hết đếm lại.
  MFE rebase theo giá vốn mới, không hạ sàn Protect đã lưu. Tiền để dành/chưa chia không cho mã khác mượn. Cash/room P1,
  no-compound vẫn chặn; FORCE 100 không phá cap. MARKET/ATO/ATC dự phòng giá trần,
  LO giá giới hạn. Giảm cap không tự bán/top-up.
- Backtest hiện chỉ hỗ trợ MAX LỆNH=1; MAX>1 phải báo lỗi rõ, không giả kết quả tương đương LIVE.
- Telegram BUY/closed lưu theo book + mã; BUY thêm có ID riêng trong digest, không ghi đè BUY trước.
  Reject BUY thêm không xóa record tổng kết vị thế đang giữ. Delivery thật phụ thuộc token/chat/mạng.
- Preview chỉ báo chọn `decisions_by_mode`; AUTO tính lại từ snapshot đúng sổ và
  P1/OVERRIDE qua `PortfolioContextBuilder.build(budget_only=True)`, không cần decision
  BUY, không đổi cooldown/vị thế. Thiếu dữ liệu không đoán vốn. CHƯA LƯU chưa áp dụng.
  Priority preview dùng bản sao setting nháp và cùng bộ tính vốn; không gọi DNSE.
- Bảng vị thế chọn decision đúng sổ/trade còn mới, TP theo giá trigger thật (không theo
  PNL sau phí); vị thế chưa nhận quản lý không hiện SL tự động. Log UI theo snapshot
  queue đã lưu, fingerprint trạng thái/khớp/còn lại; không gọi broker hoặc phát lại khi restart.
- Refresh tài khoản xử lý lỗi riêng REAL/PAPER; không bỏ kết quả sổ đã đọc được.
  HEALTH kiểm tra cả hai REST client và dùng `quote_is_fresh`, không lấy số request làm mốc mới nhất.
- Secdef dùng chung cache 60 giây trong mỗi client; 429 nghỉ toàn endpoint 60 giây.
  Lỗi tạm giữ dữ liệu tối đa 5 phút, không qua ngày VN; hết hiệu lực thì chờ giá, không báo hết tiền.
- XÓA API bỏ Key/Secret/token ở RAM và .env, nạp lại daemon cùng workspace;
  giữ account/runtime/settings và lệnh DNSE. XÓA TOKEN không xóa API.
- Chỉ quản lý Deal đã chọn của đúng account/mã/gói. Giao dịch ngoài app đối soát được
  thì cập nhật; dữ liệu mơ hồ giữ `RECONCILE_REQUIRED`, không đoán giá khớp.

## Dữ liệu / kiểm tra

Git: source, requirements pin, tests/tools/docs, `.env.example`.
Local: `.env`, `runtime/accounts/<id>/` (settings, SQLite, logs), venv, giá/exports,
`.artifacts/` và backup — **không commit, không chép giữa tài khoản**.
SQLite trên ổ local; backup lúc app/daemon dừng hoặc dùng SQLite backup API.

```powershell
.\ckvnvenv\Scripts\python.exe support\tools\run_offline.py support\tests -q -p no:cacheprovider
.\ckvnvenv\Scripts\python.exe -m compileall -q viking_v2 support
.\ckvnvenv\Scripts\python.exe -m pip check
git diff --check
```

Runner bỏ secrets, chặn mạng, runtime tạm. Không chạy money-path với credentials thật.
Đổi nghiệp vụ phải thống nhất LIVE/PAPER/backtest và thêm regression; không refactor
execution/fill để đổi bố cục. Không gửi lệnh/Telegram khi chỉ audit.
