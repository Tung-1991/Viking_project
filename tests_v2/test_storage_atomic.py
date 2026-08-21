from __future__ import annotations

import os

from viking_v2.storage import AtomicJSONStore


def test_atomic_json_store_retries_short_windows_replace_lock(tmp_path, monkeypatch):
    store = AtomicJSONStore(tmp_path / "runtime_status.json", default={})
    original = os.replace
    calls = 0

    def briefly_locked(source, target):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "Access is denied")
        original(source, target)

    monkeypatch.setattr("viking_v2.storage.os.replace", briefly_locked)
    store.write({"heartbeat_at": 123})

    assert calls == 3
    assert store.read() == {"heartbeat_at": 123}
