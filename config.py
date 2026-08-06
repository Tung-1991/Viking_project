# -*- coding: utf-8 -*-
# FILE: config.py
# V4.2: UNIFIED CONFIG - LEGO MASTER & MULTI-GROUP (KAISER EDITION)

import copy
import os
from dotenv import load_dotenv

# Tự động load biến môi trường từ file .env
load_dotenv(encoding="utf-8-sig")

# ==============================================================================
# 0. BẢO MẬT & API (DNSE)
# ==============================================================================
# Lưu ý: Các thiết lập API Key, Secret, Account Number và OTP Type 
# được đọc trực tiếp từ file .env để đảm bảo tính riêng tư. 
# Không ghi hardcode các thông tin đó vào file này!

# ==============================================================================
# 1. HỆ THỐNG & KẾT NỐI (DNSE - PHÁI SINH VN)
# ==============================================================================
COIN_LIST = ["VN30F1M"]
DEFAULT_SYMBOL = "VN30F1M"
BOT_ACTIVE_SYMBOLS = ["VN30F1M"]
# CKCS được người vận hành đánh dấu ưu tiên. Chỉ vượt giới hạn tổng số vị thế mở;
# không vượt cooldown, max-loss, spread, risk gate hoặc phanh biến động.
PRIORITY_SYMBOLS = []
CKPS_SYMBOLS = [s.strip().upper() for s in os.getenv("DNSE_CKPS_WATCHLIST", "VN30F1M").split(",") if s.strip()]
CKCS_WATCHLIST = [s.strip().upper() for s in os.getenv("DNSE_CKCS_WATCHLIST", "").split(",") if s.strip()]
# Mã hợp đồng phái sinh THẬT (vd 41I1G6000, đổi theo tháng đáo hạn) — để settlement biết KHÔNG phải CKCS.
DERIVATIVE_REAL_SYMBOLS = [s.strip().upper() for s in os.getenv("DNSE_DERIVATIVE_REAL_SYMBOLS", "").split(",") if s.strip()]
DNSE_CUSTODY_CODE = os.getenv("DNSE_CUSTODY_CODE", "")
DNSE_STOCK_ACCOUNT_NO = os.getenv("DNSE_STOCK_ACCOUNT_NO", "")
DNSE_DERIVATIVE_ACCOUNT_NO = os.getenv("DNSE_DERIVATIVE_ACCOUNT_NO", "")
# Thời hạn trading-token theo tài liệu DNSE: cả Email OTP và Smart OTP đều ~8 giờ.
DNSE_TOKEN_TTL_HOURS = {"email_otp": 8.0, "smart_otp": 8.0}
CRYPTO_SYMBOLS = []
WEEKDAY_ONLY_SYMBOLS = ["VN30F1M"]
MARKET_HOURS_UTC_OFFSET = 7 # Giờ VN
MARKET_PREOPEN_MINUTES = int(os.getenv("MARKET_PREOPEN_MINUTES", "5"))
# Danh sách ngày nghỉ bổ sung dạng YYYY-MM-DD, phân tách bằng dấu phẩy. Việc kiểm tra
# hoàn toàn cục bộ để không tạo request mạng khi thị trường đóng cửa.
MARKET_HOLIDAYS = {
    day.strip()
    for day in os.getenv("MARKET_HOLIDAYS", "").split(",")
    if day.strip()
}
MARKET_CALENDAR_CACHE_FILE = os.getenv(
    "MARKET_CALENDAR_CACHE_FILE", "data/market_calendar_cache.json"
)
MARKET_CALENDAR_DEFAULT = {
    "use_dnse_working_dates": True,
    "manual_closed_dates": [],
    "block_entry_ato": True,
    "block_entry_atc": True,
    "avoid_vn30_expiry_entry": False,
    "avoid_vn30_rebalance_entry": False,
    "vn30_rebalance_dates": [],
    "avoid_ckcs_open_entry": True,
    "ckcs_entry_delay_minutes": 15,
}
BOT_OPPORTUNITY_DEFAULT = {
    "enabled": True,
    "retention_hours": 24.0,
    "history_enabled": True,
    "default_order_mode": "MARKET",
    "default_slippage_ticks": 2,
}
WEEKEND_CLOSE_WEEKDAY = 4 # Thứ 6
WEEKEND_CLOSE_HOUR = 15 # Đóng cửa 15:00
WEEKEND_OPEN_WEEKDAY = 0 # Thứ 2
WEEKEND_OPEN_HOUR = 8 # Mở cửa 8:45 (tính 8)

