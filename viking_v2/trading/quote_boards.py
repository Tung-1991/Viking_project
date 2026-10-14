"""Separate round-lot matched data from negotiated and odd-lot boards."""
from __future__ import annotations

from typing import Any


ROUND_LOT_BOARD = "G1"
MATCHED_DATA_POLICY = "G1_MATCH_V1"


def message_board(data: dict[str, Any], channel: Any = "") -> str:
    board = str(data.get("boardId", "") or "").strip().upper()
    if isinstance(channel, dict):
        channel = channel.get("name", "")
    parts = str(channel or "").split(".")
    channel_board = parts[1].upper() if len(parts) > 1 and parts[0] in {
        "tick", "tick_extra", "top_price", "expected_price",
    } else ""
    if board and channel_board and board != channel_board:
        return ""
    return board or channel_board


def select_round_lot_row(value: Any, symbol: str, *, scoped_request: bool = False) -> dict | None:
    """An explicitly G1-scoped REST request may return one legacy untagged row."""
    rows = value if isinstance(value, list) else [value]
    candidates = [row for row in rows if isinstance(row, dict)
                  and str(row.get("symbol", symbol) or "").strip().upper() == symbol.upper()]
    normal = next((row for row in candidates if message_board(row) == ROUND_LOT_BOARD), None)
    if normal is None and scoped_request and len(rows) == 1 and len(candidates) == 1:
        normal = candidates[0] if not candidates[0].get("boardId") else None
    return {**normal, "boardId": ROUND_LOT_BOARD} if normal is not None else None
