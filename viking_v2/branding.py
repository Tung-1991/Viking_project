"""Display branding only; package names, runtime paths and IDs stay compatible."""

from . import __version__

APP_NAME = "Money Hunter"
APP_VERSION = __version__


def window_title(section: str) -> str:
    return f"{APP_NAME} · {section}"
