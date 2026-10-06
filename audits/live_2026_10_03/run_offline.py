"""Run the existing suite offline against an isolated runtime, without real keys."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
for key in list(os.environ):
    if key.startswith(("DNSE_", "TELE_", "TELEGRAM_")):
        os.environ.pop(key, None)

import viking_v2.config as config


def block_network(*args, **kwargs):
    raise RuntimeError("AUDIT_NETWORK_DISABLED")


socket.socket.connect = block_network
socket.socket.connect_ex = block_network
socket.create_connection = block_network

with tempfile.TemporaryDirectory(prefix="viking-audit-tests-") as directory:
    root = Path(directory)
    config.RUNTIME_ROOT = root
    config.ACCOUNTS_ROOT = root / "accounts"
    config.ENV_PATH = root / ".env"
    config.update_env.__defaults__ = (config.ENV_PATH,)

    import pytest

    raise SystemExit(pytest.main(sys.argv[1:] or ["tests_v2", "-q", "--disable-warnings", "--tb=short"]))
