# Viking V2 — rà soát trước giao dịch tiền thật

> **Báo cáo lịch sử, trước bản vá.** Kết luận NO-GO dưới đây áp dụng cho commit
> `97f6db9b068a68b1f6f32a2917bfb808bc30afd2`, không phải source sau cập nhật
> 05/10/2026. Xem [bản vá, kết quả kiểm chứng và hướng dẫn cập nhật](GO_LIVE.md).
> Các test audit hiện đã chuyển sang assertion an toàn sau sửa, không còn
> assertion tái hiện lỗi. Nghiệp vụ chốt sau audit: OFF chỉ chặn BUY BOT;
> quản lý toàn bộ Deal tiền mặt đã chọn, không mặc định chỉ phần mua từ bot.
> Những đề xuất khác nghiệp vụ đã chốt trong báo cáo này không được triển khai.

Ngày kiểm tra: 03/10/2026. Phiên dự kiến: thứ Hai 05/10/2026.
Commit được kiểm tra: `97f6db9b068a68b1f6f32a2917bfb808bc30afd2`.

## Kết luận

**NO-GO cho giao dịch tự động tiền thật trên bản hiện tại.**

Đã xác nhận bằng mô phỏng offline các đường xử lý có thể gửi trùng lệnh, dùng giá sai mã, ghi sai giá vốn, mất vị thế được quản lý và bán vượt phần cổ phiếu của bot. Đây là lỗi vận hành, không phải nhận định về khả năng sinh lời của chiến lược.

Source vận hành, cấu hình tài khoản, token và dữ liệu runtime thật không được chỉnh. Không chạy app thật, không gọi API tài khoản, không gửi lệnh hoặc Telegram. Chỉ thêm bộ kiểm chứng và báo cáo trong thư mục audit này. Việc đọc đặc tả DNSE công khai được thực hiện riêng, không dùng credentials.

## Phạm vi và kết quả kiểm chứng

- Lập danh mục và kiểm tra biên dịch 46 file Python của `viking_v2`; kiểm tra dependency bằng `pip check` không thấy dependency bị hỏng.
- Đọc sâu các luồng có thể tác động tiền: daemon → dữ liệu giá → EMA/RSI/ATR/PROTECT → portfolio → planner → queue → DNSE → fill → trade/rule state; startup/restart, nút OFF, PAPER/REAL, sửa/hủy và đối soát vị thế trong UI.
- Rà các đường liên quan PAPER và backtest/replay, đồng thời chạy toàn bộ 27 module test hiện có. Không đồng nghĩa đã thực hiện end-to-end mọi tương tác GUI hay đọc từng dòng layout với độ sâu như execution.
- Bộ test có sẵn: **382 passed** khi chặn mạng, vô hiệu hóa đọc `.env` và tách runtime khỏi workspace tài khoản thật.
- Bộ audit bổ sung: **15 bài tái hiện hành vi rủi ro**. Các assertion mô tả lỗi hiện tại, nên `passed` ở đây nghĩa là **tái hiện được lỗi**, không phải đã sửa an toàn.
- Chạy chung suite hiện có và bộ audit: **397 passed**. Biên dịch source và `pip check` đều thành công.
- Không kiểm chứng production DNSE, lịch sử khớp thật, crash ở hệ điều hành, broker sandbox, toàn bộ lệnh nhập thủ công trên GUI, hay hiệu quả đầu tư. Không có kết luận “hết edge case”.

## Các lỗi/rủi ro đã xác nhận

P0: nguy cơ tạo thêm giao dịch ngoài ý định, cần chặn trước live. P1: có thể sai giao dịch hoặc mất quản trị vị thế. P2: sai kế toán/đánh giá rủi ro cần xử lý; không phải lỗi giao diện đơn thuần.

### A01 — P0: POST đặt lệnh bị phát lại sau timeout

Vị trí: `viking_v2/connections/dnse/client.py:170`, `:205`, `:502`; `viking_v2/config.py:30`.

`_request` retry mọi HTTP method khi gặp exception, với `HTTP_RETRIES=1`. Đối soát trong `place_order` chỉ diễn ra sau khi retry đã hết. Nếu broker nhận POST đầu nhưng phản hồi bị mất, POST thứ hai đã được gửi trước khi xác định lệnh đầu.

