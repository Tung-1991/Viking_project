# Đối chiếu bộ đếm EMA IDC với API DNSE — 13/10/2026

Đọc `GET /price/ohlc` (`1D`, `1`) và `GET /price/IDC/trades` (`boardId=G1`)
qua DNSE, không gửi lệnh hoặc thay đổi tài khoản. Tính EMA 3/6 theo cùng công thức
`SessionCrossService._marks`: các nến ngày trước phiên được giữ cố định, giá khớp
hiện tại thay giá đóng tạm của nến ngày đang chạy. Chỉ đếm khi dấu của EMA3−EMA6
đổi từ âm sang dương hoặc ngược lại; dấu bằng là trung tính.

| Phiên | Ngưỡng giá EMA3=EMA6 | Khoảng giá khớp | Cắt trong toàn bộ giao dịch khớp |
| --- | ---: | ---: | ---: |
| 08/10 | 33.12705 | 34.9–35.6 | 0 |
| 09/10 | 33.53562 | 34.4–35.6 | 0 |
| 12/10 | 33.74788 | 34.5–35.6 | 0 |
| 13/10 đến 14:30 | 34.11685 | 33.9–35.3 | 27 lên, 27 xuống |

Ngày 13/10, giá 34.1 nằm dưới ngưỡng, 34.2 nằm trên; nhiều giao dịch đổi qua
lại giữa hai giá này. Lấy **giá khớp cuối mỗi khoảng 2 giây** cho 19 lên, 19 xuống.
Nếu lần xuống đầu (11:25:27) là mẫu nền trước khi bắt đầu đếm thì còn 19 lên,
18 xuống, đúng hình chụp. Lấy giá cuối nến một phút chỉ có 4 lên, 4 xuống.
Do daemon đọc giá WebSocket khoảng mỗi giây và không lưu mọi mẫu đã thấy,
dữ liệu giao dịch khớp qua API giải thích được con số nhưng **chưa chứng minh
chính xác chuỗi 19/18 mà VM đã quan sát**. Cần `rule_state.json` và lịch sử mẫu
WebSocket/TRACE của chính VM để xác nhận mốc khởi tạo và từng lần đổi phía.

Nếu bộ nhớ EMA của VM thật sự khởi tạo 08/10 và quan sát đến 13/10 thì phạm vi
theo lịch là bốn phiên: 08, 09, 12, 13/10. Tiến trình app chạy từ 08/10 không tự
chứng minh bộ nhớ EMA khởi tạo cùng ngày: đổi cặp EMA, đổi REALTIME/CLOSED hoặc
thêm lại mã sẽ lập mốc mới. Tooltip hiện cả `started_at` và mốc quét trước.

`VA QUÉT TRƯỚC` với X phiên đã được sửa để lấy X phiên **trước mốc bắt đầu theo
dõi mã**, kể cả bật công tắc sau nhiều ngày. Khóa cache quét có phiên bản mới,
buộc quét lại state cũ từng lấy trước ngày bật, giữ nguyên bộ đếm quan sát trực
tiếp và ID giao cắt đã dùng.

Preview hiện đếm theo phạm vi: `0P` chỉ phiên hôm nay; `XP` gồm hôm nay và X phiên
giao dịch trước đó, bỏ ngày nghỉ. Ví dụ `37 CẮT - 3P` là 37 lần trong bốn phiên,
không phải 37 lần mỗi phiên. Hint ghi ngày đầu và ngày cuối. Bộ đếm ngày lưu qua
restart; dữ liệu cũ chỉ có tổng tích lũy không được suy thành số cắt của một phạm vi
hẹp hơn. Phạm vi đếm không thay đổi thời hạn và việc sử dụng tín hiệu VA.

Gọi lại API sau phiên: 08/10 có 220 nến phút, 09/10 có 225, 12/10 có 222;
low mỗi phiên đều cao hơn ngưỡng EMA3=EMA6 tương ứng nên ba phiên trước không có
giao cắt. Ngày 13/10 có 222 nến phút và 4 lên + 4 xuống theo close phút; đây không
phải chuỗi tick VM, nên không dùng 8 lần này để thay bộ đếm 37 của VM.
Hai phạm vi cùng 37 là hợp lý khi phần ba phiên trước đã xác minh bằng 0.
Nếu mã chỉ bắt đầu theo dõi hôm nay và chưa quét trước, preview XP phải báo thiếu
dữ liệu, không lấy số hôm nay làm tổng của XP. Nút ⇄ chỉ đổi góc xem; mode thực
thi vẫn lấy từ RULE. VA giữ lần cắt tới khi dùng; X=3 hết hạn cuối phiên thứ ba,
tính cả phiên cắt (cắt 13/10 thì hết hạn lúc đóng phiên 15/10).
