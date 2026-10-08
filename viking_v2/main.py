from __future__ import annotations

import argparse
import tkinter as tk

import customtkinter as ctk

from .dashboard.window import VikingApp
from .branding import APP_NAME


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} CKCS")
    parser.add_argument("--account", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("dark-blue")
    app = VikingApp(account_id=args.account)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C / terminal Stop is a normal user shutdown, not an app crash.
        try:
            if app.winfo_exists():
                app.close()
        except tk.TclError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
