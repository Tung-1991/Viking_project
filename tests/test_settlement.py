# -*- coding: utf-8 -*-
from datetime import datetime

import config
from core import settlement as s


def test_is_cash_stock_distinguishes_derivative(monkeypatch):
    monkeypatch.setattr(config, "CKPS_SYMBOLS", ["VN30F1M"])
    assert s.is_cash_stock("FPT") is True
    assert s.is_cash_stock("CTG") is True
    assert s.is_cash_stock("VN30F1M") is False
    assert s.is_cash_stock("VN30F2M") is False


def test_add_working_days_skips_weekend_fallback():
    # Thứ 4 18/06/2026 + 2 ngày làm việc = Thứ 6 20/06? (18=T5 thực tế tùy năm)
    start = datetime(2026, 6, 18)  # Thứ 5
    end = s.add_working_days(start, 2, working_dates=None)
    # T5 -> T6 (1) -> T2 tuần sau (2), bỏ T7/CN
    assert end.weekday() < 5  # phải là ngày trong tuần


def test_add_working_days_friday_rolls_over_weekend():
    start = datetime(2026, 6, 19)  # Thứ 6
    end = s.add_working_days(start, 2, working_dates=None)
    # T6 -> T2 (1) -> T3 (2)
    assert end == datetime(2026, 6, 23)  # Thứ 3 tuần sau


def test_add_working_days_uses_api_calendar_excluding_holiday():
    # Giả lập lịch có nghỉ lễ 22/06 (bỏ khỏi danh sách)
    wd = ["2026-06-19", "2026-06-23", "2026-06-24", "2026-06-25"]  # 22 nghỉ lễ
    start = datetime(2026, 6, 19)  # Thứ 6
    end = s.settle_date(start, working_dates=wd)
    assert end == datetime(2026, 6, 24)  # T+2 = 24 (bỏ qua 22 lễ)


def test_available_and_pending_to_sell():
    today = datetime(2026, 6, 25)
    positions = [
        {"symbol": "FPT", "type": 0, "volume": 100, "settle_date": "2026-06-24"},  # đã về
        {"symbol": "FPT", "type": 0, "volume": 50, "settle_date": "2026-06-29"},   # chưa về
        {"symbol": "CTG", "type": 0, "volume": 200, "settle_date": "2026-06-25"},  # về hôm nay
    ]
    assert s.available_to_sell(positions, "FPT", today) == 100
    assert s.pending_to_settle(positions, "FPT", today) == 50
    assert s.available_to_sell(positions, "CTG", today) == 200


def test_no_settle_date_treated_as_settled():
    # Vị thế cũ không có settle_date -> coi như đã về (an toàn)
    today = datetime(2026, 6, 25)
    positions = [{"symbol": "FPT", "type": 0, "volume": 100}]
    assert s.available_to_sell(positions, "FPT", today) == 100
