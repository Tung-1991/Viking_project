# VIKING CKCS — FINAL BUSINESS RULES

**Phạm vi:** danh sách cổ phiếu đầu vào mặc định đã đạt FA, bot không xử lý FA.

Nguyên tắc chung là **xác nhận xu hướng rồi mới hành động**, không cố bắt đáy hay bán đúng đỉnh. Các con số bên dưới là **Default Parameter**, operator có thể điều chỉnh mà không làm thay đổi Business Rule.

---

## Phase 1 — Xác định trạng thái thị trường

Dùng **VNINDEX khung 1D**, gồm **MA200, Pivot High/Low và Volume** để phân loại thị trường thành:

**Uptrend, Downtrend, Accumulation, Distribution.**

Nếu cấu trúc chưa đủ rõ thì giữ trạng thái trước trong thời gian xác nhận hoặc để **Transition/Unknown**.

Logic chính:

* **Uptrend:** HH + HL, giá chủ yếu trên MA200.
* **Downtrend:** LH + LL, giá chủ yếu dưới MA200.
* **Accumulation:** xuất hiện sau downtrend, không tiếp tục tạo LL rõ, các Pivot dần đi ngang.
* **Distribution:** xuất hiện sau uptrend, không tiếp tục tạo HH rõ, các Pivot dần đi ngang.
* Tối thiểu **2 Pivot cùng loại** có thể tạo trendline sơ bộ; Pivot thứ 3 làm tăng độ tin cậy.
* MA200 dùng xác nhận bối cảnh dài hạn.
* Volume chỉ tăng độ tin cậy, **không bắt buộc quyết định state**.

Tỷ trọng cổ phiếu mặc định theo state:

**Accumulation 60% — Distribution 50% — Uptrend 90% — Downtrend 10%.**

---

## Phase 2 — Điểm mua / bán

Chỉ dùng **EMA3, EMA6 và RSI14** để tạo tín hiệu M/B.

**Mua:** EMA3 vừa cắt lên EMA6 và RSI14 tăng so với phiên trước.

**Bán:** EMA3 vừa cắt xuống EMA6 và RSI14 giảm so với phiên trước.

MA200, Pivot và Trendline thuộc Phase 1, **không tự tạo thêm điểm mua/bán riêng**.

Khung luôn là **1D** nhưng cho phép hai chế độ:

* **Realtime 1D — default:** nến hôm nay đang chạy được cập nhật liên tục và có thể phát M/B trong phiên.
* **Closed 1D:** chỉ dùng nến đã đóng.

Volume hiện tại **không tham gia điều kiện M/B**.

Một mã chỉ được có **một position mở**, không averaging, pyramiding hay mua bổ sung khi đang giữ hàng.

---

## Phase 3 — Vốn, SL và quản lý thoát vị thế

Tối đa mặc định **5 mã**. Tổng vốn được phép sử dụng không vượt tỷ trọng cổ phiếu do Phase 1 quy định.

Mỗi mã có vốn riêng và **không compound lợi nhuận**. Ví dụ vốn ban đầu 100 triệu:

* Lời thành 108 triệu → lần sau vẫn tối đa 100 triệu.
* Lỗ còn 97 triệu → lần sau tối đa 97 triệu.

### Stop Loss / Re-entry

Lệnh đầu của một chu kỳ dùng **SL -3% realtime**.

Nếu lệnh đó thua và sau này xuất hiện M mới thì được **re-entry với SL -2.1%**.

Khi xuất hiện một lệnh WIN, chu kỳ re-entry kết thúc và lần mua mới sau đó quay lại **SL -3%**.

Nếu một mã có **3 lệnh lỗ liên tiếp** thì khóa mua mới và cảnh báo operator. Một lệnh WIN xen giữa sẽ reset bộ đếm loss về 0.

### Exit Management

Exit Management là nhóm quản lý việc **bảo vệ lợi nhuận và thoát position**, mặc định **ON**.

Bên trong gồm các cơ chế có thể bật/tắt độc lập. **Stop Loss nằm ngoài nhóm này và vẫn xử lý riêng.**

#### Price Protection

Dùng biến động giá để bảo vệ phần lợi nhuận đã có.

**Tier 1 — Normal Protection**

Khi position từng đạt **+7%**, bắt đầu theo dõi peak lợi nhuận.

Nếu lợi nhuận giảm **3 điểm phần trăm từ peak**, bán **1/3 phần vị thế còn lại**.

Phần còn lại tiếp tục giữ và vẫn được quản lý bởi Indicator Exit.

**Tier 2 — High-Profit Protection**

