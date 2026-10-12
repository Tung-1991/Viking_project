# Lịch sử, tín hiệu và thống kê — kiểm chứng 12/10/2026

Đọc source và chạy kiểm thử mới trên bản sau `835caa2`. Không dùng tài khoản thật, secrets, DNSE/Telegram thật hoặc runtime thật. Tất cả kiểm thử chạy qua `support/tools/run_offline.py`.

## Hiển thị trùng nguồn và giờ

Tình huống: app ghi broker ID `236032` lúc 14:09:48 rồi khớp 600 HDB lúc 14:09:49; lịch sử DNSE trả `20261012_236032`, side `NB`, giờ UTC 07:09:48.

Trước sửa, `_history_popup_groups` đối chiếu ID nguyên chuỗi, nên nguồn DNSE thành một giao dịch khác; `NB` không được nhận là BUY và giờ có offset bị bỏ offset khi sắp xếp. Đây là lỗi trình bày, không phải bằng chứng bot đặt ba lệnh.

Sau sửa, `dashboard/trade_history.py` đối chiếu theo ngày + broker ID + mã + chiều. Một giao dịch có một dòng BUY tổng hợp; xổ dòng đó để xem CSV gửi, CSV khớp và DNSE đối chiếu. Tất cả giờ hiển thị theo Việt Nam. ID được dùng lại ngày khác hoặc khác mã/chiều vẫn giữ riêng.

Khớp từng phần dùng lượng khớp tăng thêm và lượng đặt của từng lệnh broker. Snapshot broker tích lũy được dùng để đối chiếu, không cộng thêm vào fill của sổ app. Giá khớp thực được bổ sung vào audit cho bản ghi mới. Phí điều chỉnh không tạo thêm cổ hoặc đổi trạng thái khớp; nguồn broker đã khớp mà sổ app chưa ghi thì hiện `CHỜ ĐỐI SOÁT`.

## Phí và tiền

`PHÍ MUA ~16.3K` là ước tính theo mức phí có sẵn. Lịch sử và PHÍ NGÀY dùng số đã ghi nhận, không lấy ước tính cộng thành phí thật. Broker chưa cung cấp phí thì hiện `CHỜ DNSE`. Broker xác nhận phí bằng 0 thì hiện 0; trường hợp xác nhận muộn được ghi một sự kiện audit có số tiền/fill bằng 0, không sửa số cổ hoặc PNL.

CSV/Excel bổ sung các cột `fee_known`, `tax_known`, `fill_price_vnd`, `fill_delta_quantity`, `broker_quantity`; schema cũ được nâng cấp và giữ bản ghi cũ. Số 0 của bản ghi cũ không được tự khẳng định là miễn phí nếu thiếu thông tin xác nhận.

Số lớn trên dashboard ưu tiên NAV trực tiếp từ broker và ghi `TỔNG TÀI SẢN / NAV`. Nếu API không có NAV nhưng đủ `totalCash`, `totalDebt`, số cổ đang giữ và giá thị trường, UI tính `tổng tiền + giá trị cổ đang giữ − tổng nợ` và ghi `NAV ƯỚC TÍNH · API`. Cổ chờ về cũng thuộc tài sản; không dùng giá mua thay giá thị trường. Thiếu dữ liệu định giá thì hiện `TIỀN KHẢ DỤNG · DNSE`; thiếu cả tiền thì hiện `CHỜ TÀI KHOẢN`, không tự điền 0. Dòng tài khoản ghi riêng tiền khả dụng.

Kaiser đã xác nhận **49.951.941đ là NAV trên ứng dụng DNSE**, khác số 50 triệu trong ảnh Money Hunter. Chưa có các số thành phần trong snapshot thật nên chưa xác nhận phép tính của UI sẽ khớp con số này. Chênh 48.059đ không được tự coi là phí. NAV ước tính có thể khác DNSE do các thành phần tài sản/điều chỉnh khác hoặc thời điểm snapshot. Rê chuột lên số lớn/tiêu đề để xem tiền khả dụng, tổng tiền, tổng nợ, giá trị cổ và nguồn NAV; chỉ đọc snapshot đã có, không gọi thêm API. Hàm mới chỉ phục vụ hiển thị, không cấp vốn cho rule, sửa ledger hoặc sửa phí.

