from __future__ import annotations

from collections.abc import Iterable


# One source of truth shared by settings, live orders, UI and backtest.
# NORMAL stays as the internal key during the current compatibility phase;
# operators only see the PROTECT label.
EXIT_MODES: tuple[str, ...] = ("TP", "NORMAL", "IND_EXIT")
EXIT_MODE_LABELS: dict[str, str] = {
    "TP": "TP",
    "NORMAL": "PROTECT",
    "IND_EXIT": "E",
}
NORMAL_POLICIES: tuple[str, ...] = ("CLASSIC", "AUTO")


def normalize_exit_modes(values: Iterable[object] | None) -> list[str]:
    """Return unique, supported internal exit-mode keys in input order."""
    return list(
        dict.fromkeys(
            str(value or "").strip().upper()
            for value in (values or ())
            if str(value or "").strip().upper() in EXIT_MODES
        )
    )


def exit_mode_label(value: object) -> str:
    return EXIT_MODE_LABELS.get(str(value or "").strip().upper(), "")


def normalize_normal_policy(value: object) -> str:
    policy = str(value or "CLASSIC").strip().upper()
    return policy if policy in NORMAL_POLICIES else "CLASSIC"
