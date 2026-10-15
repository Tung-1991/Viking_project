"""Readable hints with bounded widths and no empty panel below short text."""
import tkinter as tk

import customtkinter as ctk

HINT_SIZE = (500, 240)
COMPACT_HINT_SIZE = (350, 180)


def fixed_hint(host, text, *, compact=False):
    scale = ctk.ScalingTracker.get_widget_scaling(host)
    limit = COMPACT_HINT_SIZE if compact else HINT_SIZE
    width = min(round(limit[0] * scale), max(1, host.winfo_width() - 24))
    max_height = min(round(limit[1] * scale), max(1, host.winfo_height() - 24))
    padx, pady = round(12 * scale), round(8 * scale)
    font = ("Segoe UI", -round(14 * scale))
    # Measure wrapped text before constructing the viewport. Short hints fit
    # their content; only long details need the bounded, scrollable panel.
    # Reserve scrollbar space so adding it cannot introduce clipped lines.
    measure = tk.Label(host, text=text, font=font, justify="left", borderwidth=0,
                       highlightthickness=0, padx=padx, pady=pady,
                       wraplength=max(1, width - 2 * padx - round(18 * scale)))
    if compact:
        width = min(width, measure.winfo_reqwidth() + round(18 * scale) + 2)
    height = min(max_height, measure.winfo_reqheight() + 2)
    measure.destroy()
    box = tk.Frame(host, width=width, height=height, bg="#252A31", highlightthickness=1,
                   highlightbackground="#59616E")
    box.grid_propagate(False)
    box.grid_rowconfigure(0, weight=1)
    box.grid_columnconfigure(0, weight=1)
    body = tk.Text(box, wrap="word", font=font,
                   bg="#252A31", fg="#F5F7FA", relief="flat", borderwidth=0,
                   highlightthickness=0, cursor="arrow", padx=padx,
                   pady=pady, spacing1=0, spacing3=0,
                   exportselection=False)
    body.grid(row=0, column=0, sticky="nsew")
    scroll = tk.Scrollbar(box, orient="vertical", command=body.yview)
    scroll.grid(row=0, column=1, sticky="ns", padx=(0, 3), pady=3)
    def viewport(first, last):
        scroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            scroll.grid_remove()
        else:
            scroll.grid()
    body.configure(yscrollcommand=viewport)
    body.insert("1.0", text)
    body.configure(state="disabled")
    box.body = body
    return box


def contains_pointer(widget):
    if widget is None or not widget.winfo_exists():
        return False
    x, y = widget.winfo_pointerxy()
    return (widget.winfo_rootx() <= x < widget.winfo_rootx() + widget.winfo_width()
            and widget.winfo_rooty() <= y < widget.winfo_rooty() + widget.winfo_height())
