# Viking — ghi chú repo cho AI/dev

Hướng dẫn gửi đối tác: [VAN_HANH.md](VAN_HANH.md). Code là nguồn sự thật;
setting đang chạy nằm riêng theo tài khoản, không suy ra từ ảnh/backtest cũ.
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
danh sách/hạn mức/P1/max mã, giữ kết nối/rule khác/runtime; yêu cầu app/daemon dừng.
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
- Priority vốn riêng OFF: giữ phần NAV × P1 / max_positions mỗi mã. ON: hạn mức
  gồm phí × sử dụng%; tiền để dành/chưa phân bổ không cho mã khác mượn. Cash/room P1,
  no-compound vẫn chặn; FORCE 100 không phá cap. MARKET/ATO/ATC dự phòng giá trần,
  LO giá giới hạn. Giảm cap không tự bán/top-up.
- Preview chỉ báo chọn `decisions_by_mode`; AUTO tính lại từ snapshot đúng sổ và
  P1/OVERRIDE qua `PortfolioContextBuilder.build(budget_only=True)`, không cần decision
  BUY, không đổi cooldown/vị thế. Thiếu dữ liệu không đoán vốn. CHƯA LƯU chưa áp dụng.
  Priority preview dùng bản sao setting nháp và cùng bộ tính vốn; không gọi DNSE.
- Refresh tài khoản xử lý lỗi riêng REAL/PAPER; không bỏ kết quả sổ đã đọc được.
  HEALTH kiểm tra cả hai REST client và dùng `quote_is_fresh`, không lấy số request làm mốc mới nhất.
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