Tái hiện: broker giả nhận lệnh 1 rồi Timeout, nhận lệnh 2 và trả thành công. Quan sát **2 POST, 2 lệnh được chấp nhận, cùng remark**; bot chỉ ghi nhận ID lệnh 2.

Không khẳng định đã thấy DNSE production đặt trùng. Kết luận xác nhận là client phát lại POST mà chưa có bằng chứng broker bảo đảm idempotency. Không được coi `remark` là khóa chống trùng mặc định.

Test hiện có `test_transport_timeout_reconciles_by_remark_without_resend` lại chấp nhận `len(session.calls)==2` khi cả hai request đều là POST; tên test che khuất khoảng hở này.

Hướng sửa cần duyệt: không tự replay mutation sau timeout; ghi UNKNOWN, khóa symbol/quantity, đối soát rồi mới quyết định. Chỉ retry mutation nếu có hợp đồng idempotency được broker xác nhận.

### A02 — P1: request tag không được lưu, lệnh UNKNOWN không tự phục hồi

Vị trí: `viking_v2/trading/execution.py:248`, `:266`; `viking_v2/connections/dnse/client.py:491`; `viking_v2/trading/orders.py:303`.

Execution gửi một bản copy của intent; DNSE adapter gán `request_tag` vào copy. `queue.finish` nhận intent gốc nên tag trong JSON vẫn rỗng. Khi chưa nhận order ID, reconcile không còn khóa để tìm lệnh broker.

Tái hiện: gửi → UNKNOWN → order Filled xuất hiện muộn với đúng tag → tạo ExecutionService mới → reconcile không tìm được lệnh; queue vẫn UNKNOWN, TradeCycle không tồn tại.

Hướng sửa: lưu định danh bền vững trước network hand-off; xác minh broker thật trả lại định danh đó. Thiếu cơ chế correlation tin cậy thì giữ trạng thái cần operator đối soát, không đoán là chưa đặt.

### A03 — P1: crash giữa hai file làm mất fill/vị thế quản lý

Vị trí: `viking_v2/trading/execution.py:266`, `:272`, `:587`, `:601`.

Queue được ghi FILLED/cộng số lượng khớp trước khi TradeState ghi fill. Hai lần ghi không nằm trong cùng transaction; journal cũng được append sau đó.

Tái hiện fault injection: queue đã FILLED rồi `_record_trade_fill` gặp OSError. Sau restart, `reconcile_working` bỏ qua FILLED, TradeCycle vẫn không có. Vị thế thật có thể bị coi là external, không được SL/PROTECT tự quản lý.

Hướng sửa: durable fill ledger và recovery replay idempotent, hoặc SQLite transaction cập nhật queue/trade/rule state cùng một lần commit. Test phải cắt tiến trình tại từng ranh giới ghi.

### A04 — P1: thiếu tick của mã sau có thể dùng tick mã trước

Vị trí: `viking_v2/services/daemon.py:429`.

Trong nhánh phiên live, biến `tick` chỉ được gán nếu `live_tick` có dữ liệu. Nó không được reset khi đổi symbol. Python không có block scope cho biến trong vòng lặp.

Tái hiện nguyên một chu kỳ daemon: FPT trả giá 100, VIX trả None. Kết quả `ticks['VIX']['symbol']=='FPT'`, `decisions['VIX']['symbol']=='FPT'`; rule nhận context FPT hai lần.

Hậu quả: nến/tín hiệu/giá vốn so sánh sai mã; planner sử dụng `decision.symbol` nên có nguy cơ lập lệnh sai mã trong điều kiện phù hợp.

Hướng sửa: tick riêng từng symbol; thiếu dữ liệu phải WAIT với lý do rõ; assert symbol của tick/context/decision/intent khớp nhau ở mỗi boundary.

### A05 — P1: cache giá cũ và quyết định cũ vẫn được dùng để giao dịch

Vị trí: `viking_v2/trading/market.py:416`; `viking_v2/rules/planner.py:52`; `viking_v2/services/daemon.py:183`, `:670`; `viking_v2/dashboard/actions.py:2762`.