Chỉ kích hoạt sau khi position từng đạt **+20%**.

Sau đó theo dõi **Highest Close trên 1D**. Nếu giá đóng cửa giảm **5% từ Highest Close**, bán **1/3 phần vị thế còn lại**.

Dùng giá đóng cửa thay vì intraday để tránh rung lắc mạnh làm mất hàng trong cổ đang chạy trend.

#### Indicator Exit

Dùng tín hiệu B của Phase 2 để quyết định thoát position:

**EMA3 cắt xuống EMA6 + RSI14 giảm so với phiên trước → bán toàn bộ phần position còn lại.**

Indicator Exit vẫn hoạt động dù Price Protection chưa trigger hay đã bán một phần trước đó.

Ví dụ:

**Đạt +7% → tụt 3 điểm % → Price Protection bán 1/3 → giữ phần còn lại → xuất hiện B → Indicator Exit bán hết phần còn lại.**

Nếu tín hiệu **B xuất hiện trước Price Protection**, bot bán toàn bộ ngay theo Indicator Exit, không cần chờ giá đạt điều kiện bảo vệ lợi nhuận.

Cách tổ chức này giữ đúng ý trong tài liệu VA: bảo vệ lợi nhuận bằng cách bán từng phần nhưng phần còn lại vẫn tiếp tục chạy theo EMA/RSI cho tới khi xu hướng đảo chiều được xác nhận.

### Whipsaw Guard

Dùng để xử lý trường hợp EMA3/EMA6 cắt lên xuống liên tục khiến bot bị “nhay”.

Nếu số crossover vượt **N lần trong X phiên**, bot cảnh báo và tạm khóa BUY/Re-entry mới của mã. Position đang giữ vẫn tiếp tục được quản lý bình thường.

**N và X chưa khóa cứng**, phải backtest rồi operator điều chỉnh.

### Corporate Action

Operator quản lý thủ công từng mã.

Trước ngày chốt quyền thì thoát position; sau sự kiện chỉ mua lại khi Phase 2 xuất hiện **M mới**.

---

## Future — chưa triển khai

**Margin:** để riêng cho giai đoạn sau vì còn liên quan leverage, margin call, pha loãng và corporate action.

**Liquidity Cap 5%:** khi triển khai, khối lượng bot mua một mã không được vượt **5% thanh khoản trung bình phiên**. Nếu sức mua dự kiến vượt trần thì chỉ mua tới trần, phần vốn còn lại giữ dự phòng.

---

## Default Parameters

| Nhóm                   | Default                                   |
| ---------------------- | ----------------------------------------- |
| Market                 | VNINDEX 1D                                |
| MA dài hạn             | MA200                                     |
| Pivot                  | Left 3 / Right 3                          |
| Pivot ngang            | ±1%                                       |
| Vùng quanh MA200       | ±1%                                       |
| Confirm Market State   | 3 phiên                                   |
| Volume Confirmation    | OFF                                       |
| Volume Average         | 20 phiên                                  |
| High / Low Volume      | ≥150% / <80% average                      |
| Exposure               | Acc 60% / Dist 50% / Up 90% / Down 10%    |
| Entry / Exit Signal    | EMA3 / EMA6 / RSI14                       |
| Signal Mode            | Realtime 1D                               |
| Maximum Positions      | 5 mã                                      |
| Initial SL             | -3%                                       |
| Re-entry SL            | -2.1%                                     |
| Lock Symbol            | 3 loss liên tiếp                          |
| Exit Management        | ON                                        |
| Price Protection       | ON                                        |
| Normal Protection      | +7%, giveback 3 điểm %, bán 1/3           |
| High-Profit Protection | +20%, Highest Close giảm 5%, bán 1/3      |
| Indicator Exit         | ON — B signal → bán toàn bộ phần còn lại  |
| Whipsaw Guard          | ON — N crossover / X phiên, chưa chốt N/X |
| Margin                 | Future                                    |
| Liquidity Cap 5%       | Future                                    |

**Chốt nguyên tắc:** Business Rule bám theo phương pháp VA; các giá trị số là cấu hình vận hành. Operator được phép thay đổi parameter theo backtest/thị trường mà không làm thay đổi logic của ba Phase.

Tôi thấy cấu trúc này **dễ code hơn bản dùng nhiều chữ TSL**:

**Exit Management**
→ Price Protection
→ Indicator Exit

Trong **Price Protection** mới có **Normal + High-Profit**. Như vậy không còn câu hỏi “có 2 hay 3 TSL”, trong khi nghiệp vụ vẫn giữ nguyên.
