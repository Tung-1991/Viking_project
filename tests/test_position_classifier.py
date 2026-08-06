# -*- coding: utf-8 -*-

import unittest
from types import SimpleNamespace

from core.position_classifier import (
    is_bot_position,
    is_external_position,
    is_manual_position,
)


class PositionClassifierTests(unittest.TestCase):
    def setUp(self):
        self.magics = {
            "bot_magic": 11,
            "manual_magic": 22,
        }

    def test_classifies_by_magic(self):
        self.assertTrue(is_bot_position(SimpleNamespace(magic=11, comment=""), self.magics))
        self.assertTrue(is_manual_position(SimpleNamespace(magic=22, comment=""), self.magics))

    def test_classifies_by_comment(self):
        self.assertTrue(is_bot_position(SimpleNamespace(magic=0, comment="[BOT] BUY"), self.magics))
        self.assertTrue(is_manual_position(SimpleNamespace(magic=0, comment="[USER] SELL"), self.magics))

    def test_none_is_not_classified(self):
        self.assertFalse(is_bot_position(None, self.magics))
        self.assertFalse(is_manual_position(None, self.magics))
        self.assertFalse(is_external_position(None, self.magics))

    def test_external_position_has_no_app_magic_or_comment(self):
        position = SimpleNamespace(magic=0, comment="")
        self.assertTrue(is_external_position(position, self.magics))
        self.assertFalse(
            is_external_position(
                SimpleNamespace(magic=11, comment=""),
                self.magics,
            )
        )


if __name__ == "__main__":
    unittest.main()