- WS hết hạn và REST thất bại: market trả tick cache cũ, không gắn `stale`. Planner chỉ kiểm tra giá dương/flag stale, không kiểm tra tuổi quote.
- Heartbeat chạy thread riêng nên vẫn mới khi vòng tính toán bị kẹt. Exception theo symbol giữ lại decision cũ; `_latest_sell_decision` chỉ xét heartbeat, không xét tuổi decision.

Tái hiện: tick cũ một giờ vẫn được planner chấp nhận tạo BUY 200 CP. Decision SELL mang thời gian năm 2020 vẫn được provider trả về khi heartbeat mới.

Hướng sửa: kiểm tra freshness ở trước network send, không chỉ lúc hiển thị; tách timestamp trade/bid/ask và quyết định; status phải mang execution_mode/cycle ID; lỗi mỗi symbol phải vô hiệu hóa decision cũ. Heartbeat chỉ chứng minh thread sống, không chứng minh dữ liệu hợp lệ.

### A06 — P1: RECHECK chờ T+2 không chặn khi không có quyết định mới

Vị trí: `viking_v2/trading/execution.py:320`, `:409`, `:185`.

Provider trả None → revalidation `continue` → queue vẫn claim được → gửi SELL theo yêu cầu cũ. UI provider có thể trả None khi heartbeat cũ hoặc đang chọn mode khác.

Tái hiện: EM SELL WAITING_SETTLEMENT, RECHECK, broker đã có 100 CP bán được, provider=None → vẫn gửi SELL 100 CP.

Hướng sửa: phân biệt “điều kiện còn đúng”, “không còn đúng”, “không đủ dữ liệu”. Với RECHECK, thiếu dữ liệu phải tiếp tục chờ, không tự đổi thành KEEP.

### A07 — P1: recheck tăng SELL theo tổng tài khoản, bán cả cổ phiếu external

Vị trí: `viking_v2/trading/execution.py:366`, `:377`.

Quantity được tính lại từ tổng broker holding thay vì quantity của TradeCycle. Đây là lệch khỏi giới hạn ownership mà PortfolioContextBuilder áp dụng cho quyết định ban đầu.

Tái hiện: TradeCycle quản lý 100 FPT; tài khoản có 300 FPT gồm 200 mua ngoài; SELL chờ T+2 ban đầu 100, fraction=100%. Khi recheck khớp, intent và lệnh gửi lên thành **300 CP**.

Hướng sửa: cap theo managed open quantity/trade ID, trừ các reservation SELL liên quan; không tự mở rộng vào phần external nếu chưa được operator chọn quản lý.

### A08 — P1: khớp từng phần làm sai giá vốn và ngưỡng SL/PROTECT

Vị trí: `viking_v2/trading/execution.py:589`, `:490`; `viking_v2/models.py:277`.

Reconcile lấy chênh lệch số lượng nhưng vẫn truyền `averagePrice` lũy kế của cả order như giá riêng phần khớp mới.

Tái hiện: 100 CP @100 rồi 100 CP @120; broker báo cumulative average 110. Bot ghi giá vốn **105**, thay vì 110. Portfolio ưu tiên giá vốn TradeCycle nên SL/PnL/PROTECT đều bị ảnh hưởng.

Hướng sửa: tính delta notional từ cumulative quantity × average price, hoặc đọc executions với fill ID và giá riêng từng fill. Đơn vị giá phải được chuẩn hóa ngay tại adapter.

### A09 — P1: PendingCancel bị đánh dấu CANCELLED, bỏ theo dõi fill đến sau

Vị trí: `viking_v2/dashboard/actions.py:1559`; `viking_v2/trading/orders.py:534`; `viking_v2/trading/execution.py:552`.

UI chỉ kiểm tra HTTP/result ok, rồi local queue được đánh dấu terminal CANCELLED bất kể broker còn PendingCancel. Lệnh này không còn được `reconcile_working` theo dõi.

Tái hiện gọi đúng handler hủy của UI với broker giả trả PendingCancel; sau đó order Filled. Bot không ghi nhận fill, TradeCycle không có.

Hướng sửa: CANCEL_PENDING phải tiếp tục reserve và reconcile. Chỉ terminal khi broker xác nhận cuối cùng, đồng thời ghi đủ fill đã khớp trước/trong lúc hủy. Luồng sửa cũng phải đối soát order ID mới thay vì chỉ sửa local quantity/price.

