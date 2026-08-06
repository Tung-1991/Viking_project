# RAT-CKVN AI Advisor Flow

File này là bản đồ nghiệp vụ để AI hiểu RAT-CKVN mà không cần nhận full source code. Hãy xem đây là guide diễn giải, không phải runtime config.

## Nhiệm vụ
Review RAT-CKVN như một trader/risk manager: tìm nguyên nhân lời/lỗ, rủi ro, missed profit, bad exit, repeated block, config drift, module bất thường, T+2 settlement và bối cảnh thị trường liên quan.

Không đề xuất đặt lệnh tự động, không yêu cầu bot tự sửa config, không bịa hành vi module khi thiếu bằng chứng.

## Thứ tự đọc
Khi gửi thủ công cho AI bên ngoài, chọn trực tiếp các file cần thiết trong thư mục `advisor/`. Không gửi `.env`, trading-token hoặc file workspace tài khoản.
1. advisor_flow.md: hiểu luồng nghiệp vụ, glossary và cách diễn giải field.
2. user_context.md: hiểu câu hỏi/mục tiêu hiện tại của operator.
3. expert_context.md: đọc tài liệu/tổng hợp chuyên gia do operator cung cấp và đối chiếu, không mặc định là fact tuyệt đối.
4. technical_settings.json: đọc riêng cấu hình TRADE và CHECK, runtime snapshot, advisor_guide và state module.
5. advisor_export.xlsx: đọc trade evidence, summary, events, config snapshots và config changes.
6. previous_advisor_response.md nếu có: chỉ là lời khuyên cũ để đối chiếu, không phải fact.

## File Advisor
- advisor_prompt.md: instruction chính gửi vào API hoặc paste vào web UI.
- advisor_flow.md: bản đồ nghiệp vụ và quy tắc đọc hiểu.
- user_context.md: ghi chú thủ công của operator.
- expert_context.md: tài liệu/tổng hợp chuyên gia do operator chủ động nhập.
- technical_settings.json: snapshot config/state tự động gen; không phải file để AI yêu cầu sửa trực tiếp.
- advisor_export.xlsx: workbook evidence tự động gen theo số ngày export.
- advisor_response.md: câu trả lời mới nhất của AI sau khi gọi API.

Nếu người dùng gửi thêm `scan_report.md` từ `ckcs_research/`, đó chỉ là dữ liệu thị trường bổ trợ hoặc dữ liệu nghiên cứu ứng viên CKCS. Không trộn nhận định chọn CKCS với tín hiệu và kết quả BOT.

## Quy tắc dùng web
- Dữ liệu nội bộ RAT-CKVN là nguồn chính để chẩn đoán bot/config/trade.
- Nếu web_search được bật, phải kiểm tra bối cảnh thị trường cho symbol active hoặc symbol có trade trong export.
- Web chỉ dùng để bổ sung bối cảnh bên ngoài khi nó tác động trực tiếp tới chẩn đoán: biến động giá, trend regime, tin doanh nghiệp/vĩ mô, thanh khoản, sự kiện rủi ro, signal quality, SL/TP/TSL/BE behavior hoặc operator action.
- Phải tách rõ nhận định từ nội bộ và nhận định từ web.
- Không được nói nguyên nhân thị trường nếu web/source không đủ bằng chứng.
- Không viết bản tin tổng hợp. Nếu web context không làm thay đổi cách hiểu dữ liệu RAT-CKVN, hãy nói ngắn trong 1-2 dòng.

## Markets
- CKPS: phái sinh VN30F, ví dụ alias VN30F1M. Không áp T+2; có thể mua/bán liên tục theo phiên hợp lệ.
- CKCS: cổ phiếu cơ sở như FPT, SSI, VCB, CTG, BID, MBB. Long-only; bán/đóng phải tôn trọng T+2.
- ATO/ATC: order mode có thể tự chọn lệnh ATO/ATC trong phiên tương ứng, còn lại dùng lệnh liên tục.

## Config layers trong technical_settings.json
- settings.config_py: default/static từ config.py, chỉ dùng làm nền.
- settings.active_global: global settings đang active.
- settings.active_by_symbol: effective settings theo symbol; ưu tiên khi review từng mã CKPS/CKCS.
- settings.raw_sources: snapshot file runtime như brain_settings.json, symbol_overrides.json, tsl_settings.json, presets_config.json, bot_state.json, live_signals.json, system_meta.json.

Conflict rule: review symbol thì ưu tiên active_by_symbol; review global risk thì ưu tiên active_global; review module thì đọc raw source tương ứng; config_py chỉ dùng làm default/background.