# [FIX LAG] Nhịp vòng lặp UI nền. 0.25s (4Hz) khiến hàm vẽ nặng chạy 4 lần/giây -> đơ.
# Swing CKCS/T+2 không cần refresh nhanh; 1.0s (1Hz) mượt hơn, giảm 4× tải render + tải mạng.
LOOP_SLEEP_SECONDS = float(os.getenv("LOOP_SLEEP_SECONDS", "1.0"))
DNSE_TICK_CACHE_TTL_SECONDS = float(os.getenv("DNSE_TICK_CACHE_TTL_SECONDS", "2.0"))
DNSE_OHLC_CACHE_TTL_SECONDS = float(os.getenv("DNSE_OHLC_CACHE_TTL_SECONDS", "30.0"))
# [24/7] Ngoài giờ giao dịch: đóng băng cache OHLC lâu hơn để khỏi spam API (giây). 0 = tắt.
DNSE_OHLC_CACHE_TTL_CLOSED_SECONDS = float(os.getenv("DNSE_OHLC_CACHE_TTL_CLOSED_SECONDS", "1800.0"))
# [24/7] Số lần thử lại khi dính rate-limit 429 (backoff theo Retry-After / luỹ thừa 2). 0 = không retry.
DNSE_RATE_LIMIT_RETRIES = int(os.getenv("DNSE_RATE_LIMIT_RETRIES", "1"))
# [Audit F1] Hệ số nhân cửa sổ thời gian khi fetch OHLC: TT VN chỉ mở ~5h/ngày nên muốn đủ N nến
# intraday phải lùi xa hơn N×bar (công thức gốc Exness giả định thị trường 24h). Daily nhân thêm
# để bù cuối tuần + nghỉ lễ.
DNSE_OHLC_WINDOW_FACTOR_INTRADAY = float(os.getenv("DNSE_OHLC_WINDOW_FACTOR_INTRADAY", "8.0"))
DNSE_OHLC_WINDOW_FACTOR_DAILY = float(os.getenv("DNSE_OHLC_WINDOW_FACTOR_DAILY", "1.6"))
# Số entry tối đa của cache nến OHLC (mỗi mã ~3 khung riêng biệt -> 512 đủ cho ~170 mã).
DNSE_OHLC_CACHE_MAX_ENTRIES = int(os.getenv("DNSE_OHLC_CACHE_MAX_ENTRIES", "512"))
# [FIX 429] Daemon tick 2s chỉ poll mã ĐANG GIỮ VỊ THẾ (canh SL/TSL). Không giữ gì -> không poll.
# Bật cờ dưới nếu scalping phái sinh, cần tick CKPS ngay cả khi chưa có vị thế.
DAEMON_TICK_INCLUDE_CKPS = os.getenv("DAEMON_TICK_INCLUDE_CKPS", "false").strip().lower() in ("1", "true", "yes", "on")
DNSE_ACCOUNT_CACHE_TTL_SECONDS = float(os.getenv("DNSE_ACCOUNT_CACHE_TTL_SECONDS", "5.0"))
DNSE_POSITIONS_CACHE_TTL_SECONDS = float(os.getenv("DNSE_POSITIONS_CACHE_TTL_SECONDS", "5.0"))
DNSE_ORDERS_CACHE_TTL_SECONDS = float(os.getenv("DNSE_ORDERS_CACHE_TTL_SECONDS", "5.0"))
DNSE_CLOSED_PRIVATE_SYNC_SECONDS = float(os.getenv("DNSE_CLOSED_PRIVATE_SYNC_SECONDS", "300.0"))
DNSE_BROKER_BACKOFF_SECONDS = float(os.getenv("DNSE_BROKER_BACKOFF_SECONDS", "60.0"))
DNSE_ACCOUNT_REPOSITORY_BACKOFF_SECONDS = float(
    os.getenv("DNSE_ACCOUNT_REPOSITORY_BACKOFF_SECONDS", "60.0")
)
# Cache gói phí/loan-package (giây) — phí ít đổi trong ngày nên TTL dài.
DNSE_FEE_CACHE_TTL_SECONDS = float(os.getenv("DNSE_FEE_CACHE_TTL_SECONDS", "3600.0"))

# --- Market-data WebSocket streaming (giảm tải REST để add nhiều mã không bị BAN) ---
# Chế độ mới: auto chỉ kết nối trong warm-up/phiên giao dịch và tự ngắt ngoài giờ.
# DNSE_WS_ENABLED vẫn tồn tại để code/config bundle cũ đọc được; DNSE_WS_MODE=off
# là công tắc tắt rõ ràng. Cài đặt mới mặc định auto theo chính sách RAT6.
_DNSE_WS_LEGACY_RAW = os.getenv("DNSE_WS_ENABLED", "")
DNSE_WS_LEGACY_ENABLED = _DNSE_WS_LEGACY_RAW.strip().lower() in ("1", "true", "yes", "on")
_DNSE_WS_MODE_RAW = os.getenv("DNSE_WS_MODE", "").strip().lower()
# Thứ tự ưu tiên tương thích ngược:
#   DNSE_WS_MODE được khai báo -> dùng mode mới;
#   nếu chưa có mode mới nhưng có DNSE_WS_ENABLED cũ -> ánh xạ true/false;
#   nếu cả hai đều chưa có -> auto.
DNSE_WS_MODE = _DNSE_WS_MODE_RAW or (
    ("auto" if DNSE_WS_LEGACY_ENABLED else "off") if _DNSE_WS_LEGACY_RAW.strip() else "auto"
)
if DNSE_WS_MODE not in ("auto", "off"):
    DNSE_WS_MODE = "auto"