Đặc tả DNSE phiên bản 2026-05-07 có ví dụ response hủy là PendingCancel; đây không chỉ là một trạng thái tự đặt trong mock.

### A10 — P1: JSON atomic write không bảo vệ read–modify–write giữa UI/daemon

Vị trí: `viking_v2/rules/state.py:30`, `:145`; `viking_v2/storage.py:232`.

RLock của RuleStateStore thuộc từng instance/process. Lock của AtomicJSONStore chỉ bảo vệ từng read hoặc write trong cùng process, không toàn bộ read–modify–write và không liên process. UI và daemon cùng ghi rule state; TradeState và PAPER cũng có nhiều đường read/write.

Tái hiện hai writer đọc cùng snapshot: UI lưu khóa BUY 15 phút sau manual SELL; daemon ghi buy confirmation từ snapshot cũ → **khóa BUY biến mất**. Test điều khiển interleaving của hai instance; đây là bằng chứng lost update, chưa phải stress test OS crash.

Hướng sửa: single writer với IPC, hoặc database transaction/process-safe locking trên cả thao tác. Atomic rename tránh JSON viết dở, không tránh mất cập nhật.

### A11 — P1: PAPER hoặc BUY OFF không phải công tắc dừng giao dịch thật

Vị trí: `viking_v2/dashboard/actions.py:2984`, `:2990`; `viking_v2/trading/execution.py:185`.

Execution worker xử lý cả PAPER và REAL mỗi lần; OFF chỉ khóa BOT BUY, không khóa SELL/MANUAL. Việc tiếp tục quản lý vị thế có thể là chủ ý, nhưng mode PAPER không tạo ranh giới “không giao dịch thật”.

Tái hiện handler `_process_orders`: runtime.paper_mode=True và bot_enabled=False, nhưng một EM SELL REAL chờ T+2 vẫn được gửi. Đây là rủi ro vận hành được xác nhận, không tự suy diễn rằng mọi SELL khi OFF đều sai nghiệp vụ.

Hướng xử lý cần Ngài quyết định: tách MUA TỰ ĐỘNG OFF khỏi REAL execution arm và emergency halt; hiển thị rõ tác dụng. Nếu muốn quản trị REAL khi đang xem PAPER thì phải là lựa chọn rõ, không dùng PAPER để thử nghiệm trong cùng workspace có quyền REAL.

### A12 — P1: bấm BUY OFF giữa batch vẫn có thể gửi BUY chưa hand-off

Vị trí: `viking_v2/dashboard/actions.py:2983`; `viking_v2/trading/execution.py:186`, `:205`; `viking_v2/trading/orders.py:177`.

Worker đọc bot_enabled một lần và claim cả batch thành SENDING. Lệnh sau chưa gửi cũng đã SENDING nên handler OFF không hủy được; vòng gửi dùng giá trị allow_bot_buys cũ.

Tái hiện: hai BOT BUY FPT/VIX; ngay sau broker nhận FPT, gọi đường cancel_unsubmitted_bot_buys tương tự OFF. Cả hai được xem là broker-managed, không có local cancellation; VIX vẫn bị gửi tiếp.

Hướng sửa: kiểm tra arm/generation mới nhất ngay trước từng network send; phân biệt CLAIMED chưa gửi với HANDOFF/UNKNOWN. Nút OFF không thể thu hồi lệnh broker đã nhận; UI phải báo phần nào còn cần hủy riêng.

### A13 — P1: lỗi API positions bị coi là tài khoản không có cổ phiếu

Vị trí: `viking_v2/connections/dnse/client.py:232`, `:246`; `viking_v2/trading/execution.py:549`, `:668`; `viking_v2/dashboard/actions.py:3066`.

HTTP positions lỗi → `[]`; không phân biệt empty success với dữ liệu thiếu. External reconciliation có thể lấy một SELL cũ cùng symbol, không kiểm tra xảy ra sau khi trade mở hoặc execution đã dùng, để đóng trade hiện tại.

