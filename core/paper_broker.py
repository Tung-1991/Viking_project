# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import config
from core import settlement
from core.dnse_connector import (
    BrokerOrderResult,
    BrokerPosition,
    BrokerTick,
    DNSE_POINT_VALUE,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
)


class PaperBroker:
    def __init__(
        self,
        account_no: str,
        tick_provider: Optional[Callable[[str], Optional[BrokerTick]]] = None,
        fee_profile_provider: Optional[Callable[[str], Any]] = None,
        working_dates_provider: Optional[Callable[[], List[str]]] = None,
    ):
        self.account_no = account_no or "PAPER"
        self.tick_provider = tick_provider
        self.fee_profile_provider = fee_profile_provider
        self.working_dates_provider = working_dates_provider
        self.state_path = os.path.join("data", "paper", f"{self.account_no}_state.json")
        self.state = self._load_state()

    def _working_dates(self) -> List[str]:
        if not self.working_dates_provider:
            return []
        try:
            return self.working_dates_provider() or []
        except Exception:
            return []

    def available_to_sell(self, symbol: str) -> float:
        return settlement.available_to_sell(self.state.get("positions", []), symbol)

    def pending_to_settle(self, symbol: str) -> float:
        return settlement.pending_to_settle(self.state.get("positions", []), symbol)

    def _default_balance(self) -> float:
        return float(getattr(config, "PAPER_INITIAL_BALANCE", 100000000.0))

    def _point_value(self, symbol: str) -> float:
        profile = self._real_fee_profile(symbol)
        if profile:
            if hasattr(profile, "point_value"):
                return float(getattr(profile, "point_value", 1.0) or 1.0)
            if isinstance(profile, dict):
                return float(profile.get("point_value", 1.0) or 1.0)
        if str(symbol or "").upper().startswith("VN30F"):
            return float(getattr(config, "DNSE_POINT_VALUE", DNSE_POINT_VALUE) or DNSE_POINT_VALUE)
        return float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0)

    def _fee_per_contract(self, symbol: str) -> float:
        profile = self._real_fee_profile(symbol)
        if profile:
            if hasattr(profile, "fixed_per_contract"):
                return float(profile.fixed_per_contract())
            if isinstance(profile, dict):
                return (
                    float(profile.get("broker_fee_per_contract", 0.0) or 0.0)
                    + float(profile.get("exchange_fee_per_contract", 0.0) or 0.0)
                    + float(profile.get("clearing_fee_per_contract", 0.0) or 0.0)
                )
        return self._broker_fee_per_contract() + self._exchange_fee_per_contract() + self._clearing_fee_per_contract()

    def _real_fee_profile(self, symbol: Optional[str] = None):
        if not self.fee_profile_provider:
            return None
        try:
            return self.fee_profile_provider(symbol or getattr(config, "DEFAULT_SYMBOL", "VN30F1M"))
        except Exception:
            return None

    def _broker_fee_per_contract(self) -> float:
        return float(
            getattr(
                config,
                "DNSE_BROKER_FEE_PER_CONTRACT",
                getattr(config, "PAPER_FEE_PER_CONTRACT", 0.0),
            )
            or 0.0
        )

    def _exchange_fee_per_contract(self) -> float:
        return float(getattr(config, "DNSE_EXCHANGE_FEE_PER_CONTRACT", 2700.0) or 0.0)

    def _clearing_fee_per_contract(self) -> float:
        return float(getattr(config, "DNSE_CLEARING_FEE_PER_CONTRACT", 2550.0) or 0.0)

    def _broker_fee_rate(self, symbol: str) -> float:
        profile = self._real_fee_profile(symbol)
        if profile:
            if hasattr(profile, "broker_fee_rate"):
                return float(getattr(profile, "broker_fee_rate", 0.0) or 0.0)
            if isinstance(profile, dict):
                return float(profile.get("broker_fee_rate", 0.0) or 0.0)
        return 0.0

    def _tax_rate(self, symbol: str) -> float:
        profile = self._real_fee_profile(symbol)
        if profile:
            if hasattr(profile, "tax_rate"):
                return float(getattr(profile, "tax_rate", 0.0) or 0.0)
            if isinstance(profile, dict):
                return float(profile.get("tax_rate", 0.0) or 0.0)
        return float(getattr(config, "DNSE_TAX_RATE", 0.0) or 0.0)

    def fee_profile(self, symbol: Optional[str] = None) -> Dict[str, float]:
        profile = self._real_fee_profile(symbol)
        if profile:
            if hasattr(profile, "as_dict"):
                return profile.as_dict()
            if isinstance(profile, dict):
                return dict(profile)
        return {
            "broker_fee_per_contract": self._broker_fee_per_contract(),
            "exchange_fee_per_contract": self._exchange_fee_per_contract(),
            "clearing_fee_per_contract": self._clearing_fee_per_contract(),
            "broker_fee_rate": self._broker_fee_rate(symbol or getattr(config, "DEFAULT_SYMBOL", "VN30F1M")),
            "tax_rate": self._tax_rate(symbol or getattr(config, "DEFAULT_SYMBOL", "VN30F1M")),
            "point_value": self._point_value(symbol or getattr(config, "DEFAULT_SYMBOL", "VN30F1M")),
            "source": "fallback",
        }

    def _calc_fee(self, symbol: str, price: float, qty: float, side: Any = None) -> float:
        qty = max(0.0, float(qty or 0.0))
        profile = self._real_fee_profile(symbol)
        if profile is not None and hasattr(profile, "estimate_fee"):
            return float(profile.estimate_fee(price, qty, side=side))
        point_value = self._point_value(symbol)
        fixed = self._fee_per_contract(symbol) * qty
        notional = max(0.0, float(price or 0.0)) * qty * point_value
        rate_fee = notional * self._broker_fee_rate(symbol)
        if settlement.is_cash_stock(symbol):
            side_key = str(side or "").upper()
            tax = notional * self._tax_rate(symbol) if (
                not side_key or side_key in {"1", "SELL", "SHORT", "NS", "S"}
            ) else 0.0
        else:
            initial_margin_rate = float(
                getattr(profile, "initial_margin_rate", 0.0)
                if profile is not None
                else getattr(config, "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE", 0.20)
            )
            tax = notional * initial_margin_rate / 2.0 * self._tax_rate(symbol)
        return fixed + rate_fee + tax

    def _spread_points(self) -> float:
        return float(getattr(config, "PAPER_SPREAD_POINTS", 0.0) or 0.0)

    def _load_state(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data.setdefault("balance", self._default_balance())
                        data.setdefault("realized_pnl", 0.0)
                        data.setdefault("next_ticket", 1)
                        data.setdefault("positions", [])
                        data.setdefault("closed_trades", {})
                        return data
        except Exception:
            pass
        return {
            "balance": self._default_balance(),
            "realized_pnl": 0.0,
            "next_ticket": 1,
            "positions": [],
            "closed_trades": {},
        }

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def reset(self, balance: Optional[float] = None) -> Dict[str, Any]:
        self.state = {
            "balance": float(balance if balance is not None else self._default_balance()),
            "realized_pnl": 0.0,
            "next_ticket": 1,
            "positions": [],
            "closed_trades": {},
        }
        self._save_state()
        return self.get_account_info()

    def _record_closed_trade(self, position: BrokerPosition, reason: str) -> None:
        ticket = str(position.ticket)
        closed = self.state.setdefault("closed_trades", {})
        raw = position.raw or {}
        mae_value = raw.get("mae", position.profit)
        mfe_value = raw.get("mfe", position.profit)
        closed[ticket] = {
            "ticket": ticket,
            "symbol": position.symbol,
            "type": int(position.type),
            "volume": float(position.volume or 0.0),
            "price_open": float(position.price_open or 0.0),
            "price_close": float(position.price_current or 0.0),
            "sl": float(position.sl or 0.0),
            "tp": float(position.tp or 0.0),
            "fee": float(position.commission or 0.0),
            "profit": float(position.profit or 0.0),
            "mae": float(mae_value if mae_value is not None else position.profit or 0.0),
            "mfe": float(mfe_value if mfe_value is not None else position.profit or 0.0),
            "reason": str(reason or "CLOSED"),
            "open_time": float(position.time or 0.0),
            "closed_at": time.time(),
        }
        # Chỉ cần giữ lịch sử gần nhất để file PAPER không phình vô hạn.
        if len(closed) > 500:
            ordered = sorted(closed.items(), key=lambda item: item[1].get("closed_at", 0.0))
            self.state["closed_trades"] = dict(ordered[-500:])

    def get_closed_trade(self, ticket: Any) -> Optional[Dict[str, Any]]:
        item = self.state.get("closed_trades", {}).get(str(ticket))
        return dict(item) if isinstance(item, dict) else None

    def get_closed_trades(self) -> List[Dict[str, Any]]:
        rows = [
            dict(item)
            for item in self.state.get("closed_trades", {}).values()
            if isinstance(item, dict)
        ]
        return sorted(rows, key=lambda item: float(item.get("closed_at", 0.0) or 0.0), reverse=True)

    def delete_closed_trades_for_day(self, day: str) -> int:
        target = str(day or "").strip()
        try:
            target = datetime.strptime(target, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return 0
        closed = self.state.get("closed_trades", {})
        kept = {}
        removed = 0
        for ticket, item in closed.items():
            closed_at = float((item or {}).get("closed_at", 0.0) or 0.0)
            item_day = datetime.fromtimestamp(closed_at).strftime("%Y-%m-%d") if closed_at else ""
            if item_day == target:
                removed += 1
            else:
                kept[ticket] = item
        if removed:
            self.state["closed_trades"] = kept
            self._save_state()
        return removed

    def _get_tick(self, symbol: str) -> Optional[BrokerTick]:
        if self.tick_provider:
            try:
                return self.tick_provider(symbol)
            except Exception:
                return None
        return None

    def _fallback_price(self, symbol: str) -> float:
        return float(getattr(config, "PAPER_FALLBACK_PRICE", 0.0) or 0.0)

    def _fill_price(self, symbol: str, side: int, requested_price: float = 0.0) -> float:
        if requested_price:
            return float(requested_price)
        tick = self._get_tick(symbol)
        spread = self._spread_points()
        if tick:
            if side == ORDER_TYPE_BUY:
                price = float(tick.ask or tick.last or tick.bid or 0.0)
                return price + (spread / 2.0)
            price = float(tick.bid or tick.last or tick.ask or 0.0)
            return price - (spread / 2.0)
        return self._fallback_price(symbol)

    def _normalize_side(self, order_type: Any) -> int:
        text = str(order_type).upper()
        return ORDER_TYPE_BUY if text in ("0", "BUY", "LONG", "NB") else ORDER_TYPE_SELL

    def _calc_pnl(self, pos: Dict[str, Any], current_price: float) -> float:
        direction = 1.0 if int(pos.get("type", ORDER_TYPE_BUY)) == ORDER_TYPE_BUY else -1.0
        qty = float(pos.get("volume", 0.0) or 0.0)
        entry = float(pos.get("price_open", 0.0) or 0.0)
        open_fee = float(pos.get("open_fee", pos.get("commission", 0.0)) or 0.0)
        symbol = str(pos.get("symbol", "")).upper()
        exit_side = ORDER_TYPE_SELL if int(pos.get("type", ORDER_TYPE_BUY)) == ORDER_TYPE_BUY else ORDER_TYPE_BUY
        exit_fee = self._calc_fee(symbol, current_price, qty, side=exit_side)
        fee = open_fee + exit_fee
        return ((current_price - entry) * direction * qty * self._point_value(symbol)) - fee

    def _current_price_for_position(self, pos: Dict[str, Any]) -> float:
        symbol = str(pos.get("symbol", "")).upper()
        side = int(pos.get("type", ORDER_TYPE_BUY))
        tick = self._get_tick(symbol)
        if tick:
            if side == ORDER_TYPE_BUY:
                return float(tick.bid or tick.last or tick.ask or pos.get("price_current") or pos.get("price_open") or 0.0)
            return float(tick.ask or tick.last or tick.bid or pos.get("price_current") or pos.get("price_open") or 0.0)
        return float(pos.get("price_current") or pos.get("price_open") or 0.0)

    def _position_from_state(self, pos: Dict[str, Any]) -> BrokerPosition:
        if "open_fee" not in pos:
            pos["open_fee"] = float(pos.get("commission", 0.0) or 0.0)
        current = self._current_price_for_position(pos)
        profit = self._calc_pnl(pos, current)
        qty = float(pos.get("volume", 0.0) or 0.0)
        open_fee = float(pos.get("open_fee", pos.get("commission", 0.0)) or 0.0)
        exit_side = ORDER_TYPE_SELL if int(pos.get("type", ORDER_TYPE_BUY)) == ORDER_TYPE_BUY else ORDER_TYPE_BUY
        estimated_close_fee = self._calc_fee(
            str(pos.get("symbol", "")).upper(), current, qty, side=exit_side
        )
        total_fee = open_fee + estimated_close_fee
        pos["price_current"] = current
        pos["profit"] = profit
        pos["commission"] = total_fee
        pos["estimated_close_fee"] = estimated_close_fee
        pos["mfe"] = max(float(pos.get("mfe", profit) or profit), profit)
        pos["mae"] = min(float(pos.get("mae", profit) or profit), profit)
        return BrokerPosition(
            ticket=str(pos.get("ticket", "")),
            position_id=str(pos.get("position_id", pos.get("ticket", ""))),
            order_id=str(pos.get("order_id", pos.get("ticket", ""))),
            symbol=str(pos.get("symbol", "")).upper(),
            type=int(pos.get("type", ORDER_TYPE_BUY)),
            volume=float(pos.get("volume", 0.0) or 0.0),
            price_open=float(pos.get("price_open", 0.0) or 0.0),
            price_current=current,
            profit=profit,
            commission=total_fee,
            sl=float(pos.get("sl", 0.0) or 0.0),
            tp=float(pos.get("tp", 0.0) or 0.0),
            comment=str(pos.get("comment", "")),
            magic=int(pos.get("magic", 0) or 0),
            time=float(pos.get("time", time.time()) or time.time()),
            raw=dict(pos),
        )

    def _apply_stop_hits(self):
        survivors: List[Dict[str, Any]] = []
        changed = False
        for pos in list(self.state.get("positions", [])):
            broker_pos = self._position_from_state(pos)
            price = broker_pos.price_current
            side = broker_pos.type
            sl = float(pos.get("sl", 0.0) or 0.0)
            tp = float(pos.get("tp", 0.0) or 0.0)
            hit_sl = sl > 0 and ((side == ORDER_TYPE_BUY and price <= sl) or (side == ORDER_TYPE_SELL and price >= sl))
            hit_tp = tp > 0 and ((side == ORDER_TYPE_BUY and price >= tp) or (side == ORDER_TYPE_SELL and price <= tp))
            if hit_sl or hit_tp:
                # T+2: cổ phiếu chưa về thì TREO lệnh — giữ vị thế, tự đóng ngay khi đã về.
                if settlement.is_cash_stock(pos.get("symbol")) and not settlement.is_settled(pos.get("settle_date")):
                    survivors.append(pos)
                    continue
                self.state["realized_pnl"] = float(self.state.get("realized_pnl", 0.0) or 0.0) + broker_pos.profit
                self._record_closed_trade(broker_pos, "SL" if hit_sl else "TP")
                changed = True
            else:
                survivors.append(pos)
        if changed:
            self.state["positions"] = survivors
            self._save_state()

    def get_positions(self) -> List[BrokerPosition]:
        self._apply_stop_hits()
        positions = [self._position_from_state(pos) for pos in self.state.get("positions", [])]
        self._save_state()
        return positions

    def get_account_info(self) -> Dict[str, Any]:
        positions = self.get_positions()
        floating = sum(float(p.profit or 0.0) for p in positions)
        balance = float(self.state.get("balance", self._default_balance()) or 0.0) + float(self.state.get("realized_pnl", 0.0) or 0.0)
        equity = balance + floating
        return {
            "login": self.account_no,
            "server": "DNSE_API_PAPER",
            "status": "PAPER",
            "balance": balance,
            "equity": equity,
            "margin": 0.0,
            "free_margin": equity,
            "margin_free": equity,
            "margin_level": 0.0,
            "cash_available": equity,
            "buying_power": equity,
            "margin_debt": 0.0,
            "rtt": None,
            "margin_call_level": 87.0,
            "margin_force_level": 80.0,
            "positions": len(positions),
            "realized_pnl": float(self.state.get("realized_pnl", 0.0) or 0.0),
        }

    def place_order(
        self,
        symbol: str,
        order_type: Any,
        volume: float,
        price: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        magic: int = 0,
        comment: str = "",
    ) -> BrokerOrderResult:
        side = self._normalize_side(order_type)
        qty = max(1, int(round(float(volume or 0.0))))
        symbol_key = str(symbol).upper()

        # T+2: cổ phiếu cơ sở không bán khống. Lệnh SELL phải có long ĐÃ VỀ.
        is_stock = settlement.is_cash_stock(symbol_key)
        if side == ORDER_TYPE_SELL and is_stock:
            avail = self.available_to_sell(symbol_key)
            if avail <= 0:
                pending = self.pending_to_settle(symbol_key)
                msg = (
                    f"Cổ phiếu {symbol_key} chưa về T+2 (chờ về: {pending:.0f}), chưa bán được."
                    if pending > 0
                    else f"Không có cổ phiếu {symbol_key} để bán (không bán khống CKCS)."
                )
                return BrokerOrderResult(ok=False, error="STOCK_NOT_SETTLED_T2", message=msg)

        fill = self._fill_price(symbol, side, price)
        ticket = f"PAPER-{int(self.state.get('next_ticket', 1))}"
        self.state["next_ticket"] = int(self.state.get("next_ticket", 1)) + 1
        open_fee = self._calc_fee(symbol_key, fill, qty, side=side)
        # Cổ phiếu mua hôm nay → ghi ngày về T+2 (phái sinh để trống = bán bất kỳ lúc nào).
        settle = ""
        if side == ORDER_TYPE_BUY and is_stock:
            settle = settlement.settle_date_str(datetime.now(), self._working_dates())
        pos = {
            "ticket": ticket,
            "position_id": ticket,
            "order_id": ticket,
            "symbol": symbol_key,
            "type": side,
            "volume": float(qty),
            "price_open": fill,
            "price_current": fill,
            "profit": -open_fee,
            "open_fee": open_fee,
            "estimated_close_fee": self._calc_fee(
                symbol_key,
                fill,
                qty,
                side=ORDER_TYPE_SELL if side == ORDER_TYPE_BUY else ORDER_TYPE_BUY,
            ),
            "commission": open_fee,
            "settle_date": settle,
            "sl": float(sl or 0.0),
            "tp": float(tp or 0.0),
            "comment": comment,
            "magic": int(magic or 0),
            "time": time.time(),
            "mae": -open_fee,
            "mfe": -open_fee,
        }
        self.state.setdefault("positions", []).append(pos)
        self._save_state()
        return BrokerOrderResult(
            ok=True,
            order_id=ticket,
            position_id=ticket,
            status="FILLED",
            message="PAPER_FILLED",
            raw=pos,
        )

    def modify_position(self, position_or_ticket: Any, sl: float = 0.0, tp: float = 0.0) -> BrokerOrderResult:
        ticket = str(getattr(position_or_ticket, "position_id", None) or getattr(position_or_ticket, "ticket", None) or position_or_ticket)
        for pos in self.state.get("positions", []):
            if str(pos.get("ticket")) == ticket or str(pos.get("position_id")) == ticket:
                pos["sl"] = float(sl or 0.0)
                pos["tp"] = float(tp or 0.0)
                self._save_state()
                return BrokerOrderResult(ok=True, order_id=ticket, position_id=ticket, status="MODIFIED", message="PAPER_MODIFIED", raw=dict(pos))
        return BrokerOrderResult(ok=False, position_id=ticket, error="PAPER_POSITION_NOT_FOUND", message="Paper position not found.")

    def close_position(self, position_or_ticket: Any, comment: str = "") -> BrokerOrderResult:
        ticket = str(getattr(position_or_ticket, "position_id", None) or getattr(position_or_ticket, "ticket", None) or position_or_ticket)
        survivors: List[Dict[str, Any]] = []
        closed: Optional[BrokerPosition] = None
        for pos in self.state.get("positions", []):
            if str(pos.get("ticket")) == ticket or str(pos.get("position_id")) == ticket:
                # T+2: cổ phiếu chưa về thì chưa bán/đóng được (caller treo lệnh chờ về).
                if (
                    int(pos.get("type", 0)) == ORDER_TYPE_BUY
                    and settlement.is_cash_stock(pos.get("symbol"))
                    and not settlement.is_settled(pos.get("settle_date"))
                ):
                    return BrokerOrderResult(
                        ok=False,
                        position_id=ticket,
                        error="STOCK_NOT_SETTLED_T2",
                        message=f"Cổ phiếu {pos.get('symbol')} chưa về T+2 (về {str(pos.get('settle_date'))[:10]}), chưa đóng được.",
                    )
                closed = self._position_from_state(pos)
                self.state["realized_pnl"] = float(self.state.get("realized_pnl", 0.0) or 0.0) + closed.profit
                self._record_closed_trade(closed, comment or "MANUAL_CLOSE")
            else:
                survivors.append(pos)
        self.state["positions"] = survivors
        self._save_state()
        if not closed:
            return BrokerOrderResult(ok=False, position_id=ticket, error="PAPER_POSITION_NOT_FOUND", message="Paper position not found.")
        return BrokerOrderResult(
            ok=True,
            order_id=closed.order_id,
            position_id=closed.position_id,
            status="CLOSED",
            message=comment or "PAPER_CLOSED",
            raw=closed.raw,
        )
