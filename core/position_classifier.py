# -*- coding: utf-8 -*-
"""Shared helpers for classifying trade ownership by magic/comment."""

from typing import Any, Dict, Optional


BOT_COMMENT_PREFIX = "[BOT]"
MANUAL_COMMENT_PREFIX = "[USER]"


def _comment(obj: Any) -> str:
    return str(getattr(obj, "comment", "") or "")


def _magic(obj: Any) -> Optional[int]:
    try:
        return int(getattr(obj, "magic", None))
    except (TypeError, ValueError):
        return None


def is_bot_position(pos: Any, magics: Optional[Dict[str, int]] = None) -> bool:
    if pos is None:
        return False
    bot_magic = (magics or {}).get("bot_magic")
    comment = _comment(pos)
    return (bot_magic is not None and _magic(pos) == int(bot_magic)) or BOT_COMMENT_PREFIX in comment


def is_manual_position(pos: Any, magics: Optional[Dict[str, int]] = None) -> bool:
    if pos is None:
        return False
    manual_magic = (magics or {}).get("manual_magic")
    comment = _comment(pos)
    return (manual_magic is not None and _magic(pos) == int(manual_magic)) or MANUAL_COMMENT_PREFIX in comment


def is_external_position(pos: Any, magics: Optional[Dict[str, int]] = None) -> bool:
    """A broker position that was not opened/tagged by this application."""
    return bool(
        pos is not None
        and not is_bot_position(pos, magics)
        and not is_manual_position(pos, magics)
    )
