"""Read-only IDC/other symbol indicator replay: no orders, OTP or Telegram."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from viking_v2 import config
from viking_v2.backtest.data import normalize_ohlc
from viking_v2.backtest.entry_audit import audit_entry_day
from viking_v2.connections.dnse.client import DNSEClient
from viking_v2.services.signal_trace import export_trace
from viking_v2.trading.market import VN_TZ


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--account", default=config.active_account_id())
    parser.add_argument("--exchange", choices=["HOSE", "HNX", "UPCOM"], required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-api", action="store_true", help="Explicitly allow read-only DNSE OHLC GETs")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing output report")
    args = parser.parse_args()
    if not args.allow_api:
        parser.error("Dùng --allow-api để xác nhận chỉ đọc lịch sử DNSE.")
    root = config.account_root(args.account)
    settings = config.load_settings(args.account)
    cache = json.loads((root / "market_bars.json").read_text(encoding="utf-8-sig"))
    symbol = args.symbol.strip().upper()
    cached_daily = cache.get("symbols", {}).get(symbol, [])
    start = int(datetime.combine(args.date, datetime.min.time(), VN_TZ).timestamp())
    client = DNSEClient()
    try:
        minutes = normalize_ohlc(client.get_ohlc(symbol, "1", start, start + 86400 - 1))
        api_daily = normalize_ohlc(client.get_ohlc(symbol, "1D", start - 450 * 86400, start - 1))
    finally:
        client.close()
    if not minutes:
        raise RuntimeError("API không có nến 1 phút ngày yêu cầu; không dựng dữ liệu giả.")
    report = []
    for source, daily in (("DNSE LIVE CACHE", cached_daily), ("DNSE API HIỆN TẠI", api_daily)):
        if len(daily) < 100:
            print(f"[SKIP] {source}: thiếu nến 1D trước phiên")
            continue
        rows = audit_entry_day(symbol, args.date, daily, minutes, settings, exchange=args.exchange, baseline_source=source)
        report.extend(rows)
        afternoon = [r for r in rows if "T14:" in r["timestamp"] and r["timestamp"][14:16] <= "30"]
        print(f"[{source}] 14:00–14:30: {len(afternoon)} mẫu 1m; EMA/RSI đạt {sum(r['entry'] for r in afternoon)}; "
              f"EMA vừa cắt lên {sum(r['fresh_cross_entry'] for r in afternoon)}")
        if afternoon:
            r = afternoon[0]
            print(f"[{r['timestamp']}] Giá {r['price_vnd']:,.0f} · EMA {r['ema_comparison']} · RSI {r['rsi_comparison']} "
                  f"(phiên {r['rsi_previous_date']}) · WHIPSAW {r['whipsaw_count']}/{r['whipsaw_limit']} · {r['reason']}")
    if not report:
        raise RuntimeError("Không đủ nền 1D; không xuất báo cáo trống.")
    if args.output.exists() and not args.overwrite:
        raise FileExistsError(f"Không ghi đè báo cáo đã có: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    export_trace(report, args.output)
    print(f"[OUTPUT] {args.output.resolve()}")
    print("[LIMIT] Đây là phát lại close 1 phút + nền 1D, không phải nhật ký tick/lệnh thật VPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
