# Money Hunter

Ứng dụng theo dõi và giao dịch CKCS qua DNSE, có REAL/PAPER, rule EMA/RSI,
quản lý vị thế, Telegram và backtest.

Mở `START_SYSTEM.bat` trên Windows:

| Mục | Chức năng |
| --- | --- |
| 1 | Mở Money Hunter |
| 2 | Cập nhật source |
| 3 | Nạp preset VA |
| 4 | Cài đặt/sửa môi trường |
| 5 | Chẩn đoán: môi trường, log, dữ liệu local |
| 0 | Thoát |

Hướng dẫn: [vận hành](support/docs/VAN_HANH.md), [preset](support/presets/README.md).
Preview START nằm trong ô PROTECT; `PREVIEW` là ngưỡng của lệnh nháp,
`ĐÃ BẬT` chỉ xuất hiện khi vị thế được theo dõi và đã đạt điều kiện DYNAMIC.

Hiện vẫn cập nhật từ repo Viking theo lựa chọn của Ngài. URL nằm trong
`support/repository.json`; repo Money Hunter private sẽ đổi khi Ngài chỉ định.
Source dùng package `viking_v2` để giữ tương thích đường dẫn và dữ liệu;
runtime/API/token/venv thuộc máy cài, không đưa lên Git.

Kiểm thử offline:

```powershell
.\ckvnvenv\Scripts\python.exe support/tools/run_offline.py support/tests -q --disable-warnings --tb=short
```