DNSE_WS_ENABLED = DNSE_WS_MODE != "off"
DNSE_WS_URL = os.getenv("DNSE_WS_URL", "wss://ws-openapi.dnse.com.vn")
DNSE_WS_ENCODING = os.getenv("DNSE_WS_ENCODING", "json")
DNSE_WS_BOARD_ID = os.getenv("DNSE_WS_BOARD_ID", "G1")
DNSE_WS_RECONNECT_SECONDS = float(os.getenv("DNSE_WS_RECONNECT_SECONDS", "5.0"))
DNSE_WS_HEARTBEAT_SECONDS = float(os.getenv("DNSE_WS_HEARTBEAT_SECONDS", "25.0"))
DNSE_WS_HEARTBEAT_TIMEOUT_SECONDS = float(os.getenv("DNSE_WS_HEARTBEAT_TIMEOUT_SECONDS", "60.0"))
DNSE_WS_FALLBACK_DELAY_SECONDS = float(os.getenv("DNSE_WS_FALLBACK_DELAY_SECONDS", "5.0"))
# Giữ tên cũ để bundle setting cũ không lỗi. Tuổi tick chỉ dùng để hiển thị; không còn
# dùng nó để kết luận WebSocket chết khi một mã đứng giá.
DNSE_WS_PONG_INTERVAL = DNSE_WS_HEARTBEAT_SECONDS
DNSE_WS_STALE_SECONDS = float(os.getenv("DNSE_WS_STALE_SECONDS", "5.0"))
DNSE_MARKET_REST_MAX_SYMBOLS_PER_SECOND = float(
    os.getenv("DNSE_MARKET_REST_MAX_SYMBOLS_PER_SECOND", "2.0")
)
DNSE_WS_MAX_CONNECTION_SECONDS = float(os.getenv("DNSE_WS_MAX_CONNECTION_SECONDS", "28200"))  # 7h50
DNSE_WS_RECONCILE_SECONDS = float(os.getenv("DNSE_WS_RECONCILE_SECONDS", "30"))
RESET_HOUR = 0
STRICT_MODE_DEFAULT = True
MAX_PING_MS = 150
MAX_SPREAD_POINTS = 5 # Phái sinh VN spread thường 0.1 - 0.5 điểm, đặt 5 điểm là cao
ENABLE_DEBUG_LOGGING = False

# ==============================================================================
# 2. TÀI KHOẢN & GIỚI HẠN GIAO DỊCH
# ==============================================================================
ACCOUNT_TYPES_CONFIG = {
    "STANDARD": {"COMMISSION_PER_LOT": 0.0},
}
DEFAULT_ACCOUNT_TYPE = "STANDARD"

COMMISSION_RATES = {
    "VN30F1M": 0.0,
    "VN30F2M": 0.0,
    "VN30F1Q": 0.0,
    "VN30F2Q": 0.0,
}

MAX_DAILY_LOSS_PERCENT = 2.5
LOSS_COUNT_MODE = "TOTAL"
MAX_LOSING_STREAK = 3
MAX_TRADES_PER_DAY = 30
MAX_OPEN_POSITIONS = 3

MIN_LOT_SIZE, MAX_LOT_SIZE = 1.0, 200.0
LOT_STEP = 1.0
DNSE_POINT_VALUE = 100000.0
DNSE_STOCK_PRICE_VALUE = 1000.0
DNSE_PRICE_POINT = 0.1

# --- UI đặt lệnh tay ---
# Gộp "EXECUTE BUY" + "LIMIT ORDER" thành 1 nút thông minh + tick "Thị trường".
# Tick -> lệnh thị trường (ATO/ATC tự nhận theo phiên); bỏ tick -> LO theo giá nhập/giá hiện tại.
# Đặt False để quay lại 2 nút + dropdown ATO/ATC cũ (lego, không xóa code cũ).
UNIFIED_ORDER_BUTTON = True

# --- Ràng buộc CKCS (cổ phiếu cơ sở) ---
STOCK_ROUND_LOT = 100  # lệnh thường phải là bội số 100 CP (lô chẵn)
# CKCS không đòn bẩy: giá trị 1 lệnh ≤ % NAV (chống dồn quá nhiều vốn vào 1 mã do SL hẹp -> lot khổng lồ). 0 = tắt.
STOCK_MAX_ORDER_NAV_PCT = 20.0
STOCK_DEFAULT_EXCHANGE = "HOSE"  # CKCS hiện (FPT,SSI,VCB,CTG,BID,MBB) đều HOSE
STOCK_EXCHANGE_BANDS = {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15}  # biên độ trần/sàn theo sàn
# map mã->sàn override (env "DNSE_STOCK_EXCHANGE_MAP" dạng "SHS:HNX,ABC:UPCOM"); để trống là đủ
STOCK_SYMBOL_EXCHANGE = {
    p.split(":")[0].strip().upper(): p.split(":")[1].strip().upper()
    for p in os.getenv("DNSE_STOCK_EXCHANGE_MAP", "").split(",")
    if ":" in p
}
MONEY_DISPLAY_ZERO_TRIM = os.getenv("MONEY_DISPLAY_ZERO_TRIM", "000").strip() or "000"
MONEY_DISPLAY_UNIT = {
    "NONE": "VND",
    "0": "VND",
    "000": "K_VND",
    "000000": "M_VND",
    "000 000": "M_VND",
}.get(MONEY_DISPLAY_ZERO_TRIM.upper(), "K_VND")
DNSE_BROKER_FEE_PER_CONTRACT = 0.0
DNSE_EXCHANGE_FEE_PER_CONTRACT = 2700.0
DNSE_CLEARING_FEE_PER_CONTRACT = 2550.0
DNSE_TAX_RATE = float(os.getenv("DNSE_DERIVATIVE_TAX_RATE", "0.001"))
DNSE_STOCK_BROKER_FEE_RATE = 0.0
DNSE_STOCK_TAX_RATE = float(os.getenv("DNSE_STOCK_TAX_RATE", "0.001"))
# Fallback chỉ dùng khi endpoint loan-packages không trả initialRate. Bình thường
# app luôn lấy tỷ lệ ký quỹ thật từ DNSE cho công thức thuế VN30F.
DNSE_DERIVATIVE_INITIAL_MARGIN_RATE = float(
    os.getenv("DNSE_DERIVATIVE_INITIAL_MARGIN_RATE", "0.20")
)
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").strip().lower() in ("1", "true", "yes", "on")
# ⚠️ BẢO MẬT: lưu trading-token xuống đĩa để restart không phải OTP lại. Token = quyền đặt lệnh thật,
# ai có file cũng giao dịch được tài khoản. Mặc định TẮT. Chỉ bật nếu máy của riêng mày + chấp nhận rủi ro.
PERSIST_TRADING_TOKEN = os.getenv("PERSIST_TRADING_TOKEN", "False").strip().lower() in ("1", "true", "yes", "on")
PAPER_INITIAL_BALANCE = float(os.getenv("PAPER_INITIAL_BALANCE", "100000000.0"))
PAPER_FEE_PER_CONTRACT = DNSE_BROKER_FEE_PER_CONTRACT
PAPER_SPREAD_POINTS = 0.0
PAPER_FALLBACK_PRICE = 0.0
MAX_LOT_CAP = 0.0  # [NEW V4.4] Giới hạn Lot tối đa cho mỗi lệnh (0 = Không GH)
MANUAL_CONFIG = {"BYPASS_CHECKLIST": False, "DEFAULT_LOT": 0.0}
PENDING_ORDER_EXPIRE_HOURS = float(os.getenv("PENDING_ORDER_EXPIRE_HOURS", "24"))
# [FIX] Tự dọn lệnh local đã EXPIRED/FAILED/CANCELLED khỏi bảng running sau X giờ (0 = không dọn).
PENDING_PURGE_AFTER_HOURS = float(os.getenv("PENDING_PURGE_AFTER_HOURS", "2"))
MANUAL_MARGIN_CONFIG = {
    "ENABLE_MANUAL_MARGIN": False,
    "MARGIN_RISK_BASE": "EQUITY_NAV",
    "MAX_MARGIN_ORDER_VALUE_PCT": 50.0,
    "MIN_RTT_TO_OPEN": 100.0,
    "CALL_RTT": 87.0,
    "FORCE_RTT": 80.0,
    "MAX_MANUAL_MARGIN_LOSS_PCT": 3.0,
    "BOT_ALLOW_MARGIN": False,
}