Tái hiện: trade REAL đang có 100 FPT, mở một phút trước; positions trả 503; orders/history có external SELL 100 FPT một giờ trước, trước khi trade hiện tại mở → TradeCycle bị ghi CLOSED dù mô phỏng không hề bán lần này. Một SELL trước đó trong cùng ngày là đủ, không cần dữ liệu lịch sử xa.

Hướng sửa: typed snapshot gồm status/asof/completeness, fail-closed khi thiếu endpoint; không reconcile ownership từ empty error. Ghép external execution theo thời gian và fill ID, không tái sử dụng giao dịch đã đối soát.

### A14 — P2: feeRate/taxRate không được chuyển thành phí thực hiện

Vị trí: `viking_v2/trading/orders.py:257`; `viking_v2/trading/execution.py:491`.

Hạch toán chỉ đọc fee/totalFee/tax. Schema DNSE public có feeRate/taxRate ở order detail; nếu response chỉ có rate thì bot ghi chi phí=0.

Tái hiện với response đúng dạng rate: BUY 100 CP ×100.000 đồng, feeRate=0,15%; bot ghi fee=0 thay vì 15.000 đồng. Sai PnL, WIN/LOSS, no-compound/loss lock ở các lệnh sát hòa vốn.

Chưa xác minh tài khoản production của Ngài có thêm trường tổng phí hay không. Cần hợp đồng phí/executions theo version thực tế, không cộng feeRate và exchangeFeeRate tùy tiện nếu tổng phí đã bao gồm phí sở.

## Nhận xét thuật toán và giới hạn mô phỏng

- EMA và RSI dùng smoothing thông thường; ATR/PROTECT lấy nến completed. Trong phần kiểm tra chưa xác nhận lỗi công thức riêng của EMA/RSI. Điều đó không cứu được tín hiệu khi tick sai mã/cũ hoặc giá vốn sai.
- SL được ưu tiên trước TP/indicator/protection; broker tradeQuantity là giới hạn bán được ở REAL. PAPER mô phỏng T+2 và fill tức thì, không kiểm chứng vòng đời khớp thật.
- Backtest có phân biệt DAILY/REPLAY và xử lý một số tình huống nhìn trước; không dùng nó để xác nhận order engine an toàn. Chưa có kiểm chứng đủ slippage/spread/độ sâu/thanh khoản/khớp từng phần/treo sàn. Ngưỡng SL không bảo đảm giá bán hay mức lỗ tối đa, nhất là T+2 và không có bên mua.
- Các bộ lọc loss lock/whipsaw không thay thế hard cap tiền mỗi lệnh, exposure thực tế, drawdown toàn tài khoản và một emergency halt đã được kiểm chứng. Chưa xác nhận các giới hạn tiền cứng độc lập trước broker send.

## Hợp đồng API cần xác minh thêm, không coi là lỗi đã chứng minh

1. Repo ghim mặc định version `2026-05-07`; **không kết luận endpoint `/accounts/orders` cũ là sai**. DNSE công bố vẫn hỗ trợ endpoint này. Không nâng version/đổi endpoint sát live mà chưa contract-test.
2. `remark` không xuất hiện trong các section đặt lệnh/chi tiết lệnh của YAML 2026-05-07 đã đọc. Không chứng minh DNSE tuyệt đối không hỗ trợ field đó, nhưng chưa có cơ sở dựa vào việc echo/deduplicate nó cho recovery.
3. Payload hiện không có loanPackageId; phải xác minh gói tiền mặt đúng với tiểu khoản/version và response thực tế, không mặc định hệ thống chọn cash package hoặc margin đúng ý định.
4. Cần test cancellation/replace order IDs, cumulative fill/average/notional, phí cuối cùng, sổ lệnh hết ngày và order history. Reconciliation hiện chỉ tìm working order trong get_orders; chưa có fallback đầy đủ khi lệnh chuyển sang history.
5. Chưa kiểm tra nhiều app cùng account; không thấy cơ chế singleton/process lock trong đường startup đã rà. Không mở hai instance trên workspace REAL trước khi kiểm chứng ownership/locking.

Nguồn công khai chính thức đã đọc:

- [DNSE API versioning và YAML 2026-05-07](https://developers.dnse.com.vn/docs/guide/versioning/api/).
- [DNSE trading order lifecycle, loanPackageId và loại lệnh theo sàn](https://developers.dnse.com.vn/docs/guide/trading-api/trading_order/).
- [Đặc tả YAML 2026-05-07](https://cdn.entrade.com.vn/dnse-openapi/doc/dnse-openapi-2026-05-07.yaml).
- [Sandbox DNSE](https://developers.dnse.com.vn/docs/guide/sandbox/): credentials và môi trường tách production, mô phỏng vòng đời lệnh; không phải backtest lợi nhuận.

## Tiêu chí trước khi xét lại quyết định NO-GO

1. Chặn unsafe mutation retry; sửa symbol/freshness guards; sửa delta notional/giá vốn và SELL ownership cap.
2. Có định danh hand-off bền vững, fill ledger/recovery; hủy/sửa chỉ terminal theo broker, không theo HTTP ok.
3. RECHECK thiếu dữ liệu phải chờ; account snapshot lỗi không được coi empty; single-writer/transaction cho state.
4. Xác định và kiểm chứng chính xác PAPER/REAL arm, BUY OFF và emergency halt, kể cả đổi mode/close/restart giữa batch. Halt không đồng nghĩa tự hủy/đóng mọi vị thế.
5. Chuyển các bài tái hiện thành safety regression: expectation an toàn phải fail trước patch, pass sau patch. Chạy lại cả suite, kiểm thử broker sandbox riêng với key sandbox; không sử dụng production token để thử.
6. Crash/restart/fault tests tại trước send, sau accept, giữa queue/trade/journal commit, partial fill, cancel race, reconnect và qua ngày giao dịch. Đối chiếu tổng quantity/notional/fees với broker.
7. Chỉ sau các điều kiện trên mới bàn pilot giới hạn vốn với giám sát trực tiếp và quyền quyết định lệnh rõ ràng. Pilot không khắc phục hoặc làm an toàn các lỗi đã xác nhận.

## Cách chạy lại, không dùng tài khoản thật

Từ root repo:

```powershell
.\ckvnvenv\Scripts\python.exe audits\live_2026_10_03\run_offline.py tests_v2 -q --disable-warnings --tb=short
.\ckvnvenv\Scripts\python.exe audits\live_2026_10_03\run_offline.py audits\live_2026_10_03\test_observed_risks.py -q --tb=short
```

Runner chặn socket connect, không load `.env`, bỏ các environment key DNSE/Telegram, chuyển runtime/config writes sang thư mục tạm. Chạy file test trực tiếp không có các lớp bảo vệ này là ngoài quy trình audit.

`read_public_contract.py` là công cụ riêng chỉ tải một URL YAML công khai của DNSE; không chạy cùng pytest runner vì runner chặn mạng. Nó không import config/app và không đọc token.

Các bài tái hiện quan trọng:

| Test | Phát hiện |
| --- | --- |
| duplicate_post_after_accepted_timeout | A01 |
| timeout_correlation_tag_lost_and_late_fill_not_recovered | A02 |
| crash_between_queue_fill_and_trade_fill_loses_managed_position | A03 |
| daemon_missing_tick_reuses_previous_symbol_price | A04 |
| rest_outage_returns_old_tick_and_planner_accepts_it | A05 |
| old_sell_decision_accepted_with_fresh_heartbeat | A05 |
| recheck_missing_live_decision_still_submits_sell | A06 |
| recheck_expands_managed_sell_into_external_holdings | A07 |
| cumulative_average_price_misused_as_new_partial_fill_price | A08 |
| cancel_ack_finalizes_queue_before_late_fill | A09 |
| rule_state_concurrent_daemon_write_erases_manual_entry_pause | A10 |
| paper_selected_and_bot_off_still_execute_real_waiting_sell | A11 |
| bot_off_mid_batch_cannot_cancel_already_claimed_unsent_buy | A12 |
| failed_positions_request_can_close_trade_using_older_external_sell | A13 |
| documented_fee_rate_fields_are_not_booked_as_costs | A14 |

Đây là báo cáo kiểm tra, chưa phải bản vá hoặc giấy chứng nhận an toàn. Việc sửa source vận hành và sử dụng sandbox cần một yêu cầu tiếp theo từ Ngài.