## Sheet chính trong advisor_export.xlsx
- closed_trades: trade đã đóng, gồm symbol, source type, close reason, trigger, tactic, MAE/MFE, module tags, config snapshot id.
- open_trades: trade đang mở, floating PnL, tactic, market mode, MAE/MFE và snapshot id.
- config_snapshots: snapshot config theo id.
- config_changes: diff giữa các snapshot.
- events: event hệ thống/module/advisor.
- summary_daily, summary_symbol, summary_timeframe, summary_signal_group, summary_close_reason, summary_module: aggregate để chẩn đoán.
- trade_config_map: map ticket với config snapshot.

## Nguồn trade
- MANUAL: lệnh do operator kích hoạt hoặc magic/comment thuộc manual.
- BOT: order flow theo signal/safeguard/EntryExit/SLTP/TSL/DCA/PCA/REV_C.

## Timeframe groups
- G0: macro/base timeframe.
- G1: trend/context timeframe.
- G2: execution/swing timeframe, hay dùng cho SL/TP reference.
- G3: fast confirmation timeframe.

## Luồng bot cấp cao
1. bot_daemon quét symbol active và gọi data_engine.fetch_data_v4.
2. data_engine tạo OHLC, indicator, ATR/swing và market context.
3. nhánh TRADE đưa riêng `indicators` vào signal_generator để đánh giá G0/G1/G2/G3, market mode, vote và sinh BUY/SELL/NONE.
4. nhánh CHECK đưa riêng `check_indicators` vào kho tổng hợp ngày và báo cáo; không có vote, market mode hoặc quyền đặt lệnh.
5. signal_listener chuyển pending signal sang trade_manager.
6. trade_manager chạy market-hours, safeguard, T+2/long-only, Entry/Exit, lot, SL/TP và gửi order DNSE.
7. Runtime managers chạy TSL, BE, BE_CASH, REV_C, DCA/PCA, watermark, basket drawdown và ATC-exit nếu bật.
8. Closed trades và events được export vào advisor_export.xlsx.

## T+2 settlement
- CKCS BUY ghi buy_date và settle_date.
- CKCS SELL/close chỉ hợp lệ với volume đã về.
- CKCS chưa về nên được treo/retry, không mở short để thay thế.
- CKPS không áp dụng T+2.

## Safeguards và gates
Block phổ biến: market hours, re-entry lock, max daily loss, max open positions, max trades/day, losing streak, ping, spread, cooldown, Entry/Exit WAIT/BLOCK, thiếu swing/ATR, SL quá gần, lot nhỏ hơn minimum, NO_SETTLED_LONG, STOCK_NOT_SETTLED_T2.

Nếu nhiều SAFEGUARD_FAIL, hãy phân biệt gate đang bảo vệ đúng hay đang bỏ lỡ cơ hội tốt.

## Module glossary
- TSL: trailing stop tổng.
- BE: break-even stop.
- BE_CASH: khóa lợi nhuận theo cash/fee.
- STEP_R: trailing theo R multiple.
- SWING: trailing hoặc reference theo swing point.
- PSAR_TRAIL: trailing theo Parabolic SAR.
- REV_C: reverse/recovery close.
- DCA: add/average khi adverse.
- PCA: add khi đang thắng/confirmed.
- A.CUT / ANTI_CASH: chống giveback/cash protection.
- ATO/ATC: lệnh mở/đóng cửa theo phiên chứng khoán.
- T+2: quy tắc cổ phiếu cơ sở phải chờ hàng về mới bán/đóng.

## MAE/MFE và close reason
- MAE cao trước khi có profit: entry timing, SL distance hoặc averaging stress.
- MFE cao nhưng final PnL thấp: exit/trailing/giveback issue.
- Nhiều SL sau MFE tốt: nghi TSL/BE/giveback/stop placement.
- Basket close cần phân tích theo DCA/PCA/REV_C/TSL, không gom chung với BOT thường.

## Format trả lời khuyến nghị
## Tóm tắt điều hành
## Bằng chứng nội bộ RAT-CKVN
## Bối cảnh web/thị trường
## Chẩn đoán
## Rủi ro chính
## Hành động đề xuất
## Độ tin cậy / Thiếu dữ liệu

Ưu tiên câu trả lời gọn để đọc trên Telegram, khoảng 700-1000 từ và 1-2 chunks khi dữ liệu ít hoặc trung bình. Nếu có nhiều evidence quan trọng, được trả lời dài hơn nhưng phải giữ cấu trúc gọn. Không dùng markdown bold/italic, không dùng ký tự **, không dùng bảng Markdown, không paste URL dài trong thân bài. Mỗi kết luận lớn cần có evidence và confidence: Cao / Trung bình / Thấp.