# ==============================================================================
# 3. QUẢN LÝ VỐN (Dành cho MANUAL PRESETS trên UI)
# ==============================================================================
DEFAULT_PRESET = "SCALPING"

PRESETS = {
    "SCALPING": {
        "DESC": "Nhanh, SL ngắn",
        "SL_PERCENT": 0.5,
        "TP_RR_RATIO": 1.5,
        "RISK_PERCENT": 1.0,
        "MANUAL_SL_MODE": "SWING_REJECTION",
        "MANUAL_TP_MODE": "FIB",
        "USE_SWING_SL": True,
        "USE_SWING_TP": False,
        "MANUAL_SL_GROUP": "G1",
        "MANUAL_TP_GROUP": "G1",
        "MANUAL_SWING_SL_GROUP": "G1",
        "MANUAL_SWING_TP_GROUP": "G1",
        "MANUAL_SWING_SL_ATR_MULT": 0.2,
        "MANUAL_SWING_TP_ATR_MULT": 0.2,
        "MANUAL_FIB_TP_LEVELS": "1.272,1.618,2.0",
        "MANUAL_PULLBACK_SOURCE": "EMA20",
        "MANUAL_PULLBACK_ATR_WIDTH": 0.5,
        "MANUAL_PULLBACK_TP_ATR_MULT": 1.5,
    },
    "SAFE": {
        "DESC": "An toàn",
        "SL_PERCENT": 0.8,
        "TP_RR_RATIO": 1.2,
        "RISK_PERCENT": 0.2,
        "MANUAL_SL_MODE": "PERCENT",
        "MANUAL_TP_MODE": "RR",
        "USE_SWING_SL": False,
        "USE_SWING_TP": False,
        "MANUAL_SL_GROUP": "G2",
        "MANUAL_TP_GROUP": "G2",
        "MANUAL_SWING_SL_GROUP": "G2",
        "MANUAL_SWING_TP_GROUP": "G2",
        "MANUAL_SWING_SL_ATR_MULT": 0.2,
        "MANUAL_SWING_TP_ATR_MULT": 0.2,
        "MANUAL_FIB_TP_LEVELS": "1.272,1.618,2.0",
        "MANUAL_PULLBACK_SOURCE": "EMA20",
        "MANUAL_PULLBACK_ATR_WIDTH": 0.5,
        "MANUAL_PULLBACK_TP_ATR_MULT": 1.5,
    },
    "BREAKOUT": {
        "DESC": "Săn trend lớn",
        "SL_PERCENT": 1.0,
        "TP_RR_RATIO": 3.0,
        "RISK_PERCENT": 0.5,
        "MANUAL_SL_MODE": "PERCENT",
        "MANUAL_TP_MODE": "RR",
        "USE_SWING_SL": False,
        "USE_SWING_TP": False,
        "MANUAL_SL_GROUP": "G2",
        "MANUAL_TP_GROUP": "G2",
        "MANUAL_SWING_SL_GROUP": "G2",
        "MANUAL_SWING_TP_GROUP": "G2",
        "MANUAL_SWING_SL_ATR_MULT": 0.2,
        "MANUAL_SWING_TP_ATR_MULT": 0.2,
        "MANUAL_FIB_TP_LEVELS": "1.272,1.618,2.0",
        "MANUAL_PULLBACK_SOURCE": "EMA20",
        "MANUAL_PULLBACK_ATR_WIDTH": 0.5,
        "MANUAL_PULLBACK_TP_ATR_MULT": 1.5,
    },
}

