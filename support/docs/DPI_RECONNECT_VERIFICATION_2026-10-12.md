# UI DPI reconnect verification — 2026-10-12

The reported VM UI enlarged after entering a remote session, then stopped responding
to interaction. DNS/TCP tests supplied by the operator succeeded from the VM's
Ethernet0 interface at that time. DNS recovery alone does not establish UI health.

## Reproduction and impact

- Run the real application constructor with broker/service startup disabled, socket
  connections blocked, dotenv disabled, and account/config paths redirected to a
  temporary directory. Open the actual settings popups, change the detected DPI
  through 100%/125%/150%, switch PREVIEW and generate main-window clicks.
- Expected: Tk's 100 ms heartbeat completes before Windows' unresponsive threshold,
  and returning to 100% restores the logical window dimensions.
- Before the fix: the probe exceeded 35/45 seconds. Captured stacks repeatedly entered
  CTkScrollbar._draw -> canvas.update_idletasks -> scroll/Configure callbacks -> draw.
  CTkOptionMenu also forced an idle pass while the widget tree was partially rescaled.
- Relevant application paths: dashboard/window.py `_on_dashboard_resize` queued two
  idle jobs for every descendant Configure; dashboard/panels.py `_sync_preview_scrollbar`
  could schedule another immediate idle pass. The installed CTk root/toplevel
  `block_update_dimensions_event` method clears rather than sets its flag.
- Impact: UI interaction can stall during DPI transitions, including the main-loop
  callbacks which consume trading decisions. This reproduces a failure consistent
  with the report; it does not establish the exact stack of the killed VM process.

## Final change

`dashboard/ui_compat.py` lets Tk process idle layout after scrollbar/option-menu
drawing returns and protects logical root/toplevel dimensions during a DPI update.
Temporary canvas overrides and dimension flags are restored in `finally` blocks.
The installer is idempotent. Dashboard resize requests are coalesced into a deferred
25 ms pass, and the preview height recheck yields to the event loop.

No live broker submission, account-runtime change, real Telegram delivery, secret read,
strategy change, or dependency-file edit was performed by this verification.

## Validation

All tests ran through `support/tools/run_offline.py`.

- 289 related tests passed, 0 failures/errors/skips.
- Both REAL and PAPER complete nine live DPI transitions with interaction; returning
  to 100% retains the original logical size. The same probe asserts an empty order
  queue and BOT OFF throughout. A drawing-error case checks normal Tk idle processing
  resumes after an exception.
- Existing startup, popup/layout, loss-lock, history/normalization, TRACE, Telegram
  batching and fake-broker business-flow tests passed.
- Evidence: `.artifacts/dpi_reconnect_20261012/verified.xml`, `real_output.txt`,
  `paper_output.txt`; original failure in
  `.artifacts/remote_dpi_probe_20261012/verified.xml`.

The tests simulate DPI changes, not VMware/RDP drivers or a full 24-hour VM session.
