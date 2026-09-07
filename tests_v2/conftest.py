from __future__ import annotations

import customtkinter as ctk
import pytest


@pytest.fixture(scope="session")
def ui_root():
    """Reuse one Tcl interpreter; Windows Store Python cannot reliably reopen it."""
    root = ctk.CTk()
    root.withdraw()
    yield root
    if root.winfo_exists():
        root.destroy()