# ==============================================================================
# 4. THAM SỐ RIÊNG CHO BOT (BOT SPECIFIC SETTINGS)
# ==============================================================================
BOT_RISK_PERCENT = 0.30
BOT_TP_RR_RATIO = 1.5
BOT_DEFAULT_TSL = "BE+STEP_R+SWING"
BOT_BASE_SL = "G2"  # Đã cập nhật từ 'entry' sang 'G2'
BOT_DAILY_TRADE_LIMIT = 10
BOT_BYPASS_CHECKLIST = False
FORCE_ANY_MODE = False  # True: Bỏ qua check Vĩ mô G0/G1, ép Market Mode = ANY
STRICT_RISK_CALC = False  # True: Trừ hao chi phí Spread/Commission thẳng vào Lot Size

# ==============================================================================
# 5. BOT SAFEGUARD (HÀNG RÀO BẢO VỆ ĐỘC LẬP CỦA BOT)
# ==============================================================================
BOT_SAFEGUARD = {
    "MAX_DAILY_LOSS_PERCENT": 2.5,
    # Kiểu khớp lệnh của bot: NORMAL = luôn khớp liên tục (LO/MOK);
    # AUTO = trong phiên ATO/ATC thì đặt lệnh ATO/ATC, ngoài phiên thì liên tục.
    "BOT_ORDER_MODE": "NORMAL",
    # Kiểu lệnh VÀO của bot phiên liên tục: MARKET (khớp ngay mọi giá) | LO (đặt limit tại giá hiện tại, không đuổi giá).
    "BOT_ENTRY_ORDER_TYPE": "MARKET",
    # Đóng vị thế bot ở phiên ATC cuối ngày (tránh giữ qua đêm).
    "BOT_ATC_EXIT": False,
    "PENDING_ORDER_EXPIRE_HOURS": PENDING_ORDER_EXPIRE_HOURS,
    "MAX_OPEN_POSITIONS": 3,
    "MAX_TRADES_PER_DAY": 30,
    "MAX_LOSING_STREAK": 3,
    # Phanh biến động: so trực tiếp giá trong cửa sổ ngắn, không dùng indicator/AI.
    # Khi đủ số lần xác nhận: đóng toàn bộ vị thế, khóa Global Cooldown và gửi cảnh báo.
    "VOLATILITY_BRAKE_ENABLED": False,
    "VOLATILITY_BRAKE_SYMBOLS": ["VN30F1M"],
    "VOLATILITY_BRAKE_ACTION": "ALERT_ONLY",
    "VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES": 30.0,
    "VOLATILITY_BRAKE_TELEGRAM_ENABLED": True,
    "VOLATILITY_BRAKE_WINDOW_SECONDS": 60.0,
    "VOLATILITY_BRAKE_STOCK_PCT": 1.5,
    "VOLATILITY_BRAKE_DERIVATIVE_POINTS": 5.0,
    "VOLATILITY_BRAKE_CONFIRMATIONS": 2,
    # Cảnh báo nhịp đi xa trong phiên, độc lập với cú sốc 5 điểm/60 giây.
    # Sau mỗi cảnh báo detector lấy giá hiện tại làm mốc mới; cooldown vẫn theo mã + chiều.
    "VOLATILITY_BRAKE_SESSION_ENABLED": True,
    "VOLATILITY_BRAKE_SESSION_DERIVATIVE_POINTS": 20.0,
    "VOLATILITY_BRAKE_SESSION_STOCK_PCT": 3.0,
    # [CKCS] Bật: lô CKCS tính theo rủi ro < 1 lô -> ép lên 1 lô chẵn (100 CP), chấp nhận rủi ro > mục tiêu %. Tắt = bỏ lệnh.
    "FORCE_MIN_LOT": False,
    # [CKCS] Cap giá trị 1 lệnh cổ phiếu cơ sở ≤ % NAV (0 = tắt). Chống SL hẹp -> lot khổng lồ, dồn vốn 1 mã.
    "STOCK_MAX_ORDER_NAV_PCT": STOCK_MAX_ORDER_NAV_PCT,
    # [RISK GATE] Trần %NAV mất nếu dính SL cho 1 lệnh (0 = tắt). Van đo TIỀN-MẤT (các van khác đo SIZE).
    # PS trần cao vì floor 1 HĐ (NAV nhỏ + SL rộng -> risk% lớn bất khả kháng).
    # Vượt trần: bot/telegram chặn cứng; manual hiện popup xác nhận.
    "RISK_GATE_MAX_PCT_PS": 10.0,
    "RISK_GATE_MAX_PCT_CS": 3.0,
    "LOSS_COUNT_MODE": "TOTAL",
    "COOLDOWN_MINUTES": 1,
    "NUM_H1_BARS": 100,
    "NUM_M15_BARS": 100,
    "CHECK_PING": True,
    "MAX_PING_MS": 150,
    "CHECK_SPREAD": True,
    # Spread DNSE = khoảng giá ask-bid (điểm PS / nghìn VND CS); 5 là rất rộng, chỉ chặn bất thường.
    "MAX_SPREAD_POINTS": 5,
    "DAEMON_LOOP_DELAY": 15.0,
    "DCA_PCA_SCAN_INTERVAL": 2.0,
    "LOG_COOLDOWN_MINUTES": 60.0,
    "MANUAL_SIGNAL_LOG_ENABLE": False,
    "BOT_USE_TP": True,
    "BOT_TP_RR_RATIO": 1.5,  # [NEW] Rầu thưởng khi dùng TP theo R (fallback nếu không dùng SwingPoint)
    "BOT_USE_SWING_TP": False,
    "BOT_USE_RR_TP": True,
    "STRICT_MIN_LOT": False,  # [NEW V4.4] Chặn Lot < Min_Vol. LƯU Ý: van này gần như không kích hoạt được
    # (calculate_lot_size luôn clamp qty >= volume_min nên không trả 0) — RISK_GATE_MAX_PCT_* kế nhiệm vai trò này.
    "POST_CLOSE_COOLDOWN": 0,  # [NEW V4.4] Thời gian nghỉ nến sau SL (Giây)
    "CLOSE_ON_REVERSE_MIN_TIME": 180,  # [NEW V4.4] Min Hold Time cho REVERSE_CLOSE
    "DCA_PCA_COOLDOWN_SECONDS": 300,  # [NEW V4.4] Khoảng nghỉ giữa 2 lần nhồi (Giây)
    "CLOSE_ON_REVERSE_USE_PNL": True,  # [NEW V4.4] Bật/Tắt kiểm tra PnL khi đảo chiều
    "REV_CLOSE_ON_NONE": False,  # Nếu True: Master Action NONE cũng được phép cắt theo reverse guard
    "REV_CONFIRM_SECONDS": 300,  # Tin hieu dao chieu phai giu du lau truoc khi REV_C cat
    "REV_CONFIRM_SCANS": 2,  # So lan quet signal dao chieu lien tiep toi thieu
    "REV_CLOSE_MIN_PROFIT": 0.0,  # Chỉ đảo khi lãi ít nhất X (0 = ko tính)
    "REV_CLOSE_MIN_PROFIT_UNIT": "USD",
    "REV_CLOSE_MAX_LOSS": 0.0,  # Chỉ đảo khi lỗ ko quá Y (âm, ví dụ -10, 0 = ko tính)
    "REV_CLOSE_MAX_LOSS_UNIT": "USD",
    "WATERMARK_TRIGGER": 0.0,  # [NEW V5] Mức USD bắt đầu kích hoạt Khóa lãi (0 = Tắt)
    "WATERMARK_TRIGGER_UNIT": "USD",
    "WATERMARK_DRAWDOWN": 0.0,  # [NEW V5] Mức USD sụt giảm cho phép từ đỉnh
    "WATERMARK_DRAWDOWN_UNIT": "USD",
    "MIN_SL_POINTS": 0,  # [NEW V5] Khoảng cách SL tối thiểu bằng Point
    "REJECT_ON_MAX_LOT": False,  # [NEW V5] True: Bỏ lệnh nếu vượt trần. False: Ép về Max Lot Cap
    "MAX_BASKET_DRAWDOWN_USD": 0.0,  # [NEW V5.1] Mức âm tối đa của cả rổ lệnh (Mẹ + DCA/PCA) (0 = Tắt)
    "MAX_BASKET_DRAWDOWN_UNIT": "USD",
}

