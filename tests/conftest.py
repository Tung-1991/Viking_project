# -*- coding: utf-8 -*-
import shutil
from pathlib import Path

import pytest

import config


@pytest.fixture(autouse=True)
def cleanup_repo_test_account():
    # Tắt lưu token xuống đĩa trong test (cô lập, không đụng file thật / không phụ thuộc .env).
    _prev_persist = getattr(config, "PERSIST_TRADING_TOKEN", False)
    config.PERSIST_TRADING_TOKEN = False
    repo_root = Path(__file__).resolve().parents[1]
    artifacts = [repo_root / "data" / "TEST_ACCOUNT", repo_root / "data" / "ACC1"]
    for a in artifacts:
        if a.exists():
            shutil.rmtree(a)
    yield
    config.PERSIST_TRADING_TOKEN = _prev_persist
    for a in artifacts:
        if a.exists():
            shutil.rmtree(a)