SDK chính thức DNSE mô tả `total_cash`, `total_debt`, `available_cash` trong [StockBalance](https://github.com/dnse-tech/dnse-py/blob/main/src/dnse/models/accounts.py). Đây là căn cứ về các trường dữ liệu; công thức cơ bản của UI là **ước tính**, không khẳng định là công thức NAV đầy đủ của DNSE.

## Mốc chốt và Excel

Mặc định/preset chốt thống kê lúc **15:00 Việt Nam**, giữ số của ngày vừa chốt qua restart và qua đêm đến **09:00 sáng hôm sau**. Phí cập nhật muộn vẫn hiện trong ngày đang giữ; không cộng lại vào ngày kế tiếp. Reset tay vẫn có hiệu lực ngay. Lựa chọn cũ 00:00 được chuyển một lần; lịch tùy chọn được giữ, và chọn lại 00:00 trong phiên bản mới vẫn được lưu.

Excel audit ghi ngay từng sự kiện vào file theo tháng, không đợi giờ chốt. Mốc 15:00 là chốt thống kê PNL/phí, không phải trì hoãn ghi Excel. Cơ chế clear cooldown khi chốt kỳ có sẵn được giữ; BLOCK và lịch sử giao dịch không bị xóa.

## Tín hiệu và UI

Tăng font lịch sử/tín hiệu từ 14 lên 18; ngày/nhóm từ 12 lên 16. Thu gọn cột giờ, bỏ lặp mã ở từng dòng con, đưa số sự kiện/mẫu vào ô riêng. Phiên RSI trước và trạng thái thị trường vẫn có trong chi tiết dòng chọn và Excel. Dữ liệu gốc, thứ tự thời gian, số mẫu, chuẩn hóa và xuất Excel được giữ.

Đối chứng 4 mã × 16 mốc từ 14:00 đến 14:30 = **64 mẫu mỗi sổ**. `SLOT 1/4` là số chỗ dùng của toàn danh mục. `ĐỊNH KỲ / Chưa đạt` vẫn là ảnh chụp điều kiện, không phải lệnh mua. Native UI được kiểm tra ở 100% và 150%, cả chế độ chuẩn hóa và bảng gốc; trạng thái xổ/thu lệnh được giữ khi làm mới.

## Kiểm thử

25 kiểm thử mới tại `support/tests/test_trade_history_display.py`: ví dụ HDB, ID theo ngày, UTC/GMT+7, khớp từng phần, phí thiếu/0/dương/điều chỉnh, xác nhận 0 sau khớp, số dư/NAV, chốt 15:00 qua restart/đêm, reset tay, preset migration và native UI.

Lượt hồi quy cuối: **871 passed, 101.83 giây**, ghi tại `.artifacts/history_display_20261012/verified.xml`; ảnh UI dùng dữ liệu giả ở cùng thư mục. Bao gồm execution kernel, phí/fill, vốn/slot/UNKNOWN, log/TRACE, settings/preset, UI/startup và chặn tín hiệu Telegram sau phiên. Kiểm tra lại ảnh native sau khi tab chuyển xong: 2 passed, tại `native.xml`. Không chạy kiểm thử nào với broker thật.

Bổ sung sau khi xác nhận số DNSE là NAV: **324 passed, 76.14 giây**, tại `.artifacts/nav_display_20261012/verified.xml`. Bao gồm 17 ca mới trong `support/tests/test_account_money_display.py`, lịch sử/preview, UI/startup, refresh, vốn và Priority. Kiểm tra NAV trực tiếp, NAV ước tính, tiền khả dụng riêng, 0 so với thiếu dữ liệu, giá VND/giá bảng, cổ T+, thiếu giá thị trường và thay snapshot cũ bằng snapshot mới. Các số tiền/nợ dùng để tính ra 49.951.941đ trong fixture là dữ liệu giả kiểm tra số học, không phải payload tài khoản thật. Không cộng số test của hai lượt vì có ca trùng nhau.