# ==============================================================================
# 6. LOGIC TRAILING STOP CƠ BẢN (BE & STEP & PNL)
# ==============================================================================
TSL_CONFIG = {
    # LƯU Ý ĐƠN VỊ: "USD" ở đây = TIỀN THÔ của tài khoản = VND (account VN). Giá trị là VND.
    "BE_CASH_TYPE": "USD",  # USD = so theo tiền thô (VND). PERCENT/POINT/R = quy đổi khác.
    "BE_VALUE": 125000.0,  # Bước khóa lãi (VND)
    "BE_TRIGGER": 250000.0,  # Ngưỡng lãi bắt đầu khóa BE_CASH (VND)
    "BE_CASH_STRAT": "TRAILING (Gap)",
    "BE_CASH_FEE_PROTECT": True,
    "BE_CASH_SOFT_BUFFER_TYPE": "USD",
    "BE_CASH_SOFT_BUFFER": 75000.0,  # Đệm mềm (VND)
    "BE_CASH_MIN_LOCK": 0.0,
    "BE_MODE": "SOFT",
    "BE_OFFSET_RR": 0.8,
    "BE_OFFSET_POINTS": 0,
    "BE_SL_LOSS_ENABLE": False,
    "BE_SL_LOSS_UNIT": "R",
    "BE_SL_LOSS_TRIGGER": 0.5,
    "BE_SL_LOSS_STEP": 0.15,
    "BE_SL_GUARD_BUFFER": 0.075,
    "BE_SL_LOSS_ACTION": "RECOVERY_GUARD",
    "BE_SL_REENTRY_LOCK_SEC": 1800,
    "ONE_TIME_BE": False,  # [NEW V4.4] Chỉ kích hoạt BE/BE_CASH 1 lần duy nhất
    "PNL_LEVELS": [[0.5, 0.1], [1.0, 0.5], [2.0, 1.2]],
    "STEP_R_SIZE": 1.0,
    "STEP_R_RATIO": 0.8,
    "SWING_GROUP": "G2",
    "PSAR_GROUP": "G2",
    "PSAR_STEP": 0.02,
    "PSAR_MAX": 0.2,
    "PSAR_PROFIT_ONLY": True,
    "PSAR_PROFIT_BUFFER_POINTS": 0,
    # ANTI_CASH: đơn vị "USD" = tiền thô VND (account VN). Giá trị dưới là VND.
    "ANTI_CASH_USD": 250000.0,  # Ngưỡng cắt lỗ cứng (VND)
    "ANTI_CASH_HARD_STOP_UNIT": "USD",
    "ANTI_CASH_TIME": 3600,  # [NEW V4.4] Thời gian âm tối đa (giây) - 1 Giờ
    "ANTI_CASH_TIME_ENABLE": True,  # Bật/tắt Time Cut
    "ANTI_CASH_MAE_ENABLE": True,
    "ANTI_CASH_MAE_MAX_LOSS_USD": 600000.0,  # (VND)
    "ANTI_CASH_MAE_MAX_LOSS_UNIT": "USD",
    "ANTI_CASH_MAE_MIN_HOLD_SEC": 300,
    "ANTI_CASH_MAE_LOW_MFE_USD": 120000.0,  # (VND)
    "ANTI_CASH_MAE_LOW_MFE_UNIT": "USD",
    "ANTI_CASH_MFE_ENABLE": True,
    "ANTI_CASH_MFE_TRIGGER_USD": 700000.0,  # (VND)
    "ANTI_CASH_MFE_TRIGGER_UNIT": "USD",
    "ANTI_CASH_MFE_GIVEBACK_USD": 450000.0,  # (VND)
    "ANTI_CASH_MFE_GIVEBACK_UNIT": "USD",
    "ANTI_CASH_MFE_FLOOR_USD": 0.0,  # (VND)
    "ANTI_CASH_MFE_FLOOR_UNIT": "USD",
    "ANTI_CASH_REENTRY_LOCK_SEC": 900,
}

