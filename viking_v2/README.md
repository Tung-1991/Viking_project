# Viking V2 — bản đồ source

Đọc file này trước khi sửa code. V2 được chia theo **tính năng người dùng**, không chia thành các file kỹ thuật vụn.

## Chạy ứng dụng

```powershell
.\ckvnvenv\Scripts\python.exe -m viking_v2.main
```

Đây là lệnh chạy duy nhất. UI tự quản lý daemon; không có mode khởi động phụ.

## Sửa việc gì, mở ở đâu

| Việc cần sửa | Nơi bắt đầu |
|---|---|
| Bố cục panel trái/phải, Preview và Health | `dashboard/panels.py` |
| Click button, BUY, BOT, popup, vòng refresh | `dashboard/actions.py` |
| Bảng lệnh đang chạy và render dữ liệu | `dashboard/tables.py` |
| Khởi tạo cửa sổ/app | `dashboard/window.py` |
| Popup RULE | `rules/window.py` |
| Phase 1–3, tín hiệu M/B và Exit Manager | `rules/business.py` |
| Biến tín hiệu thành yêu cầu đặt lệnh | `rules/planner.py` |
| State riêng của rule | `rules/state.py` |
| Popup KẾT NỐI | `connections/window.py` |
| DNSE REST, PAPER, WebSocket, chữ ký | `connections/dnse/` |
| Telegram | `connections/telegram.py` |
| Hàng đợi/cache lệnh | `trading/orders.py` |
| Gửi, hủy, đối soát và lifecycle lệnh | `trading/execution.py` |
| Vốn, lô chẵn, sức bán và context danh mục | `trading/portfolio.py` |
| Phiên, ngày nghỉ, chốt quyền và market data | `trading/market.py` |
| TradeCycle và state position | `trading/state.py` |
| Process daemon | `services/daemon.py` |
| File bridge, heartbeat và logging | `services/runtime.py` |
| Schema dữ liệu dùng chung | `models.py` |
| Settings, đường dẫn và workspace account | `config.py` |
| JSON/CSV journal | `storage.py` |

## Luồng chính

```text
dashboard -> rules -> trading -> connections/DNSE
     |           \         |
     +------------ services/daemon
                  |
              runtime/account
```

- UI nhận thao tác manual và hiển thị state.
- Rule chỉ tạo decision; planner chuyển decision thành `OrderIntent`.
- Trading sở hữu cache, kiểm tra và lifecycle lệnh.
- DNSE/PAPER là broker adapter, không chứa business rule.
- Daemon sở hữu dữ liệu thị trường, đánh giá rule và heartbeat.

## Dữ liệu và cấu hình

- API Key/Secret: `viking_v2/.env` — không commit. Trading Token mặc định chỉ ở RAM; chỉ ghi `.env` khi người dùng chủ động bật lưu token.
- Cấu hình theo tài khoản: `viking_v2/runtime/accounts/<account>/settings.json`.
- Cache, state, CSV và log: cùng workspace của tài khoản.
- `config.py` là nơi duy nhất định nghĩa đường dẫn và schema settings.
- `models.py` là nơi duy nhất định nghĩa object truyền giữa các khối.

## Quy tắc mở rộng

1. Sửa tính năng hiện hữu trong đúng module ở bảng trên; không tạo `*_new.py`, `*_v2.py`, `helper.py` hoặc wrapper tương thích.
2. Chỉ tạo file mới khi đó là một nhóm nghiệp vụ mới có vòng đời độc lập. Một thay đổi nhỏ không được sinh thêm module.
3. UI không tự tính business rule; rule không gọi widget; DNSE không quyết định chiến lược.
4. Xóa thẳng code V2 hết hạn sau khi đã chuyển dữ liệu và test. Legacy ngoài `viking_v2` được giữ riêng để tham khảo, không được import vào V2.
5. Sau khi đổi cấu trúc hoặc interface, cập nhật bảng “Sửa việc gì, mở ở đâu” trong file này.

## Kiểm tra

```powershell
.\ckvnvenv\Scripts\python.exe -m pytest -q tests_v2
.\ckvnvenv\Scripts\python.exe -m pytest -q
```
