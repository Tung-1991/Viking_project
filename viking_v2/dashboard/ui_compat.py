"""Keep a CustomTkinter DPI update from pumping a half-rescaled layout."""
from contextlib import contextmanager
from functools import wraps

import customtkinter as ctk
from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

_missing = object()


@contextmanager
def _scaling_layout(windows):
    previous = [(window, window._block_update_dimensions_event) for window in windows]
    for window, _value in previous:
        window._block_update_dimensions_event = True
    try:
        yield
    finally:
        for window, value in previous:
            window._block_update_dimensions_event = value


def _without_idle_flush(draw):
    @wraps(draw)
    def deferred(widget, *args, **kwargs):
        # These two CTk draws force a synchronous idle pass on their canvas.
        # Let Tk render it in the normal event loop, after this draw returns.
        canvas = widget._canvas
        previous = canvas.__dict__.get("update_idletasks", _missing)
        canvas.update_idletasks = lambda: None
        try:
            return draw(widget, *args, **kwargs)
        finally:
            if previous is _missing:
                del canvas.update_idletasks
            else:
                canvas.update_idletasks = previous
    return deferred


def install_dpi_resize_guard() -> None:
    """Defer idle layout until all widgets have received the new DPI.

    CTk scrollbar/option-menu draws call update_idletasks mid-rescale. That
    processes Configure/scroll callbacks against mixed old/new sizes and can
    recurse indefinitely. Tk processes the deferred work normally as soon as
    the scaling callback returns. Preserve logical window dimensions as well:
    the installed CTk block_update_dimensions_event mistakenly clears its flag.
    """
    if getattr(ScalingTracker.update_scaling_callbacks_for_window, "_money_hunter_dpi_guard", False):
        return
    update_window = ScalingTracker.update_scaling_callbacks_for_window
    update_all = ScalingTracker.update_scaling_callbacks_all

    @classmethod
    @wraps(update_window)
    def guarded_window(cls, window):
        with _scaling_layout((window,)):
            return update_window(window)

    @classmethod
    @wraps(update_all)
    def guarded_all(cls):
        with _scaling_layout(tuple(cls.window_widgets_dict)):
            return update_all()

    guarded_window.__func__._money_hunter_dpi_guard = True
    ctk.CTkScrollbar._draw = _without_idle_flush(ctk.CTkScrollbar._draw)
    ctk.CTkOptionMenu._draw = _without_idle_flush(ctk.CTkOptionMenu._draw)
    ScalingTracker.update_scaling_callbacks_for_window = guarded_window
    ScalingTracker.update_scaling_callbacks_all = guarded_all