# ==============================================================================
# 6. LOGIC TSL SWING POINT BÁM THEO CẤU TRÚC GIÁ
# ==============================================================================
TSL_LOGIC_MODE = "STATIC"

# ==============================================================================
# 7. BỘ NÃO PHÂN TÍCH V4.2 (LEGO MASTER DEFAULTS)
# ==============================================================================
# Bỏ các biến trùng lặp (đã chuyển vào BOT_SAFEGUARD)
AUTO_TRADE_ENABLED = False
sl_atr_multiplier = 0.2

MASTER_EVAL_MODE = "VETO"
MIN_MATCHING_VOTES = 3

# Khung thời gian (resolution) dạng String để DataEngine map sang DNSE OHLC API
G0_TIMEFRAME = "1d"
G1_TIMEFRAME = "1h"
G2_TIMEFRAME = "15m"
G3_TIMEFRAME = "15m"

SANDBOX_CONFIG = {
    "voting_rules": {
        # CKCS mặc định: D1 chốt xu hướng, H1 xác nhận, M15 canh thời điểm.
        "G0": {"max_opposite": 0, "max_none": 0, "master_rule": "FIX"},
        "G1": {"max_opposite": 0, "max_none": 1, "master_rule": "PASS"},
        "G2": {"max_opposite": 0, "max_none": 2, "master_rule": "PASS"},
        "G3": {"max_opposite": 0, "max_none": 1, "master_rule": "IGNORE"},
    },
    "indicators": {
        # Bổ sung groups: [], is_trend, macro_role cho toàn bộ Indicator
        "adx": {
            "active": True,
            "groups": ["G1"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"period": 14, "strong": 23},
            "trigger_mode": "STRICT_CLOSE",
        },
        "ema": {
            "active": True,
            "groups": ["G0", "G1"],
            "is_trend": True,
            "macro_role": "BASE",
            "active_modes": ["ANY"],
            "params": {"period": 50},
            "trigger_mode": "STRICT_CLOSE",
            "group_params": {"G1": {"period": 20}},
        },
        "swing_point": {
            "active": True,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"lookback": 50, "strength": 2, "atr_buffer": 0.5},
            "trigger_mode": "STRICT_CLOSE",
        },
        "atr": {
            "active": False,
            "groups": ["G1"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"period": 14, "multiplier": 1.5},
            "trigger_mode": "REALTIME_TICK",
        },
        "pivot_points": {
            "active": False,
            "groups": ["G3"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {},
            "trigger_mode": "REALTIME_TICK",
        },
        "ema_cross": {
            "active": False,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["TREND"],
            "params": {"fast": 9, "slow": 21},
            "trigger_mode": "STRICT_CLOSE",
        },
        "volume": {
            "active": True,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"period": 20, "multiplier": 1.5},
            "trigger_mode": "STRICT_CLOSE",
        },
        "supertrend": {
            "active": True,
            "groups": ["G0"],
            "is_trend": True,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"period": 10, "multiplier": 3.0},
            "trigger_mode": "STRICT_CLOSE",
        },
        "psar": {
            "active": False,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["TREND"],
            "params": {"step": 0.02, "max_step": 0.2},
            "trigger_mode": "REALTIME_TICK",
        },
        "bollinger_bands": {
            "active": False,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["RANGE"],
            "params": {"period": 20, "std_dev": 2.0},
            "trigger_mode": "REALTIME_TICK",
        },
        "fibonacci": {
            "active": False,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["RANGE"],
            "params": {"tolerance": 0.001},
            "trigger_mode": "REALTIME_TICK",
        },
        "rsi": {
            "active": False,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["RANGE"],
            "params": {"period": 14, "upper": 70, "lower": 30},
            "trigger_mode": "STRICT_CLOSE",
        },
        "stochastic": {
            "active": False,
            "groups": ["G2"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["RANGE"],
            "params": {"k": 14, "d": 3, "smooth": 3, "upper": 80, "lower": 20},
            "trigger_mode": "STRICT_CLOSE",
        },
        "macd": {
            "active": True,
            "groups": ["G1"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"fast": 12, "slow": 26, "signal": 9},
            "trigger_mode": "STRICT_CLOSE",
        },
        "multi_candle": {
            "active": False,
            "groups": ["G3"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["EXHAUSTION"],
            "params": {"num_candles": 3, "min_total_pips": 50},
            "trigger_mode": "STRICT_CLOSE",
        },
        "candle": {
            "active": False,
            "groups": ["G3"],
            "is_trend": False,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"min_body_size": 1.2, "check_volume": True},
            "trigger_mode": "STRICT_CLOSE",
        },
        "simple_breakout": {
            "active": True,
            "groups": ["G2"],
            "is_trend": True,
            "macro_role": "NONE",
            "active_modes": ["ANY"],
            "params": {"lookback": 2, "atr_buffer": 0.0},
            "trigger_mode": "STRICT_CLOSE",
        },
    },
}

