"""Display branding only; package names, runtime paths and IDs stay compatible."""

APP_NAME = "Money Hunter"


def window_title(section: str) -> str:
    return f"{APP_NAME} · {section}"