# CHECK/REPORT dùng cùng catalog LEGO với TRADE nhưng là một cấu hình độc lập.
# Chỉ clone thông số mặc định để UI có đủ danh sách module; không kế thừa trạng
# thái ON/OFF, vote, market-mode hay quyền đặt lệnh của bộ TRADE.
CHECK_INDICATORS = {}
for _check_name, _trade_default in SANDBOX_CONFIG["indicators"].items():
    CHECK_INDICATORS[_check_name] = {
        "active": False,
        "groups": copy.deepcopy(_trade_default.get("groups", ["G2"])),
        "params": copy.deepcopy(_trade_default.get("params", {})),
        "group_params": {},
    }

# ==============================================================================
# 8. TÍNH NĂNG NHỒI LỆNH (AUTO DCA/PCA & MINI-BRAIN)
# ==============================================================================
MINI_BRAIN_DEFAULT = {
    "active": False,
    "timeframe": "15m",
    "max_opposite": 0,
    "max_none": 0,
    "indicators": {},  # Tái sử dụng cấu trúc indicators của Sandbox
}

DCA_CONFIG = {
    "ENABLED": False,
    "MAX_STEPS": 3,
    "STEP_MULTIPLIER": 1.5,
    "DISTANCE_ATR_R": 1.0,
    "USE_PARENT_SL": True,
    "COOLDOWN": 60,
    "MINI_BRAIN": MINI_BRAIN_DEFAULT.copy(),
}
PCA_CONFIG = {
    "ENABLED": False,
    "MAX_STEPS": 2,
    "STEP_MULTIPLIER": 0.5,
    "DISTANCE_ATR_R": 1.5,
    "USE_PARENT_SL": True,
    "CONFIRM_ADX": 23,
    "MINI_BRAIN": MINI_BRAIN_DEFAULT.copy(),
}

# ==============================================================================
# 8. AI ADVISOR — catalog đa provider/model (setting GỐC ở đây; UI lưu JSON override)
# ==============================================================================
AI_ADVISOR_DEFAULT_PROVIDER = "openai"
ADVISOR_API_TIMEOUT_SECONDS = float(os.getenv("ADVISOR_API_TIMEOUT_SECONDS", "300"))
ADVISOR_API_RETRIES = int(os.getenv("ADVISOR_API_RETRIES", "2"))
AI_ADVISOR_DEFAULT_MAX_OUTPUT_TOKENS = 8000
# Mỗi provider: endpoint REST, biến môi trường chứa key, danh sách model, context + giá (USD/1M token).
AI_ADVISOR_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "endpoint": "https://api.openai.com/v1/responses",
        "env_key": "OPENAI_API_KEY",
        "models": ["gpt-5.6-terra", "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.4-mini", "gpt-5.4", "gpt-5.5"],
        "default_model": "gpt-5.6",
        "context_tokens": {
            "gpt-5.6-terra": 1050000,
            "gpt-5.6": 1050000,
            "gpt-5.6-sol": 1050000,
            "gpt-5.6-luna": 1050000,
            "gpt-5.4-mini": 400000,
            "gpt-5.4": 1000000,
            "gpt-5.5": 1000000,
        },
        "pricing": {
            "gpt-5.6-terra": {"input": 2.50, "output": 15.00},
            "gpt-5.6": {"input": 5.00, "output": 30.00},
            "gpt-5.6-sol": {"input": 5.00, "output": 30.00},
            "gpt-5.6-luna": {"input": 1.00, "output": 6.00},
            "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
            "gpt-5.4": {"input": 2.50, "output": 15.00},
            "gpt-5.5": {"input": 5.00, "output": 30.00},
        },
    },
    "anthropic": {
        "label": "Claude (Anthropic)",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "env_key": "ANTHROPIC_API_KEY",
        "models": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "default_model": "claude-sonnet-4-6",
        "context_tokens": {"claude-opus-4-8": 200000, "claude-sonnet-4-6": 200000, "claude-haiku-4-5": 200000},
        "pricing": {
            "claude-opus-4-8": {"input": 5.00, "output": 25.00},
            "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
            "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
        },
    },
}

# --- CKCS RAW DATA (kho dữ liệu quét độc lập với AI Advisor) ---
# Daemon quét mỗi vòng nhưng chỉ LƯU mẫu định kỳ; tín hiệu BUY/SELL thì ghi ngay.
SCAN_SNAPSHOT_ENABLED = os.getenv("SCAN_SNAPSHOT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
SCAN_SNAPSHOT_INTERVAL_MINUTES = float(os.getenv("SCAN_SNAPSHOT_INTERVAL_MINUTES", "15"))
SCAN_SNAPSHOT_RETENTION_DAYS = int(os.getenv("SCAN_SNAPSHOT_RETENTION_DAYS", "250"))
# Danh sách RAW DATA là động và độc lập với quyền vào lệnh BOT. Khi chưa có
# setting riêng, mặc định lấy toàn bộ mã hiện có (CKPS + CKCS).
SCAN_SNAPSHOT_SYMBOLS = list(dict.fromkeys(CKPS_SYMBOLS + CKCS_WATCHLIST))
